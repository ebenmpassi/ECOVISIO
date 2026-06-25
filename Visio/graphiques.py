"""
graphiques.py — Génération de graphes côté back-end avec matplotlib.

Le cahier des charges demande DEUX approches de visualisation :
  - Chart.js (front-end, déjà fait dans le dashboard)
  - matplotlib (back-end) : génère des images PNG -> c'est ce module.

Chaque fonction renvoie l'image en mémoire (BytesIO), prête à être servie
par Flask sans écrire de fichier sur le disque (plus léger -> argument Green IT).
"""

import io
import matplotlib
matplotlib.use("Agg")  # backend sans interface graphique (serveur)
import matplotlib.pyplot as plt

from database import LABEL_AFFICHAGE

# Palette cohérente avec l'identité Visio
COULEURS = {"vide": "#2e8b57", "a_moitie": "#e0a83e", "pleine": "#e0573e", "inconnu": "#5b6e63"}
VERT_SIGNAL = "#5bbf3a"
FORET = "#14352a"


def _png(fig):
    """Convertit une figure matplotlib en PNG (BytesIO) et ferme la figure."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def matrice_confusion_png(matrice, classes):
    """
    Heatmap de la matrice de confusion.
    matrice = dict {vraie: {predite: compte}} ; classes = liste ordonnée.
    """
    n = len(classes)
    data = [[matrice[v][p] for p in classes] for v in classes]

    fig, ax = plt.subplots(figsize=(5, 4.2))
    im = ax.imshow(data, cmap="Greens")

    ax.set_xticks(range(n)); ax.set_yticks(range(n))
    ax.set_xticklabels([LABEL_AFFICHAGE[c] for c in classes])
    ax.set_yticklabels([LABEL_AFFICHAGE[c] for c in classes])
    ax.set_xlabel("Classe prédite")
    ax.set_ylabel("Vraie classe")
    ax.set_title("Matrice de confusion")

    # Annoter chaque case avec sa valeur
    vmax = max((max(r) for r in data), default=0)
    for i in range(n):
        for j in range(n):
            val = data[i][j]
            couleur = "white" if (vmax and val > vmax / 2) else "#14352a"
            ax.text(j, i, str(val), ha="center", va="center", color=couleur, fontweight="bold")

    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    return _png(fig)


def repartition_statuts_png(repartition):
    """
    Camembert de la répartition des statuts des poubelles.
    repartition = dict {vide, a_moitie, pleine, inconnu}
    """
    labels, valeurs, couleurs = [], [], []
    for cle in ("vide", "pleine", "inconnu"):
        v = repartition.get(cle, 0)
        if v > 0:
            nom = LABEL_AFFICHAGE.get(cle, "Aucune donnée") if cle != "inconnu" else "Aucune donnée"
            labels.append(f"{nom} ({v})")
            valeurs.append(v)
            couleurs.append(COULEURS[cle])

    fig, ax = plt.subplots(figsize=(5, 4))
    if valeurs:
        ax.pie(valeurs, labels=labels, colors=couleurs, autopct="%1.0f%%",
               startangle=90, textprops={"fontsize": 10})
        ax.axis("equal")
    else:
        ax.text(0.5, 0.5, "Aucune donnée", ha="center", va="center")
        ax.axis("off")
    ax.set_title("Répartition des statuts")
    return _png(fig)


def distribution_tailles_png(tailles_octets):
    """
    Histogramme de la distribution des tailles de fichiers (en Ko).
    tailles_octets = liste de tailles en octets.
    """
    fig, ax = plt.subplots(figsize=(5.5, 3.6))
    if tailles_octets:
        tailles_ko = [t / 1024 for t in tailles_octets]
        ax.hist(tailles_ko, bins=12, color=VERT_SIGNAL, edgecolor="#14352a")
        ax.set_xlabel("Taille du fichier (Ko)")
        ax.set_ylabel("Nombre d'images")
    else:
        ax.text(0.5, 0.5, "Aucune donnée", ha="center", va="center")
        ax.axis("off")
    ax.set_title("Distribution des tailles de fichiers")
    return _png(fig)


if __name__ == "__main__":
    # Test rapide : génère les 3 images dans des fichiers
    mat = {
        "vide": {"vide": 5, "a_moitie": 1, "pleine": 0},
        "a_moitie": {"vide": 1, "a_moitie": 3, "pleine": 1},
        "pleine": {"vide": 0, "a_moitie": 1, "pleine": 6},
    }
    classes = ["vide", "a_moitie", "pleine"]
    open("/tmp/mc.png", "wb").write(matrice_confusion_png(mat, classes).read())
    open("/tmp/rep.png", "wb").write(repartition_statuts_png({"vide": 3, "a_moitie": 2, "pleine": 4, "inconnu": 1}).read())
    open("/tmp/tail.png", "wb").write(distribution_tailles_png([50000, 80000, 120000, 95000, 60000]).read())
    print("3 graphes générés dans /tmp/")
