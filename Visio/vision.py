"""
vision.py — Cœur "traitement d'image" du projet.

POINT 3 : extraire des caractéristiques simples d'une photo de poubelle.
POINT 4 : appliquer des RÈGLES CONDITIONNELLES (pas de machine learning)
          pour estimer le niveau de remplissage à partir de ces caractéristiques.

Idée générale :
  Une poubelle PLEINE contient des déchets en désordre -> beaucoup de contours,
  beaucoup de texture, un contraste élevé.
  Une poubelle VIDE montre une surface plus lisse et uniforme -> peu de contours,
  peu de texture.
On mesure donc surtout la "densité de contours" et la "texture", et on compare
à des seuils calibrés.
"""

import os
import json
import numpy as np
import cv2

from database import NIVEAU_VERS_LABEL


# =========================================================
# POINT 3 — EXTRACTION DES CARACTÉRISTIQUES
# =========================================================

def extraire_caracteristiques(chemin_image):
    """
    Lit une image et renvoie un dictionnaire de caractéristiques simples.
    Toutes les valeurs sont des nombres, faciles à stocker et à comparer.
    """
    image = cv2.imread(chemin_image)
    if image is None:
        raise ValueError(f"Impossible de lire l'image : {chemin_image}")

    hauteur, largeur = image.shape[:2]
    gris = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # --- Couleur moyenne (BGR -> on renvoie en RGB pour la lisibilité) ---
    moyenne_bgr = image.mean(axis=(0, 1))
    couleur_moyenne_rgb = [
        round(float(moyenne_bgr[2]), 1),  # R
        round(float(moyenne_bgr[1]), 1),  # G
        round(float(moyenne_bgr[0]), 1),  # B
    ]
    luminosite_moyenne = round(float(gris.mean()), 2)

    # --- Contraste = écart-type des niveaux de gris ---
    contraste = round(float(gris.std()), 2)

    # --- Densité de contours (Canny) ---
    # Proportion de pixels qui sont des bords. Plus c'est élevé, plus l'image
    # est "chargée" en détails -> indice d'une poubelle pleine.
    contours = cv2.Canny(gris, 100, 200)
    densite_contours = round(float(np.count_nonzero(contours) / contours.size), 4)

    # --- Texture = variance du Laplacien ---
    # Mesure la quantité de détails/netteté. Surface lisse (vide) -> faible.
    laplacien = cv2.Laplacian(gris, cv2.CV_64F)
    texture = round(float(laplacien.var()), 2)

    # --- Taille du fichier (octets) ---
    taille_fichier = os.path.getsize(chemin_image)

    # --- Histogrammes (demandés par le cahier des charges) ---
    # On résume chaque histogramme en 8 "bins" (tranches) pour rester léger
    # et stockable, tout en gardant la forme de la distribution.
    def histogramme_8bins(canal):
        hist = cv2.calcHist([canal], [0], None, [8], [0, 256]).flatten()
        total = hist.sum()
        if total > 0:
            hist = (hist / total * 100).round(1)  # en pourcentage
        return hist.tolist()

    b, g, r = cv2.split(image)
    hist_rouge = histogramme_8bins(r)
    hist_vert = histogramme_8bins(g)
    hist_bleu = histogramme_8bins(b)
    hist_luminance = histogramme_8bins(gris)

    return {
        "largeur": largeur,
        "hauteur": hauteur,
        "taille_fichier": taille_fichier,
        "couleur_moyenne_rgb": couleur_moyenne_rgb,
        "luminosite_moyenne": luminosite_moyenne,
        "contraste": contraste,
        "densite_contours": densite_contours,
        "texture": texture,
        "histogramme_rouge": hist_rouge,
        "histogramme_vert": hist_vert,
        "histogramme_bleu": hist_bleu,
        "histogramme_luminance": hist_luminance,
    }


# =========================================================
# CONTRÔLE DE CONFORMITÉ (sans ML)
# =========================================================
# On rejette les photos inexploitables AVANT d'analyser le niveau.
# Remarque honnête : "est-ce vraiment une poubelle ?" relèverait de la
# reconnaissance d'objet (deep learning), hors périmètre. On se limite donc
# à des règles de QUALITÉ + on s'appuie sur le contexte du QR code (le citoyen
# a forcément scanné une poubelle précise).

# Seuils de conformité par défaut (utilisés en secours si la base est indisponible).
# Les vraies valeurs sont lues dans la table config_seuils (modifiables via l'admin).
SEUIL_FLOU = 60.0
LUMINOSITE_MIN = 35
LUMINOSITE_MAX = 225
SEUIL_UNIFORME_CONTRASTE = 12.0
SEUIL_UNIFORME_CONTOURS = 0.008
DIMENSION_MIN = 150


def verifier_conformite(features, seuils=None):
    """
    Vérifie si une photo est exploitable à partir de ses caractéristiques.
    Renvoie (conforme: bool, raison: str|None).
    Si conforme=True, raison=None.

    Les seuils sont lus depuis la base (table config_seuils) pour être
    configurables via l'interface. On passe `seuils` en paramètre pour éviter
    de relire la base plusieurs fois.

    L'ordre des tests va du plus spécifique au plus général, pour donner
    au citoyen le message le plus juste.
    """
    if seuils is None:
        from database import get_seuils
        seuils = get_seuils()

    # 1) Résolution suffisante
    if features["largeur"] < seuils["dimension_min"] or features["hauteur"] < seuils["dimension_min"]:
        return False, "Image de trop faible résolution. Reprenez une photo de plus près."

    # 2) Exposition correcte (testée avant le flou : une photo sombre/claire
    #    a peu de détails et serait sinon faussement signalée comme floue)
    lum = features["luminosite_moyenne"]
    if lum < seuils["luminosite_min"]:
        return False, "Photo trop sombre. Rapprochez-vous ou éclairez la scène."
    if lum > seuils["luminosite_max"]:
        return False, "Photo surexposée. Évitez le contre-jour et reprenez la photo."

    # 3) Image trop uniforme (mur, ciel, sol uni...) = ne montre pas de poubelle
    if (features["contraste"] < seuils["seuil_uniforme_contraste"]
            and features["densite_contours"] < seuils["seuil_uniforme_contours"]):
        return False, "La photo ne semble pas montrer de poubelle. Cadrez bien la poubelle."

    # 4) Netteté (anti-flou) : la variance du Laplacien = notre mesure 'texture'
    if features["texture"] < seuils["seuil_flou"]:
        return False, "Photo trop floue. Tenez le téléphone stable et reprenez la photo."

    return True, None


# =========================================================
# POINT 4 — RÈGLES CONDITIONNELLES (sans ML)
# =========================================================

# Seuils par défaut (secours). Les vraies valeurs viennent de la base (config_seuils).
SEUIL_CONTOURS_BAS = 0.04
SEUIL_CONTOURS_HAUT = 0.09
SEUIL_TEXTURE_BAS = 100.0
SEUIL_TEXTURE_HAUT = 400.0


def _appliquer_regles_libres(features):
    """
    Évalue les règles libres définies par l'utilisateur (table regles_libres),
    par ordre de priorité. La première règle ACTIVE qui s'applique l'emporte.
    Renvoie (label, explication) ou (None, None) si aucune ne s'applique.
    """
    from database import lister_regles

    ops = {
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
    }

    for regle in lister_regles():
        if not regle["active"]:
            continue
        carac = regle["caracteristique"]
        if carac not in features:
            continue
        op = ops.get(regle["operateur"])
        if op is None:
            continue
        if op(features[carac], regle["valeur"]):
            explication = (
                f"Règle libre #{regle['id']} : SI {carac} {regle['operateur']} "
                f"{regle['valeur']} ALORS {regle['label']}"
            )
            return regle["label"], explication

    return None, None


def classifier_par_regles(features, seuils=None):
    """
    Décide d'un niveau (vide / pleine) à partir des caractéristiques.
    Renvoie (label, explication).

    Logique :
      1) On applique d'abord les RÈGLES LIBRES (définies via l'interface).
         La première qui s'applique l'emporte.
      2) Sinon, logique par SEUILS : on combine deux indices (densité de contours
         et texture). Chaque indice vote "pleine" s'il dépasse son seuil bas.
         Si au moins un des deux vote "pleine", la poubelle est classée pleine.
    """
    # 1) Règles libres prioritaires
    label_libre, expli_libre = _appliquer_regles_libres(features)
    if label_libre is not None:
        return label_libre, expli_libre

    # 2) Logique par seuils (lus depuis la base)
    if seuils is None:
        from database import get_seuils
        seuils = get_seuils()

    contours = features["densite_contours"]
    texture = features["texture"]

    # Chaque indicateur "vote" : au-dessus du seuil bas => signe de remplissage
    vote_contours = contours >= seuils["seuil_contours_bas"]
    vote_texture = texture >= seuils["seuil_texture_bas"]

    # Une poubelle pleine présente beaucoup de contours OU beaucoup de texture
    if vote_contours or vote_texture:
        label = "pleine"
    else:
        label = "vide"

    explication = (
        f"contours={contours} ({'≥' if vote_contours else '<'} {seuils['seuil_contours_bas']}), "
        f"texture={texture} ({'≥' if vote_texture else '<'} {seuils['seuil_texture_bas']}) "
        f"-> {label}"
    )
    return label, explication



def analyser_image(chemin_image):
    """
    Pipeline complet pour une photo :
      1) extrait les caractéristiques (Point 3)
      2) vérifie la conformité (qualité de la photo)
      3) si conforme, applique les règles de niveau (Point 4)

    Renvoie un dict :
      {
        "features": {...},
        "conforme": bool,
        "raison_rejet": str|None,   # rempli seulement si non conforme
        "verdict": str|None,        # vide/a_moitie/pleine, seulement si conforme
        "explication": str|None,
      }
    """
    features = extraire_caracteristiques(chemin_image)

    # On charge les seuils une seule fois (configurables via l'admin).
    from database import get_seuils
    seuils = get_seuils()

    conforme, raison = verifier_conformite(features, seuils)

    if not conforme:
        return {
            "features": features,
            "conforme": False,
            "raison_rejet": raison,
            "verdict": None,
            "explication": None,
        }

    verdict, explication = classifier_par_regles(features, seuils)
    return {
        "features": features,
        "conforme": True,
        "raison_rejet": None,
        "verdict": verdict,
        "explication": explication,
    }


def comparer_citoyen_ia(eval_citoyen, verdict_ia):
    """
    L'IA fait foi : le statut final = verdict de l'IA.
    On note si le citoyen était d'accord (1) ou non (0).
    Renvoie (statut_final, accord).
    """
    accord = 1 if eval_citoyen == verdict_ia else 0
    statut_final = verdict_ia  # l'IA confirme ou corrige
    return statut_final, accord


if __name__ == "__main__":
    # Petit test manuel : python vision.py chemin/vers/image.jpg
    import sys
    if len(sys.argv) > 1:
        chemin = sys.argv[1]
        res = analyser_image(chemin)
        print("Caractéristiques :")
        print(json.dumps(res["features"], indent=2, ensure_ascii=False))
        if res["conforme"]:
            print(f"\nConforme : oui")
            print(f"Verdict IA : {res['verdict']}")
            print(f"Explication : {res['explication']}")
        else:
            print(f"\nConforme : NON")
            print(f"Raison du rejet : {res['raison_rejet']}")
