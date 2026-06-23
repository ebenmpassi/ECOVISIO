"""
Génération des QR codes imprimables pour chaque poubelle.

Pour chaque poubelle enregistrée en base, ce script crée une ÉTIQUETTE PNG
contenant :
  - le QR code (qui encode l'URL  BASE_URL/signaler/<id>)
  - en dessous, du texte lisible : l'ID de la poubelle et le nom du lieu

Ainsi, quand tu imprimes et que tu vas coller l'étiquette, tu sais
exactement à quelle poubelle elle correspond et où l'apposer.

Lance :
    python generer_qrcodes.py

Les étiquettes sont créées dans le dossier  qrcodes/

IMPORTANT — BASE_URL :
  Le QR encode une URL. Pour un test en local sur ton PC, localhost suffit,
  MAIS un vrai téléphone ne peut pas atteindre "localhost".
  Pour scanner avec un vrai mobile, remplace BASE_URL par :
    - l'adresse IP de ton PC sur le WiFi  (ex: http://192.168.1.23:5000), ou
    - une URL de tunnel ngrok            (ex: https://abcd.ngrok-free.app)
"""

import os
import qrcode
from PIL import Image, ImageDraw, ImageFont

from database import init_db, lister_poubelles

# --- À CONFIGURER ---
BASE_URL = "https://answering-enunciate-switch.ngrok-free.dev"   # voir la note ci-dessus pour un vrai mobile
DOSSIER_SORTIE = "qrcodes"
# --------------------


def charger_police(taille):
    """Essaie de charger une police lisible, sinon retombe sur la police par défaut."""
    chemins_possibles = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for chemin in chemins_possibles:
        if os.path.exists(chemin):
            return ImageFont.truetype(chemin, taille)
    return ImageFont.load_default()


def generer_etiquette(id_poubelle, nom_lieu):
    """Crée une étiquette PNG : QR code + texte lisible en dessous."""
    url = f"{BASE_URL}/signaler/{id_poubelle}"

    # 1) Génère le QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # tolère ~15% de dégâts (utile en extérieur)
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    largeur_qr, hauteur_qr = img_qr.size

    # 2) Prépare la zone de texte sous le QR
    police_id = charger_police(36)
    police_lieu = charger_police(26)
    marge = 20
    hauteur_texte = 100

    # 3) Crée l'image finale (QR + bandeau texte)
    largeur_finale = largeur_qr
    hauteur_finale = hauteur_qr + hauteur_texte
    etiquette = Image.new("RGB", (largeur_finale, hauteur_finale), "white")
    etiquette.paste(img_qr, (0, 0))

    draw = ImageDraw.Draw(etiquette)

    # Texte 1 : l'ID de la poubelle, centré
    bbox_id = draw.textbbox((0, 0), id_poubelle, font=police_id)
    largeur_id = bbox_id[2] - bbox_id[0]
    draw.text(
        ((largeur_finale - largeur_id) / 2, hauteur_qr + 5),
        id_poubelle, fill="black", font=police_id,
    )

    # Texte 2 : le nom du lieu, centré (tronqué s'il est trop long)
    lieu_affiche = nom_lieu if len(nom_lieu) <= 32 else nom_lieu[:29] + "..."
    bbox_lieu = draw.textbbox((0, 0), lieu_affiche, font=police_lieu)
    largeur_lieu = bbox_lieu[2] - bbox_lieu[0]
    draw.text(
        ((largeur_finale - largeur_lieu) / 2, hauteur_qr + 50),
        lieu_affiche, fill="black", font=police_lieu,
    )

    return etiquette


def main():
    init_db()
    os.makedirs(DOSSIER_SORTIE, exist_ok=True)

    poubelles = lister_poubelles()
    if not poubelles:
        print("Aucune poubelle en base. Lance d'abord :  python seed_poubelles.py")
        return

    for p in poubelles:
        etiquette = generer_etiquette(p["id"], p["nom_lieu"])
        chemin = os.path.join(DOSSIER_SORTIE, f"{p['id']}.png")
        etiquette.save(chemin)
        print(f"  Étiquette créée : {chemin}  ->  {BASE_URL}/signaler/{p['id']}")

    print(f"\n{len(poubelles)} étiquette(s) générée(s) dans le dossier '{DOSSIER_SORTIE}/'.")
    print("Tu peux les imprimer et les coller sur les poubelles correspondantes.")


if __name__ == "__main__":
    main()
