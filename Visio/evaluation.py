"""
evaluation.py — Évaluation de la performance de la classification.

Compare le verdict des règles (vision.py) à une VÉRITÉ TERRAIN étiquetée à la main,
et calcule les métriques standard de classification, SANS bibliothèque externe
(tout est calculé explicitement, pour la pédagogie et la documentation) :

  - Accuracy (taux de bonne classification global)
  - Precision, Recall, F1-score par classe
  - Matrice de confusion

La vérité terrain vit dans la table `jeu_test` (photo + vrai_label, étiqueté par l'agent).
"""

import os
import json

from database import LABELS_VALIDES, LABEL_AFFICHAGE
from vision import analyser_image


# =========================================================
# CALCUL DES MÉTRIQUES (sans sklearn)
# =========================================================

def matrice_confusion(verites, predictions, classes=LABELS_VALIDES):
    """
    Construit la matrice de confusion.
    Lignes = vraie classe, Colonnes = classe prédite.
    Renvoie un dict {vraie: {predite: compte}}.
    """
    m = {v: {p: 0 for p in classes} for v in classes}
    for vrai, pred in zip(verites, predictions):
        if vrai in m and pred in m[vrai]:
            m[vrai][pred] += 1
    return m


def metriques_par_classe(verites, predictions, classes=LABELS_VALIDES):
    """
    Calcule Precision, Recall et F1 pour chaque classe.

    Pour une classe C :
      - VP (vrais positifs)  : prédit C ET vraiment C
      - FP (faux positifs)   : prédit C mais pas vraiment C
      - FN (faux négatifs)   : vraiment C mais pas prédit C
      - Precision = VP / (VP + FP)  -> quand on prédit C, à quel point a-t-on raison
      - Recall    = VP / (VP + FN)  -> parmi les vrais C, combien on en retrouve
      - F1        = moyenne harmonique de Precision et Recall
    """
    resultats = {}
    for c in classes:
        vp = sum(1 for v, p in zip(verites, predictions) if v == c and p == c)
        fp = sum(1 for v, p in zip(verites, predictions) if v != c and p == c)
        fn = sum(1 for v, p in zip(verites, predictions) if v == c and p != c)

        precision = vp / (vp + fp) if (vp + fp) else 0.0
        recall = vp / (vp + fn) if (vp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        support = sum(1 for v in verites if v == c)

        resultats[c] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "support": support,
        }
    return resultats


def accuracy(verites, predictions):
    """Taux de bonne classification global : bonnes réponses / total."""
    if not verites:
        return 0.0
    bonnes = sum(1 for v, p in zip(verites, predictions) if v == p)
    return round(bonnes / len(verites), 3)


def evaluer(verites, predictions, classes=LABELS_VALIDES):
    """
    Calcule toutes les métriques d'un coup.
    Renvoie un dict complet, prêt à afficher.
    """
    par_classe = metriques_par_classe(verites, predictions, classes)
    # Moyennes "macro" : moyenne simple des métriques par classe
    n = len(classes)
    macro_precision = round(sum(par_classe[c]["precision"] for c in classes) / n, 3)
    macro_recall = round(sum(par_classe[c]["recall"] for c in classes) / n, 3)
    macro_f1 = round(sum(par_classe[c]["f1"] for c in classes) / n, 3)

    return {
        "total": len(verites),
        "accuracy": accuracy(verites, predictions),
        "par_classe": par_classe,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "matrice": matrice_confusion(verites, predictions, classes),
        "classes": list(classes),
    }


# =========================================================
# LANCEMENT DE L'ÉVALUATION SUR LE JEU DE TEST
# =========================================================

def lancer_evaluation():
    """
    Parcourt le jeu de test étiqueté (table jeu_test), fait tourner la
    classification sur chaque image, et renvoie les métriques + le détail.

    Renvoie (metriques, details) où details liste chaque image avec
    son vrai label, le verdict, et si c'est correct.
    """
    from database import lister_jeu_test

    items = lister_jeu_test()
    verites, predictions, details = [], [], []

    for it in items:
        chemin = it["chemin_image"]
        vrai = it["vrai_label"]
        if not os.path.exists(chemin):
            continue
        try:
            res = analyser_image(chemin)
        except Exception:
            continue

        # On évalue uniquement les images conformes (sinon pas de verdict)
        if not res["conforme"]:
            details.append({
                "id": it["id"], "nom": os.path.basename(chemin),
                "vrai": vrai, "predit": None, "correct": False,
                "note": "non conforme : " + (res["raison_rejet"] or ""),
            })
            continue

        pred = res["verdict"]
        verites.append(vrai)
        predictions.append(pred)
        details.append({
            "id": it["id"], "nom": os.path.basename(chemin),
            "vrai": vrai, "predit": pred, "correct": (vrai == pred),
            "note": "",
        })

    metriques = evaluer(verites, predictions) if verites else None
    return metriques, details


if __name__ == "__main__":
    # Affichage console (utile pour la doc / debug)
    m, d = lancer_evaluation()
    if m is None:
        print("Aucune image conforme dans le jeu de test. Ajoute et étiquette des photos.")
    else:
        print(f"\n=== RÉSULTATS ({m['total']} images évaluées) ===")
        print(f"Accuracy globale : {m['accuracy']*100:.1f}%\n")
        print(f"{'Classe':<12}{'Precision':>10}{'Recall':>10}{'F1':>8}{'Support':>9}")
        for c in m["classes"]:
            r = m["par_classe"][c]
            print(f"{LABEL_AFFICHAGE[c]:<12}{r['precision']:>10}{r['recall']:>10}{r['f1']:>8}{r['support']:>9}")
        print(f"\nMacro F1 : {m['macro_f1']}")
        print("\nMatrice de confusion (lignes=vrai, colonnes=prédit) :")
        header = "          " + "".join(f"{LABEL_AFFICHAGE[c]:>10}" for c in m["classes"])
        print(header)
        for v in m["classes"]:
            ligne = f"{LABEL_AFFICHAGE[v]:<10}" + "".join(f"{m['matrice'][v][p]:>10}" for p in m["classes"])
            print(ligne)
