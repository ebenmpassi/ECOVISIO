"""
reconnaissance.py — Reconnaissance d'objet via l'API Google Cloud Vision.

Objectif : vérifier qu'une photo contient bien une poubelle / un conteneur à
déchets, et rejeter les photos qui n'en montrent pas.

IMPORTANT — sur le machine learning :
  Cette fonctionnalité s'appuie sur un service EXTERNE de reconnaissance d'image
  (Google Vision), qui repose sur du machine learning pré-entraîné. Le projet
  lui-même ne fait pas de ML (pas d'entraînement, pas de modèle local) : la
  classification pleine/vide reste faite par des règles de traitement d'image.
  Vision sert uniquement de filtre "est-ce une poubelle".

Robustesse : si la clé API est absente ou l'appel échoue, la fonction renvoie
un résultat "indéterminé" — le signalement n'est PAS bloqué (le QR code reste
le garde-fou). Le système fonctionne donc avec ou sans clé.

--- CONFIGURATION (à faire quand tu auras la clé) ---
  1. Crée un projet sur https://console.cloud.google.com
  2. Active l'API "Cloud Vision"
  3. Crée une clé API
  4. Renseigne-la ci-dessous dans CLE_API_VISION (ou via la variable
     d'environnement GOOGLE_VISION_API_KEY, plus sûr).
"""

import os
import base64
import requests

# Clé API : de préférence via variable d'environnement, sinon en dur ici.
CLE_API_VISION = "AIzaSyD6eqi6oFq6lcID0xx78Fw_OZBn_XSI9Sk"

API_URL = "https://vision.googleapis.com/v1/images:annotate"
TIMEOUT = 2  # secondes

# Termes (en anglais, car Vision répond en anglais) qui indiquent une poubelle.
# On teste en minuscules, en cherchant ces mots dans les labels retournés.
MOTS_POUBELLE = (
    "waste container", "trash", "bin", "dumpster", "garbage",
    "waste", "recycling", "trash can", "wheelie bin", "litter",
    "container", "waste basket",
)


def _est_label_poubelle(description):
    """True si un label Vision correspond à une poubelle."""
    d = description.lower()
    return any(mot in d for mot in MOTS_POUBELLE)


def analyser_avec_vision(chemin_image):
    """
    Envoie l'image à Google Vision et cherche des labels de type "poubelle".

    Renvoie un dict :
      {
        "disponible": bool,     # False si pas de clé / appel échoué
        "est_poubelle": bool,   # True si un label "poubelle" a été trouvé
        "labels": [...],        # labels détectés (pour info / debug)
      }
    Si "disponible" est False, il ne faut PAS bloquer le signalement.
    """
    if not CLE_API_VISION:
        return {"disponible": False, "est_poubelle": False, "labels": []}

    try:
        with open(chemin_image, "rb") as f:
            contenu = base64.b64encode(f.read()).decode("utf-8")

        corps = {
            "requests": [{
                "image": {"content": contenu},
                "features": [{"type": "LABEL_DETECTION", "maxResults": 15}],
            }]
        }
        r = requests.post(
            f"{API_URL}?key={CLE_API_VISION}",
            json=corps,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return {"disponible": False, "est_poubelle": False, "labels": []}

        reponses = r.json().get("responses", [{}])
        annotations = reponses[0].get("labelAnnotations", []) if reponses else []
        labels = [a.get("description", "") for a in annotations]

        est_poubelle = any(_est_label_poubelle(lbl) for lbl in labels)
        return {"disponible": True, "est_poubelle": est_poubelle, "labels": labels}

    except (requests.RequestException, ValueError, KeyError, OSError):
        # Réseau, JSON, fichier illisible... -> indéterminé, on ne bloque pas
        return {"disponible": False, "est_poubelle": False, "labels": []}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        res = analyser_avec_vision(sys.argv[1])
        if not res["disponible"]:
            print("Vision indisponible (pas de clé API ou appel échoué).")
        else:
            print("Labels détectés :", res["labels"])
            print("Est une poubelle :", res["est_poubelle"])
    else:
        print("Usage : python reconnaissance.py chemin/vers/image.jpg")
        print(f"Clé API configurée : {'oui' if CLE_API_VISION else 'non'}")
