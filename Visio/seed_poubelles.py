"""
Script de pré-enregistrement des poubelles.

C'est ici que tu déclares tes poubelles UNE FOIS, au déploiement.
Chaque poubelle a :
  - un id  -> ce sera le contenu du QR code (ex: BIN-0042)
  - un nom de lieu lisible
  - une latitude / longitude (la localisation pré-enregistrée)

Modifie la liste ci-dessous avec tes vraies poubelles, puis lance :
    python seed_poubelles.py
"""

from database import init_db, ajouter_poubelle, lister_poubelles

# --- Liste de tes poubelles (à adapter) ---
# Format : (id, nom_lieu, latitude, longitude)
POUBELLES = [
    ("BIN-0001", "Place de la République",      48.8674, 2.3636),
    ("BIN-0002", "Gare centrale - entrée nord",  48.8800, 2.3550),
    ("BIN-0003", "Parc municipal - aire de jeux",48.8610, 2.3490),
    ("BIN-0004", "Marché couvert",               48.8700, 2.3700),
    ("BIN-0005", "Arrêt de bus Liberté",         48.8550, 2.3600),
]


def main():
    init_db()
    for id_poubelle, nom_lieu, lat, lon in POUBELLES:
        ajouter_poubelle(id_poubelle, nom_lieu, lat, lon)

    print("Poubelles enregistrées :")
    for p in lister_poubelles():
        print(f"  {p['id']} | {p['nom_lieu']} | ({p['latitude']}, {p['longitude']})")


if __name__ == "__main__":
    main()
