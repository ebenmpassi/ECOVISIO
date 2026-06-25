"""
meteo.py — Récupération de la météo via l'API Open-Meteo (gratuite, sans clé).

Sert à enrichir chaque signalement avec le contexte météo, afin d'analyser
les corrélations entre conditions (beau temps, week-end...) et débordements.

Principe de robustesse : si l'API est lente ou injoignable, la fonction renvoie
None sans lever d'erreur — le signalement citoyen ne doit JAMAIS être bloqué
par un problème de météo.
"""

import requests
from datetime import datetime

API_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 6  # secondes : court, pour ne pas faire attendre le citoyen

# Codes météo WMO regroupés en catégories simples et lisibles.
# https://open-meteo.com/en/docs (weather_code)
def categorie_meteo(code):
    """Traduit un code météo WMO en libellé simple."""
    if code is None:
        return "inconnu"
    if code == 0:
        return "ensoleillé"
    if code in (1, 2, 3):
        return "nuageux"
    if code in (45, 48):
        return "brouillard"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "pluie"
    if code in (71, 73, 75, 77, 85, 86):
        return "neige"
    if code in (95, 96, 99):
        return "orage"
    return "autre"


def get_meteo(latitude, longitude):
    """
    Récupère la météo actuelle à une position donnée.
    Renvoie un dict {temperature, precipitation, code, categorie} ou None en cas d'échec.
    """
    try:
        r = requests.get(
            API_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,precipitation,weather_code",
            },
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        cur = r.json().get("current", {})
        code = cur.get("weather_code")
        return {
            "temperature": cur.get("temperature_2m"),
            "precipitation": cur.get("precipitation"),
            "code": code,
            "categorie": categorie_meteo(code),
        }
    except (requests.RequestException, ValueError, KeyError):
        # Réseau, timeout, JSON invalide... -> on renvoie None proprement
        return None


# --- Jour de la semaine (gratuit, pas d'API) ---

JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def jour_de_semaine(date_str=None):
    """
    Renvoie le jour de la semaine (en français) pour une date 'YYYY-MM-DD ...'.
    Si date_str est None, utilise la date actuelle.
    """
    if date_str:
        try:
            d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            d = datetime.now()
    else:
        d = datetime.now()
    return JOURS_FR[d.weekday()]


def est_weekend(date_str=None):
    """True si la date tombe un samedi ou dimanche."""
    jour = jour_de_semaine(date_str)
    return jour in ("samedi", "dimanche")


if __name__ == "__main__":
    print("Test catégories météo :")
    for code in [0, 2, 61, 95, None]:
        print(f"  code {code} -> {categorie_meteo(code)}")
    print(f"\nJour actuel : {jour_de_semaine()}  (week-end : {est_weekend()})")
    print("\nTest API (peut échouer si pas de réseau) :")
    print(" ", get_meteo(48.8674, 2.3636))
