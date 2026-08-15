"""Génère le site public du Radar parlementaire.

    uv run python site/generer.py            # écrit sortie/
    uv run python site/generer.py --servir   # et le sert sur http://127.0.0.1:8000

Architecture — et elle ne se négocie pas :

    radar/            tous les chiffres ; ne connaît pas le site
    radar/vues.py     la frontière : des données prêtes à afficher
    redaction.py      les phrases, choisies selon ce que disent les chiffres
    gabarits/*.html   la structure, écrite une fois
    statique/*        le système visuel et le comportement, écrits une fois
    ici               l'orchestration : quel gabarit, quel jeton, quel fichier

Ce fichier n'a le droit de contenir ni phrase rédigée ni calcul : les premières
sont dans `redaction.py`, les seconds dans `radar/`. Ce qui reste ici est la
mécanique.

Le générateur ne dépend d'aucun serveur : il construit les données en mémoire
une seule fois, puis écrit les pages. C'est ce qui rend la mise à jour tenable
— `radar update` puis cette commande, et le site est à jour.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import shutil
import time
import tomllib
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from radar import controles
from radar.controles import CONTROLES
from radar.ideal import GROUPE_ANCRAGE
from radar.vues import BOOTSTRAP, MIN_COMMUNS, MIN_COMMUNS_TEXTE, Donnees
from redaction import (
    NBSP,
    STATUTS,
    accords,
    combien,
    dec,
    echapper,
    grand_chiffre,
    jour,
    mention_delegation,
    num,
    ordinal,
    pct,
    phrase_amendements,
    phrase_carte,
    phrase_delegation,
    phrase_delegation_assemblee,
    phrase_dissidence,
    phrase_ecart,
    phrase_ecart_groupe,
    phrase_effectif_scrutin,
    phrase_hors_mandat,
    phrase_incertitude,
    phrase_interruptions,
    phrase_delegation_scrutin,
    phrase_groupes_scrutin,
    phrase_journal,
    phrase_loi,
    phrase_mandat,
    phrase_participation,
    phrase_portee,
    phrase_position,
    phrase_releve,
    phrase_sort,
    phrase_these,
    pluriel,
    provenance_amendements,
    provenance_delegation,
    reserve_denominateur,
    situer,
)

def slug(*morceaux: str | int | None) -> str:
    """Fragment d'URL lisible : minuscules, sans accent, mots liés par un tiret.

    Le nom seul suffirait aujourd'hui à distinguer les 648 fiches, mais il ne
    le garantit pas : deux homonymes élus la même législature — cas déjà vu à
    l'Assemblée — se disputeraient la même adresse, et la seconde fiche
    écraserait silencieusement la première. Le département et la
    circonscription lèvent l'ambiguïté et sont stables pour la durée du mandat,
    là où le groupe politique, lui, change en cours de route.
    """
    texte = "-".join(str(m) for m in morceaux if m not in (None, ""))
    texte = unicodedata.normalize("NFKD", texte.lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", texte)).strip("-")


# L'indicateur ordinal masculin. « n° » avec le signe degré est un abus courant
# et rendu de travers en chasse fixe ; un `<sup>` se détache de son « n » dans
# une ligne à fort interlettrage. Le caractère dédié règle les deux.
ORDINAL_MASC = "º"


def numero_scrutin(n: int | str) -> str:
    """Le numéro d'un scrutin, sans séparateur de milliers.

    `num()` compose les nombres à la française — « 8 434 » — et c'est juste
    pour une quantité. Un numéro de scrutin n'en est pas une : c'est un
    identifiant, l'Assemblée l'écrit « scrutin n° 8434 », et c'est sous cette
    forme qu'on le recopie depuis un compte rendu. Il est de surcroît l'adresse
    de la page — `scrutin-8434.html` — et les afficher différemment invitait à
    douter que ce soit le même.
    """
    return str(n)

#: Les catégories de scrutin, dites en français plutôt qu'en identifiant.
#: `radar.parse.CATEGORIES` les nomme pour le code ; ici on les nomme pour un
#: lecteur. Les deux listes doivent rester alignées — une catégorie ajoutée là
#: sans être traduite ici s'afficherait telle quelle, en minuscules et sans
#: accent, au milieu d'une phrase.
#: Les jetons que `page()` remplit elle-même, après ceux de la page. Une page
#: qui en propose un du même nom le voit écrasé en silence — d'où la garde.
RESERVES = ("TITRE", "DESCRIPTION", "CANONIQUE", "NAV_ACCUEIL", "NAV_DEPUTES",
            "NAV_VOTES", "NAV_CARTE", "NAV_METHODE")

CATEGORIES_DITES = {
    "ensemble": "vote sur l'ensemble d'un texte",
    "motion_censure": "motion de censure",
    "motion_procedure": "motion de procédure",
    "article": "vote sur un article",
    "amendement": "vote sur un amendement",
    "declaration": "déclaration du gouvernement",
    "autre": "scrutin public",
}

ICI = Path(__file__).parent
GABARITS = ICI / "gabarits"
STATIQUE = ICI / "statique"
ASSETS = ICI / "assets"
SORTIE = ICI / "sortie"

# Les portraits officiels, téléchargés par `radar portraits` dans `data/raw/`.
# Le générateur les republie recompressés : le site institutionnel sert des
# JPEG de 150×192 qui pèsent 38 Ko, soit 24 Mo pour l'Assemblée entière, pour
# une image de la taille d'une vignette. Recomprimés à taille native, ils font
# 6 Ko — 3,8 Mo en tout, moins qu'une seule page de scrutin.
#
# Ils ne sont pas dans `assets/` : `assets/` porte la marque du site, qui est
# de nous et vit dans le dépôt. Ceux-ci sont une donnée récupérée, absente d'un
# clone frais, et le site se construit sans eux.
PORTRAITS = ICI.parent / "data" / "raw" / "portraits"
PORTRAITS_PUBLIES = "statique/portraits"
PORTRAIT_QUALITE = 80

# La note méthodologique est compilée par LaTeX, hors de ce script : exiger un
# moteur TeX pour générer le site rendrait la publication dépendante d'une
# chaîne d'outils qui n'a rien à voir avec elle. On recopie le PDF s'il est là,
# et la page Méthode s'adapte à son absence — un site amputé de son annexe reste
# un site, un site qui refuse de se construire n'en est plus un.
NOTE_SOURCE = ICI.parent / "docs" / "note-methodologique.pdf"
NOTE_CHEMIN = "methode/note-methodologique.pdf"

# Le source LaTeX, uniquement pour savoir si le PDF est en retard sur lui.
# **Une annexe absente et une annexe périmée ne se valent pas.** L'absence est
# honnête : la page Méthode retire son encart et personne n'est trompé. Le
# retard, lui, publie sous l'autorité du document de référence une définition
# que le code n'applique plus — or le site affirme que « si le site et la note
# divergent, c'est le site qui a tort ». Servir une note périmée, c'est donc
# publier soi-même la preuve qu'on a tort. C'est arrivé le jour où le
# dénominateur du taux d'adoption est passé des dépôts aux examens : le `.tex`
# était corrigé, le PDF de la veille tenait encore l'ancienne formule, et rien
# ne l'aurait signalé.
NOTE_TEX = ICI.parent / "docs" / "note-methodologique.tex"


def note_perimee() -> bool:
    """Le PDF est-il plus ancien que le source dont il est tiré ?"""
    return (NOTE_SOURCE.exists() and NOTE_TEX.exists()
            and NOTE_SOURCE.stat().st_mtime < NOTE_TEX.stat().st_mtime)


def publier_portraits(uids: set[str]) -> tuple[int, int]:
    """Recomprime les portraits de `data/raw/` vers `sortie/statique/portraits/`.

    `uids` est l'ensemble des députés qui ont une fiche. Il est restreignant, et
    c'est le but : `data/raw/portraits/` porte les 648 de la table, fiche ou
    non, et publier les 71 qui n'en ont pas mettrait en ligne les portraits
    d'anciens députés sur des pages qui n'existent pas. Une image en ligne que
    rien ne référence est exactement la page « Soutenir » restée chez
    l'hébergeur, en 71 exemplaires.

    Rend (nombre publié, poids total). Zéro si le dossier source est absent :
    un clone frais n'a pas encore lancé `radar portraits`, et un site sans
    portraits reste un site — la fiche est écrite pour s'en passer.

    La republication est **entière à chaque fois**, comme le reste de
    `sortie/` : `main()` efface `statique/` avant de le réécrire, et un cache
    à invalider pour gagner trois secondes sur une génération de vingt est un
    cache qui finira par servir le portrait d'hier.
    """
    if not PORTRAITS.exists():
        return 0, 0
    from PIL import Image

    dossier = SORTIE / PORTRAITS_PUBLIES
    dossier.mkdir(parents=True, exist_ok=True)
    poids = 0
    sources = sorted(p for p in PORTRAITS.glob("PA*.jpg") if p.stem in uids)
    for src in sources:
        # Taille native conservée : l'original fait déjà 150×192, soit la
        # vignette telle qu'elle s'affiche. Ce qui est repris, c'est la
        # compression — le site institutionnel sert ces 150×192 en 38 Ko.
        with Image.open(src) as im:
            cible = dossier / src.name
            im.convert("RGB").save(
                cible, "JPEG", quality=PORTRAIT_QUALITE, optimize=True, progressive=True
            )
        poids += cible.stat().st_size
    return len(sources), poids


def portrait_html(uid: str) -> str:
    """La balise du portrait, ou rien du tout.

    **Rien du tout, et pas une silhouette de remplacement.** Deux députés n'ont
    pas de photographie sur le site de l'Assemblée ; leur en dessiner une, même
    grise et anonyme, donnerait à lire « voici son visage » là où la source
    n'a rien. C'est la même règle que pour les chiffres : une case vide vaut
    mieux qu'un zéro.

    L'alternative textuelle est vide à dessein : le nom est composé juste à
    côté, en corps 4,6 rem, et le faire relire par la synthèse vocale sous
    forme de « photographie de … » n'ajoute rien à qui n'y voit pas.

    Ce qui est vérifié est le fichier **publié**, pas sa source dans
    `data/raw/` : c'est lui que le navigateur ira chercher, et deux conditions
    différentes pour la balise et pour l'image finiraient par diverger sur une
    fiche vers un carré cassé.
    """
    if not (SORTIE / PORTRAITS_PUBLIES / f"{uid}.jpg").exists():
        return ""
    return (f'<img class="portrait" src="{PORTRAITS_PUBLIES}/{uid}.jpg" alt=""\n'
            f'         width="150" height="192" decoding="async">')

# Adresse canonique, pour le sitemap et les URL absolues des métadonnées de
# partage. Une URL relative suffit à la navigation ; un moteur de recherche et
# un aperçu de lien, eux, exigent l'adresse complète.
BASE = "https://radar-parlementaire.fr"

# Le registre des corrections : une donnée, pas du code. Il s'édite sans toucher
# à ce fichier, et son format est documenté en tête du TOML.
CORRECTIONS = ICI / "corrections.toml"

# L'identité de l'éditeur. Elle est écrite ici et nulle part ailleurs : le pied
# de page, les mentions légales et les deux références bibliographiques la
# tirent toutes de ces constantes, pour qu'un changement de nom ou d'adresse ne
# puisse pas laisser une page en arrière.
EDITEUR = "Sylvain Desroziers"
EDITEUR_BIB = "Desroziers, Sylvain"
CONTACT = "sylvain.desroziers@protonmail.com"
DEPOT = "https://github.com/sdesrozis/radar-parlementaire"
DEPOT_COURT = "github.com/sdesrozis/radar-parlementaire"

# La version du paquet, lue là où elle est déclarée. La réécrire ici en ferait
# une seconde source de vérité, qui divergerait à la première publication.
VERSION = tomllib.loads(
    (ICI.parent / "pyproject.toml").read_text())["project"]["version"]


# ── distributions : la matière de la bande des 577 ─────────────────────────

def distribution(valeurs) -> dict:
    v = sorted(round(float(x), 4) for x in valeurs)
    return {"valeurs": v, "min": v[0], "max": v[-1], "mediane": v[len(v) // 2], "n": len(v)}


def rang_parmi(valeurs: list[float], x: float) -> int:
    """Combien de députés se situent au-dessus."""
    return sum(1 for v in valeurs if v > x)


def participation_engageants(tous: list[dict]) -> dict[str, dict]:
    """Présence aux votes qui engagent, telle que `radar` la calcule — et pas autrement.

    Ce générateur a longtemps refait la division ici, à partir du cube, en
    oubliant le retrait des non-votants structurels que fait
    `analyze.participation`. Deux chiffres coexistaient donc pour la même
    mesure, jusqu'à 12,5 points d'écart. Il n'y a plus de calcul à cet endroit :
    on relit ce que `vues.py` sert.
    """
    return {
        d["acteur_uid"]: {
            "taux": d["participation_engageants"],
            "exprimes": d["votes_engageants"],
            "eligibles": d["engageants_eligibles"],
            "votables": d["engageants_votables"],
        }
        for d in tous
        if d.get("participation_engageants") is not None
    }


# ── fragments HTML ─────────────────────────────────────────────────────────

def rangs_html(voisins: list[dict], adresse: dict[str, str],
               publiees: set[str], surligne: str | None = None) -> str:
    """Les colonnes de proximité. `adresse` est la table uid → fichier : cette
    fonction ne fabrique pas d'URL, elle en lit une.

    Un voisin dont la fiche n'est pas publiée — un ancien député, ministre parti
    en cours de mandat — garde son nom et perd son lien. Un lien mort sur la
    page qui promet la vérifiabilité coûte plus cher que l'absence de lien.
    """
    lignes = []
    for v in voisins:
        classe = "rang souligne" if v["nom_complet"] == surligne else "rang"
        uid = v["acteur_uid"]
        nom = (
            f'<a class="nom" href="{adresse[uid]}">{echapper(v["nom_complet"])}</a>'
            if uid in publiees else
            f'<span class="nom ancien" title="mandat terminé">'
            f'{echapper(v["nom_complet"])}</span>'
        )
        lignes.append(
            f'<div class="{classe}">'
            f'{nom}'
            f'<span class="grp">{echapper(v["groupe"])}</span>'
            f'<span class="tx">{pct(v["accord"])}{NBSP}%</span>'
            f'<span class="n">n={num(v["scrutins_communs"])}</span>'
            f"</div>"
        )
    return "\n        ".join(lignes)


def journal_html(votes: list[dict]) -> str:
    """Le relevé des votes qui engagent, une ligne par scrutin.

    Écrit dans la page et non chargé par un script : c'est la pièce qui rend
    les taux opposables, et elle doit rester lisible sans JavaScript, indexable
    par un moteur et copiable dans un tableur. Le filtre, lui, est un confort —
    il masque des lignes déjà présentes, il n'en fabrique aucune.

    Les classes portent le statut : le filtre est un simple sélecteur CSS, et
    aucun compte n'est refait côté navigateur.

    Le balisage est réduit au strict nécessaire — `<time>`, `<p>`, `<small>`,
    `<b>` stylés par sélecteur d'élément plutôt que quatre `class` par ligne.
    Ce n'est pas de la coquetterie : la liste pèse 245 lignes sur chacune des
    577 fiches, et chaque attribut économisé vaut une centaine de kilo-octets
    à l'échelle du site.

    **Chaque titre mène à la page du scrutin.** Le relevé disait « il a voté
    contre » sans qu'on puisse voir contre quoi d'autre, ni qui d'autre : le
    lecteur qui voulait vérifier un vote devait sortir du site. Le lien est
    posé sur le titre, et sur lui seul — l'ajouter aussi sur le numéro de
    scrutin en dessous ferait deux liens vers la même page sur chacune des
    141 365 lignes du site, et un lecteur d'écran les annoncerait deux fois.
    """
    libelles = dict(STATUTS)
    lignes = []
    for v in votes:
        classes = [v["statut"]]
        marques = []
        if v.get("par_delegation"):
            classes.append("delegue")
            marques.append("<i>par délégation</i>")
        if v.get("dissident"):
            classes.append("ecart")
            marques.append('<i class="hors">écart à la ligne</i>')
        resultat = (v.get("sort_code") or "").capitalize()
        lignes.append(
            f'<li class="{" ".join(classes)}">'
            f'<time datetime="{v["date"]}">{jour(v["date"])}</time>'
            f'<p><a href="{adresse_scrutin(v)}">{echapper(v["titre"])}</a>'
            f'<small>Scrutin n{ORDINAL_MASC}&nbsp;{numero_scrutin(v["numero"])}'
            f' · {echapper(resultat)} par {num(v["n_pour"])} voix contre '
            f'{num(v["n_contre"])}{"".join(marques)}</small></p>'
            f'<b>{libelles[v["statut"]]}</b>'
            f"</li>"
        )
    return "\n      ".join(lignes)


def bilan_html(bilan: list[dict]) -> str:
    """L'activité en un coup d'œil : trois assiettes, une ligne chacune.

    Le site publie deux présences, à deux endroits, avec deux dénominateurs.
    Les rapprocher demandait de tenir deux pages en mémoire ; elles sont ici
    dans le même tableau, avec ce qui les sépare — combien de scrutins, combien
    de suffrages, quelle part déléguée.

    La ligne « tous les scrutins » est mise en avant comme une somme, pas comme
    une mesure : c'est la moins intéressante des trois, puisque les amendements
    y décident du résultat par leur nombre. Elle est là parce qu'elle est celle
    que le lecteur va chercher ailleurs, et qu'il vaut mieux la lui donner avec
    sa réserve que le laisser la trouver sans.
    """
    libelles = {
        "engageants": ("Votes qui engagent",
                       "ensemble d'un texte, motion de censure"),
        "autres": ("Autres scrutins",
                   "amendements, articles, motions de procédure"),
        "tous": ("Tous les scrutins", "la somme des deux lignes ci-dessus"),
    }
    lignes = []
    for r in bilan:
        titre, glose = libelles[r["assiette"]]
        taux = f"{pct(r['taux'])}{NBSP}%" if r["taux"] is not None else "—"
        classe = ' class="somme"' if r["assiette"] == "tous" else ""
        lignes.append(
            f"<tr{classe}>"
            f'<td><b>{titre}</b><span class="plein">{glose}</span></td>'
            f'<td class="mono">{num(r["votables"])}</td>'
            f'<td class="mono">{num(r["exprimes"])}</td>'
            f'<td class="mono">{num(r["delegues"])}</td>'
            f'<td class="mono">{taux}</td>'
            f"</tr>"
        )
    return "\n          ".join(lignes)


def solennels_html(sol: dict, apercu: dict) -> str:
    """La ligne des scrutins solennels, sous le trait des trois assiettes.

    Elle est **sous un trait** et non dans la suite du tableau, parce qu'elle
    n'est pas une quatrième assiette : les scrutins solennels recoupent les
    votes qui engagent au lieu de s'y ajouter. Une quatrième ligne alignée sur
    les trois autres serait lue comme une part du total, et le lecteur ferait
    l'addition que le tableau lui aurait tendue.

    Cf. `vues._solennels`, qui porte la définition, et le repli qui suit dans
    le gabarit, qui porte l'arithmétique.
    """
    s = apercu["solennels"]
    taux = f"{pct(sol['taux'])}{NBSP}%" if sol["taux"] is not None else "—"
    return (
        '<tr class="recoupe">'
        "<td><b>Dont scrutins solennels</b>"
        f'<span class="plein">{num(s["engageants"])} de ces {num(s["total"])} '
        "scrutins sont déjà comptés ligne 1</span></td>"
        f'<td class="mono">{num(sol["votables"])}</td>'
        f'<td class="mono">{num(sol["exprimes"])}</td>'
        f'<td class="mono">{num(sol["delegues"])}</td>'
        f'<td class="mono">{taux}</td>'
        "</tr>"
    )


def adresse_scrutin(v: dict) -> str:
    """`scrutin-8434.html`.

    Le numéro et rien d'autre, contrairement aux fiches de députés qui portent
    un nom lisible. Ce n'est pas une inconséquence : le numéro de scrutin **est**
    la façon dont l'Assemblée cite ses propres votes, celle qu'on recopie depuis
    un compte rendu ou un article de presse, et elle est stable. Un slug tiré du
    titre — « l-ensemble-de-la-proposition-de-loi-visant-a-… » — ferait des
    adresses de deux cents caractères qui changeraient au moindre changement de
    formulation de la source.
    """
    return f"scrutin-{v['numero']}.html"


def titre_scrutin(titre: str) -> str:
    """Le titre d'un scrutin, composé comme un titre et non comme une phrase.

    La source publie une phrase minuscule et ponctuée — « l'ensemble de la
    proposition de loi visant à… ». Elle est parfaite en ligne de relevé, où
    elle est effectivement une phrase, et fautive en titre : une capitale
    manque, et le point final traîne sous un `h1` ou derrière les points de
    suspension d'un titre tronqué à trois lignes, ce qui donne « …. ».

    Le texte n'est pas résumé pour autant. Il désigne le texte **tel qu'il
    ressort des débats**, amendements compris, et le raccourcir reviendrait à
    publier notre résumé sous l'autorité de la source.
    """
    t = (titre or "").strip()
    if not t:
        return ""
    return (t[0].upper() + t[1:]).rstrip(".")


def index_votes_html(votes: list[dict]) -> str:
    """L'index des votes qui engagent, par année, en HTML nu.

    Même rôle que l'index des députés par département : la recherche au-dessus
    fabrique ses résultats en JavaScript, donc elle n'existe pas pour un lecteur
    sans script, pour un lecteur d'écran qui parcourt les liens, ni pour un
    moteur. Sans cet index, les 245 pages de vote ne seraient atteignables que
    par le plan du site.

    Il ne filtre rien et ne calcule rien. Il n'affiche que ce qui identifie un
    vote — la date, le titre, le sort — et surtout pas les comptes de voix : ce
    serait refaire la liste du dessus, en moins bien.
    """
    annees: dict[str, list[dict]] = {}
    for v in votes:
        annees.setdefault(v["date"][:4], []).append(v)

    blocs = []
    for annee, groupe in sorted(annees.items(), reverse=True):
        lignes = "\n        ".join(
            f'<li><a href="{adresse_scrutin(v)}">'
            f'<time datetime="{v["date"]}">{jour(v["date"])}</time>'
            f'{echapper(titre_scrutin(v["titre"]))}</a></li>'
            for v in groupe
        )
        blocs.append(
            f'<div class="index-annee"><h3>{annee}<span class="mono">'
            f'{num(len(groupe))} vote{pluriel(len(groupe))}</span></h3>'
            f"<ul>\n        {lignes}\n      </ul></div>"
        )
    return "\n      ".join(blocs)


def votes_json(votes: list[dict]) -> str:
    """L'index servi à la recherche des votes.

    Les clés sont courtes parce qu'elles sont répétées 245 fois, et les valeurs
    sont **déjà mises en forme** : la date en français, les comptes avec leur
    espace insécable. Le JavaScript n'en fabrique aucune — c'est la même règle
    que pour l'annuaire, et pour la même raison : deux mises en forme du même
    nombre finissent par ne plus dire la même chose.
    """
    index = [{
        "u": adresse_scrutin(v),
        "n": str(v["numero"]),
        "d": jour(v["date"]),
        "t": titre_scrutin(v["titre"]),
        "loi": v["liens"]["dossier_titre"] or "",
        "a": v["adopte"],
        "s": "Adopté" if v["adopte"] else "Rejeté",
        "p": num(v["n_pour"] or 0),
        "c": num(v["n_contre"] or 0),
        "ab": num(v["n_abstention"] or 0),
    } for v in votes]
    return json.dumps(index, ensure_ascii=False, separators=(",", ":"))


def groupes_scrutin_html(groupes: list[dict]) -> str:
    """Le tableau des positions de groupe sur un scrutin.

    Un groupe partagé à égalité n'a **pas** de position majoritaire : la case
    porte un tiret cadratin et non « 50 % », parce qu'il n'y a rien à mesurer.
    C'est la même règle que partout ailleurs sur le site — une absence de
    mesure ne s'affiche pas comme une mesure basse.
    """
    lignes = []
    for g in groupes:
        if g.get("partage") or not g.get("majoritaire"):
            position, suivie = "<i>groupe partagé</i>", "—"
        else:
            position = echapper(g["majoritaire"])
            suivie = f'{pct(g["part_majoritaire"], 0)}{NBSP}%'
        lignes.append(
            f"<tr>"
            f'<td><b>{echapper(g.get("groupe") or "—")}</b>'
            f'<span class="plein">{echapper(g.get("groupe_libelle") or "")}</span></td>'
            f'<td class="mono">{num(g["n_pour"])}</td>'
            f'<td class="mono">{num(g["n_contre"])}</td>'
            f'<td class="mono">{num(g["n_abstention"])}</td>'
            f"<td>{position}</td>"
            f'<td class="mono">{suivie}</td>'
            f"</tr>"
        )
    return "\n          ".join(lignes)


def releve_html(votes: list[dict], adresse: dict[str, str], publiees: set[str]) -> str:
    """Le relevé nominatif d'un scrutin : 648 lignes, une par député.

    C'est la pièce justificative de la page, et elle obéit aux mêmes contraintes
    que le relevé des fiches : écrite dans le document, lisible sans JavaScript,
    indexable, copiable dans un tableur. Le filtre au-dessus ne fabrique rien —
    il masque des lignes déjà là, par un sélecteur CSS sur la classe de statut.

    **Le balisage est nu, et il doit le rester.** 648 lignes multipliées par 245
    pages font 158 760 lignes : le relevé pèse 87 Ko sur une page de 110, et
    chaque attribut ajouté ici coûte trois mégaoctets au site. Le nom, le groupe
    et le statut se stylent par sélecteur d'élément.

    Un député dont la fiche n'est pas publiée — mandat achevé — garde son nom et
    perd son lien. Lier vers une page absente est une promesse cassée&nbsp;; le
    retirer du relevé serait bien pire : c'est lui qui a voté.
    """
    libelles = dict(STATUTS)
    lignes = []
    for v in votes:
        classes = [v["statut"]]
        marques = []
        if v.get("par_delegation"):
            classes.append("delegue")
            marques.append("<i>par délégation</i>")
        if v.get("dissident"):
            classes.append("ecart")
            marques.append('<i class="hors">écart à la ligne</i>')
        nom = echapper(v["nom_complet"])
        uid = v["acteur_uid"]
        if uid in publiees:
            nom = f'<a href="{adresse[uid]}">{nom}</a>'
        lignes.append(
            f'<li class="{" ".join(classes)}">'
            f"<p>{nom}"
            f'<small>{echapper(v.get("groupe") or "sans groupe")}'
            f'{"".join(marques)}</small></p>'
            f'<b>{libelles[v["statut"]]}</b>'
            f"</li>"
        )
    return "\n      ".join(lignes)


def filtres_html(resume: dict) -> str:
    """Les boutons du relevé, chacun avec son effectif.

    Ils portent les comptes, et c'est leur seconde raison d'être : la ligne
    « 118 pour · 44 contre · 12 abstentions » est le résumé du relevé, et elle
    est ici garantie d'être celle des lignes affichées en dessous, puisque les
    deux viennent du même dictionnaire.

    Un statut à zéro n'a pas de bouton : proposer un filtre qui ne donnerait
    rien est une impasse offerte au lecteur.
    """
    boutons = [
        '<button type="button" data-filtre="tous" aria-pressed="true">'
        f'Tous<span class="compte mono">{num(resume["total"])}</span></button>'
    ]
    for cle, libelle in STATUTS:
        if resume.get(cle):
            boutons.append(
                f'<button type="button" data-filtre="{cle}" aria-pressed="false">'
                f'{libelle}<span class="compte mono">{num(resume[cle])}</span></button>'
            )
    for cle, libelle in (("delegue", "Par délégation"), ("ecart", "Écart à la ligne")):
        n = resume["delegues"] if cle == "delegue" else resume["dissidents"]
        if n:
            boutons.append(
                f'<button type="button" data-filtre="{cle}" aria-pressed="false">'
                f'{libelle}<span class="compte mono">{num(n)}</span></button>'
            )
    return "\n      ".join(boutons)


def sorts_html(am: dict) -> str:
    """La ventilation des sorts d'un dépôt d'amendements.

    Le compte de dépôts seul ne dit rien : mille amendements tombés à
    l'irrecevabilité et mille amendements adoptés sont le même nombre. Les six
    états sont donc affichés ensemble, avec leur somme, qui doit retomber sur le
    nombre de dépôts — c'est le contrôle que le lecteur peut faire de tête.
    """
    etats = (
        ("adoptes", "Adoptés"),
        ("rejetes", "Rejetés"),
        ("tombes", "Tombés"),
        ("retires", "Retirés"),
        ("non_soutenus", "Non soutenus"),
    )
    cases = [
        f'<div class="sort"><span class="sort-n mono">{num(am.get(cle) or 0)}</span>'
        f'<span class="sort-nom">{libelle}</span></div>'
        for cle, libelle in etats
    ]
    restant = (am.get("deposes") or 0) - (am.get("examines") or 0)
    cases.append(
        f'<div class="sort sort-attente"><span class="sort-n mono">{num(restant)}</span>'
        f'<span class="sort-nom">Pas encore examinés</span></div>'
    )
    return '<div class="sorts">\n      ' + "\n      ".join(cases) + "\n    </div>"


def lignes_delegation_html(groupes: list[dict]) -> str:
    """Le tableau de la délégation par groupe, du plus au moins délégataire."""
    mesures = [g for g in groupes if g.get("part_delegation") is not None]
    lignes = []
    for g in sorted(mesures, key=lambda x: -x["part_delegation"]):
        lignes.append(
            f"<tr>"
            f'<td><span class="sigle">{echapper(g["groupe"])}</span>'
            f'<span class="plein">{echapper(g["groupe_libelle"] or "")}</span></td>'
            f'<td class="mono">{num(g["effectif_actuel"])}</td>'
            f'<td class="mono">{pct(g["part_delegation"])}{NBSP}%</td>'
            f'<td class="mono">{num(g["delegues_groupe"])} / '
            f'{num(g["engageants_groupe"])}</td>'
            f"</tr>"
        )
    return "\n          ".join(lignes)


def redirection(vers: str) -> str:
    """Page de renvoi minimale, laissée à l'ancienne adresse d'une fiche.

    Les fiches ont longtemps porté l'identifiant de l'acteur — `PA267780.html`.
    Ces adresses sont en ligne, indexées, et citées dans des liens que ce site
    ne contrôle pas ; les supprimer casserait tout ce qui pointe vers elles.

    Trois signaux, parce qu'aucun ne suffit seul : `canonical` dit au moteur
    laquelle des deux adresses fait foi, `robots noindex` retire la page de
    renvoi de l'index sans retirer le lien qu'elle transmet, et le
    `meta refresh` emmène le lecteur. Un vrai 301 vaudrait mieux, mais il
    suppose une configuration d'hébergeur ; ceci fonctionne partout, y compris
    en local et sur un simple dossier servi tel quel.
    """
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        f'<link rel="canonical" href="{BASE}/{vers}">'
        '<meta name="robots" content="noindex,follow">'
        f'<meta http-equiv="refresh" content="0; url={vers}">'
        '<title>Radar parlementaire</title></head>'
        f'<body><p>Cette fiche a une nouvelle adresse : '
        f'<a href="{vers}">{vers}</a></p></body></html>\n'
    )


def bloc_note_html() -> str:
    """L'encart qui renvoie à la note méthodologique, ou rien si elle manque.

    Le site ne prétend pas que le PDF existe quand il n'a pas été compilé : un
    lien mort sur la page qui promet la vérifiabilité serait la contradiction
    la plus visible du projet.
    """
    if not NOTE_SOURCE.exists():
        return ""
    mo = NOTE_SOURCE.stat().st_size / 1e6
    return (
        '<section class="cadre socle">\n'
        '    <div class="lecture">\n'
        '      <p class="jalon">La spécification complète</p>\n'
        '      <a class="note-pdf" href="' + NOTE_CHEMIN + '">\n'
        '        <span class="note-pdf-icone" aria-hidden="true">PDF</span>\n'
        '        <span class="note-pdf-texte">\n'
        '          <b>Note méthodologique</b>\n'
        '          <span>Chaque mesure de ce site définie formellement&nbsp;: '
        'numérateur, dénominateur, population de référence, exclusions et '
        'incertitude. Les formules, les seuils, et les références aux travaux '
        'dont les méthodes sont tirées.</span>\n'
        '          <span class="note-pdf-meta">PDF · ' + dec(mo, 1) + '&nbsp;Mo · '
        'sous licence CC BY 4.0</span>\n'
        '        </span>\n'
        '      </a>\n'
        '      <p>C\'est le document de référence&nbsp;: si le site et la note '
        'divergent, <b>c\'est le site qui a tort</b>. Il est écrit pour être '
        'opposé au projet autant que pour l\'expliquer.</p>\n'
        '    </div>\n'
        '  </section>'
    )


def corrections_lues() -> list[dict]:
    """Le registre, du plus récent au plus ancien.

    L'ordre est celui de la lecture : on vient sur cette page pour savoir si un
    chiffre lu récemment a bougé, pas pour parcourir une histoire du projet.
    """
    if not CORRECTIONS.exists():
        return []
    entrees = tomllib.loads(CORRECTIONS.read_text())["correction"]
    return sorted(entrees, key=lambda c: c["date"], reverse=True)


def registre_html(entrees: list[dict]) -> str:
    """Le registre en HTML.

    Aucun champ n'est facultatif : une correction sans son avant/après ne
    permet pas à un lecteur de savoir s'il doit se corriger, et n'aurait donc
    pas dû être écrite. La génération s'arrête plutôt que de publier une entrée
    creuse.
    """
    obligatoires = ("date", "titre", "portee", "erreur", "cause", "effet", "pages", "commit")

    def prose(texte: str) -> str:
        """Échappe, puis rend les `identifiants` en chasse fixe.

        Une correction nomme presque toujours la fonction fautive. L'écrire
        entre accents graves dans le TOML est la façon naturelle de le faire ;
        sans cette conversion, les accents graves s'affichaient tels quels.
        L'échappement passe d'abord, pour qu'un texte ne puisse pas injecter de
        balise par ce chemin.
        """
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", echapper(texte.strip()))

    lignes = []
    for c in entrees:
        vides = [k for k in obligatoires if not str(c.get(k, "")).strip()]
        if vides:
            raise SystemExit(
                f"correction « {c.get('titre', '?')} » : champs vides {sorted(vides)}")
        lignes.append(
            '<li class="correction">\n'
            '        <div class="correction-tete">\n'
            f'          <span class="correction-date mono">{jour(c["date"])}</span>\n'
            f'          <span class="etiq-portee">{echapper(c["portee"])}</span>\n'
            f'          <h3>{echapper(c["titre"])}</h3>\n'
            '        </div>\n'
            f'        <p><b>Ce qui était faux.</b> {prose(c["erreur"])}</p>\n'
            f'        <p><b>Pourquoi.</b> {prose(c["cause"])}</p>\n'
            f'        <p class="correction-effet"><b>Ce que ça change.</b> '
            f'{prose(c["effet"])}</p>\n'
            '        <p class="provenance">Pages concernées&nbsp;: '
            f'{echapper(c["pages"])} · correctif '
            f'<a class="mono" href="{DEPOT}/commit/{c["commit"]}">{c["commit"]}</a></p>\n'
            '      </li>'
        )
    return "\n      ".join(lignes)


def index_departements_html(deputes: list[dict], adresse: dict[str, str]) -> str:
    """Les députés en exercice, groupés par département, en liens HTML.

    C'est le seul endroit du site où les {{EN_EXERCICE}} fiches sont atteignables
    sans JavaScript. L'annuaire fabrique ses résultats à la frappe : excellent
    pour chercher, inexistant pour un moteur, qui n'y voyait aucun lien et ne
    découvrait donc les fiches que par le plan du site — sans jamais savoir
    laquelle compte.
    """
    par_departement: dict[tuple, list[dict]] = {}
    for d in deputes:
        # Le numéro de département est une chaîne : « 2A » et « 976 » n'ont pas
        # d'ordre numérique commun, et le tri lexicographique sur une chaîne
        # zéro-remplie donne l'ordre officiel.
        cle = (str(d["num_departement"] or "zz"), d["departement"] or "Sans département")
        par_departement.setdefault(cle, []).append(d)

    blocs = []
    for (num, nom), membres in sorted(par_departement.items()):
        membres.sort(key=lambda d: (int(d["num_circo"] or 0), d["nom_complet"]))
        liens = "\n          ".join(
            f'<li><a href="{adresse[d["acteur_uid"]]}">{echapper(d["nom_complet"])}'
            f'<span class="circo mono">{d["num_circo"]}{ordinal(d["num_circo"])}</span></a></li>'
            for d in membres
        )
        blocs.append(
            '<div class="index-dep">\n'
            f'        <h3><span class="mono">{echapper(num)}</span> {echapper(nom)}</h3>\n'
            f'        <ul>\n          {liens}\n        </ul>\n'
            '      </div>'
        )
    return "\n      ".join(blocs)


def citations(calcule_le: str) -> tuple[str, str]:
    """La citation courte et les deux références BibTeX.

    Deux entrées et non une : la note spécifie les mesures et ne bouge qu'à la
    révision, le logiciel les calcule et bouge à chaque version. Un article qui
    discute une définition cite la première ; un travail qui rejoue les calculs
    cite les deux. Aucune n'est écrite à la main — le nom, la version et la date
    viennent des constantes et du calcul en cours.
    """
    annee = date.today().year
    cle = f"desroziers{annee}radar"
    courte = (
        f"Radar parlementaire, {EDITEUR}, {annee}. Calculs de l'auteur d'après "
        f"l'open data de l'Assemblée nationale, données au {calcule_le}. "
        f"{BASE}"
    )
    bibtex = f"""@techreport{{{cle}note,
  author      = {{{EDITEUR_BIB}}},
  title       = {{Mesurer l'activité de l'Assemblée nationale à partir de ses
                 données ouvertes : définitions, dénominateurs et incertitudes}},
  institution = {{Radar parlementaire}},
  type        = {{Note méthodologique}},
  year        = {{{annee}}},
  url         = {{{BASE}/{NOTE_CHEMIN}}},
  note        = {{Version du {calcule_le}}}
}}

@software{{{cle}logiciel,
  author  = {{{EDITEUR_BIB}}},
  title   = {{Radar parlementaire : votes, amendements et sujets de
             l'Assemblée nationale}},
  year    = {{{annee}}},
  version = {{{VERSION}}},
  url     = {{{DEPOT}}},
  license = {{AGPL-3.0-or-later}},
  note    = {{Données : open data de l'Assemblée nationale,
             Licence Ouverte 2.0 ; calcul du {calcule_le}}}
}}"""
    return courte, bibtex


def lignes_groupes_html(groupes: list[dict]) -> str:
    lignes = []
    for g in sorted(groupes, key=lambda x: -x["effectif_actuel"]):
        etendue = ("—" if g.get("position_min") is None
                   else f"{dec(g['position_min'])} → {dec(g['position_max'])}")
        mediane = "—" if g.get("position_mediane") is None else dec(g["position_mediane"])
        lignes.append(
            f"<tr>"
            f'<td><span class="sigle">{echapper(g["groupe"])}</span>'
            f'<span class="plein">{echapper(g["groupe_libelle"] or "")}</span></td>'
            f'<td class="mono">{num(g["effectif_actuel"])}</td>'
            f'<td class="mono">{pct(g["cohesion"])}{NBSP}%</td>'
            f'<td class="mono repliable">{pct(g["dissidence_moyenne"])}{NBSP}%</td>'
            f'<td class="mono repliable">{mediane}</td>'
            f'<td class="mono repliable">{etendue}</td>'
            f"</tr>"
        )
    return "\n          ".join(lignes)


# ── assemblage des pages ───────────────────────────────────────────────────

class Site:
    """Porte le contexte calculé une fois, et écrit les pages."""

    @staticmethod
    def _adresse(d: dict) -> str:
        """`agnes-firmin-le-bodo-seine-maritime-10.html`.

        Un identifiant d'acteur — `PA267780` — ne dit rien à un lecteur, ne se
        retient pas, ne se lit pas à voix haute et ne porte aucun des mots par
        lesquels on cherche un député. Le nom et la circonscription portent les
        deux.
        """
        return slug(d["nom_complet"], d.get("departement"), d.get("num_circo")) + ".html"

    def __init__(self, donnees: Donnees):
        self.donnees = donnees
        self.apercu = donnees.apercu()
        self.tous = donnees.liste_deputes()
        self.deputes = [d for d in self.tous if d["en_exercice"]]
        self.groupes = {g["groupe"]: g for g in self.apercu["groupes"]}
        self.portees = self.apercu["scrutins_par_portee"]
        self.categories = self.apercu["scrutins_par_categorie"]
        self.modele = self.apercu["modele"]
        self.n_texte = self.portees.get("texte", 0)
        # Les votes qui engagent, dans l'ordre où l'onglet les montre. Calculés
        # une fois : l'index de la recherche, l'index HTML et les 245 pages en
        # sortent, et trois listes construites séparément finiraient par ne plus
        # contenir les mêmes scrutins.
        self.votes_index = donnees.index_votes()

        # L'adresse d'une fiche, calculée une fois et lue partout. Cinq endroits
        # fabriquaient auparavant `{uid}.html` chacun de leur côté ; une table
        # unique garantit qu'un lien interne, le plan du site et le fichier
        # écrit sur le disque désignent la même page.
        self.adresse = {d["acteur_uid"]: self._adresse(d) for d in self.tous}
        # Les fiches ne sont écrites que pour les députés en exercice, alors que
        # les colonnes de proximité peuvent citer un ancien — un ministre parti
        # en cours de mandat reste un voisin de vote. Lier vers une page qui
        # n'existe pas produisait 61 liens morts, bien avant cette version. On
        # garde le nom, on retire le lien.
        self.publiees = {d["acteur_uid"] for d in self.deputes}
        doublons = len(self.adresse) - len(set(self.adresse.values()))
        if doublons:
            raise SystemExit(
                f"{doublons} députés partagent une même adresse : le slug ne "
                "distingue pas assez. Ajouter un discriminant avant de publier."
            )

        pe = participation_engageants(self.tous)
        self.pe = pe
        # La distribution de comparaison — médiane, maximum, rangs, bande des
        # 577 — ne retient que les taux assis sur assez de scrutins. Sans ce
        # filtre, le maximum de l'Assemblée était fixé par le plus petit
        # dénominateur : 19 votes sur 19 pour la présidente, qui ne vote pas
        # tant qu'elle préside. Cf. `vues.MIN_VOTABLES`.
        comparables = {
            d["acteur_uid"] for d in self.deputes
            if d.get("participation_comparable")
        }
        self.d_part = distribution([p["taux"] for u, p in pe.items() if u in comparables])
        # Même règle pour la dissidence, et pour la même raison. Elle n'y était
        # pas : les trois députés à 0,0 % de la législature sont exactement ceux
        # dont le groupe n'a eu de ligne que sur 4, 8 et 25 scrutins, et ils
        # fixaient l'extrémité « discipline » de la bande des 577.
        # Cf. `analyze.MIN_VOTES_LIGNE`.
        self.d_diss = distribution([
            d["taux_dissidence"] for d in self.deputes
            if d["taux_dissidence"] is not None and d.get("dissidence_comparable")])
        self.d_pos = distribution(
            [d["axe1"] for d in self.deputes if d["axe1"] is not None])
        # La part déléguée sur les votes qui engagent, distribuée comme les
        # autres mesures. Sans ce repère, « 30 % de ses votes sont délégués » se
        # lit comme une anomalie alors que c'est la pratique courante de
        # l'Assemblée. Le filtre est celui de la présence : un taux assis sur
        # quinze suffrages ne fixe pas le maximum des 577.
        self.d_deleg = distribution([
            d["part_delegation_engageants"] for d in self.deputes
            if d.get("part_delegation_engageants") is not None
            and d.get("participation_comparable")
        ])
        # Les dépôts d'amendements. La distribution est très dissymétrique — la
        # médiane est à quelques dizaines, le maximum à plusieurs milliers — et
        # c'est précisément ce que la bande doit montrer : le compte de dépôts
        # n'est pas une mesure d'effort, il est une mesure de pratique de séance.
        #
        # Ici, et ici seulement, une case vide vaut zéro. La règle du site est
        # l'inverse — une absence n'est pas un zéro — mais elle vise les trous
        # de la source. Le fichier des amendements, lui, est complet : n'y
        # figurer comme auteur d'aucun amendement, c'est n'en avoir déposé
        # aucun. Écarter ces seize députés de la distribution reviendrait à
        # calculer la médiane de l'Assemblée sur les seuls déposants.
        self.d_amd = distribution([
            d.get("amendements") or 0 for d in self.deputes
        ]) if self.apercu["avec_amendements"] else None
        self.donnees_json = json.dumps(
            {
                "participation": self.d_part,
                "dissidence": self.d_diss,
                "positions": self.d_pos,
                "delegation": self.d_deleg,
                **({"amendements": self.d_amd} if self.d_amd else {}),
            },
            ensure_ascii=False, separators=(",", ":"))

        self.groupes_tries = sorted(
            [g for g in self.apercu["groupes"] if g.get("position_mediane") is not None],
            key=lambda g: g["position_mediane"])
        self.genere_le = jour(date.today().isoformat())

        self.corrections = corrections_lues()
        self.citation_courte, self.citation_bibtex = citations(self.genere_le)

        self.base = (GABARITS / "base.html").read_text()
        sol = self.apercu["solennels"]
        dos = self.apercu["dossiers"]
        self.commun = {
            "BASE_URL": BASE,
            "EDITEUR": echapper(EDITEUR),
            "CONTACT": CONTACT,
            "DEPOT": DEPOT,
            "DEPOT_COURT": DEPOT_COURT,
            "VERSION": VERSION,
            # Le pied de page annonce le nombre de corrections : c'est ce qui
            # rend la promesse vérifiable d'un coup d'œil, et ce qui interdit
            # de laisser le registre prendre du retard sans que ça se voie.
            "N_CORRECTIONS": num(len(self.corrections)),
            "LEGISLATURE": self.apercu["legislature"],
            "DEBUT": jour(self.apercu["debut"]),
            "FIN": jour(self.apercu["fin"]),
            "SCRUTINS": num(self.apercu["scrutins"]),
            "VOTES": num(self.apercu["votes"]),
            "DEPUTES": num(self.apercu["deputes"]),
            "EN_EXERCICE": num(self.apercu["en_exercice"]),
            # Le nombre de sièges, disponible partout : c'est le repère contre
            # lequel se lit tout effectif publié, et il ne s'écrit nulle part à
            # la main. Il ne vient pas des données mais du droit — article 24
            # de la Constitution — et `radar.config` le porte à ce titre.
            "SIEGES": num(self.apercu["sieges"]),
            "PORTEE_TEXTE": num(self.portees.get("texte", 0)),
            "PORTEE_DETAIL": num(self.portees.get("detail", 0)),
            "PART_DETAIL_PCT": pct(self.portees.get("detail", 0) / self.apercu["scrutins"], 0),
            # Les scrutins solennels, et de quoi les remettre à leur place. Ces
            # jetons sont communs parce que la démonstration se refait à
            # l'identique sur la fiche, sur l'onglet des votes et sur chaque
            # page de scrutin solennel : trois copies d'un même calcul auraient
            # divergé au premier changement d'assiette.
            "SOL_TOTAL": num(sol["total"]),
            "SOL_MOIS": num(sol["mois"]),
            "SOL_PAR_MOIS": dec(sol["par_mois"], 1),
            "SOL_POIDS": pct(sol["poids_d_une_absence"]),
            "SOL_POIDS_10": pct(sol["poids_de_dix_absences"], 0),
            "SOL_POIDS_ENG_10": pct(sol["poids_engageant_dix"], 0),
            "SOL_ENGAGEANTS": num(sol["engageants"]),
            "SOL_HORS": num(sol["hors_engageants"]),
            "SOL_DELEG_PCT": pct(self.apercu["delegation"]["solennels"]["part"], 0),
            "DELEG_ENG_PCT": pct(self.apercu["delegation"]["engageants"]["part"], 0),
            # Le rattachement d'un scrutin à sa loi, tel que la source le donne.
            "DOSSIERS_AVEC": num(dos["scrutins_avec"]),
            "DOSSIERS_SANS": num(dos["scrutins_sans"]),
            "DOSSIERS_DISTINCTS": num(dos["distincts"]),
            "DOSSIERS_DEPUIS": jour(dos["depuis"]) if dos["depuis"] else "—",
            "DOSSIERS_ENG_AVEC": num(dos["engageants_avec"]),
            "DOSSIERS_ENG_SANS": num(dos["engageants_sans"]),
            "GENERE_LE": self.genere_le,
        }

    # -- rendu ------------------------------------------------------------

    def page(self, corps: str, jetons: dict, *, titre: str, description: str,
             chemin: str, onglet: str = "") -> str:
        """Assemble une page.

        `chemin` est son adresse relative à la racine — `""` pour l'accueil.
        Elle sert deux fois : à la balise canonique, qui dit au moteur laquelle
        de plusieurs adresses possibles fait foi, et à `og:url`, que les
        aperçus de lien affichent. Elle est obligatoire pour qu'une page ne
        puisse pas être écrite en oubliant de se nommer.
        """
        html = self.base.replace("{{CORPS}}", corps)
        tout = {
            **self.commun, **jetons,
            "TITRE": echapper(titre),
            "DESCRIPTION": echapper(description),
            "CANONIQUE": f"{BASE}/{chemin}",
            "NAV_ACCUEIL": ' aria-current="page"' if onglet == "accueil" else "",
            "NAV_DEPUTES": ' aria-current="page"' if onglet == "deputes" else "",
            "NAV_VOTES": ' aria-current="page"' if onglet == "votes" else "",
            "NAV_CARTE": ' aria-current="page"' if onglet == "carte" else "",
            "NAV_METHODE": ' aria-current="page"' if onglet == "methode" else "",
        }
        manquants = set(re.findall(r"\{\{(\w+)\}\}", html)) - set(tout)
        if manquants:
            raise SystemExit(f"jetons absents du générateur : {sorted(manquants)}")
        # Un jeton de page qui porte le nom d'un jeton réservé est écrasé sans
        # bruit, et la page affiche autre chose que ce que son gabarit demande.
        # C'est arrivé avec `TITRE` : le `h1` d'une page de scrutin affichait le
        # titre de l'onglet du navigateur, « — Radar parlementaire » compris.
        # La garde existante ne voyait rien — le jeton *avait* une valeur.
        ecrases = set(jetons) & set(RESERVES)
        if ecrases:
            raise SystemExit(
                f"jetons réservés par page() : {sorted(ecrases)}. "
                f"Les renommer dans le gabarit et dans le générateur."
            )
        for cle, valeur in tout.items():
            html = html.replace("{{" + cle + "}}", str(valeur))
        return html

    # -- carte ------------------------------------------------------------

    def matrice_groupes_html(self) -> tuple[str, str, str]:
        """Les en-têtes, les lignes du tableau des groupes, et l'écart des conventions.

        La teinte de chaque case est passée en `--t` et la feuille de style en
        fait une couleur : aucun code hexadécimal ne s'écrit ici, et la case
        reste lisible dans les deux thèmes.

        **Chaque case porte les deux mesures**, la moyenne des taux de paires et
        le taux agrégé sur tous les votes communs. Elles répondent à deux
        questions différentes et diffèrent de plusieurs points ; n'en publier
        qu'une sans nommer la convention ferait passer un choix de méthode pour
        un fait. Le tableau bascule de l'une à l'autre par une classe, et le
        nombre de paires est dans l'infobulle de chaque case.
        """
        mg = self.donnees.matrice_groupes()
        ordre = mg["ordre"]

        entetes = "".join(
            f'<th scope="col">{echapper(g)}</th>' for g in ordre
        )

        ecart_max = 0.0
        lignes = []
        for i, a in enumerate(ordre):
            tds = []
            for j, b in enumerate(ordre):
                v = mg["cases"][i][j]
                agrege = mg["cases_agregees"][i][j]
                if v is None:
                    tds.append('<td class="mono">—</td>')
                    continue
                if agrege is not None:
                    ecart_max = max(ecart_max, abs(v - agrege))
                classes = "mono fonce" if v > 0.6 else "mono"
                if a == b:
                    classes += " soi"
                infobulle = (
                    f'{num(mg["paires"][i][j])} paires · '
                    f'{num(mg["scrutins_communs"][i][j])} scrutins communs'
                )
                tds.append(
                    f'<td class="{classes}" style="--t:{v:.3f}" title="{infobulle}">'
                    f'<span class="par-paires">{pct(v, 0)}</span>'
                    f'<span class="agregee">'
                    f'{pct(agrege, 0) if agrege is not None else "—"}</span></td>'
                )
            effectif = mg["effectifs"].get(a)
            lignes.append(
                f'<tr><th scope="row">{echapper(a)}'
                f'<span class="effectif">{num(effectif) if effectif else "—"}</span></th>'
                + "".join(tds) + "</tr>"
            )
        return entetes, "\n          ".join(lignes), dec(100 * ecart_max, 1)

    def carte(self) -> str:
        m = self.donnees.matrice_accords()
        entetes, lignes_groupes, ecart_conventions = self.matrice_groupes_html()
        textes = phrase_carte(m, m["groupes"])

        # La délégation appartient à cette page autant qu'aux fiches : un taux
        # d'accord compte des suffrages imputés, et près d'un quart d'entre eux
        # ont été déposés par quelqu'un d'autre.
        deleg = self.apercu["delegation"]
        deleg1, deleg2 = phrase_delegation_assemblee(deleg, self.apercu["groupes"])

        # La paire la plus élevée sert d'exemple chiffré dans le bloc « piège » :
        # elle est déjà calculée, on ne la réinvente pas.
        exemple_haut = pct(m["paire_haute"]["accord"], 0)

        corps = (GABARITS / "carte.html").read_text()
        return self.page(
            corps,
            {
                **textes,
                "N_DEPUTES": num(m["n"]),
                "PAIRES": num(m["paires"]),
                "MIN_COMMUNS": num(m["min_communs"]),
                "N_GROUPES": num(len(m["groupes"])),
                "ECHELLE_BAS": pct(m["echelle_bas"], 0),
                "ECHELLE_HAUT": pct(m["echelle_haut"], 0),
                "EXEMPLE_HAUT": exemple_haut,
                "EXEMPLE_NOM": echapper(m["paire_haute"]["a"]),
                "ENTETES_GROUPES": entetes,
                "LIGNES_MATRICE_GROUPES": lignes_groupes,
                "ECART_CONVENTIONS": ecart_conventions,
                "DELEG_ASSEMBLEE_PCT": pct(deleg["engageants"]["part"]),
                "DELEG_ASSEMBLEE_N": num(deleg["engageants"]["delegues"]),
                "DELEG_ASSEMBLEE_DENOM": num(deleg["engageants"]["exprimes"]),
                "DELEG_ASSEMBLEE_PHRASE": deleg1,
                "DELEG_ASSEMBLEE_PHRASE_2": deleg2,
                "LIGNES_DELEGATION": lignes_delegation_html(self.apercu["groupes"]),
                "CARTE_JSON": json.dumps(m, ensure_ascii=False, separators=(",", ":")),
            },
            titre="Qui vote avec qui ? — Radar parlementaire",
            description=(
                f"Les {num(m['paires'])} paires de députés de l'Assemblée nationale, "
                f"chacune mesurée sur les scrutins où les deux ont voté."
            ),
            chemin="carte.html", onglet="carte")

    # -- méthode ----------------------------------------------------------

    def methode(self) -> str:
        p1, p2 = phrase_portee(self.apercu)
        corps = (GABARITS / "methode.html").read_text()
        return self.page(
            corps,
            {
                "PHRASE_PORTEE": p1,
                "PHRASE_PORTEE_2": p2,
                "ENTRES_EN_COURS": num(self.apercu["entres_en_cours"]),
                "INTERROMPUS": num(self.apercu["mandats_interrompus"]),
                # Le nombre d'invariants n'est pas écrit dans la page : il est
                # celui du module qui les exécute. Une page qui promettrait
                # plus de contrôles qu'il n'en existe serait exactement le
                # genre d'affirmation invérifiable que ce site refuse.
                "N_CONTROLES": num(len(CONTROLES)),
                "BOOTSTRAP": num(self.apercu["bootstrap"]),
                "BLOCS": num(self.apercu["blocs_bootstrap"]),
                "MIN_COMMUNS": num(MIN_COMMUNS),
                "MIN_COMMUNS_TEXTE": num(MIN_COMMUNS_TEXTE),
                # La composition exacte de l'assiette « votes qui engagent ».
                # La page l'énonçait de mémoire, et se trompait : elle y rangeait
                # les déclarations de politique générale, que `parse.PORTEES`
                # classe en portée intermédiaire. Ces trois nombres viennent
                # désormais du même comptage que le reste du site.
                "N_ENSEMBLE": num(self.categories.get("ensemble", 0)),
                "N_MOTIONS": num(self.categories.get("motion_censure", 0)),
                "N_DECLARATIONS": num(self.categories.get("declaration", 0)),
                "N_MOTIONS_N": num(self.categories.get("motion_censure", 0)),
                # Ce que le modèle retient réellement, et ce qu'un second axe
                # apporterait. Cf. `vues._modele_apercu` et `vues._ajustement`.
                "MODELE_SCRUTINS": num(self.modele["scrutins_retenus"]),
                "APRE_1D": dec(self.modele["apre_test_1d"], 2),
                "APRE_2D": dec(self.modele["apre_test_2d"], 2),
                "ANCRAGE": echapper(GROUPE_ANCRAGE),
                # La lacune du dossier législatif, datée. La page affirmait que
                # l'open data ne renseignait pas ce champ ; il le renseigne
                # depuis mars 2026, et c'est le parseur qui le perdait.
                "DOSSIERS_DEPUIS": jour(self.apercu["dossiers"]["depuis"]),
                "DOSSIERS_ENGAGEANTS": num(self.apercu["dossiers"]["engageants_avec"]),
                "BLOC_NOTE": bloc_note_html(),
                "CITATION_COURTE": echapper(self.citation_courte),
                "CITATION_BIBTEX": echapper(self.citation_bibtex),
            },
            titre="Méthode — Radar parlementaire",
            description=("La définition exacte de chaque mesure du Radar parlementaire, "
                         "son dénominateur, et ce qu'elle n'est pas capable de dire."),
            chemin="methode.html", onglet="methode")

    # -- mentions légales et registre des corrections ----------------------

    def mentions(self) -> str:
        """Qui édite, avec quel argent, et sous quelles licences.

        Cette page manquait, et c'était le manque le plus coûteux du site : des
        chiffres nominatifs sur des élus, publiés par personne. Elle ne contient
        aucune mesure — seulement ce qui permet de savoir qui opposer à un
        chiffre qu'on conteste.
        """
        return self.page(
            (GABARITS / "mentions.html").read_text(), {},
            titre="Qui édite ce site — Radar parlementaire",
            description=(
                f"{EDITEUR} édite le Radar parlementaire : éditeur, hébergeur, "
                "financement, indépendance, données personnelles et licences."),
            chemin="mentions.html")

    def corrections_page(self) -> str:
        return self.page(
            (GABARITS / "corrections.html").read_text(),
            {"CORRECTIONS": registre_html(self.corrections)},
            titre="Corrections — Radar parlementaire",
            description=(
                f"Les {num(len(self.corrections))} erreurs trouvées dans les chiffres "
                "publiés par le Radar parlementaire : ce qu'elles valaient, ce qu'elles "
                "valent, et le correctif qui les a réparées."),
            chemin="corrections.html")

    # -- fiche ------------------------------------------------------------

    def fiche(self, uid: str) -> str:
        f = self.donnees.fiche(uid)
        i, a, p = f["identite"], f["activite"], f["position"]
        groupe = self.groupes.get(i["groupe"])

        p_eng = self.pe.get(uid, {"taux": 0.0, "exprimes": 0, "eligibles": 0, "votables": 0})
        taux = p_eng["taux"]
        exprimes, eligibles, votables = (
            p_eng["exprimes"], p_eng["eligibles"], p_eng["votables"])
        a["participation_engageants"] = taux
        a["votes_engageants"] = exprimes
        # Le dénominateur est propre au député, et deux retraits l'ont formé : les
        # scrutins hors mandat, et ceux où la source dit qu'il ne pouvait pas
        # voter (ministre, président de séance). Cf. `analyze.participation`.
        a["engageants_eligibles"] = eligibles
        a["engageants_votables"] = votables

        rang_part = rang_parmi(self.d_part["valeurs"], taux)
        rang_diss = rang_parmi(self.d_diss["valeurs"], a["taux_dissidence"] or 0)
        communs = len({v["nom_complet"] for v in f["proches"]["tous"]}
                      & {v["nom_complet"] for v in f["proches"]["texte"]})

        # Un député trop peu présent n'a pas de position estimée. On ne bricole pas
        # un chiffre : on pose l'autre bloc, qui dit pourquoi la case est vide.
        estimee = p.get("axe1") is not None and p.get("borne_basse") is not None
        if estimee:
            recouvre = sum(
                1 for d in self.deputes
                if d.get("borne_basse") is not None
                and d["borne_haute"] >= p["borne_basse"]
                and d["borne_basse"] <= p["borne_haute"]
                and d["acteur_uid"] != uid)
            # Les groupes qui encadrent sa position : repères concrets, jamais une
            # étiquette « gauche » ou « droite » que rien dans les données ne fonde.
            encadrants = ([g for g in self.groupes_tries if g["position_mediane"] <= p["axe1"]][-1:]
                          + [g for g in self.groupes_tries if g["position_mediane"] > p["axe1"]][:1])
            pos1, pos2 = phrase_position(p, a, i, self.d_pos, recouvre, encadrants)
            bloc = (GABARITS / "bloc-position.html").read_text()
        else:
            pos1 = pos2 = ""
            bloc = (GABARITS / "bloc-position-absente.html").read_text()

        these, these_suite = phrase_these(
            f, self.d_diss, self.d_part, rang_diss, rang_part, groupe,
            self.apercu["groupes"], self.n_texte)
        part1, part2 = phrase_participation(a, i, self.d_part, rang_part, self.n_texte)
        # Le pendant, pour la dissidence, du recouvrement calculé plus haut sur
        # les positions : combien de députés ce rang ne départage pas.
        indiscernables = 0
        if a.get("dissidence_basse") is not None:
            indiscernables = sum(
                1 for d in self.deputes
                if d.get("dissidence_basse") is not None
                and d["dissidence_haute"] >= a["dissidence_basse"]
                and d["dissidence_basse"] <= a["dissidence_haute"]
                and d["acteur_uid"] != uid)
        diss1, diss2 = phrase_dissidence(
            a, i, self.d_diss, rang_diss, groupe, indiscernables)
        ec1, ec2 = phrase_ecart(f, communs)

        # La délégation : le taux vient de `vues.py`, jamais d'une division
        # refaite ici. Un député sans suffrage exprimé n'en a pas, et la bande
        # ne le situe pas — comme pour la présence, une case vide vaut mieux
        # qu'un zéro qui affirmerait une mesure.
        part_deleg = a.get("part_delegation_engageants")
        rang_deleg = rang_parmi(self.d_deleg["valeurs"], part_deleg or 0)
        deleg1, deleg2 = phrase_delegation(a, i, self.d_deleg, rang_deleg, groupe)

        journal = self.donnees.votes_engageants(uid)
        jour1, jour2 = phrase_journal(journal["resume"], i)

        am = f["amendements"] or {}
        rang_amd = rang_parmi(self.d_amd["valeurs"], am.get("deposes") or 0) if self.d_amd else 0
        amd1, amd2 = (
            phrase_amendements(am, i, self.d_amd, rang_amd, groupe)
            if self.d_amd else ("", ""))

        # On surligne dans les deux colonnes le binôme de tête des votes qui engagent :
        # c'est lui qui rend l'écart entre les deux assiettes visible d'un coup d'œil.
        pt = f["proches"]["texte"]
        surligne = pt[0]["nom_complet"] if pt else None

        profession = i.get("profession") or ""
        identite = ", ".join(x for x in [
            f"{i['age']} ans" if i.get("age") else "",
            profession[0].lower() + profession[1:] if profession else "",
        ] if x)
        identite = (identite + ", " if identite else "") + phrase_mandat(i) + "."

        # Le bloc des amendements disparaît si la table n'a pas été construite —
        # elle pèse 300 Mo au téléchargement, et un site généré sans elle reste
        # un site. Ce qui ne se ferait pas : afficher la section avec des tirets,
        # qui ferait lire « zéro amendement » à une absence de données.
        bloc_amd = (GABARITS / "bloc-amendements.html").read_text() if self.d_amd else ""
        corps = (
            (GABARITS / "fiche.html").read_text()
            .replace("{{BLOC_POSITION}}", bloc)
            .replace("{{BLOC_AMENDEMENTS}}", bloc_amd)
        )

        jetons = {
            "FIL": echapper(f"Députés · {i['departement']} · "
                            f"{i['num_circo']}{ordinal(i['num_circo'])} circonscription"),
            "NOM": echapper(i["nom_complet"]),
            "NOM_COURT": echapper(i["nom"]),
            "PORTRAIT": portrait_html(uid),
            "GROUPE": echapper(i["groupe"]),
            "GROUPE_LIBELLE": echapper(i["groupe_libelle"] or i["groupe"]),
            "GROUPE_AUTRES": num(max((groupe["effectif_actuel"] if groupe else 1) - 1, 0)),
            "GROUPE_MEDIANE": dec(groupe["position_mediane"]) if groupe and groupe.get("position_mediane") is not None else "—",
            "GROUPE_MIN": dec(groupe["position_min"]) if groupe and groupe.get("position_min") is not None else "—",
            "GROUPE_MAX": dec(groupe["position_max"]) if groupe and groupe.get("position_max") is not None else "—",
            "ETAT": "en exercice" if i["en_exercice"] else "mandat terminé",
            "IDENTITE_PHRASE": echapper(identite),
            "THESE": these,
            "THESE_SUITE": these_suite,
            "BILAN": bilan_html(f["bilan"]),
            "SOLENNELS": solennels_html(f["solennels"], self.apercu),
            "HATVP": i.get("uri_hatvp") or "https://www.hatvp.fr",
            "BOOTSTRAP": num(self.apercu.get("bootstrap") or BOOTSTRAP),

            # Une mesure qui n'existe pas ne s'affiche pas à zéro. Un député
            # dont la source ne publie aucun vote nominatif portait « 0,0 % »
            # en corps 88 juste au-dessus du paragraphe qui explique qu'on
            # préfère une case vide à un zéro trompeur.
            "PART_ENG_VALEUR": grand_chiffre(
                pct(taux) if a["votes_exprimes"] else None, "%"),
            "PART_ENG_N": num(exprimes),
            "PART_ENG_DENOM": num(votables),
            # Le dénominateur affiché est celui qui a été divisé, et les deux
            # retraits qui l'ont formé sont nommés : sans cela le chiffre paraît
            # faux à quiconque compare aux totaux de la législature.
            "PART_ENG_RESERVE": reserve_denominateur(
                eligibles, votables, self.n_texte),
            # La délégation qualifie le numérateur, pas le dénominateur : elle
            # s'affiche donc accolée aux votes exprimés, et se déplie dans la
            # mesure qui lui est consacrée juste en dessous.
            "PART_ENG_DELEGATION": mention_delegation(
                a.get("engageants_delegues") or 0, exprimes),
            # La bande situe un député dans la population. Elle n'a donc rien à
            # dessiner quand le taux ne s'y compare pas : le losange irait se
            # poser hors de l'étendue des barres et étirerait l'échelle des 577
            # pour loger un chiffre calculé sur quinze votes.
            "PART_BANDE": (
                f'<div class="bande" data-bande="participation"\n'
                f'         data-valeur="{taux:.4f}" data-etiquette="{echapper(i["nom"])}"\n'
                f'         data-format="pct"></div>\n'
                f'    <div class="legende-bande">\n'
                f'      <span><i>Une barre = un député en exercice</i></span>\n'
                f'      <span><i>Trait pointillé = médiane de l\'Assemblée</i></span>\n'
                f'    </div>'
                if a.get("participation_comparable") else
                '<p class="provenance">Pas de comparaison à la population&nbsp;: '
                'le dénominateur est trop petit pour situer ce taux parmi les autres.</p>'
            ),
            "PART_TOUS": f"{taux:.4f}",
            "PART_TOUS_PCT": pct(a["participation"] or 0),
            "PART_PHRASE": part1,
            "PART_PHRASE_2": part2,

            # Sans suffrage exprimé, il n'y a pas de part à calculer : le
            # dénominateur est nul, et `vues.py` sert `null` plutôt qu'un zéro.
            "DELEG_VALEUR": grand_chiffre(
                pct(part_deleg) if part_deleg is not None else None, "%"),
            "DELEG_PROVENANCE": provenance_delegation(
                a.get("engageants_delegues") or 0, exprimes),
            "DELEG_PHRASE": deleg1,
            "DELEG_PHRASE_2": deleg2,
            # Même réserve que pour la présence : une part mesurée sur quinze
            # suffrages ne se range pas parmi les 577, et un député sans suffrage
            # exprimé n'a pas de part du tout.
            "DELEG_BANDE": (
                f'<div class="bande" data-bande="delegation"\n'
                f'         data-valeur="{part_deleg:.4f}" data-etiquette="{echapper(i["nom"])}"\n'
                f'         data-format="pct"></div>\n'
                f'    <div class="legende-bande">\n'
                f'      <span><i>Une barre = un député en exercice</i></span>\n'
                f'      <span><i>Trait pointillé = médiane de l\'Assemblée</i></span>\n'
                f'    </div>'
                if part_deleg is not None and a.get("participation_comparable") else
                '<p class="provenance">Pas de comparaison à la population&nbsp;: '
                'trop peu de suffrages exprimés pour situer cette part parmi les autres.</p>'
            ),

            "JOURNAL": journal_html(journal["votes"]),
            "JOURNAL_FILTRES": filtres_html(journal["resume"]),
            "JOURNAL_PHRASE": jour1,
            "JOURNAL_PHRASE_2": jour2,

            "AMD_N": num(am.get("deposes") or 0),
            "AMD_PROVENANCE": provenance_amendements(
                am.get("deposes") or 0, am.get("examines") or 0,
                am.get("adoptes") or 0),
            "AMD_COSIGNES": num(am.get("cosignes") or 0),
            "AMD_SORTS": sorts_html(am) if am.get("deposes") else "",
            "AMD_PHRASE": amd1,
            "AMD_PHRASE_2": amd2,
            "AMD_BANDE": (
                f'<div class="bande" data-bande="amendements"\n'
                f'         data-valeur="{am["deposes"]:.4f}" data-etiquette="{echapper(i["nom"])}"\n'
                f'         data-format="num"></div>\n'
                f'    <div class="legende-bande">\n'
                f'      <span><i>Une barre = un député en exercice</i></span>\n'
                f'      <span><i>Trait pointillé = médiane de l\'Assemblée</i></span>\n'
                f'    </div>'
                if am.get("deposes") else
                '<p class="provenance">Aucun dépôt&nbsp;: rien à situer dans la population.</p>'
            ),

            # Même règle : un groupe qui n'a eu de ligne sur aucun scrutin où ce
            # député a voté ne donne pas une dissidence de 0 %, il n'en donne
            # aucune. Le tiret le dit, le zéro l'aurait démenti.
            "DISS_VALEUR": grand_chiffre(
                pct(a["taux_dissidence"]) if a["taux_dissidence"] is not None else None,
                "%"),
            "DISS_N": num(a["votes_dissidents"] or 0),
            "DISS_DENOM": num(a["votes_avec_ligne"] or 0),
            # La bande ne situe que ce qui est mesuré. Elle recevait `0` quand le
            # taux était absent, et posait donc le losange à l'extrémité
            # « le plus discipliné de l'Assemblée » sous une phrase disant qu'il
            # n'y avait rien à compter — l'aria-label allait jusqu'à énoncer
            # « Bouloux : 0,0 % ». Une valeur vide efface la bande ; c'est la
            # même règle que `grand_chiffre`, appliquée au dispositif d'à côté.
            "DISS_VAL": (
                f"{a['taux_dissidence']:.4f}"
                if a["taux_dissidence"] is not None and a.get("dissidence_comparable")
                else ""),
            # L'intervalle est affiché sous le taux, dans la même colonne de
            # provenance que le dénominateur : c'est là qu'on lit ce que le
            # chiffre vaut, pas dans une note de bas de page.
            "DISS_IC": (
                "" if a.get("dissidence_basse") is None else
                f"<br>intervalle à 90{NBSP}%&nbsp;: {pct(a['dissidence_basse'])}"
                f"{NBSP}% à {pct(a['dissidence_haute'])}{NBSP}%"
            ),
            "DISS_PHRASE": diss1,
            "DISS_PHRASE_2": diss2,

            "POS_VAL": dec(p["axe1"]) if estimee else "—",
            "POS_VAL_BRUT": f"{p['axe1']:.4f}" if estimee else "0",
            "POS_BAS": dec(p["borne_basse"]) if estimee else "—",
            "POS_BAS_BRUT": f"{p['borne_basse']:.4f}" if estimee else "0",
            "POS_HAUT": dec(p["borne_haute"]) if estimee else "—",
            "POS_HAUT_BRUT": f"{p['borne_haute']:.4f}" if estimee else "0",
            "POS_RANG": num(p["rang"]) if estimee else "—",
            "POS_CLASSES": num(p["classes"]) if estimee else "—",
            # Les votes qui ont réellement situé ce député. La fiche annonçait
            # ses votes sur les scrutins qui engagent — plus nombreux, puisque
            # le modèle est binaire et qu'il écarte des scrutins entiers.
            "POS_N_VOTES": num(p.get("votes_modele") or 0),
            "POS_IL": accords(i)["il"],
            "MODELE_SCRUTINS": num(self.modele["scrutins_retenus"]),
            "POS_PHRASE": pos1,
            "POS_PHRASE_2": pos2,
            "POS_ECART_GROUPE": phrase_ecart_groupe(p, i, groupe) if estimee else "",
            "POS_INCERTITUDE": phrase_incertitude(p, i, a, self.d_pos) if estimee else "",

            "PROCHES_TOUS": rangs_html(f["proches"]["tous"], self.adresse, self.publiees, surligne),
            "PROCHES_TEXTE": rangs_html(f["proches"]["texte"], self.adresse, self.publiees, surligne),
            "ECART_VERDICT": ec1,
            "ECART_EXPLIC": ec2,

            "DONNEES_JSON": self.donnees_json,
        }
        return self.page(
            corps, jetons,
            titre=f"{i['nom_complet']} — Radar parlementaire",
            description=(f"{i['nom_complet']}, député{'e' if i.get('civilite') == 'Mme' else ''} "
                         f"de {i['departement']} ({i['groupe']}) : présence aux votes, écarts à la "
                         f"ligne de son groupe et position estimée, chaque chiffre avec son dénominateur."),
            chemin=self.adresse[uid], onglet="deputes")

    # -- accueil et annuaire ----------------------------------------------

    def accueil(self) -> str:
        corps = (GABARITS / "accueil.html").read_text()
        return self.page(
            corps,
            {
                "LIGNES_GROUPES": lignes_groupes_html(self.apercu["groupes"]),
                # Le même index que l'annuaire : une seule recherche, un seul
                # jeu de résultats. Cf. `recherche()` dans `radar.js`.
                "ANNUAIRE_JSON": self.index_annuaire(),
            },
            titre="Radar parlementaire — ce que fait votre député, avec le dénominateur",
            description=("Chaque chiffre sur l'activité des députés, recalculé depuis les votes "
                         "nominatifs de l'Assemblée nationale et affiché avec son dénominateur."),
            chemin="", onglet="accueil")

    def index_annuaire(self) -> str:
        """L'index des députés, servi tel quel à la recherche.

        Les trois signaux sont **déjà mis en forme ici** : le pourcentage, la
        virgule décimale et l'espace insécable sont du français, pas du calcul,
        et `radar.js` n'a ni à les fabriquer ni à savoir qu'un taux manquant
        n'est pas un zéro. Une valeur absente sort en chaîne vide, et la liste
        affiche « — ».
        """
        def mesure(valeur, forme) -> str:
            return "" if valeur is None else forme(valeur)

        index = [{
            "u": self.adresse[d["acteur_uid"]].removesuffix(".html"),
            "n": d["nom_complet"],
            "d": d["departement"] or "",
            "dn": str(d["num_departement"] or ""),
            "c": str(d["num_circo"] or ""),
            "r": self.region.get(d["acteur_uid"], ""),
            "g": d["groupe"] or "",
            "gl": d["groupe_libelle"] or "",
            "pres": mesure(d.get("participation_engageants"),
                           lambda v: f"{pct(v, 0)}{NBSP}%"),
            "ecart": mesure(d.get("taux_dissidence"), lambda v: f"{pct(v, 1)}{NBSP}%"),
            "pos": mesure(d.get("axe1"), dec),
        } for d in sorted(self.deputes, key=lambda x: x["nom_complet"])]
        return json.dumps(index, ensure_ascii=False, separators=(",", ":"))

    def annuaire(self) -> str:
        corps = (GABARITS / "annuaire.html").read_text()
        return self.page(
            corps,
            {
                "ANNUAIRE_JSON": self.index_annuaire(),
                "INDEX_DEPARTEMENTS": index_departements_html(self.deputes, self.adresse),
            },
            titre="Trouver un député — Radar parlementaire",
            description=("Cherchez votre député par nom, département, région ou groupe "
                         "parlementaire, et lisez ses chiffres avec leur dénominateur."),
            chemin="deputes.html", onglet="deputes")

    # -- les votes ---------------------------------------------------------

    def votes(self) -> str:
        """L'onglet « Les votes » : le miroir de l'annuaire, côté scrutins.

        L'annuaire liste les députés que les fiches détaillent&nbsp;; celui-ci
        liste les votes que les pages de scrutin détaillent. Même recherche,
        même index HTML de repli, mêmes règles sur les chiffres.
        """
        corps = (GABARITS / "votes.html").read_text()
        adoptes = sum(1 for v in self.votes_index if v["adopte"])
        return self.page(
            corps,
            {
                "VOTES_JSON": votes_json(self.votes_index),
                "INDEX_VOTES": index_votes_html(self.votes_index),
                "VOTES_ADOPTES": num(adoptes),
                "VOTES_REJETES": num(len(self.votes_index) - adoptes),
            },
            titre="Les votes qui engagent — Radar parlementaire",
            description=("Les votes sur l'ensemble d'un texte et les motions de censure "
                         "de la législature : qui a voté quoi, groupe par groupe et député "
                         "par député, avec le lien vers l'analyse de l'Assemblée."),
            chemin="votes.html", onglet="votes")

    def scrutin_page(self, v: dict) -> str:
        """La page d'un vote : le résultat, les groupes, et les 648 positions.

        Le titre du scrutin sert de `h1` tel que l'Assemblée le publie, sans
        raccourci ni résumé. « L'ensemble de la proposition de loi visant à… »
        est long et c'est très bien&nbsp;: il désigne le texte **tel qu'il
        ressort des débats**, amendements compris, et le raccourcir reviendrait
        à publier un résumé du texte sous l'autorité de la source.
        """
        corps = (GABARITS / "scrutin.html").read_text()
        d = self.donnees.scrutin(v["uid"])
        s, liens, resume = d["scrutin"], d["liens"], d["resume"]

        # Le relevé des fiches, lui, garde la phrase telle quelle : elle y
        # est une ligne de liste, pas un titre. Cf. `titre_scrutin`.
        titre = titre_scrutin(s["titre"])

        # « N'ont pas pris part » ne compte pas les hors-mandat : un député élu
        # trois mois plus tard n'a pas manqué ce vote, la question ne lui était
        # pas posée. Les inclure gonflait le compte de 69 sur un scrutin de
        # juillet 2026 et faisait un total de 648 sous une phrase qui annonçait
        # 577 sièges.
        #
        # L'effectif, lui, ne se calcule plus ici : `resume` porte `concernes`,
        # `sieges` et `vacants`, tenus par `vues.scrutin()`. Une page qui déduit
        # son propre effectif finit par en publier un que son relevé ne
        # justifie pas — c'est très exactement ce qui s'est produit.
        absents = resume["absent"] + resume["empeche"]
        jetons = {
            # `TITRE` est réservé par `page()` au titre du document : un jeton
            # de ce nom posé ici était écrasé, et le `h1` affichait le contenu
            # de l'onglet du navigateur, « — Radar parlementaire » compris.
            "TITRE_SCRUTIN": echapper(titre),
            "DATE": jour(s["date"]),
            "NUMERO": numero_scrutin(s["numero"]),
            "CATEGORIE": CATEGORIES_DITES.get(s["categorie"], s["categorie"] or ""),
            # Le mot « solennel » n'apparaît que là où il est vrai. Il est un
            # fait sur ce scrutin, pas une mesure : le site le signale et ne
            # s'en sert nulle part comme dénominateur. Cf. l'onglet des votes.
            "SOLENNEL_MARQUE": (
                " · <b>scrutin solennel</b>" if s["type_vote_code"] == "SPS" else ""),
            "SORT_PHRASE": phrase_sort(s),
            "N_POUR": num(s["n_pour"] or 0),
            "N_CONTRE": num(s["n_contre"] or 0),
            "N_ABSTENTION": num(s["n_abstention"] or 0),
            "S_ABSTENTION": pluriel(s["n_abstention"] or 0),
            "N_ABSENTS": num(absents),
            "VOTANTS": num(s["nb_votants"] or 0),
            "EXPRIMES": num(s["suffrages_exprimes"] or 0),
            "REQUIS": num(s["suffrages_requis"] or 0),
            "EFFECTIF_PHRASE": phrase_effectif_scrutin(resume, jour(s["date"])),
            "HORS_MANDAT_PHRASE": phrase_hors_mandat(resume),
            "TOUS_DEPUTES": num(resume["total"]),
            "INTERRUPTION_PHRASE": phrase_interruptions(self.apercu),
            "DELEGATION_PHRASE": phrase_delegation_scrutin(resume),
            "LOI_BLOC": f'<p class="enclair">{phrase_loi(liens, s, self.apercu)}</p>'
                        f'<p class="enclair">L\'analyse nominative publiée par '
                        f'l\'Assemblée&nbsp;: <a href="{liens["scrutin"]}">'
                        f'scrutin n{ORDINAL_MASC}&nbsp;{numero_scrutin(s["numero"])}</a>.</p>',
            "GROUPES": groupes_scrutin_html(d["groupes"]),
            "GROUPES_PHRASE": phrase_groupes_scrutin(d["groupes"], resume),
            "RELEVE": releve_html(d["votes"], self.adresse, self.publiees),
            "RELEVE_FILTRES": filtres_html(resume),
            "RELEVE_PHRASE": phrase_releve(resume, self.apercu),
        }
        loi = liens["dossier_titre"] or titre
        return self.page(
            corps, jetons,
            titre=f"Scrutin n{ORDINAL_MASC} {s['numero']} — {loi} — Radar parlementaire",
            description=(f"{jour(s['date'])} : {titre[:120]}. "
                         f"{s['n_pour'] or 0} pour, {s['n_contre'] or 0} contre. "
                         f"Qui a voté quoi, groupe par groupe et député par député."),
            chemin=adresse_scrutin(v), onglet="votes")


def ecrire_index_moteurs(uids: list[str], scrutins: list[str] = ()) -> None:
    """`sitemap.xml` et `robots.txt`.

    Sans sitemap, un moteur découvre les fiches en suivant les liens de
    l'annuaire — et ignore le PDF de la note, qu'aucune page n'atteint par un
    chemin qu'il sait remonter. Or c'est précisément le document qu'on veut
    trouvable quand quelqu'un cherche comment un chiffre a été obtenu.
    """
    aujourdhui = date.today().isoformat()
    urls = ["", "deputes.html", "votes.html", "carte.html", "methode.html",
            "corrections.html", "mentions.html"]
    urls += list(scrutins)
    if NOTE_SOURCE.exists():
        urls.append(NOTE_CHEMIN)
    urls += list(uids)   # déjà des adresses complètes, cf. Site.adresse

    lignes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lignes.append(
            f"  <url><loc>{BASE}/{u}</loc><lastmod>{aujourdhui}</lastmod></url>")
    lignes.append("</urlset>")
    (SORTIE / "sitemap.xml").write_text("\n".join(lignes) + "\n")

    (SORTIE / "robots.txt").write_text(
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {BASE}/sitemap.xml\n")
    print(f"  · sitemap.xml ({len(urls)} URL) et robots.txt")


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    # Le défaut est celui de `radar`, pas un second nombre écrit ici : les deux
    # avaient divergé une fois déjà, et c'est la page Méthode qui l'annonce.
    parseur.add_argument("--bootstrap", type=int, default=BOOTSTRAP,
                         help=f"rééchantillonnages pour les intervalles "
                              f"(défaut {BOOTSTRAP}, 0 = sans intervalle)")
    parseur.add_argument("--limite", type=int, default=0,
                         help="ne générer que N fiches, pour une mise au point rapide")
    parseur.add_argument("--servir", action="store_true",
                         help="servir sortie/ après génération, comme le fera l'hébergeur")
    parseur.add_argument("--port", type=int, default=8000, help="port du mode --servir")
    parseur.add_argument("--note-perimee", action="store_true",
                         help="générer malgré un PDF de note plus ancien que son source")
    args = parseur.parse_args()

    # Le contrôle vient avant les trente secondes de calcul, et il arrête tout :
    # une annexe en retard sur le code publie, sous l'autorité du document de
    # référence, une définition que le site n'applique plus. Une commande le
    # règle, et l'échappatoire existe pour qui n'a pas de moteur TeX sous la main.
    if note_perimee() and not args.note_perimee:
        raise SystemExit(
            f"La note méthodologique est en retard sur son source.\n"
            f"  {NOTE_TEX} a changé après {NOTE_SOURCE}\n\n"
            f"Le site servirait un document de référence qui contredit ses "
            f"propres chiffres. Recompiler d'abord :\n\n"
            f"    cd docs && latexmk -pdf note-methodologique.tex\n\n"
            f"…ou passer --note-perimee pour générer quand même."
        )

    debut = time.monotonic()
    donnees = Donnees.construire(bootstrap=args.bootstrap, journal=lambda m: print(f"  · {m}"))

    # Les invariants des données, avant d'écrire une seule page. Ils ne testent
    # pas le code — les tests s'en chargent — mais les *chiffres publiables* :
    # au plus 577 députés en fonction un jour donné, un siège par personne, un
    # relevé qui s'additionne, des totaux égaux à ceux de l'Assemblée.
    #
    # Le site a publié pendant toute sa vie des pages annonçant « les 581
    # députés dont le mandat courait », sous un régime qui en compte 577, sans
    # qu'aucun des 230 tests ne s'en aperçoive : un effectif faux ne contredit
    # aucune formule, il ne se voit qu'en comptant. D'où ce garde-fou, et d'où
    # le fait qu'il **arrête** la génération au lieu d'avertir. Un site qui ne
    # sait pas compter ses députés n'est pas discutable, il est réfuté.
    controles.exiger(donnees.cube)
    print(f"  · contrôles des données : {len(CONTROLES)} invariants vérifiés")

    site = Site(donnees)

    # La région n'est pas dans `liste_deputes` : on la prend à la source.
    site.region = dict(donnees.deputes.select(["acteur_uid", "region"]).iter_rows())

    SORTIE.mkdir(exist_ok=True)
    # Les pages d'une génération précédente sont effacées avant d'écrire les
    # nouvelles. Sans cela, une page retirée du site — ou renommée — restait
    # dans `sortie/` et partait chez l'hébergeur à la publication suivante :
    # c'est ainsi qu'une page « Soutenir » abandonnée est restée en ligne,
    # sans lien entrant, absente du plan du site, avec un appel au don que
    # plus rien dans le projet ne soutenait. Une page que le générateur ne
    # produit plus ne doit exister nulle part.
    #
    # Seuls les HTML de la racine sont concernés : `.vercel` porte l'identité
    # du projet chez l'hébergeur, et `statique/` et `methode/` sont réécrits
    # juste après.
    perimes = [f for f in SORTIE.glob("*.html")]
    for f in perimes:
        f.unlink()
    if perimes:
        print(f"  · {len(perimes)} pages de la génération précédente effacées")
    if (SORTIE / "statique").exists():
        shutil.rmtree(SORTIE / "statique")
    shutil.copytree(STATIQUE, SORTIE / "statique")
    # Les images de marque vivent dans `assets/` — elles ne sont ni du style ni
    # du comportement — mais elles sont servies depuis `statique/`, parce que
    # c'est le seul dossier que l'hébergeur voit.
    #
    # Seuls les fichiers de premier niveau sont copiés. `assets/sources/` garde
    # les originaux dont sont tirés les fichiers publiés : ils appartiennent au
    # dépôt, pas au site. La distinction n'est pas cosmétique — les planches
    # d'origine du logo comportent le bloc-marque de l'État, réservé aux
    # entités publiques, et les recopier telles quelles les aurait mises en
    # ligne sur radar-parlementaire.fr.
    for image in sorted(ASSETS.glob("*")):
        if image.is_file():
            shutil.copy2(image, SORTIE / "statique" / image.name)

    publies, poids = publier_portraits(site.publiees)
    if publies:
        manquants = len(site.publiees) - publies
        # Le nombre manquant se dit. Les portraits ne viennent d'aucun jeu de
        # données : ils tiennent à une convention d'adresse du site
        # institutionnel, et le jour où elle change, c'est ce compte qui le
        # signalera — pas 577 fiches devenues muettes sans que rien ne parle.
        print(f"  · {publies} portraits ({poids / 1e6:.1f} Mo)"
              + (f", {manquants} sans photo à la source" if manquants else ""))
    else:
        print("  · aucun portrait publié — lancer `uv run radar portraits`")

    if NOTE_SOURCE.exists():
        (SORTIE / "methode").mkdir(exist_ok=True)
        shutil.copy2(NOTE_SOURCE, SORTIE / NOTE_CHEMIN)
        print(f"  · note méthodologique ({NOTE_SOURCE.stat().st_size / 1e6:.1f} Mo)")
    else:
        print(f"  · note méthodologique absente ({NOTE_SOURCE}) — encart omis")

    (SORTIE / "index.html").write_text(site.accueil())
    (SORTIE / "deputes.html").write_text(site.annuaire())
    (SORTIE / "votes.html").write_text(site.votes())
    (SORTIE / "carte.html").write_text(site.carte())
    (SORTIE / "methode.html").write_text(site.methode())
    (SORTIE / "mentions.html").write_text(site.mentions())
    (SORTIE / "corrections.html").write_text(site.corrections_page())
    print(f"  · accueil, annuaire, votes, carte, méthode, mentions et corrections "
          f"({len(site.corrections)} entrées au registre)")

    # Les pages de vote. Elles sont écrites avant les fiches parce que
    # `--limite` ne les concerne pas : la mise au point d'une fiche n'a aucune
    # raison de faire disparaître l'onglet des votes qu'elle référence.
    votes_cibles = site.votes_index[: args.limite] if args.limite else site.votes_index
    for n, v in enumerate(votes_cibles, 1):
        (SORTIE / adresse_scrutin(v)).write_text(site.scrutin_page(v))
        if n % 50 == 0 or n == len(votes_cibles):
            print(f"  · votes {n}/{len(votes_cibles)}")

    cibles = site.deputes[: args.limite] if args.limite else site.deputes
    for n, d in enumerate(cibles, 1):
        uid = d["acteur_uid"]
        (SORTIE / site.adresse[uid]).write_text(site.fiche(uid))
        # L'ancienne adresse `PA267780.html` reste servie : elle est en ligne,
        # indexée, et citée dans des liens qu'on ne contrôle pas. Un site
        # statique n'a pas de 301 à offrir, mais une page de renvoi porte le
        # `canonical` qui dit aux moteurs laquelle des deux compte.
        (SORTIE / f"{uid}.html").write_text(redirection(site.adresse[uid]))
        if n % 100 == 0 or n == len(cibles):
            print(f"  · fiches {n}/{len(cibles)}")

    ecrire_index_moteurs(
        [site.adresse[d["acteur_uid"]] for d in cibles],
        [adresse_scrutin(v) for v in votes_cibles],
    )

    poids = sum(f.stat().st_size for f in SORTIE.rglob("*") if f.is_file())
    pages = len(list(SORTIE.glob("*.html")))
    print(f"\n{pages} pages écrites dans {SORTIE} "
          f"({poids / 1e6:.1f} Mo, {time.monotonic() - debut:.0f} s)")

    if not args.servir:
        print(f"→ ouvrir {SORTIE / 'index.html'}")
        return

    from serveur import ouvrir

    ouvrir(SORTIE, port=args.port)


if __name__ == "__main__":
    main()
