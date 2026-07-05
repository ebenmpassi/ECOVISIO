"""
verification.py — Module de vérification de la conformité des données stockées.

Différent du contrôle de conformité des PHOTOS (qui se fait à l'upload).
Ici, on audite l'INTÉGRITÉ des données déjà en base : cohérence, complétude,
validité. C'est un contrôle qualité de la base de données.

Chaque vérification renvoie une liste de problèmes détectés (vide = tout va bien).
"""

import os
import json

from database import get_connection


def verifier_signalements_orphelins():
    """Signalements rattachés à une poubelle qui n'existe plus."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.id, s.poubelle_id
        FROM signalements s
        LEFT JOIN poubelles p ON s.poubelle_id = p.id
        WHERE p.id IS NULL
    """).fetchall()
    conn.close()
    return [
        f"Signalement #{r['id']} rattaché à la poubelle inexistante « {r['poubelle_id']} »"
        for r in rows
    ]


def verifier_images_manquantes():
    """Signalements dont le fichier image n'existe plus sur le disque."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, chemin_image FROM signalements"
    ).fetchall()
    conn.close()
    problemes = []
    for r in rows:
        if r["chemin_image"] and not os.path.exists(r["chemin_image"]):
            problemes.append(f"Signalement #{r['id']} : fichier image introuvable ({r['chemin_image']})")
    return problemes


def verifier_coordonnees_poubelles():
    """Poubelles avec des coordonnées GPS invalides ou hors limites."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, latitude, longitude FROM poubelles"
    ).fetchall()
    conn.close()
    problemes = []
    for r in rows:
        lat, lon = r["latitude"], r["longitude"]
        if lat is None or lon is None:
            problemes.append(f"Poubelle « {r['id']} » : coordonnées manquantes")
        elif not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            problemes.append(f"Poubelle « {r['id']} » : coordonnées hors limites ({lat}, {lon})")
    return problemes


def verifier_statuts_valides():
    """Signalements avec un statut ou une évaluation hors des valeurs autorisées."""
    from database import LABELS_VALIDES
    valides = set(LABELS_VALIDES)
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, statut, eval_citoyen, verdict_ia FROM signalements"
    ).fetchall()
    conn.close()
    problemes = []
    for r in rows:
        for champ in ("statut", "eval_citoyen", "verdict_ia"):
            val = r[champ]
            if val is not None and val not in valides:
                problemes.append(f"Signalement #{r['id']} : {champ} invalide (« {val} »)")
    return problemes


def verifier_features_json():
    """Signalements dont les caractéristiques stockées sont illisibles ou incomplètes."""
    champs_attendus = {"largeur", "hauteur", "taille_fichier", "densite_contours", "texture"}
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, features_json FROM signalements WHERE features_json IS NOT NULL"
    ).fetchall()
    conn.close()
    problemes = []
    for r in rows:
        try:
            f = json.loads(r["features_json"])
        except (ValueError, TypeError):
            problemes.append(f"Signalement #{r['id']} : caractéristiques illisibles (JSON invalide)")
            continue
        manquants = champs_attendus - set(f.keys())
        if manquants:
            problemes.append(f"Signalement #{r['id']} : caractéristiques incomplètes (manque {', '.join(sorted(manquants))})")
    return problemes


def lancer_verification_complete():
    """
    Exécute toutes les vérifications et renvoie un rapport structuré.
    Renvoie un dict {categorie: [problèmes...]} + un total.
    """
    rapport = {
        "Signalements orphelins": verifier_signalements_orphelins(),
        "Images manquantes": verifier_images_manquantes(),
        "Coordonnées invalides": verifier_coordonnees_poubelles(),
        "Statuts invalides": verifier_statuts_valides(),
        "Caractéristiques incomplètes": verifier_features_json(),
    }
    total = sum(len(v) for v in rapport.values())
    return {"rapport": rapport, "total_problemes": total}


if __name__ == "__main__":
    r = lancer_verification_complete()
    print(f"=== Vérification des données : {r['total_problemes']} problème(s) ===\n")
    for categorie, problemes in r["rapport"].items():
        statut = "OK" if not problemes else f"{len(problemes)} problème(s)"
        print(f"[{categorie}] {statut}")
        for p in problemes:
            print(f"   - {p}")
