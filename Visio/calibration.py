"""
calibration.py — Calibration automatique des seuils de classification.

Principe : on analyse les photos DÉJÀ ÉTIQUETÉES (jeu de test, vérité terrain),
on mesure les valeurs réelles de chaque caractéristique par classe, et on en
déduit les meilleurs seuils pour séparer vide / pleine.

C'est la démarche de "calibration empirique" : au lieu de deviner les seuils,
on les calcule à partir des données réelles.

Deux améliorations importantes :
  - On mesure TOUS les critères, y compris ceux de débordement (contours au sol,
    taches claires au sol, diversité des couleurs).
  - On lit les caractéristiques depuis le CACHE (features_json) quand il est à
    jour, pour ne pas réanalyser les photos à chaque chargement (rapidité).
  - On calcule un score de SÉPARABILITÉ par critère, pour indiquer lequel
    discrimine le mieux les classes.
"""

import os
import json
import statistics

from database import lister_jeu_test, LABELS_VALIDES, cacher_analyse_jeu_test
from vision import extraire_caracteristiques

# Les critères numériques qu'on calibre (nom technique -> libellé lisible)
CRITERES = {
    "densite_contours": "Densité de contours",
    "texture": "Texture",
    "densite_contours_bas": "Contours au sol",
    "taches_claires_bas": "Taches claires (sol)",
    "diversite_couleurs": "Diversité des couleurs",
}


def collecter_features_par_classe():
    """
    Pour chaque image étiquetée, récupère ses caractéristiques (via le cache si
    disponible et à jour, sinon en les recalculant une fois) et les range par classe.
    Renvoie un dict {classe: {critere: [valeurs...]}}.
    """
    data = {c: {crit: [] for crit in CRITERES} for c in LABELS_VALIDES}

    for it in lister_jeu_test():
        label = it["vrai_label"]
        chemin = it["chemin_image"]
        if label not in LABELS_VALIDES:
            continue

        # 1) Essayer le cache
        f = None
        if it["features_json"]:
            try:
                f = json.loads(it["features_json"])
            except (ValueError, TypeError):
                f = None

        # 2) Cache périmé (sans les nouveaux critères) ou absent -> recalcul
        if f is None or "taches_claires_bas" not in f:
            if not os.path.exists(chemin):
                continue
            try:
                f = extraire_caracteristiques(chemin)
            except Exception:
                continue
            # On met à jour le cache pour les prochains chargements
            cacher_analyse_jeu_test(it["id"], json.dumps(f, ensure_ascii=False), None)

        for crit in CRITERES:
            if crit in f:
                data[label][crit].append(f[crit])

    return data


def _stats(valeurs):
    """Petites statistiques d'une liste de valeurs."""
    if not valeurs:
        return None
    return {
        "n": len(valeurs),
        "moyenne": round(statistics.mean(valeurs), 4),
        "min": round(min(valeurs), 4),
        "max": round(max(valeurs), 4),
        "mediane": round(statistics.median(valeurs), 4),
        "ecart_type": round(statistics.pstdev(valeurs), 4) if len(valeurs) > 1 else 0.0,
    }


def _seuil_entre(classe_basse, classe_haute):
    """
    Seuil séparant deux classes : point médian entre les deux moyennes.
    Renvoie None si une classe manque de données.
    """
    if classe_basse is None or classe_haute is None:
        return None
    return round((classe_basse["moyenne"] + classe_haute["moyenne"]) / 2, 4)


def _separabilite(stat_vide, stat_pleine):
    """
    Mesure à quel point un critère sépare 'vide' de 'pleine'.
    On calcule un score inspiré de la distance de Fisher :
      écart des moyennes / somme des dispersions.
    Plus le score est grand, mieux le critère discrimine.
    Renvoie (score, sens) où sens = 'pleine>vide', 'vide>pleine' ou None.
    """
    if stat_vide is None or stat_pleine is None:
        return 0.0, None
    ecart = abs(stat_pleine["moyenne"] - stat_vide["moyenne"])
    dispersion = stat_vide["ecart_type"] + stat_pleine["ecart_type"]
    if dispersion == 0:
        # Pas de dispersion : séparable si les moyennes diffèrent
        score = 999.0 if ecart > 0 else 0.0
    else:
        score = round(ecart / dispersion, 3)
    sens = "pleine>vide" if stat_pleine["moyenne"] >= stat_vide["moyenne"] else "vide>pleine"
    return score, sens


def proposer_seuils():
    """
    Analyse les photos étiquetées et propose des seuils pour chaque critère,
    avec un diagnostic de séparabilité.
    """
    data = collecter_features_par_classe()

    # Statistiques par classe pour chaque critère
    stats = {c: {crit: _stats(data[c][crit]) for crit in CRITERES} for c in LABELS_VALIDES}

    # Comptes (basés sur un critère toujours présent)
    comptes = {c: len(data[c]["densite_contours"]) for c in LABELS_VALIDES}
    total = sum(comptes.values())

    # Seuils proposés + séparabilité, pour chaque critère
    seuils_proposes = {}
    separabilite = {}
    for crit in CRITERES:
        sv = stats["vide"][crit]
        sp = stats["pleine"][crit]
        # nom du seuil : convention seuil_<crit>_bas
        seuils_proposes[f"seuil_{crit}_bas" if crit in ("densite_contours", "texture")
                        else f"seuil_{crit}"] = _seuil_entre(sv, sp)
        score, sens = _separabilite(sv, sp)
        separabilite[crit] = {"score": score, "sens": sens}

    # Le meilleur critère discriminant
    meilleur = max(separabilite.items(), key=lambda kv: kv[1]["score"])
    meilleur_critere = meilleur[0] if meilleur[1]["score"] > 0 else None

    # Diagnostic
    avertissements = []
    for c in LABELS_VALIDES:
        if comptes[c] == 0:
            avertissements.append(f"Aucune image étiquetée '{c}' : impossible de calibrer.")
        elif comptes[c] < 5:
            avertissements.append(f"Seulement {comptes[c]} image(s) '{c}' : calibration peu fiable (visez 15+).")

    # Avertir si même le meilleur critère sépare mal
    if meilleur_critere and separabilite[meilleur_critere]["score"] < 0.5:
        avertissements.append(
            "Aucun critère ne sépare nettement vos classes (chevauchement fort). "
            "Vérifiez l'étiquetage, ou les photos vides/pleines se ressemblent trop "
            "sur ces mesures."
        )

    return {
        "stats": stats,
        "comptes": comptes,
        "total": total,
        "seuils_proposes": seuils_proposes,
        "separabilite": separabilite,
        "meilleur_critere": meilleur_critere,
        "criteres": CRITERES,
        "avertissements": avertissements,
    }


if __name__ == "__main__":
    r = proposer_seuils()
    print(f"=== Calibration sur {r['total']} images ({r['comptes']}) ===\n")
    print(f"{'Critère':<24}{'Vide':>12}{'Pleine':>12}{'Sépare?':>12}")
    for crit, lib in r["criteres"].items():
        sv = r["stats"]["vide"][crit]
        sp = r["stats"]["pleine"][crit]
        mv = sv["moyenne"] if sv else "-"
        mp = sp["moyenne"] if sp else "-"
        sc = r["separabilite"][crit]["score"]
        verdict = "OUI" if sc >= 0.5 else "non"
        print(f"{lib:<24}{str(mv):>12}{str(mp):>12}{verdict+' ('+str(sc)+')':>12}")
    print(f"\nMeilleur critère discriminant : {r['meilleur_critere']}")
    print("\nSeuils proposés :")
    for k, v in r["seuils_proposes"].items():
        print(f"  {k} = {v}")
    if r["avertissements"]:
        print("\nAvertissements :")
        for a in r["avertissements"]:
            print(f"  - {a}")
