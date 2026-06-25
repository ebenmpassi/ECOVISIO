"""
calibration.py — Calibration automatique des seuils de classification.

Principe : on analyse les photos DÉJÀ ÉTIQUETÉES (jeu de test, vérité terrain),
on mesure les valeurs réelles de chaque caractéristique par classe, et on en
déduit les meilleurs seuils pour séparer vide / à moitié / pleine.

C'est la démarche de "calibration empirique" : au lieu de deviner les seuils,
on les calcule à partir des données réelles.
"""

import os
import statistics

from database import lister_jeu_test, LABELS_VALIDES
from vision import extraire_caracteristiques


def collecter_features_par_classe():
    """
    Pour chaque image étiquetée, extrait ses caractéristiques et les range par classe.
    Renvoie un dict {classe: {feature: [valeurs...]}}.
    """
    data = {c: {"densite_contours": [], "texture": []} for c in LABELS_VALIDES}

    for it in lister_jeu_test():
        label = it["vrai_label"]
        chemin = it["chemin_image"]
        if label not in LABELS_VALIDES or not os.path.exists(chemin):
            continue
        try:
            f = extraire_caracteristiques(chemin)
        except Exception:
            continue
        data[label]["densite_contours"].append(f["densite_contours"])
        data[label]["texture"].append(f["texture"])

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
    }


def _seuil_entre(classe_basse, classe_haute):
    """
    Propose un seuil séparant deux classes : le point médian entre la moyenne
    de la classe basse et la moyenne de la classe haute.
    Renvoie None si l'une des classes manque de données.
    """
    if classe_basse is None or classe_haute is None:
        return None
    return round((classe_basse["moyenne"] + classe_haute["moyenne"]) / 2, 4)


def proposer_seuils():
    """
    Analyse les photos étiquetées et propose des seuils.
    Renvoie un dict complet : stats par classe + seuils proposés + diagnostic.
    """
    data = collecter_features_par_classe()

    # Statistiques par classe pour chaque feature
    stats = {}
    for c in LABELS_VALIDES:
        stats[c] = {
            "densite_contours": _stats(data[c]["densite_contours"]),
            "texture": _stats(data[c]["texture"]),
        }

    # Compter les images par classe (diagnostic d'équilibre)
    comptes = {c: len(data[c]["densite_contours"]) for c in LABELS_VALIDES}
    total = sum(comptes.values())

    # Proposition de seuils :
    #   En binaire, le seuil "bas" sépare directement 'vide' de 'pleine'.
    #   On le place au milieu des moyennes des deux classes.
    dc = {c: stats[c]["densite_contours"] for c in LABELS_VALIDES}
    tx = {c: stats[c]["texture"] for c in LABELS_VALIDES}

    seuils_proposes = {
        "seuil_contours_bas": _seuil_entre(dc.get("vide"), dc.get("pleine")),
        "seuil_texture_bas": _seuil_entre(tx.get("vide"), tx.get("pleine")),
    }

    # Diagnostic : avertir si données insuffisantes ou déséquilibrées
    avertissements = []
    for c in LABELS_VALIDES:
        if comptes[c] == 0:
            avertissements.append(f"Aucune image étiquetée '{c}' : impossible de calibrer cette classe.")
        elif comptes[c] < 5:
            avertissements.append(f"Seulement {comptes[c]} image(s) '{c}' : calibration peu fiable (visez 15+).")

    # Vérifier la séparabilité : la moyenne 'vide' doit être < moyenne 'pleine'
    if dc.get("vide") and dc.get("pleine"):
        if dc["vide"]["moyenne"] >= dc["pleine"]["moyenne"]:
            avertissements.append(
                "Les classes ne se séparent pas nettement sur la densité de contours "
                "(moyenne 'vide' ≥ 'pleine'). La texture pourrait mieux discriminer."
            )

    return {
        "stats": stats,
        "comptes": comptes,
        "total": total,
        "seuils_proposes": seuils_proposes,
        "avertissements": avertissements,
    }


if __name__ == "__main__":
    r = proposer_seuils()
    print(f"=== Calibration sur {r['total']} images étiquetées ===")
    print(f"Répartition : {r['comptes']}\n")
    for c in LABELS_VALIDES:
        s = r["stats"][c]
        print(f"[{c}]")
        if s["densite_contours"]:
            print(f"  densite_contours : moy={s['densite_contours']['moyenne']} "
                  f"(min {s['densite_contours']['min']}, max {s['densite_contours']['max']}, n={s['densite_contours']['n']})")
            print(f"  texture          : moy={s['texture']['moyenne']} "
                  f"(min {s['texture']['min']}, max {s['texture']['max']})")
        else:
            print("  (aucune donnée)")
    print("\nSeuils proposés :")
    for k, v in r["seuils_proposes"].items():
        print(f"  {k} = {v}")
    if r["avertissements"]:
        print("\nAvertissements :")
        for a in r["avertissements"]:
            print(f"  - {a}")
