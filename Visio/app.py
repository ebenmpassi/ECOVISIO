"""
Application Flask — Plateforme de traitement des déchets.

Flux citoyen (après scan du QR) :
  1. La page /signaler/<id> affiche le lieu de la poubelle.
  2. Le citoyen prend une photo ET choisit le niveau (vide / à moitié / pleine).
  3. À l'envoi :
       - la photo est stockée,
       - l'IA extrait les caractéristiques (Point 3) et décide d'un verdict (Point 4),
       - on compare l'avis du citoyen et celui de l'IA (l'IA fait foi),
       - on enregistre le tout et on attribue un statut à la poubelle.
  4. Une page de confirmation montre le verdict.

Lancer :
    python app.py
Puis http://localhost:5000/
"""

import os
import json
from datetime import datetime
from werkzeug.utils import secure_filename
from functools import wraps
from flask import (
    Flask, request, redirect, url_for,
    render_template_string, abort, send_from_directory, session, jsonify,
)

from database import (
    init_db, get_poubelle, lister_poubelles, ajouter_signalement,
    desactiver_poubelle, reactiver_poubelle,
    marquer_poubelle_videe,
    ajouter_poubelle,verifier_compte,creer_compte,
    LABELS_VALIDES, LABEL_AFFICHAGE,
    verifier_limite, enregistrer_tentative, nettoyer_vieilles_tentatives,
    LIMITE_UPLOADS, FENETRE_MINUTES,
    statut_actuel_poubelle, donnees_carte, repartition_statuts,
    signalements_par_jour, poubelles_pleines, zones_a_risque, compter_signalements,
    get_seuils, maj_seuils, reinitialiser_seuils, SEUILS_PAR_DEFAUT,
    lister_regles, ajouter_regle, supprimer_regle, basculer_regle,
    CARACTERISTIQUES_REGLES, OPERATEURS_REGLES,
    importer_images_jeu_test, lister_jeu_test, prochain_jeu_test_non_etiquete,
    etiqueter_jeu_test, compter_jeu_test, vider_jeu_test,
    cacher_analyse_jeu_test,
    correlation_jour_semaine, correlation_meteo,
)
from vision import (
    analyser_image,
    comparer_citoyen_ia,
    compresser_image,
)
from reconnaissance import analyser_avec_vision
from evaluation import lancer_evaluation
import graphiques
from meteo import get_meteo, jour_de_semaine
from calibration import proposer_seuils
from verification import lancer_verification_complete

# --- Configuration ---
DOSSIER_UPLOADS = "uploads"
EXTENSIONS_AUTORISEES = {"jpg", "jpeg", "png"}
TAILLE_MAX = 10 * 1024 * 1024  # 10 Mo

# Mot de passe d'accès au dashboard agents (à changer en production !)
MOT_DE_PASSE_AGENTS = "agent2024"
# ---------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = TAILLE_MAX
# Clé secrète nécessaire pour gérer les sessions (connexion agents).
# En production, mettre une vraie valeur aléatoire secrète.
app.secret_key = "change-moi-en-production-cle-secrete-aleatoire"

os.makedirs(DOSSIER_UPLOADS, exist_ok=True)
init_db()


def extension_autorisee(nom_fichier):
    return "." in nom_fichier and \
        nom_fichier.rsplit(".", 1)[1].lower() in EXTENSIONS_AUTORISEES


def ip_du_client():
    """
    Récupère l'IP réelle du citoyen.
    Derrière ngrok / un proxy, l'IP d'origine est dans l'en-tête X-Forwarded-For
    (le premier élément de la liste). Sinon on prend l'IP directe.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "inconnue"


def format_attente(secondes):
    """Transforme un nombre de secondes en texte lisible (ex: '12 min 30 s')."""
    minutes = secondes // 60
    sec = secondes % 60
    if minutes and sec:
        return f"{minutes} min {sec} s"
    if minutes:
        return f"{minutes} min"
    return f"{sec} s"


# ====================== IDENTITÉ VISUELLE VISO (thèmes partagés) ======================
# Deux registres d'une même identité :
#   - THEME_AGENT : sombre, vert forêt, pour l'espace agents (dashboard, admin, login, équipe)
#   - THEME_CITOYEN : clair, mêmes couleurs, pour les pages vues en rue sur mobile

PALETTE = """
  :root{
    --foret:#14352a; --mousse:#2f5d3f; --signal:#7ed957; --signal-d:#5bbf3a;
    --brume:#cfe3d4; --crayon:#eef5ef;
    --vide:#2e8b57; --moitie:#e0a83e; --pleine:#e0573e;
  }
"""

# Thème AGENT — sombre, raffiné
THEME_AGENT = PALETTE + """
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:var(--foret);
    color:var(--crayon);-webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:0 1.5rem}
  nav{display:flex;justify-content:space-between;align-items:center;padding:1.4rem 0;
    border-bottom:1px solid #ffffff14}
  .marque{font-weight:800;letter-spacing:.2em;font-size:.95rem;text-transform:uppercase;color:var(--signal)}
  .marque span{color:var(--crayon);font-weight:400}
  nav a{color:var(--brume);text-decoration:none;font-size:.9rem;margin-left:1.4rem;transition:color .2s}
  nav a:hover{color:var(--signal)}
  h1{font-size:clamp(1.7rem,3.6vw,2.4rem);letter-spacing:-.02em;margin:1.8rem 0 .4rem}
  h2{font-size:1.05rem;margin:1.6rem 0 .7rem;color:var(--crayon)}
  .sous{color:var(--brume);line-height:1.6;max-width:60ch}
  a{color:var(--signal)}
  .carte{background:#ffffff0a;border:1px solid #ffffff1a;border-radius:14px;padding:1.3rem}
  .badge{display:inline-block;padding:.18rem .6rem;border-radius:999px;color:#0c2118;font-weight:700;font-size:.8rem}
  .b_vide{background:var(--vide);color:#fff}.b_a_moitie{background:var(--moitie)}
  .b_pleine{background:var(--pleine);color:#fff}.b_inconnu{background:#5b6e63;color:#fff}
  .btn{display:inline-flex;align-items:center;gap:.5rem;padding:.75rem 1.3rem;border-radius:999px;
    text-decoration:none;font-weight:600;font-size:.95rem;border:none;cursor:pointer;transition:transform .15s,box-shadow .2s}
  .btn-p{background:var(--signal);color:var(--foret)}
  .btn-p:hover{transform:translateY(-2px);box-shadow:0 10px 28px -10px var(--signal)}
  .btn-s{background:transparent;color:var(--crayon);border:1px solid #ffffff33}
  .btn-s:hover{border-color:var(--signal);color:var(--signal)}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem;border-bottom:1px solid #ffffff14;font-size:.9rem}
  th{color:var(--brume);font-weight:600;text-transform:uppercase;letter-spacing:.05em;font-size:.75rem}
  @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

# Thème CITOYEN — clair, lisible en plein soleil, mobile-first
THEME_CITOYEN = PALETTE + """
  *{box-sizing:border-box}
  body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:#f4f8f4;color:#16271d;
    -webkit-font-smoothing:antialiased}
  .wrap{max-width:480px;margin:0 auto;padding:1.2rem}
  .topbar{display:flex;align-items:center;gap:.5rem;padding:.4rem 0 1rem}
  .topbar .marque{font-weight:800;letter-spacing:.18em;text-transform:uppercase;font-size:.9rem;color:var(--mousse)}
  .topbar .leaf{width:22px;height:22px}
  h1{font-size:1.3rem;margin:.2rem 0}
  h2{font-size:1.05rem;margin:1.2rem 0 .6rem;color:#16271d}
  .carte{background:#fff;border:1px solid #dce8df;border-radius:14px;padding:1.1rem;
    box-shadow:0 1px 3px #0000000a}
  .lieu{display:flex;align-items:center;gap:.5rem;color:#3a5547}
  .lieu svg{width:18px;height:18px;flex:none;stroke:var(--mousse)}
  .id{font-size:1.05rem;font-weight:700;color:var(--mousse)}
  .badge{display:inline-block;padding:.2rem .6rem;border-radius:999px;color:#fff;font-weight:700;font-size:.85rem}
  .b_vide{background:var(--vide)}.b_a_moitie{background:var(--moitie);color:#3a2c00}.b_pleine{background:var(--pleine)}
  a{color:var(--mousse)}
"""


# ====================== PAGE D'ACCUEIL (vitrine animée) ======================
PAGE_ACCUEIL = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>ecoVisio — Prévention des dépôts sauvages</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{
    --foret:#14352a;       /* vert forêt profond (fond) */
    --mousse:#2f5d3f;      /* vert mousse */
    --signal:#7ed957;      /* vert signal vif (accent) */
    --brume:#cfe3d4;       /* vert très clair (texte secondaire) */
    --crayon:#eef5ef;      /* presque blanc */
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:var(--foret);
    color:var(--crayon);overflow-x:hidden}
  .wrap{max-width:1080px;margin:0 auto;padding:0 1.5rem}

  /* --- Barre haute --- */
  nav{display:flex;justify-content:space-between;align-items:center;padding:1.5rem 0}
  .marque{font-weight:800;letter-spacing:.18em;font-size:.95rem;text-transform:uppercase;
    color:var(--signal)}
  .marque span{color:var(--crayon)}
  nav a{color:var(--brume);text-decoration:none;font-size:.9rem;margin-left:1.5rem;
    transition:color .2s}
  nav a:hover{color:var(--signal)}

  /* --- Héros --- */
  .hero{display:grid;grid-template-columns:1.1fr .9fr;gap:2rem;align-items:center;
    padding:3rem 0 4rem;min-height:60vh}
  @media(max-width:820px){.hero{grid-template-columns:1fr;text-align:left}}
  .eyebrow{font-size:.8rem;letter-spacing:.22em;text-transform:uppercase;color:var(--signal);
    opacity:0;transform:translateY(12px);animation:rise .7s .1s forwards}
  h1{font-size:clamp(2.1rem,5vw,3.4rem);line-height:1.05;margin:.6rem 0 1rem;font-weight:800;
    letter-spacing:-.02em;opacity:0;transform:translateY(16px);animation:rise .7s .25s forwards}
  h1 b{color:var(--signal);font-weight:800}
  .sous{font-size:1.05rem;line-height:1.6;color:var(--brume);max-width:46ch;
    opacity:0;transform:translateY(16px);animation:rise .7s .4s forwards}
  .actions{display:flex;gap:1rem;flex-wrap:wrap;margin-top:2rem;
    opacity:0;transform:translateY(16px);animation:rise .7s .55s forwards}
  .btn{display:inline-flex;align-items:center;gap:.6rem;padding:.9rem 1.5rem;border-radius:999px;
    text-decoration:none;font-weight:600;font-size:.98rem;transition:transform .15s,box-shadow .2s}
  .btn-p{background:var(--signal);color:var(--foret)}
  .btn-p:hover{transform:translateY(-2px);box-shadow:0 10px 30px -8px var(--signal)}
  .btn-s{background:transparent;color:var(--crayon);border:1px solid #ffffff33}
  .btn-s:hover{border-color:var(--signal);color:var(--signal)}
  .btn svg{width:18px;height:18px}

  /* --- Scène animée (signature) --- */
  .scene{position:relative;opacity:0;animation:fade 1s .5s forwards;
    display:flex;flex-direction:column;align-items:center;justify-content:center}
  .scene #scene-svg{max-height:220px}
  @keyframes fade{to{opacity:1}}

  /* --- Bandeau chiffres / valeur --- */
  .socle{border-top:1px solid #ffffff1a;padding:2.5rem 0 4rem;
    display:grid;grid-template-columns:repeat(3,1fr);gap:1.5rem}
  @media(max-width:700px){.socle{grid-template-columns:1fr}}
  .pt{opacity:0;transform:translateY(16px)}
  .pt.vu{animation:rise .6s forwards}
  .pt .k{font-size:.78rem;letter-spacing:.16em;text-transform:uppercase;color:var(--signal);
    display:flex;align-items:center;gap:.5rem}
  .pt h3{margin:.6rem 0 .4rem;font-size:1.1rem}
  .pt p{margin:0;color:var(--brume);font-size:.92rem;line-height:1.5}

  @keyframes rise{to{opacity:1;transform:none}}

  @media(prefers-reduced-motion:reduce){
    *{animation:none!important;opacity:1!important;transform:none!important}
  }
</style></head>
<body>
<div class="wrap">

  <nav>
    <div class="marque">
      <img src="{{ url_for('static', filename='logo.png') }}" alt="ecoVisio"
           style="height:38px;vertical-align:middle;border-radius:6px">
      <span style="margin-left:.5rem"> · suivi des poubelles</span>
    </div>
    <div>
      <a href="{{ url_for('dashboard') }}">Espace agents</a>
      <a href="{{ url_for('equipe') }}">Équipe</a>
    </div>
  </nav>


   <header class="hero">
    <div>
      <div class="eyebrow">Prévention des dépots sauvages</div>
      <h1>Anticiper les débordements <b>avant</b> qu'ils ne deviennent des dépôts sauvages.</h1>
      <p class="sous">Une plateforme de suivi des poubelles publiques par l'image. Les citoyens
        signalent, le système analyse le niveau de remplissage, et les services de collecte
        interviennent au bon endroit, au bon moment.</p>
      <div class="actions">
        <a class="btn btn-p" href="{{ url_for('dashboard') }}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 13h8V3H3zM13 21h8V8h-8zM3 21h8v-5H3z"/></svg>
          Tableau de bord agents
        </a>
        <a class="btn btn-s" href="{{ url_for('equipe') }}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="7" r="3"/><path d="M2 21v-1a5 5 0 015-5h4a5 5 0 015 5v1M17 11l2 2 4-4"/></svg>
          Équipe du projet
        </a>
      </div>
    </div>

    <!-- Logo + SIGNATURE : scène animée maison -->
    <div class="scene" aria-hidden="true">
      <img src="{{ url_for('static', filename='logo.png') }}" alt="ecoVisio"
           style="display:block;width:100%;max-width:280px;margin:0 auto 1rem">
      <svg id="scene-svg" viewBox="0 0 420 340" width="100%" height="100%"></svg>
    </div>
  </header>

  <section class="socle">
    <div class="pt">
      <div class="k"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7ed957" stroke-width="2"><path d="M3 7h18M6 7v13h12V7M9 7V4h6v3"/></svg> Collecte</div>
      <h3>Signalement citoyen</h3>
      <p>Un QR code sur chaque poubelle. Une photo suffit à déclencher l'analyse.</p>
    </div>
    <div class="pt">
      <div class="k"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7ed957" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg> Analyse</div>
      <h3>Détection par l'image</h3>
      <p>Des règles de traitement d'image estiment le niveau de remplissage, sans boîte noire.</p>
    </div>
    <div class="pt">
      <div class="k"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7ed957" stroke-width="2"><path d="M12 2C8 6 6 9 6 13a6 6 0 0012 0c0-4-2-7-6-11z"/></svg> Prévention</div>
      <h3>Zones à risque</h3>
      <p>La carte révèle les points qui débordent souvent, là où naissent les dépôts sauvages.</p>
    </div>
  </section>

</div>

<script>
  // ---- Scène animée : des poubelles qui se remplissent + signal de détection ----
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.getElementById("scene-svg");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Trois poubelles avec des niveaux de remplissage différents
  const bacs = [
    {x: 60,  niveau: 0.9, retard: 0},     // pleine
    {x: 180, niveau: 0.45, retard: 0.4},  // à moitié
    {x: 300, niveau: 0.15, retard: 0.8},  // vide
  ];
  const couleurNiveau = n => n > 0.7 ? "#e0573e" : (n > 0.35 ? "#e0a83e" : "#7ed957");

  function bac(x, niveau, retard){
    const g = document.createElementNS(NS, "g");
    const largeur = 78, hauteur = 110, y = 150;

    // corps (contour)
    const corps = document.createElementNS(NS, "rect");
    corps.setAttribute("x", x); corps.setAttribute("y", y);
    corps.setAttribute("width", largeur); corps.setAttribute("height", hauteur);
    corps.setAttribute("rx", 8);
    corps.setAttribute("fill", "#ffffff10");
    corps.setAttribute("stroke", "#cfe3d4"); corps.setAttribute("stroke-width", "2");

    // remplissage (monte avec animation)
    const hRempli = hauteur * niveau;
    const rempli = document.createElementNS(NS, "rect");
    rempli.setAttribute("x", x+3); rempli.setAttribute("width", largeur-6);
    rempli.setAttribute("rx", 5);
    rempli.setAttribute("fill", couleurNiveau(niveau));
    rempli.setAttribute("y", y+hauteur-3);
    rempli.setAttribute("height", 0);

    // couvercle
    const couv = document.createElementNS(NS, "rect");
    couv.setAttribute("x", x-6); couv.setAttribute("y", y-12);
    couv.setAttribute("width", largeur+12); couv.setAttribute("height", 12);
    couv.setAttribute("rx", 4); couv.setAttribute("fill", "#2f5d3f");

    g.appendChild(corps); g.appendChild(rempli); g.appendChild(couv);
    svg.appendChild(g);

    if(reduce){
      rempli.setAttribute("y", y+hauteur-3-hRempli);
      rempli.setAttribute("height", hRempli);
      return;
    }
    // animation : le remplissage monte
    let t = 0;
    const dur = 1100, start = performance.now() + retard*1000;
    function anim(now){
      if(now < start){ requestAnimationFrame(anim); return; }
      t = Math.min((now-start)/dur, 1);
      const e = 1-Math.pow(1-t,3); // easeOutCubic
      rempli.setAttribute("height", hRempli*e);
      rempli.setAttribute("y", y+hauteur-3-hRempli*e);
      if(t<1) requestAnimationFrame(anim);
      else if(niveau>0.7) pulse(x+largeur/2, y-24); // signal sur la pleine
    }
    requestAnimationFrame(anim);
  }

  // onde de détection (sur la poubelle pleine)
  function pulse(cx, cy){
    function onde(){
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", cx); c.setAttribute("cy", cy);
      c.setAttribute("r", 6); c.setAttribute("fill", "none");
      c.setAttribute("stroke", "#e0573e"); c.setAttribute("stroke-width", "2");
      svg.appendChild(c);
      let r = 6, op = 0.9;
      function grow(){
        r += 1.4; op -= 0.018;
        c.setAttribute("r", r); c.setAttribute("opacity", Math.max(op,0));
        if(op>0) requestAnimationFrame(grow); else c.remove();
      }
      grow();
    }
    onde(); setInterval(onde, 1400);
  }

  bacs.forEach(b => bac(b.x, b.niveau, b.retard));

  // Révélation au scroll des points du socle
  const io = new IntersectionObserver((entries)=>{
    entries.forEach((e,i)=>{ if(e.isIntersecting){
      e.target.style.animationDelay = (i*0.12)+"s";
      e.target.classList.add("vu"); io.unobserve(e.target);
    }});
  },{threshold:.3});
  document.querySelectorAll(".pt").forEach(p=>io.observe(p));
</script>
</body></html>
"""


# ====================== PAGE ÉQUIPE ======================
PAGE_EQUIPE = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Équipe — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--foret:#14352a;--signal:#7ed957;--brume:#cfe3d4;--crayon:#eef5ef}
  *{box-sizing:border-box}
  body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:var(--foret);color:var(--crayon)}
  .wrap{max-width:980px;margin:0 auto;padding:0 1.5rem}
  nav{display:flex;justify-content:space-between;align-items:center;padding:1.5rem 0}
  .marque{font-weight:800;letter-spacing:.18em;font-size:.95rem;text-transform:uppercase;color:var(--signal)}
  nav a{color:var(--brume);text-decoration:none;font-size:.9rem}
  nav a:hover{color:var(--signal)}
  h1{font-size:clamp(1.8rem,4vw,2.6rem);margin:2rem 0 .5rem;letter-spacing:-.02em}
  .intro{color:var(--brume);max-width:55ch;line-height:1.6;margin-bottom:2.5rem}
  .grille{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:1.2rem;padding-bottom:4rem}
  .membre{background:#ffffff0a;border:1px solid #ffffff1a;border-radius:14px;padding:1.5rem;
    transition:transform .15s,border-color .2s}
  .membre:hover{transform:translateY(-3px);border-color:var(--signal)}
  .avatar{width:54px;height:54px;border-radius:50%;background:#2f5d3f;display:flex;align-items:center;
    justify-content:center;margin-bottom:1rem}
  .avatar svg{width:26px;height:26px;stroke:var(--signal)}
  .nom{font-weight:700;font-size:1.05rem;color:var(--crayon)}
  .nom .vide{color:#ffffff55;font-weight:400;font-style:italic}
  .role{color:var(--brume);font-size:.88rem;margin-top:.3rem}
</style></head>
<body>
<div class="wrap">
  <nav>
    <div class="marque">ecoVisio</div>
    <div><a href="{{ url_for('accueil') }}">Retour à l'accueil</a></div>
  </nav>

  <h1>L'équipe du projet</h1>
  <p class="intro">Le projet ecoVisio a été conçu et réalisé dans le cadre du
    Master Camp D.</p>

  <div class="grille">
   
    <div class="membre">
      <div class="avatar">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0112 0v1"/></svg>
      </div>
      <div class="nom"><span class="vide">MPASSI Exauce Eben-Ezer</span></div>
      <div class="role">Chef de projet</div>
    </div>
    
    <div class="membre">
      <div class="avatar">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0112 0v1"/></svg>
      </div>
      <div class="nom"><span class="vide">MOULIOM YACHERE
Malika Nourane Lea</span></div>
      <div class="role">Développeur Back-end</div>
    </div>
   



<div class="membre">
      <div class="avatar">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0112 0v1"/></svg>
      </div>
      <div class="nom"><span class="vide">NEBO PELE
Maxime Bryan</span></div>
      <div class="role">Traitement image & IA</div>
    </div>
   
 
<div class="membre">
      <div class="avatar">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0112 0v1"/></svg>
      </div>
      <div class="nom"><span class="vide">NGOUOWA SIEUNOU
Lyne Merveille
</span></div>
      <div class="role">Front-end & UX</div>
    </div>

   
 
<div class="membre">
      <div class="avatar">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0112 0v1"/></svg>
      </div>
      <div class="nom"><span class="vide">NTCHORERE MEPAS
Guss-erwyn</span></div>
      <div class="role">Tests & Documentation</div>
    
   
</div class="grille">
</body></html>
"""


@app.route("/")
def accueil():
    return render_template_string(PAGE_ACCUEIL)


@app.route("/equipe")
def equipe():
    return render_template_string(PAGE_EQUIPE)


# ====================== PAGE DE SIGNALEMENT (citoyen) ======================
PAGE_SIGNALER = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Signaler — {{ poubelle['id'] }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_CITOYEN__
  label.btn{display:flex;align-items:center;justify-content:center;gap:.5rem;width:100%;
    padding:.95rem;border-radius:12px;font-size:1rem;font-weight:600;cursor:pointer;
    background:var(--mousse);color:#fff;margin-bottom:.8rem}
  label.btn svg{width:20px;height:20px;stroke:#fff}
  input[type=file]{display:none}
  #apercu{width:100%;border-radius:12px;margin-bottom:.8rem;display:none}
  .niveaux{display:flex;gap:.5rem;margin-bottom:1.2rem}
  .niveaux input{display:none}
  .niveaux label{flex:1;text-align:center;padding:.85rem .3rem;border-radius:12px;cursor:pointer;
    font-weight:700;color:#fff;opacity:.5;transition:opacity .15s,transform .15s}
  .niveaux input:checked + label{opacity:1;transform:translateY(-2px);box-shadow:0 4px 12px -3px #0005}
  .lv{background:var(--vide)}.lm{background:var(--moitie);color:#3a2c00}.lp{background:var(--pleine)}
  button[type=submit]{width:100%;padding:1rem;border:none;border-radius:12px;
    background:var(--signal-d);color:#fff;font-size:1.05rem;font-weight:700;cursor:pointer}
  .nomf{text-align:center;color:#5a6f61;margin-bottom:1rem;min-height:1.1rem;font-size:.9rem}
  .step{font-size:.78rem;letter-spacing:.08em;text-transform:uppercase;color:var(--signal-d);font-weight:700}
</style></head>
<body>
<div class="wrap">
  <div class="topbar">
    <svg class="leaf" viewBox="0 0 24 24" fill="none" stroke="#2f5d3f" stroke-width="2"><path d="M12 2C8 6 6 9 6 13a6 6 0 0012 0c0-4-2-7-6-11z"/></svg>
    <span class="marque">ecoVisio</span>
  </div>

  <div class="carte" style="margin-bottom:1.2rem">
    <div class="id">{{ poubelle['id'] }}</div>
    <div class="lieu" style="margin-top:.3rem">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M12 21s7-5.5 7-11a7 7 0 10-14 0c0 5.5 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>
      {{ poubelle['nom_lieu'] }}
    </div>
  </div>

  <form method="post" enctype="multipart/form-data">
    <div class="step">Étape 1</div>
    <h2 style="margin-top:.2rem">Photo de la poubelle</h2>
    <label class="btn" for="photo">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M3 7h4l2-2h6l2 2h4v12H3z"/><circle cx="12" cy="13" r="3.5"/></svg>
      Prendre une photo
    </label>
    <input id="photo" type="file" name="photo" accept="image/*" capture="environment" required>
    <img id="apercu" alt="aperçu">
    <div class="nomf" id="nomf"></div>

    <div class="step">Étape 2</div>
    <h2 style="margin-top:.2rem">Selon vous, la poubelle est</h2>
    <div class="niveaux">
      <input type="radio" id="n_vide" name="eval_citoyen" value="vide" required>
      <label class="lv" for="n_vide">Vide</label>
      <input type="radio" id="n_pleine" name="eval_citoyen" value="pleine">
      <label class="lp" for="n_pleine">Pleine</label>
    </div>

    <button type="submit">Envoyer le signalement</button>
  </form>
</div>

  <script>
    const input = document.getElementById('photo');
    input.addEventListener('change', () => {
      if (input.files && input.files[0]) {
        const apercu = document.getElementById('apercu');
        apercu.src = URL.createObjectURL(input.files[0]);
        apercu.style.display = 'block';
        document.getElementById('nomf').textContent = input.files[0].name;
      }
    });
  </script>
</body></html>
"""


# ====================== PAGE DE CONFIRMATION ======================
PAGE_MERCI = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Signalement enregistré — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_CITOYEN__
  .center{text-align:center}
  .mark{width:60px;height:60px;margin:0 auto 1rem;border-radius:50%;background:#e6f6ea;
    display:flex;align-items:center;justify-content:center}
  .mark svg{width:30px;height:30px;stroke:var(--vide)}
  h1{color:var(--mousse);font-size:1.25rem}
  .res{text-align:left;margin:1.2rem 0}
  .res .row{display:flex;justify-content:space-between;align-items:center;padding:.5rem 0;border-bottom:1px solid #eef3ef}
  .res .row:last-child{border:none}
  .res .lab{color:#3a5547;font-size:.92rem}
  .accord{color:var(--vide);font-weight:600}.desaccord{color:var(--pleine);font-weight:600}
  .alerte{display:flex;align-items:center;gap:.6rem;background:#fdecea;border:1px solid #f5c6bf;
    color:#a3261a;padding:.8rem 1rem;border-radius:12px;font-weight:600;margin-top:1rem;font-size:.92rem}
  .alerte svg{width:20px;height:20px;flex:none;stroke:#a3261a}
  .avert-vision{display:flex;align-items:center;gap:.6rem;background:#fff6e5;border:1px solid #f0d8a0;
    color:#7a5a12;padding:.8rem 1rem;border-radius:12px;margin-top:1rem;font-size:.88rem;text-align:left}
  .avert-vision svg{width:20px;height:20px;flex:none;stroke:#b9871a}
</style></head>
<body>
<div class="wrap">
  <div class="carte center">
    <div class="mark">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>
    </div>
    <h1>Signalement enregistré</h1>
    <div class="res">
      <div class="row"><span class="lab">Votre évaluation</span>
        <span class="badge b_{{ eval_citoyen }}">{{ aff[eval_citoyen] }}</span></div>
      <div class="row"><span class="lab">Analyse ecoVisio</span>
        <span class="badge b_{{ verdict_ia }}">{{ aff[verdict_ia] }}</span></div>
      <div class="row"><span class="lab">Résultat</span>
        {% if accord %}<span class="accord">Évaluation confirmée</span>
        {% else %}<span class="desaccord">Évaluation corrigée</span>{% endif %}</div>
    </div>
    {% if verdict_ia == 'pleine' %}
      <div class="alerte">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9l-8 13.8A2 2 0 004 21h16a2 2 0 001.7-3.3l-8-13.8a2 2 0 00-3.4 0z"/></svg>
        Poubelle pleine — alerte transmise aux services de collecte
      </div>
    {% endif %}
    {% if avertissement_vision %}
      <div class="avert-vision">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>
        {{ avertissement_vision }}
      </div>
    {% endif %}
    <p style="margin-top:1.5rem"><a href="{{ url_for('accueil') }}">Retour à l'accueil</a></p>
  </div>
</div>
</body></html>
"""


# ====================== PAGE DE REJET (photo non conforme) ======================
PAGE_REJET = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Photo non conforme — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_CITOYEN__
  .center{text-align:center}
  .mark{width:60px;height:60px;margin:0 auto 1rem;border-radius:50%;background:#fdecea;
    display:flex;align-items:center;justify-content:center}
  .mark svg{width:30px;height:30px;stroke:var(--pleine)}
  h1{color:var(--pleine);font-size:1.2rem}
  .raison{background:#fdecea;border:1px solid #f5c6bf;border-radius:12px;padding:1rem;
    margin:1rem 0;color:#a3261a;font-weight:600}
  .hint{color:#5a6f61;font-size:.92rem}
  a.btn{display:inline-flex;align-items:center;gap:.5rem;margin-top:1.2rem;padding:.85rem 1.4rem;
    background:var(--mousse);color:#fff;text-decoration:none;border-radius:12px;font-weight:600}
  a.btn svg{width:18px;height:18px;stroke:#fff}
</style></head>
<body>
<div class="wrap">
  <div class="carte center">
    <div class="mark">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M3 7h4l2-2h6l2 2h4v12H3z"/><path d="M5 5l14 14"/></svg>
    </div>
    <h1>Photo non conforme</h1>
    <div class="raison">{{ raison }}</div>
    <p class="hint">Votre signalement n'a pas été enregistré. Reprenez une photo pour réessayer.</p>
    <a class="btn" href="{{ url_for('signaler', id_poubelle=poubelle['id']) }}">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M3 12a9 9 0 019-9 9 9 0 016.5 2.8L21 8M21 3v5h-5"/></svg>
      Reprendre une photo
    </a>
  </div>
</div>
</body></html>
"""


# ====================== PAGE LIMITE DÉPASSÉE (anti-spam) ======================
PAGE_LIMITE = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Trop de signalements — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_CITOYEN__
  .center{text-align:center}
  .mark{width:60px;height:60px;margin:0 auto 1rem;border-radius:50%;background:#fff6e0;
    display:flex;align-items:center;justify-content:center}
  .mark svg{width:30px;height:30px;stroke:var(--moitie)}
  h1{color:#b07d00;font-size:1.2rem}
  .box{background:#fff8e1;border:1px solid #ffe08a;border-radius:12px;padding:1rem;margin:1rem 0;color:#7a5b00}
  .attente{font-size:1.5rem;font-weight:800;color:var(--pleine);margin:.5rem 0}
  .hint{color:#5a6f61;font-size:.92rem}
</style></head>
<body>
<div class="wrap">
  <div class="carte center">
    <div class="mark">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
    </div>
    <h1>Trop de signalements</h1>
    <div class="box">
      Vous avez atteint la limite de {{ limite }} signalements en {{ fenetre }} minutes
      pour cette poubelle ({{ poubelle['id'] }}).<br>Réessayez dans :
      <div class="attente">{{ attente }}</div>
    </div>
    <p class="hint">Cette limite protège le service contre les envois abusifs.</p>
    <p style="margin-top:1.2rem"><a href="{{ url_for('accueil') }}">Retour à l'accueil</a></p>
  </div>
</div>
</body></html>
"""


# ====================== PAGE POUBELLE RETIRÉE (désactivée) ======================
PAGE_POUBELLE_RETIREE = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Poubelle retirée — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_CITOYEN__
  .center{text-align:center}
  .mark{width:60px;height:60px;margin:0 auto 1rem;border-radius:50%;background:#eceff0;
    display:flex;align-items:center;justify-content:center}
  .mark svg{width:30px;height:30px;stroke:#5b6e63}
  h1{color:#3a5547;font-size:1.2rem}
  .hint{color:#5a6f61;font-size:.92rem}
</style></head>
<body>
<div class="wrap">
  <div class="carte center">
    <div class="mark">
      <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M8 12h8"/></svg>
    </div>
    <h1>Cette poubelle n'est plus en service</h1>
    <p class="hint">La poubelle {{ poubelle['id'] }} ({{ poubelle['nom_lieu'] }}) a été retirée.
      Merci de votre civisme.</p>
    <p style="margin-top:1.2rem"><a href="{{ url_for('accueil') }}">Retour à l'accueil</a></p>
  </div>
</div>
</body></html>
"""


@app.route("/signaler/<id_poubelle>", methods=["GET", "POST"])
def signaler(id_poubelle):
    poubelle = get_poubelle(id_poubelle)
    if poubelle is None:
        abort(404, description="Poubelle inconnue. Le QR code n'est pas valide.")
    # Poubelle désactivée (retirée du service) : on informe sans permettre de signaler.
    if not poubelle["active"]:
        return render_template_string(PAGE_POUBELLE_RETIREE, poubelle=poubelle)

    if request.method == "POST":
        # 0) Limitation de débit (anti-spam) : max N uploads / fenêtre par (IP + poubelle)
        ip = ip_du_client()
        autorise, secondes = verifier_limite(ip, id_poubelle)
        if not autorise:
            return render_template_string(
                PAGE_LIMITE, poubelle=poubelle,
                attente=format_attente(secondes),
                limite=LIMITE_UPLOADS, fenetre=FENETRE_MINUTES,
            ), 429  # 429 = Too Many Requests

        # On enregistre la tentative (même si la photo sera ensuite rejetée :
        # ça empêche de spammer le serveur avec des photos non conformes).
        enregistrer_tentative(ip, id_poubelle)
        nettoyer_vieilles_tentatives()

        # 1) Vérifs photo
        if "photo" not in request.files:
            abort(400, description="Aucune photo reçue.")
        fichier = request.files["photo"]
        if fichier.filename == "":
            abort(400, description="Aucun fichier sélectionné.")
        if not extension_autorisee(fichier.filename):
            abort(400, description="Format non autorisé (JPG ou PNG).")

        # 2) Évaluation citoyenne
        eval_citoyen = request.form.get("eval_citoyen")
        if eval_citoyen not in LABELS_VALIDES:
            abort(400, description="Évaluation citoyenne manquante ou invalide.")

        # 3) Sauvegarde de la photo
        ext = fichier.filename.rsplit(".", 1)[1].lower()
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
        nom_sauvegarde = secure_filename(f"{id_poubelle}_{horodatage}.{ext}")
        chemin = os.path.join(DOSSIER_UPLOADS, nom_sauvegarde)
        fichier.save(chemin)
        compresser_image(chemin)

        # 4) Analyse IA : conformité (qualité) PUIS niveau (Points 3 + 4)
        res = analyser_image(chemin)

        # 4bis) Si la photo n'est pas conforme : on rejette, on supprime la photo,
        # et on ne crée PAS de signalement (donnée inexploitable).
        if not res["conforme"]:
            try:
                os.remove(chemin)
            except OSError:
                pass
            return render_template_string(
                PAGE_REJET, poubelle=poubelle, raison=res["raison_rejet"],
            )

        # 4ter) Reconnaissance d'objet (Google Vision) : la photo montre-t-elle
        # bien une poubelle ? MODE SOUPLE : on n'appelle Vision que sur les photos
        # déjà conformes, et si aucune poubelle n'est détectée, on NE rejette PAS —
        # on enregistre quand même le signalement et on affiche un simple
        # avertissement. Raison : la reconnaissance d'objet n'est pas fiable à 100 %
        # (une vraie poubelle, si elle est lointaine ou noyée dans la scène, peut ne
        # pas être détectée). Le QR code reste le garde-fou principal.
        # Échec gracieux : si Vision est indisponible (pas de clé, réseau...), pas d'avertissement.
        vision_res = analyser_avec_vision(chemin)
        avertissement_vision = None
        if vision_res["disponible"] and not vision_res["est_poubelle"]:
            avertissement_vision = (
                "La reconnaissance automatique n'a pas identifié de poubelle sur "
                "cette photo. Le signalement est tout de même enregistré ; vérifiez "
                "que la poubelle est bien visible pour de meilleurs résultats."
            )

        verdict_ia = res["verdict"]
        features = res["features"]

        # 5) Comparaison citoyen / IA (l'IA fait foi)
        statut, accord = comparer_citoyen_ia(eval_citoyen, verdict_ia)

        # 5bis) Contexte : météo (Open-Meteo) + jour de la semaine.
        # Échec gracieux : si la météo est indisponible, le signalement passe quand même.
        meteo = get_meteo(poubelle["latitude"], poubelle["longitude"])
        meteo_temp = meteo["temperature"] if meteo else None
        meteo_categorie = meteo["categorie"] if meteo else None
        jour = jour_de_semaine()

        # 6) Enregistrement complet
        ajouter_signalement(
            poubelle_id=id_poubelle,
            chemin_image=chemin,
            eval_citoyen=eval_citoyen,
            verdict_ia=verdict_ia,
            statut=statut,
            accord=accord,
            features_json=json.dumps(features, ensure_ascii=False),
            meteo_temp=meteo_temp,
            meteo_categorie=meteo_categorie,
            jour_semaine=jour,
        )

        return render_template_string(
            PAGE_MERCI, eval_citoyen=eval_citoyen, verdict_ia=verdict_ia,
            accord=accord, aff=LABEL_AFFICHAGE,
            avertissement_vision=avertissement_vision,
        )

    return render_template_string(PAGE_SIGNALER, poubelle=poubelle)


@app.route("/uploads/<nom>")
def fichier_upload(nom):
    return send_from_directory(DOSSIER_UPLOADS, nom)


# ====================== AUTHENTIFICATION AGENTS ======================
def login_requis(f):
    """Décorateur : redirige vers la page de connexion si l'agent n'est pas authentifié."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("agent_connecte"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    erreur = None

    if request.method == "POST":
        username = request.form.get("username")
        mot_de_passe = request.form.get("mot_de_passe")

        user = verifier_compte(username, mot_de_passe)

        if user:
            session["agent_connecte"] = True
            session["username"] = username
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        else:
            erreur = "Identifiants incorrects."

    return render_template_string(PAGE_LOGIN, erreur=erreur)

PAGE_LOGIN = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Connexion agents — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_AGENT__
  .center-screen{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1rem}
  .login-card{width:100%;max-width:380px}
  .lock{width:48px;height:48px;margin:0 auto 1rem;border-radius:50%;background:#ffffff14;
    display:flex;align-items:center;justify-content:center}
  .lock svg{width:24px;height:24px;stroke:var(--signal)}
  .login-card h1{text-align:center;font-size:1.2rem;margin:0 0 .3rem}
  .login-card p{text-align:center;color:var(--brume);font-size:.88rem;margin:0 0 1.2rem}
  input{width:100%;padding:.8rem;margin:.4rem 0;border:1px solid #ffffff2a;border-radius:10px;
    font-size:1rem;background:#ffffff0f;color:var(--crayon)}
  input::placeholder{color:#ffffff66}
  input:focus{outline:none;border-color:var(--signal)}
  .login-card button{width:100%;margin-top:.5rem}
  .err{color:#ff9a8a;text-align:center;margin-top:.6rem;font-size:.9rem}
</style></head>
<body>
  <div class="center-screen wrap">
    <div class="carte login-card">
      <div class="lock">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/></svg>
      </div>
      <h1>Espace agents</h1>
      <p>Réservé aux services de collecte</p>
      <form method="post">
          <input name="username" placeholder="Nom d'utilisateur" required>
          <input type="password" name="mot_de_passe" placeholder="Mot de passe" required>
          <button class="btn btn-p" type="submit">Se connecter</button>
      </form>
      <p style="text-align:center;margin-top:1rem">
          <a href="{{ url_for('register') }}">Créer un compte</a>
      </p>

      {% if erreur %}<div class="err">{{ erreur }}</div>{% endif %}
    </div>
    <a href="{{ url_for('accueil') }}">Retour à l'accueil</a>
  </div>
</body></html>
"""


@app.route("/logout")
def logout():
    session.pop("agent_connecte", None)
    return redirect(url_for("accueil"))


@app.route("/register", methods=["GET", "POST"])
def register():
    erreur = None
    succes = None

    if request.method == "POST":
        username = request.form.get("username")
        mot_de_passe = request.form.get("mot_de_passe")
        role = request.form.get("role")

        if creer_compte(username, mot_de_passe, role):
            succes = "Compte créé"
        else:
            erreur = "Utilisateur déjà existant"

    return render_template_string(PAGE_REGISTER, erreur=erreur, succes=succes)

def role_requis(role):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("agent_connecte"):
                return redirect(url_for("login"))

            if session.get("role") != role:
                abort(403)

            return f(*args, **kwargs)
        return wrapper
    return decorator


# ====================== PAGE REGISTER ======================
PAGE_REGISTER = """
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Créer un compte — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
__THEME_AGENT__
.center-screen{
  min-height:100vh;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:1rem
}
.login-card{ width:100%; max-width:380px }
.login-card h1{ text-align:center; font-size:1.2rem; margin:0 0 .3rem }
.login-card p{ text-align:center; color:var(--brume); font-size:.88rem; margin:0 0 1.2rem }
input{
  width:100%;box-sizing:border-box;padding:.8rem;margin:.4rem 0;
  border:1px solid #ffffff2a;border-radius:10px;font-size:1rem;
  background:#ffffff0f;color:var(--crayon)
}
input::placeholder{ color:#ffffff66 }
input:focus{ outline:none; border-color:var(--signal) }
.login-card button{ width:100%; margin-top:.5rem }
.err{ color:#ff9a8a; text-align:center; margin-top:.6rem; font-size:.9rem }
.msg{ color:#7CFFB2; text-align:center; margin-top:.6rem; font-size:.9rem }

/* ===== MENU DÉROULANT PERSONNALISÉ VISIO (100% stylé, bords arrondis partout) ===== */
.vselect{ position:relative; margin:.4rem 0; user-select:none }
.vselect-trigger{
  display:flex;align-items:center;justify-content:space-between;
  padding:.8rem;border:1px solid #ffffff2a;border-radius:10px;
  background:#ffffff0f;color:var(--crayon);cursor:pointer;font-size:1rem;
  transition:border-color .15s
}
.vselect.open .vselect-trigger{ border-color:var(--signal) }
.vselect-trigger .fleche{
  width:10px;height:10px;border-right:2px solid var(--brume);border-bottom:2px solid var(--brume);
  transform:rotate(45deg);transition:transform .2s;margin-left:.5rem;flex:none
}
.vselect.open .vselect-trigger .fleche{ transform:rotate(-135deg) }
.vselect-menu{
  position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:30;
  background:#1c3f31;border:1px solid #ffffff2a;border-radius:12px;
  padding:.3rem;box-shadow:0 12px 30px -8px #000a;
  opacity:0;transform:translateY(-6px);pointer-events:none;
  transition:opacity .15s,transform .15s
}
.vselect.open .vselect-menu{ opacity:1; transform:none; pointer-events:auto }
.vselect-option{ padding:.6rem .7rem;border-radius:8px;cursor:pointer;transition:background .12s }
.vselect-option:hover{ background:#ffffff14 }
.vselect-option.selected{ background:var(--mousse);color:var(--crayon);font-weight:600 }
.vselect-option.selected::after{  color:var(--signal) }
</style>
</head>
<body>
  <div class="center-screen wrap">
    <div class="carte login-card">
      <h1>Créer un compte</h1>
      <p>Accès réservé aux agents autorisés</p>
      <form method="post">
        <input name="username" placeholder="Nom d'utilisateur" required>
        <input type="password" name="mot_de_passe" placeholder="Mot de passe" required>

        <!-- Menu déroulant personnalisé : la valeur soumise est dans l'input caché 'role' -->
        <div class="vselect" id="vselect-role">
          <input type="hidden" name="role" value="agent">
          <div class="vselect-trigger">
            <span class="vselect-label">Agent</span>
            <span class="fleche"></span>
          </div>
          <div class="vselect-menu">
            <div class="vselect-option selected" data-value="agent">Agent</div>
            <div class="vselect-option" data-value="admin">Administrateur</div>
          </div>
        </div>

        <button class="btn btn-p" type="submit">Créer un compte</button>
      </form>
      {% if erreur %}<div class="err">{{ erreur }}</div>{% endif %}
      {% if succes %}<div class="msg">{{ succes }}</div>{% endif %}
      <p style="margin-top:1rem">
        <a href="{{ url_for('login') }}">Déjà un compte ? Se connecter</a>
      </p>
    </div>
    <a href="{{ url_for('accueil') }}">Retour à l'accueil</a>
  </div>

  <script>
    // Composant menu déroulant réutilisable : marche pour tout élément .vselect de la page
    document.querySelectorAll('.vselect').forEach(function(vs){
      const trigger = vs.querySelector('.vselect-trigger');
      const label   = vs.querySelector('.vselect-label');
      const hidden  = vs.querySelector('input[type=hidden]');
      const options = vs.querySelectorAll('.vselect-option');

      trigger.addEventListener('click', function(e){
        e.stopPropagation();
        document.querySelectorAll('.vselect.open').forEach(function(o){ if(o!==vs) o.classList.remove('open'); });
        vs.classList.toggle('open');
      });
      options.forEach(function(opt){
        opt.addEventListener('click', function(){
          options.forEach(function(o){ o.classList.remove('selected'); });
          opt.classList.add('selected');
          label.textContent = opt.textContent;
          hidden.value = opt.dataset.value;
          vs.classList.remove('open');
        });
      });
    });
    document.addEventListener('click', function(){
      document.querySelectorAll('.vselect.open').forEach(function(o){ o.classList.remove('open'); });
    });
  </script>
</body>
</html>
"""

# ====================== DASHBOARD AGENTS (Point 5) ======================
PAGE_DASHBOARD = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Tableau de bord — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>__THEME_AGENT__
  .wrap{max-width:1100px}
  .topnav{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem;
    padding:1.4rem 0;border-bottom:1px solid #ffffff14}
  .topnav .marque{font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--signal)}
  .topnav-actions a{color:var(--brume);text-decoration:none;font-size:.9rem;margin-left:1.2rem}
  .topnav-actions a:hover{color:var(--signal)}
  .topnav-actions a.out{color:#ff9a8a}
  .secttitle{display:flex;align-items:center;gap:.5rem}
  .secttitle svg{width:18px;height:18px;stroke:var(--signal)}
  .cartes{display:flex;gap:1rem;flex-wrap:wrap;margin:1.5rem 0}
  .stat{flex:1;min-width:130px;background:#ffffff0a;border:1px solid #ffffff1a;border-radius:14px;padding:1.1rem;text-align:center}
  .stat .n{font-size:1.9rem;font-weight:800;color:var(--signal)}
  .stat.rouge .n{color:var(--pleine)}.stat.orange .n{color:var(--moitie)}
  .stat .l{font-size:.8rem;color:var(--brume);margin-top:.2rem}
  #map{height:380px;border-radius:14px;border:1px solid #ffffff1a;margin-bottom:1rem}
  .grille{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}
  @media(max-width:700px){.grille{grid-template-columns:1fr}}
  .alerte{background:#ffffff0a;border-left:3px solid var(--pleine);border-radius:8px;padding:.55rem .8rem;margin:.35rem 0;color:var(--crayon)}
  .risque{background:#ffffff0a;border-left:3px solid var(--moitie);border-radius:8px;padding:.55rem .8rem;margin:.35rem 0;color:var(--crayon)}
  .vide-msg{color:#8aa394;font-style:italic;font-size:.9rem}
  canvas{background:#ffffff0a;border-radius:12px;padding:.5rem}
  .filtres{display:flex;gap:.4rem;margin:.3rem 0 .6rem}
  .filtre-btn{padding:.35rem .8rem;border:1px solid #ffffff2a;border-radius:999px;
    background:transparent;color:var(--brume);cursor:pointer;font-size:.82rem;transition:all .15s}
  .filtre-btn:hover{border-color:var(--signal)}
  .filtre-btn.actif{background:var(--signal);color:var(--foret);border-color:var(--signal);font-weight:700}
</style></head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="marque">ecoVisio</div>
    <div class="topnav-actions">
      <a href="{{ url_for('accueil') }}">Accueil</a>
      <a href="{{ url_for('poubelles') }}">Poubelles</a>
      <a href="{{ url_for('admin_regles') }}">Configurer les règles</a>
      {% if session.get("role") == "admin" %}
      <a href="{{ url_for('caracteristiques') }}">Caractéristiques</a>
      <a href="{{ url_for('etiqueter') }}">Jeu de test</a>
      <a href="{{ url_for('evaluation_page') }}">Résultats</a>
      <a href="{{ url_for('calibration_page') }}">Calibration</a>
      <a href="{{ url_for('verification_page') }}">Vérification</a>
      {% endif %}
      <a class="out" href="{{ url_for('logout') }}">Déconnexion</a>
    </div>
  </div>

  <h1>Tableau de bord</h1>
  <p class="sous">État des poubelles publiques en temps réel, calculé à partir des derniers signalements.
    <span id="live-indicateur" style="display:inline-flex;align-items:center;gap:.3rem;margin-left:.5rem;font-size:.82rem;color:var(--signal)">
      <span style="width:8px;height:8px;border-radius:50%;background:var(--signal);display:inline-block;animation:pulse 2s infinite"></span>
      <span id="live-texte">en direct</span>
    </span>
  </p>
  <style>@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}</style>

  <!-- Statistiques clés -->
  <div class="cartes">
    <div class="stat rouge"><div class="n" id="kpi-pleine">{{ rep['pleine'] }}</div><div class="l">Pleines</div></div>
    <div class="stat"><div class="n" id="kpi-vide">{{ rep['vide'] }}</div><div class="l">Vides</div></div>
    <div class="stat"><div class="n" id="kpi-total">{{ stats['total'] }}</div><div class="l">Signalements</div></div>
    <div class="stat"><div class="n" id="kpi-accord">{{ stats['taux_accord'] }}%</div><div class="l">Accord IA / citoyen</div></div>
  </div>

  <!-- Carte -->
  <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M9 3L3 6v15l6-3 6 3 6-3V3l-6 3-6-3z"/><path d="M9 3v15M15 6v15"/></svg> Carte des poubelles</h2>
  <div id="map"></div>

  <div class="grille">
    <div>
      <!-- Alertes : poubelles pleines -->
      <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9l-8 13.8A2 2 0 004 21h16a2 2 0 001.7-3.3l-8-13.8a2 2 0 00-3.4 0z"/></svg> À collecter en priorité</h2>
      {% if pleines %}
        {% for p in pleines %}
          <div class="alerte"><b>{{ p['id'] }}</b> — {{ p['nom_lieu'] }}</div>
        {% endfor %}
      {% else %}
        <p class="vide-msg">Aucune poubelle pleine actuellement.</p>
      {% endif %}

      <!-- Zones à risque -->
      <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M12 2C8 6 6 9 6 13a6 6 0 0012 0c0-4-2-7-6-11z"/></svg> Zones à risque de débordement</h2>
      {% if risques %}
        {% for r in risques %}
          <div class="risque"><b>{{ r['id'] }}</b> — {{ r['nom_lieu'] }}
            ({{ r['taux_pleine'] }}% de signalements pleine)</div>
        {% endfor %}
      {% else %}
        <p class="vide-msg">Pas assez de données pour identifier des zones à risque.</p>
      {% endif %}
    </div>

    <div>
      <!-- Graphique signalements par jour (avec filtre de période dynamique) -->
      <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M3 3v18h18M7 14l3-3 3 3 5-6"/></svg> Signalements par jour</h2>
      <div class="filtres" id="filtres-jours">
        <button data-jours="7" class="filtre-btn">7 jours</button>
        <button data-jours="14" class="filtre-btn actif">14 jours</button>
        <button data-jours="30" class="filtre-btn">30 jours</button>
      </div>
      <canvas id="chartJours" height="160"></canvas>

      <!-- Graphique Chart.js dynamique : répartition pleine/vide (donut interactif) -->
      <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3v9l6 3"/></svg> Répartition des statuts</h2>
      <canvas id="chartRepartition" height="160"></canvas>

      <!-- Version matplotlib (back-end), en complément -->
      <p class="vide-msg" style="font-size:.8rem;margin-top:.8rem">Version générée côté serveur (matplotlib) :</p>
      <img src="{{ url_for('graphe_repartition') }}" alt="répartition" style="width:100%;max-width:300px;background:#fff;border-radius:12px;padding:.4rem">

      <!-- Tableau récap -->
      <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M3 5h18M3 12h18M3 19h18"/></svg> Toutes les poubelles</h2>
      <div class="filtres" id="filtres-statut">
        <button data-statut="tous" class="filtre-btn actif">Toutes</button>
        <button data-statut="pleine" class="filtre-btn">Pleines</button>
        <button data-statut="vide" class="filtre-btn">Vides</button>
        <button data-statut="inconnu" class="filtre-btn">Sans donnée</button>
      </div>
      <table id="table-poubelles">
        <tr><th>ID</th><th>Lieu</th><th>Statut</th></tr>
        {% for p in carte %}
        <tr data-statut="{{ p['statut'] if p['statut'] else 'inconnu' }}">
          <td>{{ p['id'] }}</td>
          <td>{{ p['nom_lieu'] }}</td>
          <td>
            {% if p['statut'] %}<span class="badge b_{{ p['statut'] }}">{{ aff[p['statut']] }}</span>
            {% else %}<span class="badge b_inconnu">—</span>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>
  </div>

  <!-- ANALYSE DES CORRÉLATIONS (météo / jour vs débordement) -->
  <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M3 3v18h18M7 16l4-5 3 3 5-7"/></svg> Facteurs de débordement</h2>
  <p class="sous" style="font-size:.9rem">Taux de poubelles signalées « pleine » selon le contexte. Aide à anticiper les périodes à risque.</p>
  <div class="grille">
    <div class="carte">
      <h3 style="margin-top:0;font-size:1rem">Par jour de la semaine</h3>
      <table>
        <tr><th>Jour</th><th>Signalements</th><th>Taux pleine</th></tr>
        {% for c in corr_jour %}
        <tr>
          <td style="text-transform:capitalize">{{ c['jour'] }}</td>
          <td>{{ c['total'] }}</td>
          <td>{% if c['taux_pleine'] is not none %}<b style="color:var(--signal)">{{ c['taux_pleine'] }}%</b>{% else %}<span class="vide-msg">—</span>{% endif %}</td>
        </tr>
        {% endfor %}
      </table>
    </div>
    <div class="carte">
      <h3 style="margin-top:0;font-size:1rem">Par condition météo</h3>
      {% if corr_meteo %}
      <table>
        <tr><th>Météo</th><th>Signalements</th><th>Taux pleine</th></tr>
        {% for c in corr_meteo %}
        <tr>
          <td style="text-transform:capitalize">{{ c['categorie'] }}</td>
          <td>{{ c['total'] }}</td>
          <td><b style="color:var(--signal)">{{ c['taux_pleine'] }}%</b></td>
        </tr>
        {% endfor %}
      </table>
      {% else %}
      <p class="vide-msg">Pas encore de données météo. Elles s'accumulent à chaque signalement.</p>
      {% endif %}
    </div>
  </div>
</div>

  <script>
    // ---- Carte Leaflet ----
    const poubelles = {{ carte_json|safe }};
    const couleurs = {vide:"#2e8b57", pleine:"#c0392b", null:"#999"};
    const labels = {vide:"Vide", pleine:"Pleine", null:"Aucune donnée"};

    // Centre la carte sur la moyenne des poubelles (ou Paris par défaut)
    let lat = 48.86, lon = 2.35;
    if (poubelles.length) {
      lat = poubelles.reduce((s,p)=>s+p.latitude,0)/poubelles.length;
      lon = poubelles.reduce((s,p)=>s+p.longitude,0)/poubelles.length;
    }
    const map = L.map('map').setView([lat, lon], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap', maxZoom: 19
    }).addTo(map);

    const marqueurs = [];  // garde une référence pour le filtrage
    poubelles.forEach(p => {
      const c = couleurs[p.statut] || couleurs[null];
      const m = L.circleMarker([p.latitude, p.longitude], {
        radius: 10, fillColor: c, color: "#fff", weight: 2, fillOpacity: 0.9
      }).addTo(map).bindPopup(
        `<b>${p.id}</b><br>${p.nom_lieu}<br>Statut : ${labels[p.statut]||labels[null]}` +
        (p.derniere_maj ? `<br><small>Maj : ${p.derniere_maj}</small>` : "")
      );
      marqueurs.push({ marker: m, statut: p.statut || 'inconnu' });
    });

    // ---- Filtre par statut : agit sur le tableau ET la carte, sans recharger ----
    document.querySelectorAll('#filtres-statut .filtre-btn').forEach(function(btn){
      btn.addEventListener('click', function(){
        document.querySelectorAll('#filtres-statut .filtre-btn').forEach(b=>b.classList.remove('actif'));
        btn.classList.add('actif');
        const choix = btn.dataset.statut;
        // Tableau : montre/cache les lignes
        document.querySelectorAll('#table-poubelles tr[data-statut]').forEach(function(tr){
          tr.style.display = (choix === 'tous' || tr.dataset.statut === choix) ? '' : 'none';
        });
        // Carte : ajoute/retire les marqueurs
        marqueurs.forEach(function(o){
          const visible = (choix === 'tous' || o.statut === choix);
          if(visible){ if(!map.hasLayer(o.marker)) o.marker.addTo(map); }
          else { if(map.hasLayer(o.marker)) map.removeLayer(o.marker); }
        });
      });
    });

    // ---- Graphique signalements par jour (avec filtre de période) ----
    // Données pré-calculées pour chaque période, fournies par le serveur
    const donneesJours = {{ donnees_jours_json|safe }};
    let chartJours = new Chart(document.getElementById('chartJours'), {
      type: 'bar',
      data: {
        labels: donneesJours['14'].dates,
        datasets: [{ label: 'Signalements', data: donneesJours['14'].comptes,
                     backgroundColor: '#7ed957', borderRadius: 4 }]
      },
      options: { plugins:{legend:{display:false}},
        scales:{
          y:{beginAtZero:true,ticks:{precision:0,color:'#cfe3d4'},grid:{color:'#ffffff14'}},
          x:{ticks:{color:'#cfe3d4'},grid:{display:false}}
        } }
    });
    // Filtre de période : met à jour le graphe sans recharger la page
    document.querySelectorAll('#filtres-jours .filtre-btn').forEach(function(btn){
      btn.addEventListener('click', function(){
        document.querySelectorAll('#filtres-jours .filtre-btn').forEach(b=>b.classList.remove('actif'));
        btn.classList.add('actif');
        const d = donneesJours[btn.dataset.jours];
        chartJours.data.labels = d.dates;
        chartJours.data.datasets[0].data = d.comptes;
        chartJours.update();
      });
    });

    // ---- Graphique répartition pleine/vide (donut interactif Chart.js) ----
    window._chartRepartition = new Chart(document.getElementById('chartRepartition'), {
      type: 'doughnut',
      data: {
        labels: ['Vides', 'Pleines'],
        datasets: [{
          data: [{{ rep['vide'] }}, {{ rep['pleine'] }}],
          backgroundColor: ['#2e8b57', '#e0573e'],
          borderColor: '#14352a', borderWidth: 2
        }]
      },
      options: { plugins:{legend:{labels:{color:'#cfe3d4'}}} }
    });

    // ---- Rafraîchissement automatique (AJAX) : effet "temps réel" ----
    // Toutes les 30 secondes, on redemande les stats au serveur et on met à jour
    // les indicateurs sans recharger la page (polling AJAX, léger et simple).
    async function rafraichirStats(){
      try {
        const rep = await fetch('{{ url_for("dashboard_stats_json") }}');
        if(!rep.ok) return;
        const d = await rep.json();
        document.getElementById('kpi-pleine').textContent = d.pleine;
        document.getElementById('kpi-vide').textContent = d.vide;
        document.getElementById('kpi-total').textContent = d.total;
        document.getElementById('kpi-accord').textContent = d.taux_accord + '%';
        document.getElementById('live-texte').textContent = 'mis à jour à ' + d.horodatage;
        if(window._chartRepartition){
          window._chartRepartition.data.datasets[0].data = [d.vide, d.pleine];
          window._chartRepartition.update();
        }
      } catch(e){ /* erreur réseau ignorée, on réessaie au prochain cycle */ }
    }
    setInterval(rafraichirStats, 30000);  // toutes les 30 secondes
  </script>
</body></html>
"""


@app.route("/dashboard")
@login_requis
def dashboard():
    import json as _json
    carte = donnees_carte(n=5)
    rep = repartition_statuts(n=5)
    stats = compter_signalements()
    pleines = poubelles_pleines(n=5)
    risques = zones_a_risque(seuil_taux=0.4, mini_signalements=3)
    # Données pré-calculées pour le filtre de période (7 / 14 / 30 jours)
    donnees_jours = {}
    for nb in (7, 14, 30):
        d, comptes = signalements_par_jour(nb)
        donnees_jours[str(nb)] = {"dates": d, "comptes": comptes}
    corr_jour = correlation_jour_semaine()
    corr_meteo = correlation_meteo()

    return render_template_string(
        PAGE_DASHBOARD,
        carte=carte,
        carte_json=_json.dumps(carte),
        rep=rep,
        stats=stats,
        pleines=pleines,
        risques=risques,
        donnees_jours_json=_json.dumps(donnees_jours),
        corr_jour=corr_jour,
        corr_meteo=corr_meteo,
        aff=LABEL_AFFICHAGE,
    )


@app.route("/dashboard/stats.json")
@login_requis
def dashboard_stats_json():
    """
    Renvoie les statistiques courantes au format JSON, pour le rafraîchissement
    automatique du tableau de bord (AJAX) sans recharger la page.
    """
    rep = repartition_statuts(n=5)
    stats = compter_signalements()
    pleines = poubelles_pleines(n=5)
    return jsonify({
        "vide": rep["vide"],
        "pleine": rep["pleine"],
        "total": stats["total"],
        "taux_accord": stats["taux_accord"],
        "nb_pleines": len(pleines),
        "horodatage": datetime.now().strftime("%H:%M:%S"),
    })


# ====================== ADMIN : RÈGLES CONFIGURABLES (Point niveau 2-3) ======================
PAGE_ADMIN_REGLES = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Admin — Règles</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_AGENT__
  .wrap{max-width:900px}
  .topnav{display:flex;justify-content:space-between;align-items:center;padding:1.4rem 0;border-bottom:1px solid #ffffff14}
  .topnav .marque{font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--signal)}
  .topnav a{color:var(--brume);text-decoration:none;font-size:.9rem}
  .topnav a:hover{color:var(--signal)}
  .bloc{background:#ffffff0a;border:1px solid #ffffff1a;border-radius:14px;padding:1.3rem;margin:1.2rem 0}
  label{display:block;font-size:.82rem;color:var(--brume);margin-top:.5rem}
  input,select{padding:.55rem;border:1px solid #ffffff2a;border-radius:8px;font-size:.95rem;
    background:#ffffff0f;color:var(--crayon)}
  input:focus,select:focus{outline:none;border-color:var(--signal)}
  select option{background:#14352a}
  .grille-seuils{display:grid;grid-template-columns:1fr 1fr;gap:.5rem 1rem}
  @media(max-width:600px){.grille-seuils{grid-template-columns:1fr}}
  .vert{background:var(--signal);color:var(--foret)}.gris{background:#5b6e63;color:#fff}
  .rouge{background:var(--pleine);color:#fff}.orange{background:var(--moitie);color:#3a2c00}
  .msg{background:#1f4a33;border:1px solid #3a7a52;border-radius:8px;padding:.6rem;color:var(--signal);margin:.5rem 0}
  .inactive{opacity:.4}
  .ligne-form{display:flex;gap:.5rem;flex-wrap:wrap;align-items:end}
  .hint{font-size:.85rem;color:var(--brume)}
</style></head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="marque">ecoVisio</div>
    <a href="{{ url_for('accueil') }}">Accueil</a>
    <a href="{{ url_for('dashboard') }}">Retour au tableau de bord</a>
  </div>
  <h1>Règles de classification</h1>
  {% if message %}<div class="msg">{{ message }}</div>{% endif %}

  <!-- SEUILS AJUSTABLES -->
  <div class="bloc">
    <h2>Seuils ajustables</h2>
    <p class="hint">Ces seuils pilotent la classification par défaut et le contrôle de conformité.</p>
    <form method="post" action="{{ url_for('admin_seuils') }}">
      <div class="grille-seuils">
        {% for cle, val in seuils.items() %}
          <div><label>{{ cle }}</label>
            <input type="number" step="any" name="{{ cle }}" value="{{ val }}"></div>
        {% endfor %}
      </div>
      <div style="margin-top:1rem">
        <button class="btn vert" type="submit">Enregistrer les seuils</button>
      </div>
    </form>
    <form method="post" action="{{ url_for('admin_seuils_reset') }}" style="margin-top:.5rem">
      <button class="btn gris" type="submit">Réinitialiser par défaut</button>
    </form>
  </div>

  <!-- RÈGLES LIBRES -->
  <div class="bloc">
    <h2>Règles libres (prioritaires)</h2>
    <p class="hint">
      Format : SI &lt;caractéristique&gt; &lt;opérateur&gt; &lt;valeur&gt; ALORS &lt;niveau&gt;.
      Évaluées par priorité croissante ; la première qui s'applique l'emporte, avant les seuils.
    </p>
    <form method="post" action="{{ url_for('admin_ajouter_regle') }}" class="ligne-form">
      <div><label>SI</label>
        <select name="caracteristique">
          {% for c in caracteristiques %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
        </select></div>
      <div><label>opérateur</label>
        <select name="operateur">
          {% for o in operateurs %}<option value="{{ o }}">{{ o }}</option>{% endfor %}
        </select></div>
      <div><label>valeur</label><input type="number" step="any" name="valeur" required></div>
      <div><label>ALORS</label>
        <select name="label">
          <option value="vide">vide</option>
          <option value="pleine">pleine</option>
        </select></div>
      <div><label>priorité</label><input type="number" name="priorite" value="100" style="width:80px"></div>
      <div><button class="btn vert" type="submit">Ajouter</button></div>
    </form>

    <table>
      <tr><th>Prio</th><th>Règle</th><th>État</th><th>Actions</th></tr>
      {% for r in regles %}
      <tr class="{{ '' if r['active'] else 'inactive' }}">
        <td>{{ r['priorite'] }}</td>
        <td>SI <b>{{ r['caracteristique'] }}</b> {{ r['operateur'] }} {{ r['valeur'] }}
            ALORS <b>{{ r['label'] }}</b></td>
        <td>{{ 'active' if r['active'] else 'désactivée' }}</td>
        <td>
          <form method="post" action="{{ url_for('admin_basculer_regle', regle_id=r['id']) }}" style="display:inline">
            <button class="btn orange" type="submit">{{ 'Désactiver' if r['active'] else 'Activer' }}</button>
          </form>
          <form method="post" action="{{ url_for('admin_supprimer_regle', regle_id=r['id']) }}" style="display:inline">
            <button class="btn rouge" type="submit">Supprimer</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="4" class="vide-msg" style="color:#8aa394">Aucune règle libre. La classification utilise les seuils ci-dessus.</td></tr>
      {% endfor %}
    </table>
  </div>
</div>
</body></html>
"""


@app.route("/admin/regles")
@login_requis
def admin_regles():
    message = request.args.get("msg")
    
    return render_template_string(
        PAGE_ADMIN_REGLES,
        seuils=get_seuils(),
        regles=lister_regles(),        
        caracteristiques=CARACTERISTIQUES_REGLES,
        operateurs=OPERATEURS_REGLES,
        message=message,
    )


@app.route("/admin/seuils", methods=["POST"])
@login_requis
def admin_seuils():
    nouveaux = {}
    for cle in get_seuils().keys():
        val = request.form.get(cle)
        if val is not None and val != "":
            try:
                nouveaux[cle] = float(val)
            except ValueError:
                pass
    maj_seuils(nouveaux)
    return redirect(url_for("admin_regles", msg="Seuils mis à jour."))


@app.route("/admin/seuils/reset", methods=["POST"])
@login_requis
def admin_seuils_reset():
    reinitialiser_seuils()
    return redirect(url_for("admin_regles", msg="Seuils réinitialisés aux valeurs par défaut."))


@app.route("/admin/regles/ajouter", methods=["POST"])
@login_requis
def admin_ajouter_regle():
    carac = request.form.get("caracteristique")
    op = request.form.get("operateur")
    label = request.form.get("label")
    try:
        valeur = float(request.form.get("valeur"))
        priorite = int(request.form.get("priorite", 100))
    except (ValueError, TypeError):
        return redirect(url_for("admin_regles", msg="Valeur ou priorité invalide."))
    if carac in CARACTERISTIQUES_REGLES and op in OPERATEURS_REGLES and label in LABELS_VALIDES:
        ajouter_regle(carac, op, valeur, label, priorite)
        return redirect(url_for("admin_regles", msg="Règle ajoutée."))
    return redirect(url_for("admin_regles", msg="Règle invalide."))


@app.route("/admin/regles/<int:regle_id>/basculer", methods=["POST"])
@login_requis
def admin_basculer_regle(regle_id):
    basculer_regle(regle_id)
    return redirect(url_for("admin_regles"))


@app.route("/admin/regles/<int:regle_id>/supprimer", methods=["POST"])
@login_requis
def admin_supprimer_regle(regle_id):
    supprimer_regle(regle_id)
    return redirect(url_for("admin_regles", msg="Règle supprimée."))


# ====================== ÉTIQUETAGE DU JEU DE TEST (vérité terrain) ======================
DOSSIER_JEU_TEST = "jeu_de_test"

PAGE_ETIQUETER = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Jeu de test — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_AGENT__
  .wrap{max-width:720px}
  .topnav{display:flex;justify-content:space-between;align-items:center;padding:1.4rem 0;border-bottom:1px solid #ffffff14}
  .topnav .marque{font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--signal)}
  .topnav a{color:var(--brume);text-decoration:none;font-size:.9rem}
  .progress{background:#ffffff14;border-radius:8px;overflow:hidden;height:10px;margin:.8rem 0}
  .progress>div{background:var(--signal);height:100%}
  .compteur{color:var(--brume);font-size:.9rem}
  img.photo{width:100%;max-height:420px;object-fit:contain;background:#000;border-radius:14px;margin:1rem 0}
  .niveaux{display:flex;gap:.6rem}
  .niveaux button{flex:1;padding:1rem;border:none;border-radius:12px;font-weight:700;color:#fff;cursor:pointer;font-size:1rem}
  .lv{background:var(--vide)}.lm{background:var(--moitie);color:#3a2c00}.lp{background:var(--pleine)}
  .fini{text-align:center;padding:2rem}
  .fini .big{font-size:2rem;color:var(--signal);font-weight:800}
  code{background:#ffffff14;padding:.1rem .4rem;border-radius:4px}
  .kbd{display:inline-block;background:#ffffff22;border:1px solid #ffffff33;border-radius:5px;
    padding:.05rem .4rem;font-size:.8rem;font-weight:700;margin-left:.3rem}
  .aide-clavier{color:var(--brume);font-size:.82rem;text-align:center;margin-top:.8rem}
  .meta-survol{color:var(--brume);font-size:.82rem;text-align:center;min-height:1.1rem;
    background:#ffffff0a;border-radius:8px;padding:.4rem;margin-bottom:.6rem}
</style></head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="marque">ecoVisio</div>
    <div><a href="{{ url_for('accueil') }}">Accueil</a> &nbsp;
         <a href="{{ url_for('evaluation_page') }}">Voir les résultats</a> &nbsp;
         <a href="{{ url_for('dashboard') }}">Tableau de bord</a></div>
  </div>

  <h1>Étiquetage du jeu de test</h1>
  <p class="sous">Indiquez le vrai niveau de chaque photo. Ces étiquettes servent de vérité
    terrain pour mesurer la performance de la classification.</p>

  {% if item %}
    <div class="compteur">{{ stats['etiquetes'] }} / {{ stats['total'] }} photos étiquetées</div>
    <div class="progress"><div style="width: {{ pourcentage }}%"></div></div>
    <img class="photo" id="photo-courante"
         src="{{ url_for('image_jeu_test', item_id=item['id']) }}" alt="photo test"
         title="{{ item['chemin_image'] }}">
    <div class="meta-survol" id="meta-survol">Survolez l'image pour voir ses métadonnées</div>
    <form method="post" action="{{ url_for('etiqueter_post', item_id=item['id']) }}" id="form-etiquette">
      <div class="niveaux">
        <button class="lv" name="label" value="vide">Vide <span class="kbd">V</span></button>
        <button class="lp" name="label" value="pleine">Pleine <span class="kbd">P</span></button>
      </div>
    </form>
    <p class="aide-clavier">Raccourcis clavier : <span class="kbd">V</span> pour Vide,
      <span class="kbd">P</span> pour Pleine</p>
  {% else %}
    <div class="carte fini">
      {% if stats['total'] == 0 %}
        <div class="big">Aucune photo</div>
        <p class="sous">Placez vos photos dans le dossier <code>{{ dossier }}/</code> à la racine
          du projet, puis cliquez ci-dessous pour les importer.</p>
      {% else %}
        <div class="big">Étiquetage terminé</div>
        <p class="sous">Les {{ stats['total'] }} photos sont étiquetées.</p>
        <a class="btn btn-p" href="{{ url_for('evaluation_page') }}">Lancer l'évaluation</a>
      {% endif %}
    </div>
  {% endif %}

  <form method="post" action="{{ url_for('importer_jeu_test') }}" style="margin-top:1.5rem">
    <button class="btn btn-s" type="submit">Importer les photos du dossier {{ dossier }}/</button>
  </form>
  <form method="post" action="{{ url_for('vider_jeu_test_route') }}" style="margin-top:.6rem"
        onsubmit="return confirm('Vider tout le jeu de test ? Les photos enregistrées et leurs étiquettes seront supprimées de la base (le dossier n\'est pas touché). Vous pourrez réimporter ensuite.');">
    <button class="btn" type="submit"
            style="background:#7a2e26;color:#fff;border:1px solid #b04a3e">Vider le jeu de test</button>
  </form>
  <p class="vide-msg" style="font-size:.82rem;margin-top:.4rem">
    Utilisez « Vider » si vous avez changé le contenu du dossier {{ dossier }}/ :
    videz, puis réimportez pour resynchroniser.</p>
  {% if message %}<p style="color:var(--signal)">{{ message }}</p>{% endif %}
</div>
<script>
  // Raccourcis clavier : V = vide, P = pleine
  document.addEventListener('keydown', function(e){
    const form = document.getElementById('form-etiquette');
    if(!form) return;
    const k = e.key.toLowerCase();
    if(k === 'v' || k === 'p'){
      const val = (k === 'v') ? 'vide' : 'pleine';
      const btn = form.querySelector('button[value="'+val+'"]');
      if(btn) btn.click();
    }
  });
  // Métadonnées au survol de l'image (dimensions réelles + nom de fichier)
  const img = document.getElementById('photo-courante');
  const meta = document.getElementById('meta-survol');
  if(img && meta){
    function maj(){
      const nom = (img.title || '').split(/[\\\\/]/).pop();
      meta.textContent = 'Image : ' + nom + '  —  ' + img.naturalWidth + '×' + img.naturalHeight + ' px';
    }
    img.addEventListener('mouseenter', maj);
    img.addEventListener('mouseleave', function(){
      meta.textContent = 'Survolez l\\'image pour voir ses métadonnées';
    });
    if(img.complete) maj();
  }
</script>
</body></html>
"""


@app.route("/evaluation/etiqueter")
@login_requis
def etiqueter():
    item = prochain_jeu_test_non_etiquete()
    stats = compter_jeu_test()
    pourcentage = round(100 * stats["etiquetes"] / stats["total"]) if stats["total"] else 0
    return render_template_string(
        PAGE_ETIQUETER, item=item, stats=stats, pourcentage=pourcentage,
        dossier=DOSSIER_JEU_TEST, message=request.args.get("msg"),
    )


@app.route("/evaluation/etiqueter/<int:item_id>", methods=["POST"])
@login_requis
def etiqueter_post(item_id):
    label = request.form.get("label")
    if label in LABELS_VALIDES:
        etiqueter_jeu_test(item_id, label)
        # On extrait les caractéristiques maintenant (une seule fois) et on les met
        # en cache, pour que la page Résultats n'ait plus à réanalyser les photos.
        import json as _json
        from vision import extraire_caracteristiques
        for it in lister_jeu_test():
            if it["id"] == item_id:
                if os.path.exists(it["chemin_image"]):
                    try:
                        feats = extraire_caracteristiques(it["chemin_image"])
                        cacher_analyse_jeu_test(item_id, _json.dumps(feats, ensure_ascii=False), None)
                    except Exception:
                        pass
                break
    return redirect(url_for("etiqueter"))


@app.route("/evaluation/importer", methods=["POST"])
@login_requis
def importer_jeu_test():
    os.makedirs(DOSSIER_JEU_TEST, exist_ok=True)
    n = importer_images_jeu_test(DOSSIER_JEU_TEST)
    return redirect(url_for("etiqueter", msg=f"{n} nouvelle(s) photo(s) importée(s)."))


@app.route("/evaluation/vider", methods=["POST"])
@login_requis
def vider_jeu_test_route():
    """Vide entièrement le jeu de test (photos enregistrées + étiquettes + cache).
    Utile quand on a changé le contenu du dossier jeu_de_test et qu'on veut
    resynchroniser la base avec le dossier actuel."""
    vider_jeu_test()
    return redirect(url_for("etiqueter", msg="Jeu de test vidé. Cliquez sur « Importer » pour recharger le dossier."))


@app.route("/evaluation/image/<int:item_id>")
@login_requis
def image_jeu_test(item_id):
    for it in lister_jeu_test():
        if it["id"] == item_id:
            dossier = os.path.dirname(it["chemin_image"]) or "."
            return send_from_directory(dossier, os.path.basename(it["chemin_image"]))
    abort(404)


# ====================== PAGE RÉSULTATS / MÉTRIQUES ======================
PAGE_EVALUATION = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Résultats — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_AGENT__
  .topnav{display:flex;justify-content:space-between;align-items:center;padding:1.4rem 0;border-bottom:1px solid #ffffff14}
  .topnav .marque{font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--signal)}
  .topnav a{color:var(--brume);text-decoration:none;font-size:.9rem;margin-left:1.2rem}
  .topnav a:hover{color:var(--signal)}
  .grosse{display:flex;gap:1rem;flex-wrap:wrap;margin:1.5rem 0}
  .kpi{flex:1;min-width:150px;background:#ffffff0a;border:1px solid #ffffff1a;border-radius:14px;padding:1.2rem;text-align:center}
  .kpi .n{font-size:2.2rem;font-weight:800;color:var(--signal)}
  .kpi .l{font-size:.8rem;color:var(--brume);margin-top:.2rem}
  .secttitle{display:flex;align-items:center;gap:.5rem;margin-top:1.8rem}
  .secttitle svg{width:18px;height:18px;stroke:var(--signal)}
  .mc{border-collapse:collapse;margin-top:.6rem}
  .mc td,.mc th{border:1px solid #ffffff1a;padding:.7rem 1rem;text-align:center;font-size:.92rem}
  .mc th{color:var(--brume);font-size:.78rem;text-transform:uppercase}
  .mc .diag{background:#2f5d3f55;color:var(--signal);font-weight:800}
  .mc .axe{background:#ffffff0a;color:var(--brume);font-weight:600}
  .vide-msg{color:#8aa394}
  .interpret{background:#ffffff0a;border-left:3px solid var(--signal);border-radius:8px;padding:.8rem 1rem;margin-top:1rem;color:var(--brume);font-size:.92rem;line-height:1.5}
</style></head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="marque">ecoVisio</div>
    <div>
      <a href="{{ url_for('accueil') }}">Accueil</a>
      <a href="{{ url_for('etiqueter') }}">Étiqueter</a>
      <a href="{{ url_for('dashboard') }}">Tableau de bord</a>
    </div>
  </div>

  <h1>Performance de la classification</h1>

  {% if m is none %}
    <p class="vide-msg">Aucune image conforme étiquetée pour le moment.
      <a href="{{ url_for('etiqueter') }}">Étiquetez d'abord votre jeu de test.</a></p>
  {% else %}
    <p class="sous">Évaluation sur {{ m['total'] }} images, en comparant le verdict des règles
      à la vérité terrain que vous avez étiquetée.</p>

    <div class="grosse">
      <div class="kpi"><div class="n">{{ (m['accuracy']*100)|round(1) }}%</div><div class="l">Accuracy globale</div></div>
      <div class="kpi"><div class="n">{{ (m['macro_precision']*100)|round(1) }}%</div><div class="l">Precision (macro)</div></div>
      <div class="kpi"><div class="n">{{ (m['macro_recall']*100)|round(1) }}%</div><div class="l">Recall (macro)</div></div>
      <div class="kpi"><div class="n">{{ (m['macro_f1']*100)|round(1) }}%</div><div class="l">F1-score (macro)</div></div>
    </div>

    <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M3 3v18h18"/><rect x="7" y="10" width="3" height="8"/><rect x="13" y="6" width="3" height="12"/></svg> Détail par classe</h2>
    <table>
      <tr><th>Classe</th><th>Precision</th><th>Recall</th><th>F1-score</th><th>Support</th></tr>
      {% for c in m['classes'] %}
      <tr>
        <td><span class="badge b_{{ c }}">{{ aff[c] }}</span></td>
        <td>{{ (m['par_classe'][c]['precision']*100)|round(1) }}%</td>
        <td>{{ (m['par_classe'][c]['recall']*100)|round(1) }}%</td>
        <td>{{ (m['par_classe'][c]['f1']*100)|round(1) }}%</td>
        <td>{{ m['par_classe'][c]['support'] }}</td>
      </tr>
      {% endfor %}
    </table>

    <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg> Matrice de confusion</h2>
    <p class="vide-msg" style="font-size:.85rem">Lignes : vraie classe — Colonnes : classe prédite. La diagonale = bonnes prédictions.</p>
    <table class="mc">
      <tr><th></th>{% for p in m['classes'] %}<th>{{ aff[p] }}</th>{% endfor %}</tr>
      {% for v in m['classes'] %}
      <tr>
        <td class="axe">{{ aff[v] }}</td>
        {% for p in m['classes'] %}
          <td class="{{ 'diag' if v == p else '' }}">{{ m['matrice'][v][p] }}</td>
        {% endfor %}
      </tr>
      {% endfor %}
    </table>

    <div class="interpret">
      <b>Comment lire ces résultats :</b> l'accuracy indique la part de photos correctement classées.
      La precision d'une classe mesure la fiabilité quand le système prédit cette classe ; le recall,
      sa capacité à retrouver toutes les photos de cette classe. Les cases hors diagonale de la matrice
      montrent les confusions à corriger en ajustant les seuils dans la configuration des règles.
    </div>

    <h2 class="secttitle"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg> Visualisations (générées côté serveur, matplotlib)</h2>
    <p class="vide-msg" style="font-size:.85rem">Graphes PNG produits avec matplotlib, en complément des graphes interactifs.</p>
    <div style="display:flex;flex-wrap:wrap;gap:1rem">
      <img src="{{ url_for('graphe_matrice') }}" alt="matrice de confusion" style="max-width:440px;width:100%;background:#fff;border-radius:12px;padding:.5rem">
      <img src="{{ url_for('graphe_nuage') }}" alt="séparation des classes" style="max-width:440px;width:100%;background:#fff;border-radius:12px;padding:.5rem">
      <img src="{{ url_for('graphe_contraste') }}" alt="distribution du contraste" style="max-width:440px;width:100%;background:#fff;border-radius:12px;padding:.5rem">
    </div>
  {% endif %}
</div>
</body></html>
"""


@app.route("/evaluation")
@login_requis
def evaluation_page():
    metriques, details = lancer_evaluation()
    return render_template_string(
        PAGE_EVALUATION, m=metriques, details=details, aff=LABEL_AFFICHAGE,
    )


# ====================== GESTION DES POUBELLES (agents) ======================
DOSSIER_QR = "qrcodes"

PAGE_POUBELLES = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Poubelles — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_AGENT__
  .topnav{display:flex;justify-content:space-between;align-items:center;padding:1.4rem 0;border-bottom:1px solid #ffffff14}
  .topnav .marque{font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--signal)}
  .topnav a{color:var(--brume);text-decoration:none;font-size:.9rem;margin-left:1.2rem}
  .topnav a:hover{color:var(--signal)}
  .bloc{background:#ffffff0a;border:1px solid #ffffff1a;border-radius:14px;padding:1.3rem;margin:1.2rem 0}
  label{display:block;font-size:.82rem;color:var(--brume);margin-top:.7rem}
  input{width:100%;padding:.6rem;border:1px solid #ffffff2a;border-radius:8px;font-size:.95rem;
    background:#ffffff0f;color:var(--crayon)}
  input:focus{outline:none;border-color:var(--signal)}
  .duo{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
  .aide{background:#ffffff0a;border-left:3px solid var(--signal);border-radius:8px;padding:.7rem 1rem;
    margin:.8rem 0;color:var(--brume);font-size:.88rem;line-height:1.5}
  .msg{background:#1f4a33;border:1px solid #3a7a52;border-radius:8px;padding:.7rem;color:var(--signal);margin:.5rem 0}
  .msg.err{background:#4a221f;border-color:#7a3a3a;color:#ff9a8a}
  .qr-result{text-align:center;margin-top:1rem}
  .qr-result img{max-width:220px;background:#fff;padding:.5rem;border-radius:10px}
  .b_active{background:var(--vide);color:#fff}
  .b_inactive{background:#5b6e63;color:#fff}
  tr.inactive{opacity:.5}
  .rouge{background:var(--pleine);color:#fff}.vert{background:var(--signal);color:var(--foret)}
  .btn.rouge,.btn.vert{padding:.4rem .9rem;font-size:.85rem}
</style></head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="marque">ecoVisio</div>
    <div>
      <a href="{{ url_for('accueil') }}">Accueil</a>
      <a href="{{ url_for('dashboard') }}">Tableau de bord</a>
      <a href="{{ url_for('admin_regles') }}">Règles</a>
    </div>
  </div>

  <h1>Gestion des poubelles</h1>

  {% if message %}<div class="msg {{ 'err' if erreur else '' }}">{{ message }}</div>{% endif %}

  <!-- Ajout -->
  <div class="bloc">
    <h2>Ajouter une poubelle</h2>
    <div class="aide">
      <b>Trouver les coordonnées :</b> ouvrez Google Maps, faites un clic droit sur l'emplacement
      exact de la poubelle, puis cliquez sur les chiffres affichés en haut du menu
      (ex. <code>48.8674, 2.3636</code>) pour les copier. Le premier nombre est la latitude,
      le second la longitude.
    </div>
    <form method="post" action="{{ url_for('ajouter_poubelle_post') }}">
      <label>Identifiant (laisser vide pour générer automatiquement)</label>
      <input type="text" name="id" placeholder="BIN-0006">
      <label>Nom du lieu</label>
      <input type="text" name="nom_lieu" placeholder="Place du marché" required>
      <div class="duo">
        <div><label>Latitude</label>
          <input type="number" step="any" name="latitude" placeholder="48.8674" required></div>
        <div><label>Longitude</label>
          <input type="number" step="any" name="longitude" placeholder="2.3636" required></div>
      </div>
      <button class="btn btn-p" type="submit" style="margin-top:1rem">Ajouter et générer le QR code</button>
    </form>

    {% if nouveau_qr %}
      <div class="qr-result">
        <p style="color:var(--signal);font-weight:600">Poubelle {{ nouveau_qr }} ajoutée</p>
        <img src="{{ url_for('qr_poubelle', poubelle_id=nouveau_qr) }}?t={{ ts }}" alt="QR code">
        <p><a class="btn btn-s" href="{{ url_for('qr_poubelle', poubelle_id=nouveau_qr) }}" download>Télécharger le QR code</a></p>
      </div>
    {% endif %}
  </div>

  <!-- Liste -->
  <div class="bloc">
    <h2>Poubelles enregistrées ({{ poubelles|length }})</h2>
    <table>
      <tr><th>ID</th><th>Lieu</th><th>Coordonnées</th><th>État</th><th>QR</th><th>Action</th></tr>
      {% for p in poubelles %}
      <tr class="{{ '' if p['active'] else 'inactive' }}">
        <td>{{ p['id'] }}</td>
        <td>{{ p['nom_lieu'] }}</td>
        <td>{{ p['latitude'] }}, {{ p['longitude'] }}</td>
        <td>
          {% if p['active'] %}<span class="badge b_active">Active</span>
          {% else %}<span class="badge b_inactive">Désactivée</span>{% endif %}
        </td>
        <td><a href="{{ url_for('qr_poubelle', poubelle_id=p['id']) }}" download>Télécharger</a></td>
        <td>
          {% if p['active'] %}
          <form method="post" action="{{ url_for('vider_poubelle_post', poubelle_id=p['id']) }}"
                style="display:inline" onsubmit="return confirm('Confirmer que la poubelle {{ p['id'] }} a été vidée ? Elle repassera au statut « vide ».');">
            <button class="btn vert" type="submit">Marquer comme vidée</button>
          </form>
          <form method="post" action="{{ url_for('desactiver_poubelle_post', poubelle_id=p['id']) }}"
                style="display:inline" onsubmit="return confirm('Désactiver la poubelle {{ p['id'] }} ? Elle sera masquée mais son historique sera conservé.');">
            <button class="btn rouge" type="submit">Désactiver</button>
          </form>
          {% else %}
          <form method="post" action="{{ url_for('reactiver_poubelle_post', poubelle_id=p['id']) }}" style="display:inline">
            <button class="btn vert" type="submit">Réactiver</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
  </div>
</div>
</body></html>
"""


def _generer_id_poubelle():
    """Génère le prochain identifiant BIN-XXXX disponible."""
    existants = [p["id"] for p in lister_poubelles(actives_seulement=False)]
    n = 1
    while f"BIN-{n:04d}" in existants:
        n += 1
    return f"BIN-{n:04d}"


@app.route("/poubelles")
@login_requis
def poubelles():
    return render_template_string(
        PAGE_POUBELLES, poubelles=lister_poubelles(actives_seulement=False),
        message=request.args.get("msg"), erreur=request.args.get("err"),
        nouveau_qr=request.args.get("qr"), ts=request.args.get("ts", ""),
    )


@app.route("/poubelles/<poubelle_id>/desactiver", methods=["POST"])
@login_requis
def desactiver_poubelle_post(poubelle_id):
    if get_poubelle(poubelle_id) is None:
        abort(404)
    desactiver_poubelle(poubelle_id)
    return redirect(url_for("poubelles", msg=f"Poubelle {poubelle_id} désactivée (historique conservé)."))


@app.route("/poubelles/<poubelle_id>/reactiver", methods=["POST"])
@login_requis
def reactiver_poubelle_post(poubelle_id):
    if get_poubelle(poubelle_id) is None:
        abort(404)
    reactiver_poubelle(poubelle_id)
    return redirect(url_for("poubelles", msg=f"Poubelle {poubelle_id} réactivée."))


@app.route("/poubelles/<poubelle_id>/vider", methods=["POST"])
@login_requis
def vider_poubelle_post(poubelle_id):
    """Marque une poubelle comme vidée par un agent (signalement de collecte)."""
    if get_poubelle(poubelle_id) is None:
        abort(404)
    marquer_poubelle_videe(poubelle_id)
    return redirect(url_for("poubelles", msg=f"Poubelle {poubelle_id} marquée comme vidée."))


@app.route("/poubelles/ajouter", methods=["POST"])
@login_requis
def ajouter_poubelle_post():
    id_saisi = (request.form.get("id") or "").strip()
    nom_lieu = (request.form.get("nom_lieu") or "").strip()
    lat = request.form.get("latitude")
    lon = request.form.get("longitude")

    # Validations
    if not nom_lieu:
        return redirect(url_for("poubelles", msg="Le nom du lieu est obligatoire.", err=1))
    try:
        lat = float(lat); lon = float(lon)
    except (ValueError, TypeError):
        return redirect(url_for("poubelles", msg="Coordonnées invalides.", err=1))
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return redirect(url_for("poubelles", msg="Coordonnées hors limites (lat -90..90, lon -180..180).", err=1))

    id_poubelle = id_saisi or _generer_id_poubelle()
    if get_poubelle(id_poubelle) is not None:
        return redirect(url_for("poubelles", msg=f"L'identifiant {id_poubelle} existe déjà.", err=1))

    # Enregistrement
    ajouter_poubelle(id_poubelle, nom_lieu, lat, lon)

    # Génération du QR code (réutilise la logique de generer_qrcodes.py)
    os.makedirs(DOSSIER_QR, exist_ok=True)
    try:
        import generer_qrcodes
        etiquette = generer_qrcodes.generer_etiquette(id_poubelle, nom_lieu)
        etiquette.save(os.path.join(DOSSIER_QR, f"{id_poubelle}.png"))
    except Exception as e:
        return redirect(url_for("poubelles",
            msg=f"Poubelle ajoutée mais erreur QR : {e}", err=1))

    import time
    return redirect(url_for("poubelles", qr=id_poubelle, ts=str(int(time.time()))))


@app.route("/poubelles/qr/<poubelle_id>")
@login_requis
def qr_poubelle(poubelle_id):
    """Sert (ou régénère) le QR code d'une poubelle."""
    chemin = os.path.join(DOSSIER_QR, f"{poubelle_id}.png")
    if not os.path.exists(chemin):
        # Régénère à la volée si manquant
        poub = get_poubelle(poubelle_id)
        if poub is None:
            abort(404)
        os.makedirs(DOSSIER_QR, exist_ok=True)
        import generer_qrcodes
        generer_qrcodes.generer_etiquette(poubelle_id, poub["nom_lieu"]).save(chemin)
    return send_from_directory(DOSSIER_QR, f"{poubelle_id}.png")


# ====================== GRAPHES MATPLOTLIB (back-end, PNG) ======================
# Le cahier demande deux approches : Chart.js (front) ET matplotlib (back).
# Ces routes servent des images PNG générées côté serveur.
from flask import send_file


@app.route("/graphe/matrice-confusion.png")
@login_requis
def graphe_matrice():
    metriques, _ = lancer_evaluation()
    if metriques is None:
        # image vide si pas de données
        from database import LABELS_VALIDES as _L
        vide = {v: {p: 0 for p in _L} for v in _L}
        buf = graphiques.matrice_confusion_png(vide, list(_L))
    else:
        buf = graphiques.matrice_confusion_png(metriques["matrice"], metriques["classes"])
    return send_file(buf, mimetype="image/png")


@app.route("/graphe/repartition.png")
@login_requis
def graphe_repartition():
    rep = repartition_statuts(n=5)
    buf = graphiques.repartition_statuts_png(rep)
    return send_file(buf, mimetype="image/png")


@app.route("/graphe/tailles.png")
@login_requis
def graphe_tailles():
    # Récupère les tailles de fichiers depuis les features stockées
    import json as _json
    from database import get_connection
    rows = get_connection().execute(
        "SELECT features_json FROM signalements WHERE features_json IS NOT NULL"
    ).fetchall()
    tailles = []
    for r in rows:
        try:
            f = _json.loads(r["features_json"])
            if "taille_fichier" in f:
                tailles.append(f["taille_fichier"])
        except (ValueError, TypeError):
            pass
    buf = graphiques.distribution_tailles_png(tailles)
    return send_file(buf, mimetype="image/png")


@app.route("/graphe/nuage-classes.png")
@login_requis
def graphe_nuage():
    """Nuage de points contours vs texture, par classe (jeu de test étiqueté)."""
    import json as _json
    from database import lister_jeu_test
    points = {"vide": [], "pleine": []}
    for it in lister_jeu_test():
        if it["vrai_label"] in points and it["features_json"]:
            try:
                f = _json.loads(it["features_json"])
                points[it["vrai_label"]].append((f["densite_contours"], f["texture"]))
            except (ValueError, TypeError, KeyError):
                pass
    buf = graphiques.nuage_contours_texture_png(points)
    return send_file(buf, mimetype="image/png")


@app.route("/graphe/contraste.png")
@login_requis
def graphe_contraste():
    """Distribution du contraste par classe (jeu de test étiqueté)."""
    import json as _json
    from database import lister_jeu_test
    contrastes = {"vide": [], "pleine": []}
    for it in lister_jeu_test():
        if it["vrai_label"] in contrastes and it["features_json"]:
            try:
                f = _json.loads(it["features_json"])
                contrastes[it["vrai_label"]].append(f["contraste"])
            except (ValueError, TypeError, KeyError):
                pass
    buf = graphiques.distribution_contraste_png(contrastes)
    return send_file(buf, mimetype="image/png")


# ====================== CALIBRATION AUTOMATIQUE DES SEUILS ======================
PAGE_CALIBRATION = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Calibration — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_AGENT__
  .topnav{display:flex;justify-content:space-between;align-items:center;padding:1.4rem 0;border-bottom:1px solid #ffffff14}
  .topnav .marque{font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--signal)}
  .topnav a{color:var(--brume);text-decoration:none;font-size:.9rem;margin-left:1.2rem}
  .bloc{background:#ffffff0a;border:1px solid #ffffff1a;border-radius:14px;padding:1.3rem;margin:1.2rem 0}
  .mc{border-collapse:collapse;width:100%;margin-top:.5rem}
  .mc td,.mc th{border:1px solid #ffffff1a;padding:.5rem .8rem;text-align:center;font-size:.9rem}
  .mc th{color:var(--brume);font-size:.78rem;text-transform:uppercase}
  .prop{background:#1f4a33;border:1px solid #3a7a52;border-radius:10px;padding:1rem;margin-top:1rem}
  .prop code{color:var(--signal);font-weight:700}
  .avert{background:#4a3a1f;border-left:3px solid var(--moitie);border-radius:8px;padding:.6rem 1rem;margin:.4rem 0;color:#f0d090;font-size:.9rem}
  .msg{background:#1f4a33;border:1px solid #3a7a52;border-radius:8px;padding:.7rem;color:var(--signal);margin:.5rem 0}
  .vide-msg{color:#8aa394}
</style></head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="marque">ecoVisio</div>
    <div>
      <a href="{{ url_for('accueil') }}">Accueil</a>
      <a href="{{ url_for('dashboard') }}">Tableau de bord</a>
      <a href="{{ url_for('etiqueter') }}">Jeu de test</a>
      <a href="{{ url_for('evaluation_page') }}">Résultats</a>
      <a href="{{ url_for('admin_regles') }}">Règles</a>
    </div>
  </div>

  <h1>Calibration automatique des seuils</h1>
  <p class="sous">À partir de vos photos étiquetées, le système mesure les valeurs réelles
    de chaque caractéristique par classe et propose les seuils qui séparent le mieux
    vide / à moitié / pleine.</p>

  {% if message %}<div class="msg">{{ message }}</div>{% endif %}

  {% if r['total'] == 0 %}
    <p class="vide-msg">Aucune photo étiquetée. <a href="{{ url_for('etiqueter') }}">Étiquetez d'abord votre jeu de test.</a></p>
  {% else %}
    <div class="bloc">
      <h2>Mesures par classe ({{ r['total'] }} images : {{ r['comptes']['vide'] }} vides,
        {{ r['comptes']['pleine'] }} pleines)</h2>
      <p class="vide-msg" style="font-size:.85rem">Pour chaque critère : sa valeur moyenne dans
        chaque classe, et un score de <b>séparabilité</b> (plus il est élevé, mieux le critère
        distingue vide de pleine). Un critère qui sépare bien est un bon candidat pour une règle.</p>
      <table class="mc">
        <tr>
          <th>Critère</th>
          <th>Vide (moy)</th>
          <th>Pleine (moy)</th>
          <th>Sépare bien ?</th>
        </tr>
        {% for crit, libelle in r['criteres'].items() %}
        <tr {% if crit == r['meilleur_critere'] %}style="background:#1f4a3355"{% endif %}>
          <td style="text-align:left">{{ libelle }}
            {% if crit == r['meilleur_critere'] %}<span style="color:var(--signal)">★</span>{% endif %}
          </td>
          <td>{{ r['stats']['vide'][crit]['moyenne'] if r['stats']['vide'][crit] else '—' }}</td>
          <td>{{ r['stats']['pleine'][crit]['moyenne'] if r['stats']['pleine'][crit] else '—' }}</td>
          <td>
            {% set sc = r['separabilite'][crit]['score'] %}
            {% if sc >= 0.5 %}<span style="color:var(--signal);font-weight:700">oui</span>
            {% else %}<span style="color:#c98">faible</span>{% endif %}
            <span class="vide-msg">({{ sc }})</span>
          </td>
        </tr>
        {% endfor %}
      </table>
      {% if r['meilleur_critere'] %}
      <p style="margin-top:.6rem;font-size:.9rem">★ Critère le plus discriminant :
        <b>{{ r['criteres'][r['meilleur_critere']] }}</b> — c'est le meilleur candidat
        pour construire une règle de classification.</p>
      {% endif %}

      {% for a in r['avertissements'] %}<div class="avert">{{ a }}</div>{% endfor %}

      <div class="prop">
        <b>Seuils proposés :</b><br>
        {% for k, v in r['seuils_proposes'].items() %}
          {% if v is not none %}<code>{{ k }} = {{ v }}</code><br>{% endif %}
        {% endfor %}
      </div>

      <form method="post" action="{{ url_for('appliquer_calibration') }}" style="margin-top:1rem">
        <button class="btn btn-p" type="submit">Appliquer ces seuils</button>
        <a class="btn btn-s" href="{{ url_for('evaluation_page') }}">Voir les résultats actuels</a>
      </form>
    </div>
  {% endif %}
</div>
</body></html>
"""


@app.route("/calibration")
@login_requis
def calibration_page():
    r = proposer_seuils()
    return render_template_string(
        PAGE_CALIBRATION, r=r, aff=LABEL_AFFICHAGE, message=request.args.get("msg"),
    )


@app.route("/calibration/appliquer", methods=["POST"])
@login_requis
def appliquer_calibration():
    r = proposer_seuils()
    # On n'applique que les seuils effectivement calculés (non None)
    a_appliquer = {k: v for k, v in r["seuils_proposes"].items() if v is not None}
    if a_appliquer:
        from database import maj_seuils
        maj_seuils(a_appliquer)
        return redirect(url_for("calibration_page",
            msg=f"{len(a_appliquer)} seuil(s) appliqué(s). Vérifiez les résultats."))
    return redirect(url_for("calibration_page",
        msg="Impossible de calibrer : données insuffisantes."))


# ====================== PAGE CARACTÉRISTIQUES EXTRAITES ======================
PAGE_CARACTERISTIQUES = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Caractéristiques — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_AGENT__
  .topnav{display:flex;justify-content:space-between;align-items:center;padding:1.4rem 0;border-bottom:1px solid #ffffff14}
  .topnav .marque{font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--signal)}
  .topnav a{color:var(--brume);text-decoration:none;font-size:.9rem;margin-left:1.2rem}
  .topnav a:hover{color:var(--signal)}
  .card-sig{background:#ffffff0a;border:1px solid #ffffff1a;border-radius:14px;padding:1.1rem;margin:1rem 0;
    display:grid;grid-template-columns:120px 1fr;gap:1rem;align-items:start}
  @media(max-width:600px){.card-sig{grid-template-columns:1fr}}
  .card-sig img{width:120px;height:120px;object-fit:cover;border-radius:10px;background:#000}
  .feat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.5rem .9rem}
  .feat{font-size:.85rem}
  .feat .k{color:var(--brume);font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}
  .feat .v{font-weight:700;color:var(--crayon)}
  .entete{display:flex;align-items:center;gap:.6rem;margin-bottom:.6rem;flex-wrap:wrap}
  .swatch{width:20px;height:20px;border-radius:4px;border:1px solid #ffffff33;display:inline-block;vertical-align:middle}
  .histo{display:flex;align-items:flex-end;gap:2px;height:44px;margin-top:.2rem;
    background:#ffffff12;border:1px solid #ffffff1a;border-radius:6px;padding:3px}
  .histo .bar{flex:1;border-radius:1px 1px 0 0;min-height:1px;border:1px solid #00000040}
  .histo-legende{display:flex;justify-content:space-between;margin-top:.25rem;
    font-size:.7rem;color:var(--brume)}
  .histo-legende span:nth-child(2){opacity:.7}
  .vide-msg{color:#8aa394}
  .pagin{display:flex;gap:.5rem;justify-content:center;margin:1.5rem 0}
  .pagin a{padding:.4rem .9rem;background:#ffffff0a;border:1px solid #ffffff1a;border-radius:8px;
    text-decoration:none;color:var(--brume)}
  .pagin a:hover{border-color:var(--signal);color:var(--signal)}
  .pagin .cur{background:var(--signal);color:var(--foret);font-weight:700}
</style></head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="marque">ecoVisio</div>
    <div>
      <a href="{{ url_for('accueil') }}">Accueil</a>
      <a href="{{ url_for('dashboard') }}">Tableau de bord</a>
      <a href="{{ url_for('evaluation_page') }}">Résultats</a>
    </div>
  </div>

  <h1>Caractéristiques extraites des images</h1>
  <p class="sous">Pour chaque signalement, les caractéristiques calculées par traitement d'image
    (dimensions, taille, couleur moyenne, contraste, contours, texture, histogrammes).</p>

  {% if signalements %}
    {% for s in signalements %}
    <div class="card-sig">
      <img src="{{ url_for('fichier_upload', nom=s['nom_image']) }}" alt="photo"
           onerror="this.style.display='none'">
      <div>
        <div class="entete">
          <b>{{ s['poubelle_id'] }}</b>
          <span class="badge b_{{ s['statut'] }}">{{ aff.get(s['statut'], s['statut']) }}</span>
          <span class="vide-msg" style="font-size:.82rem">{{ s['date_upload'] }}</span>
        </div>
        {% set f = s['features'] %}
        {% if f %}
        <div class="feat-grid">
          <div class="feat"><div class="k">Dimensions</div><div class="v">{{ f.get('largeur') }}×{{ f.get('hauteur') }} px</div></div>
          <div class="feat"><div class="k">Taille fichier</div><div class="v">{{ (f.get('taille_fichier',0)/1024)|round(1) }} Ko</div></div>
          <div class="feat"><div class="k">Couleur moyenne</div>
            <div class="v">
              {% if f.get('couleur_moyenne_rgb') %}
              <span class="swatch" style="background:rgb({{ f['couleur_moyenne_rgb'][0] }},{{ f['couleur_moyenne_rgb'][1] }},{{ f['couleur_moyenne_rgb'][2] }})"></span>
              {{ f['couleur_moyenne_rgb'] }}
              {% endif %}
            </div></div>
          <div class="feat"><div class="k">Luminosité</div><div class="v">{{ f.get('luminosite_moyenne') }}</div></div>
          <div class="feat"><div class="k">Contraste</div><div class="v">{{ f.get('contraste') }}</div></div>
          <div class="feat"><div class="k">Densité contours</div><div class="v">{{ f.get('densite_contours') }}</div></div>
          <div class="feat"><div class="k">Texture</div><div class="v">{{ f.get('texture') }}</div></div>
          {% if f.get('densite_contours_bas') is not none %}
          <div class="feat"><div class="k">Contours au sol</div><div class="v">{{ f.get('densite_contours_bas') }}</div></div>
          <div class="feat"><div class="k">Taches claires (sol)</div><div class="v">{{ f.get('taches_claires_bas') }}</div></div>
          <div class="feat"><div class="k">Diversité couleurs</div><div class="v">{{ f.get('diversite_couleurs') }}</div></div>
          {% endif %}
        </div>
        {% if f.get('histogramme_luminance') %}
        <div class="feat" style="margin-top:.6rem">
          <div class="k">Histogramme de luminance</div>
          <div class="histo">
            {% for v in f['histogramme_luminance'] %}
              {# Niveau de gris correspondant à la tranche : de sombre (gauche) à clair (droite) #}
              {% set gris = (loop.index0 * 255 // 7) %}
              <div class="bar"
                   style="height:{{ v }}%;background:rgb({{ gris }},{{ gris }},{{ gris }})"
                   title="Tranche {{ loop.index }}/8 — {{ v }}% des pixels"></div>
            {% endfor %}
          </div>
          <div class="histo-legende">
            <span>← sombre</span>
            <span>luminosité des pixels</span>
            <span>clair →</span>
          </div>
        </div>
        {% endif %}
        {% else %}
        <p class="vide-msg">Caractéristiques non disponibles pour ce signalement.</p>
        {% endif %}
      </div>
    </div>
    {% endfor %}

    <div class="pagin">
      {% if page > 1 %}<a href="{{ url_for('caracteristiques', page=page-1) }}">← Précédent</a>{% endif %}
      <a class="cur">Page {{ page }} / {{ pages_total }}</a>
      {% if page < pages_total %}<a href="{{ url_for('caracteristiques', page=page+1) }}">Suivant →</a>{% endif %}
    </div>
  {% else %}
    <p class="vide-msg">Aucun signalement pour le moment.</p>
  {% endif %}
</div>
</body></html>
"""


@app.route("/caracteristiques")
@login_requis
def caracteristiques():
    import json as _json
    from database import get_connection
    page = max(1, request.args.get("page", 1, type=int))
    par_page = 10

    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM signalements").fetchone()[0]
    rows = conn.execute(
        """SELECT id, poubelle_id, chemin_image, date_upload, statut, features_json
           FROM signalements ORDER BY id DESC LIMIT ? OFFSET ?""",
        (par_page, (page - 1) * par_page),
    ).fetchall()
    conn.close()

    signalements = []
    for r in rows:
        try:
            feats = _json.loads(r["features_json"]) if r["features_json"] else None
        except (ValueError, TypeError):
            feats = None
        signalements.append({
            "poubelle_id": r["poubelle_id"],
            "nom_image": os.path.basename(r["chemin_image"]),
            "date_upload": r["date_upload"],
            "statut": r["statut"],
            "features": feats,
        })

    pages_total = max(1, (total + par_page - 1) // par_page)
    return render_template_string(
        PAGE_CARACTERISTIQUES, signalements=signalements, page=page,
        pages_total=pages_total, aff=LABEL_AFFICHAGE,
    )


# ====================== VÉRIFICATION DE LA CONFORMITÉ DES DONNÉES ======================
PAGE_VERIFICATION = """
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Vérification des données — ecoVisio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>__THEME_AGENT__
  .topnav{display:flex;justify-content:space-between;align-items:center;padding:1.4rem 0;border-bottom:1px solid #ffffff14}
  .topnav .marque{font-weight:800;letter-spacing:.2em;text-transform:uppercase;color:var(--signal)}
  .topnav a{color:var(--brume);text-decoration:none;font-size:.9rem;margin-left:1.2rem}
  .topnav a:hover{color:var(--signal)}
  .bilan{display:flex;align-items:center;gap:1rem;background:#ffffff0a;border:1px solid #ffffff1a;
    border-radius:14px;padding:1.3rem;margin:1.5rem 0}
  .bilan .pastille{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none}
  .bilan.ok .pastille{background:#1f4a33}.bilan.ko .pastille{background:#4a221f}
  .bilan .pastille svg{width:26px;height:26px}
  .bilan.ok .pastille svg{stroke:var(--signal)}.bilan.ko .pastille svg{stroke:#ff9a8a}
  .bilan .txt b{font-size:1.1rem}
  .cat{background:#ffffff0a;border:1px solid #ffffff1a;border-radius:12px;padding:1rem;margin:.8rem 0}
  .cat .entete{display:flex;align-items:center;justify-content:space-between;cursor:default}
  .cat h3{margin:0;font-size:1rem}
  .badge-ok{background:var(--vide);color:#fff;padding:.15rem .6rem;border-radius:999px;font-size:.78rem;font-weight:700}
  .badge-ko{background:var(--pleine);color:#fff;padding:.15rem .6rem;border-radius:999px;font-size:.78rem;font-weight:700}
  .cat ul{margin:.6rem 0 0;padding-left:1.2rem;color:#f0b8ae}
  .cat li{margin:.2rem 0;font-size:.9rem}
</style></head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="marque">ecoVisio</div>
    <div>
      <a href="{{ url_for('accueil') }}">Accueil</a>
      <a href="{{ url_for('dashboard') }}">Tableau de bord</a>
    </div>
  </div>

  <h1>Vérification de la conformité des données</h1>
  <p class="sous">Audit d'intégrité de la base : cohérence des liens, présence des fichiers,
    validité des valeurs et complétude des caractéristiques stockées.</p>

  {% if total == 0 %}
    <div class="bilan ok">
      <div class="pastille"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg></div>
      <div class="txt"><b>Aucune anomalie détectée</b><br>
        <span class="sous">Toutes les données stockées sont conformes.</span></div>
    </div>
  {% else %}
    <div class="bilan ko">
      <div class="pastille"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9l-8 13.8A2 2 0 004 21h16a2 2 0 001.7-3.3l-8-13.8a2 2 0 00-3.4 0z"/></svg></div>
      <div class="txt"><b>{{ total }} anomalie(s) détectée(s)</b><br>
        <span class="sous">Détail par catégorie ci-dessous.</span></div>
    </div>
  {% endif %}

  {% for categorie, problemes in rapport.items() %}
  <div class="cat">
    <div class="entete">
      <h3>{{ categorie }}</h3>
      {% if problemes %}<span class="badge-ko">{{ problemes|length }}</span>
      {% else %}<span class="badge-ok">OK</span>{% endif %}
    </div>
    {% if problemes %}
    <ul>
      {% for p in problemes %}<li>{{ p }}</li>{% endfor %}
    </ul>
    {% endif %}
  </div>
  {% endfor %}
</div>
</body></html>
"""


@app.route("/verification")
@login_requis
def verification_page():
    res = lancer_verification_complete()
    return render_template_string(
        PAGE_VERIFICATION, rapport=res["rapport"], total=res["total_problemes"],
    )


# ====================== INJECTION DES THÈMES VISO DANS LES PAGES ======================
# On remplace les marqueurs __THEME_AGENT__ / __THEME_CITOYEN__ par le CSS partagé,
# pour toutes les pages, en une fois au démarrage. Cela évite de répéter le CSS
# dans chaque template (modularité / maintenabilité).
def _injecter_themes():
    g = globals()
    for nom, valeur in list(g.items()):
        if nom.startswith("PAGE_") and isinstance(valeur, str):
            if "__THEME_AGENT__" in valeur:
                g[nom] = valeur.replace("__THEME_AGENT__", THEME_AGENT)
            elif "__THEME_CITOYEN__" in valeur:
                g[nom] = valeur.replace("__THEME_CITOYEN__", THEME_CITOYEN)


_injecter_themes()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


