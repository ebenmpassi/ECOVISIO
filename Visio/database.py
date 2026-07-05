"""
Gestion de la base de données SQLite pour la plateforme de traitement des déchets.

Deux tables :
  - poubelles    : la liste des poubelles déployées (ID du QR + localisation pré-enregistrée)
  - signalements : chaque photo envoyée par un citoyen, avec :
        * son évaluation citoyenne (niveau perçu par la personne)
        * les caractéristiques extraites de l'image (Point 3)
        * le verdict de l'IA / règles (Point 4)
        * le statut final retenu
"""

import os
import sqlite3
from datetime import datetime
import hashlib

DB_NAME = "smartwaste.db"

# Les deux niveaux de remplissage autorisés (binaire, conforme au cahier des charges).
LABELS_VALIDES = ("vide", "pleine")
# Valeur numérique pour pouvoir comparer / voter.
LABEL_VERS_NIVEAU = {"vide": 0, "pleine": 1}
NIVEAU_VERS_LABEL = {0: "vide", 1: "pleine"}
# Libellés lisibles pour l'affichage
LABEL_AFFICHAGE = {"vide": "Vide", "pleine": "Pleine"}


def get_connection():
    """Ouvre une connexion à la base. row_factory permet d'accéder aux colonnes par nom."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crée les tables si elles n'existent pas encore."""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            salt BLOB NOT NULL,
            hash BLOB NOT NULL,
            role TEXT NOT NULL CHECK(role IN('admin', 'agent'))
            )
        """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS poubelles (
            id        TEXT PRIMARY KEY,
            nom_lieu  TEXT NOT NULL,
            latitude  REAL NOT NULL,
            longitude REAL NOT NULL,
            active    INTEGER NOT NULL DEFAULT 1
        )
    """)

    # eval_citoyen   : niveau choisi par la personne (vide / a_moitie / pleine)
    # verdict_ia     : niveau décidé par les règles sur la photo (Point 4)
    # statut         : statut final retenu (= verdict_ia : l'IA fait foi)
    # accord         : 1 si eval_citoyen == verdict_ia, 0 sinon
    # features_json  : caractéristiques de l'image au format JSON (Point 3)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signalements (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            poubelle_id   TEXT NOT NULL,
            chemin_image  TEXT NOT NULL,
            date_upload   TEXT NOT NULL,
            eval_citoyen  TEXT,
            verdict_ia    TEXT,
            statut        TEXT,
            accord        INTEGER,
            features_json TEXT,
            meteo_temp    REAL,
            meteo_categorie TEXT,
            jour_semaine  TEXT,
            FOREIGN KEY (poubelle_id) REFERENCES poubelles(id)
        )
    """)

    # Table des tentatives d'upload, pour la limitation de débit (anti-spam).
    # On y note chaque tentative (réussie ou non) avec l'IP, la poubelle et l'heure.
    # Sert à compter les uploads récents par couple (IP + poubelle).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tentatives (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ip           TEXT NOT NULL,
            poubelle_id  TEXT NOT NULL,
            date_tent    TEXT NOT NULL
        )
    """)
    # Index pour accélérer les requêtes de comptage
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_tentatives_ip_poubelle
        ON tentatives (ip, poubelle_id, date_tent)
    """)

    # Table des seuils configurables (règles de classification ajustables).
    # Une seule ligne (id=1) contient tous les seuils. Modifiable via l'admin.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS config_seuils (
            id                       INTEGER PRIMARY KEY CHECK (id = 1),
            seuil_contours_bas       REAL NOT NULL,
            seuil_contours_haut      REAL NOT NULL,
            seuil_texture_bas        REAL NOT NULL,
            seuil_texture_haut       REAL NOT NULL,
            seuil_flou               REAL NOT NULL,
            luminosite_min           REAL NOT NULL,
            luminosite_max           REAL NOT NULL,
            seuil_uniforme_contraste REAL NOT NULL,
            seuil_uniforme_contours  REAL NOT NULL,
            dimension_min            INTEGER NOT NULL
        )
    """)

    # Table des règles libres définies par l'utilisateur.
    #   caracteristique : nom de la feature (densite_contours, texture, contraste,
    #                     luminosite_moyenne, taille_fichier...)
    #   operateur       : '>', '<', '>=', '<=' 
    #   valeur          : seuil de comparaison
    #   label           : niveau attribué si la règle s'applique (vide/a_moitie/pleine)
    #   priorite        : ordre d'évaluation (plus petit = évalué en premier)
    #   active          : 1 = règle prise en compte, 0 = désactivée
    cur.execute("""
        CREATE TABLE IF NOT EXISTS regles_libres (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            caracteristique TEXT NOT NULL,
            operateur       TEXT NOT NULL,
            valeur          REAL NOT NULL,
            label           TEXT NOT NULL,
            priorite        INTEGER NOT NULL DEFAULT 100,
            active          INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Table du jeu de test étiqueté (vérité terrain pour l'évaluation).
    # Étiqueté à la main par l'agent, séparé des signalements citoyens.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jeu_test (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            chemin_image  TEXT NOT NULL UNIQUE,
            vrai_label    TEXT,
            features_json TEXT,
            verdict_cache TEXT
        )
    """)

    conn.commit()
    conn.close()

    # Insère les seuils par défaut si la table est vide (première initialisation).
    _inserer_seuils_par_defaut()


# Seuils par défaut (valeurs de départ, à calibrer ensuite).
SEUILS_PAR_DEFAUT = {
    "seuil_contours_bas": 0.04,
    "seuil_contours_haut": 0.09,
    "seuil_texture_bas": 100.0,
    "seuil_texture_haut": 400.0,
    "seuil_flou": 60.0,
    "luminosite_min": 35.0,
    "luminosite_max": 225.0,
    "seuil_uniforme_contraste": 12.0,
    "seuil_uniforme_contours": 0.008,
    "dimension_min": 150,
}


def _inserer_seuils_par_defaut():
    """Insère la ligne de seuils par défaut si elle n'existe pas encore."""
    conn = get_connection()
    existe = conn.execute("SELECT COUNT(*) FROM config_seuils").fetchone()[0]
    if existe == 0:
        cols = ", ".join(["id"] + list(SEUILS_PAR_DEFAUT.keys()))
        placeholders = ", ".join(["?"] * (len(SEUILS_PAR_DEFAUT) + 1))
        valeurs = [1] + list(SEUILS_PAR_DEFAUT.values())
        conn.execute(f"INSERT INTO config_seuils ({cols}) VALUES ({placeholders})", valeurs)
        conn.commit()
    conn.close()


def hash_mot_de_passe(mot_de_passe, salt=None):
    if salt is None:
        salt = os.urandom(16)  # 16 bytes aléatoires

    hash_bytes = hashlib.pbkdf2_hmac(
        'sha256',
        mot_de_passe.encode(),
        salt,
        100000  # nombre d’itérations (sécurité)
    )

    return salt, hash_bytes

def creer_compte(username, mot_de_passe, role="agent"):
    salt, hash_bytes = hash_mot_de_passe(mot_de_passe)

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO accounts (username, salt, hash, role) VALUES (?, ?, ?, ?)",
            (username, salt, hash_bytes, role)
        )
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def verifier_compte(username, mot_de_passe):
    conn = get_connection()
    user = conn.execute(
        "SELECT * FROM accounts WHERE username = ?",
        (username,)
    ).fetchone()
    conn.close()

    if user is None:
        return None

    salt = user["salt"]
    hash_stocke = user["hash"]

    _, hash_test = hash_mot_de_passe(mot_de_passe, salt)

    if hash_test == hash_stocke:
        return user

    return None

def get_seuils():
    """Retourne les seuils actuels sous forme de dict."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM config_seuils WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return dict(SEUILS_PAR_DEFAUT)
    d = dict(row)
    d.pop("id", None)
    return d


def maj_seuils(nouveaux):
    """Met à jour les seuils. nouveaux = dict {nom_seuil: valeur}."""
    seuils = get_seuils()
    seuils.update({k: v for k, v in nouveaux.items() if k in seuils})
    conn = get_connection()
    set_clause = ", ".join([f"{k} = ?" for k in seuils.keys()])
    conn.execute(
        f"UPDATE config_seuils SET {set_clause} WHERE id = 1",
        list(seuils.values()),
    )
    conn.commit()
    conn.close()


def reinitialiser_seuils():
    """Remet les seuils à leurs valeurs par défaut."""
    maj_seuils(dict(SEUILS_PAR_DEFAUT))


# ---------- Règles libres configurables ----------

CARACTERISTIQUES_REGLES = (
    "densite_contours", "texture", "contraste",
    "luminosite_moyenne", "taille_fichier",
    # Critères de débordement (déchets au sol)
    "densite_contours_bas", "taches_claires_bas", "diversite_couleurs",
)
OPERATEURS_REGLES = (">", "<", ">=", "<=")


def lister_regles():
    """Retourne toutes les règles libres, triées par priorité croissante."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM regles_libres ORDER BY priorite ASC, id ASC"
    ).fetchall()
    conn.close()
    return rows


def ajouter_regle(caracteristique, operateur, valeur, label, priorite=100):
    """Ajoute une règle libre."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO regles_libres (caracteristique, operateur, valeur, label, priorite, active)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (caracteristique, operateur, valeur, label, priorite),
    )
    conn.commit()
    conn.close()


def supprimer_regle(regle_id):
    """Supprime une règle libre par son id."""
    conn = get_connection()
    conn.execute("DELETE FROM regles_libres WHERE id = ?", (regle_id,))
    conn.commit()
    conn.close()


def basculer_regle(regle_id):
    """Active/désactive une règle libre."""
    conn = get_connection()
    conn.execute("UPDATE regles_libres SET active = 1 - active WHERE id = ?", (regle_id,))
    conn.commit()
    conn.close()


# ---------- Jeu de test étiqueté (évaluation des métriques) ----------

def importer_images_jeu_test(dossier):
    """
    Scanne un dossier et ajoute toute image trouvée au jeu de test (sans label).
    Idempotent : ignore les images déjà présentes. Renvoie le nombre d'ajouts.
    """
    extensions = (".jpg", ".jpeg", ".png")
    ajouts = 0
    if not os.path.isdir(dossier):
        return 0
    conn = get_connection()
    for nom in sorted(os.listdir(dossier)):
        if nom.lower().endswith(extensions):
            chemin = os.path.join(dossier, nom)
            cur = conn.execute(
                "INSERT OR IGNORE INTO jeu_test (chemin_image, vrai_label) VALUES (?, NULL)",
                (chemin,),
            )
            if cur.rowcount:
                ajouts += 1
    conn.commit()
    conn.close()
    return ajouts


def lister_jeu_test():
    """Toutes les images du jeu de test (étiquetées ou non)."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM jeu_test ORDER BY id").fetchall()
    conn.close()
    return rows


def prochain_jeu_test_non_etiquete():
    """Prochaine image du jeu de test sans label, ou None si tout est étiqueté."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM jeu_test WHERE vrai_label IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    conn.close()
    return row


def etiqueter_jeu_test(item_id, label):
    """Enregistre la vérité terrain d'une image du jeu de test."""
    if label not in LABELS_VALIDES:
        raise ValueError(f"Label invalide : {label}")
    conn = get_connection()
    conn.execute("UPDATE jeu_test SET vrai_label = ? WHERE id = ?", (label, item_id))
    conn.commit()
    conn.close()


def cacher_analyse_jeu_test(item_id, features_json, verdict):
    """
    Mémorise les caractéristiques et le verdict calculés pour une image du jeu
    de test, afin de ne pas refaire l'analyse à chaque affichage des résultats.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE jeu_test SET features_json = ?, verdict_cache = ? WHERE id = ?",
        (features_json, verdict, item_id),
    )
    conn.commit()
    conn.close()


def vider_cache_jeu_test():
    """Efface le cache d'analyse (à appeler si l'extraction d'image change)."""
    conn = get_connection()
    conn.execute("UPDATE jeu_test SET features_json = NULL, verdict_cache = NULL")
    conn.commit()
    conn.close()


def compter_jeu_test():
    """Compte total / étiquetées / restantes dans le jeu de test."""
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM jeu_test").fetchone()[0]
    etiquetes = conn.execute(
        "SELECT COUNT(*) FROM jeu_test WHERE vrai_label IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    return {"total": total, "etiquetes": etiquetes, "restants": total - etiquetes}


def vider_jeu_test():
    """Supprime tout le jeu de test (pour repartir de zéro)."""
    conn = get_connection()
    conn.execute("DELETE FROM jeu_test")
    conn.commit()
    conn.close()


# ---------- Analyse des corrélations contexte / débordement (météo, jour) ----------

def correlation_jour_semaine():
    """
    Pour chaque jour de la semaine, calcule le taux de signalements 'pleine'.
    Renvoie une liste de dicts triée selon l'ordre des jours.
    """
    ordre = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    conn = get_connection()
    rows = conn.execute(
        """SELECT jour_semaine AS jour,
                  COUNT(*) AS total,
                  SUM(CASE WHEN statut='pleine' THEN 1 ELSE 0 END) AS pleines
           FROM signalements
           WHERE jour_semaine IS NOT NULL
           GROUP BY jour_semaine"""
    ).fetchall()
    conn.close()

    par_jour = {r["jour"]: {"total": r["total"], "pleines": r["pleines"]} for r in rows}
    resultats = []
    for jour in ordre:
        d = par_jour.get(jour)
        if d and d["total"] > 0:
            taux = round(100 * d["pleines"] / d["total"])
        else:
            taux = None
        resultats.append({
            "jour": jour,
            "total": d["total"] if d else 0,
            "taux_pleine": taux,
        })
    return resultats


def correlation_meteo():
    """
    Pour chaque catégorie météo, calcule le taux de signalements 'pleine'.
    Renvoie une liste de dicts triée du taux le plus élevé au plus bas.
    """
    conn = get_connection()
    rows = conn.execute(
        """SELECT meteo_categorie AS cat,
                  COUNT(*) AS total,
                  SUM(CASE WHEN statut='pleine' THEN 1 ELSE 0 END) AS pleines
           FROM signalements
           WHERE meteo_categorie IS NOT NULL
           GROUP BY meteo_categorie"""
    ).fetchall()
    conn.close()

    resultats = []
    for r in rows:
        if r["total"] > 0:
            resultats.append({
                "categorie": r["cat"],
                "total": r["total"],
                "taux_pleine": round(100 * r["pleines"] / r["total"]),
            })
    resultats.sort(key=lambda x: x["taux_pleine"], reverse=True)
    return resultats


# ---------- Poubelles ----------

def ajouter_poubelle(id_poubelle, nom_lieu, latitude, longitude):
    """Enregistre une nouvelle poubelle (idempotent : ignore si l'id existe déjà)."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO poubelles (id, nom_lieu, latitude, longitude) VALUES (?, ?, ?, ?)",
            (id_poubelle, nom_lieu, latitude, longitude),
        )
        conn.commit()
    finally:
        conn.close()


def get_poubelle(id_poubelle):
    """Retourne une poubelle par son id, ou None si elle n'existe pas."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM poubelles WHERE id = ?", (id_poubelle,)
    ).fetchone()
    conn.close()
    return row


def lister_poubelles(actives_seulement=True):
    """
    Retourne les poubelles. Par défaut, seulement les actives
    (les désactivées disparaissent de la carte, du dashboard, etc.).
    """
    conn = get_connection()
    if actives_seulement:
        rows = conn.execute(
            "SELECT * FROM poubelles WHERE active = 1 ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM poubelles ORDER BY id").fetchall()
    conn.close()
    return rows


def desactiver_poubelle(id_poubelle):
    """Désactive une poubelle (soft delete) : elle est masquée mais son historique reste."""
    conn = get_connection()
    conn.execute("UPDATE poubelles SET active = 0 WHERE id = ?", (id_poubelle,))
    conn.commit()
    conn.close()


def reactiver_poubelle(id_poubelle):
    """Réactive une poubelle précédemment désactivée."""
    conn = get_connection()
    conn.execute("UPDATE poubelles SET active = 1 WHERE id = ?", (id_poubelle,))
    conn.commit()
    conn.close()


# ---------- Signalements ----------

def ajouter_signalement(poubelle_id, chemin_image, eval_citoyen,
                        verdict_ia, statut, accord, features_json,
                        meteo_temp=None, meteo_categorie=None, jour_semaine=None):
    """
    Crée un signalement complet : photo + évaluation citoyenne + verdict IA + statut
    + contexte (météo, jour de la semaine).
    Retourne l'id du signalement créé.
    """
    conn = get_connection()
    date_upload = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """INSERT INTO signalements
           (poubelle_id, chemin_image, date_upload,
            eval_citoyen, verdict_ia, statut, accord, features_json,
            meteo_temp, meteo_categorie, jour_semaine)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (poubelle_id, chemin_image, date_upload,
         eval_citoyen, verdict_ia, statut, accord, features_json,
         meteo_temp, meteo_categorie, jour_semaine),
    )
    conn.commit()
    sig_id = cur.lastrowid
    conn.close()
    return sig_id


def marquer_poubelle_videe(poubelle_id):
    """
    Enregistre une COLLECTE : la poubelle vient d'être vidée par un agent.

    On crée un signalement spécial "vide" horodaté (sans photo), qui compte comme
    le signalement le plus récent. Comme le statut de la poubelle est calculé à
    partir des derniers signalements, la poubelle repasse ainsi naturellement à
    "vide", tout en gardant une trace datée de la collecte dans l'historique.

    Retourne l'id du signalement de collecte créé.
    """
    return ajouter_signalement(
        poubelle_id=poubelle_id,
        chemin_image="",            # pas de photo pour une collecte
        eval_citoyen="collecte",    # marqueur : action d'agent, pas un citoyen
        verdict_ia="vide",
        statut="vide",
        accord=1,
        features_json="{}",
    )


def get_signalement(signalement_id):
    """Retourne un signalement (avec le nom du lieu de sa poubelle)."""
    conn = get_connection()
    row = conn.execute(
        """SELECT s.*, p.nom_lieu
           FROM signalements s
           JOIN poubelles p ON s.poubelle_id = p.id
           WHERE s.id = ?""",
        (signalement_id,),
    ).fetchone()
    conn.close()
    return row


def derniers_signalements(poubelle_id, n=5):
    """Retourne les n derniers signalements d'une poubelle (du plus récent au plus ancien)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM signalements
           WHERE poubelle_id = ?
           ORDER BY id DESC
           LIMIT ?""",
        (poubelle_id, n),
    ).fetchall()
    conn.close()
    return rows


def calculer_statut(rows):
    """
    Détermine le statut actuel d'une poubelle à partir de signalements DÉJÀ
    récupérés (aucun accès à la base de données ici).

    Statut = vote majoritaire des signalements ; en cas d'égalité, le plus
    récent tranche. Retourne un label ou None.
    """
    if not rows:
        return None

    # Règle prioritaire : si le signalement le plus récent est une COLLECTE
    # (poubelle vidée par un agent), la poubelle est "vide", quel que soit
    # l'historique. Une collecte prime sur les anciens signalements citoyens.
    plus_recent = rows[0]
    try:
        if plus_recent["eval_citoyen"] == "collecte":
            return "vide"
    except (KeyError, IndexError):
        pass

    votes = {}
    for r in rows:
        statut = r["statut"]
        if statut in LABELS_VALIDES:
            votes[statut] = votes.get(statut, 0) + 1

    if not votes:
        return None

    max_votes = max(votes.values())
    gagnants = [lbl for lbl, nb in votes.items() if nb == max_votes]
    if len(gagnants) == 1:
        return gagnants[0]
    # Égalité : le plus récent parmi les gagnants tranche
    for r in rows:
        if r["statut"] in gagnants:
            return r["statut"]
    return gagnants[0]


def statut_actuel_poubelle(poubelle_id, n=5):
    """
    Calcule le statut "actuel" d'une poubelle par VOTE des n derniers signalements.
    En cas d'égalité, le signalement le plus récent tranche.
    Retourne un label (vide / pleine) ou None si aucun signalement.
    """
    rows = derniers_signalements(poubelle_id, n)
    return calculer_statut(rows)


def compter_signalements():
    """Statistiques globales : total et taux d'accord IA/citoyen."""
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM signalements").fetchone()[0]
    accords = conn.execute(
        "SELECT COUNT(*) FROM signalements WHERE accord = 1"
    ).fetchone()[0]
    conn.close()
    taux_accord = round(100 * accords / total) if total else 0
    return {"total": total, "accords": accords, "taux_accord": taux_accord}


# ---------- Données pour le dashboard agents (Point 5) ----------

def donnees_carte(n=5):
    """
    Pour chaque poubelle : ses coordonnées, son lieu et son statut actuel (par vote).
    Utilisé pour placer les marqueurs colorés sur la carte.
    """
    poubelles = lister_poubelles()
    data = []
    for p in poubelles:
        # Une seule requête par poubelle : on récupère les n derniers signalements
        # et on réutilise ces lignes pour le statut ET la date (au lieu de 2 requêtes).
        rows = derniers_signalements(p["id"], n)
        statut = calculer_statut(rows)
        derniere_maj = rows[0]["date_upload"] if rows else None
        data.append({
            "id": p["id"],
            "nom_lieu": p["nom_lieu"],
            "latitude": p["latitude"],
            "longitude": p["longitude"],
            "statut": statut,            # 'vide' / 'pleine' / None
            "derniere_maj": derniere_maj,
        })
    return data


def repartition_statuts(n=5):
    """Compte combien de poubelles sont vide / à moitié / pleine / sans donnée."""
    compte = {"vide": 0, "pleine": 0, "inconnu": 0}
    for p in lister_poubelles():
        statut = statut_actuel_poubelle(p["id"], n)
        if statut in LABELS_VALIDES:
            compte[statut] += 1
        else:
            compte["inconnu"] += 1
    return compte


def signalements_par_jour(derniers_jours=14):
    """
    Nombre de signalements par jour sur les N derniers jours.
    Renvoie deux listes parallèles : dates (YYYY-MM-DD) et comptes.
    """
    conn = get_connection()
    rows = conn.execute(
        """SELECT substr(date_upload, 1, 10) AS jour, COUNT(*) AS n
           FROM signalements
           GROUP BY jour
           ORDER BY jour DESC
           LIMIT ?""",
        (derniers_jours,),
    ).fetchall()
    conn.close()
    # On remet dans l'ordre chronologique (ancien -> récent)
    rows = list(reversed(rows))
    dates = [r["jour"] for r in rows]
    comptes = [r["n"] for r in rows]
    return dates, comptes


def poubelles_pleines(n=5):
    """Liste des poubelles actuellement pleines (pour les alertes / priorité de collecte)."""
    pleines = []
    for p in lister_poubelles():
        if statut_actuel_poubelle(p["id"], n) == "pleine":
            pleines.append(p)
    return pleines


def zones_a_risque(seuil_taux=0.4, mini_signalements=3):
    """
    Identifie les poubelles 'à risque de débordement' : celles dont une part
    importante des signalements historiques sont 'pleine'.
    On ne considère que les poubelles avec assez de signalements (fiabilité).
    Renvoie une liste de dicts triée du plus à risque au moins à risque.
    """
    conn = get_connection()
    rows = conn.execute(
        """SELECT poubelle_id,
                  COUNT(*) AS total,
                  SUM(CASE WHEN statut = 'pleine' THEN 1 ELSE 0 END) AS nb_pleines
           FROM signalements
           GROUP BY poubelle_id
           HAVING total >= ?""",
        (mini_signalements,),
    ).fetchall()
    conn.close()

    resultats = []
    for r in rows:
        taux = r["nb_pleines"] / r["total"]
        if taux >= seuil_taux:
            poub = get_poubelle(r["poubelle_id"])
            resultats.append({
                "id": r["poubelle_id"],
                "nom_lieu": poub["nom_lieu"] if poub else r["poubelle_id"],
                "taux_pleine": round(taux * 100),
                "total": r["total"],
            })
    resultats.sort(key=lambda x: x["taux_pleine"], reverse=True)
    return resultats


# ---------- Limitation de débit (anti-spam) ----------

# Paramètres : max 3 uploads par 20 minutes pour un même couple (IP + poubelle).
LIMITE_UPLOADS = 3
FENETRE_MINUTES = 20


def enregistrer_tentative(ip, poubelle_id):
    """Note une tentative d'upload (IP + poubelle + heure actuelle)."""
    conn = get_connection()
    date_tent = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO tentatives (ip, poubelle_id, date_tent) VALUES (?, ?, ?)",
        (ip, poubelle_id, date_tent),
    )
    conn.commit()
    conn.close()


def verifier_limite(ip, poubelle_id):
    """
    Vérifie si (ip, poubelle_id) a dépassé la limite d'uploads sur la fenêtre.
    Renvoie (autorise: bool, secondes_a_attendre: int).
      - autorise=True  -> la personne peut uploader (secondes_a_attendre=0)
      - autorise=False -> bloqué, secondes_a_attendre = temps avant déblocage
    """
    from datetime import timedelta

    maintenant = datetime.now()
    debut_fenetre = maintenant - timedelta(minutes=FENETRE_MINUTES)
    debut_str = debut_fenetre.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    # Récupère les tentatives récentes de ce couple, les plus anciennes d'abord
    rows = conn.execute(
        """SELECT date_tent FROM tentatives
           WHERE ip = ? AND poubelle_id = ? AND date_tent >= ?
           ORDER BY date_tent ASC""",
        (ip, poubelle_id, debut_str),
    ).fetchall()
    conn.close()

    nb_recent = len(rows)

    if nb_recent < LIMITE_UPLOADS:
        return True, 0

    # Limite atteinte : on calcule quand la plus ancienne tentative sortira
    # de la fenêtre de 20 min -> c'est le moment du déblocage.
    plus_ancienne = datetime.strptime(rows[0]["date_tent"], "%Y-%m-%d %H:%M:%S")
    deblocage = plus_ancienne + timedelta(minutes=FENETRE_MINUTES)
    secondes = int((deblocage - maintenant).total_seconds())
    secondes = max(secondes, 1)  # au moins 1s pour éviter d'afficher 0
    return False, secondes


def nettoyer_vieilles_tentatives():
    """
    Supprime les tentatives plus vieilles que la fenêtre (entretien de la table).
    À appeler de temps en temps pour ne pas laisser la table grossir indéfiniment.
    """
    from datetime import timedelta
    limite = (datetime.now() - timedelta(minutes=FENETRE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("DELETE FROM tentatives WHERE date_tent < ?", (limite,))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Base de données initialisée (smartwaste.db).")