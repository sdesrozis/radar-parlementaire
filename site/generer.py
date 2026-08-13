"""Génère le site public du Radar parlementaire.

    uv run python site/generer.py

Architecture — et elle ne se négocie pas :

    Python (ici + radar/)      → tous les chiffres, toutes les distributions
    site/gabarits/*.html       → la structure, écrite une fois
    site/statique/*            → le système visuel et le comportement, écrits une fois
    la couche éditoriale (ici) → les phrases, choisies selon ce que disent les chiffres

Aucun chiffre n'est saisi à la main. Aucune phrase portant un jugement relatif
n'est figée : « au-dessus de la médiane » se calcule, on ne l'écrit pas d'avance.

Le générateur ne dépend d'aucun serveur : il construit les données en mémoire
une seule fois, puis écrit les 577 fiches. C'est ce qui rend la mise à jour
tenable — `radar update` puis cette commande, et le site est à jour.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from radar.site import Donnees

ICI = Path(__file__).parent
GABARITS = ICI / "gabarits"
STATIQUE = ICI / "statique"
SORTIE = ICI / "sortie"
NBSP = " "


# ── mise en forme française ────────────────────────────────────────────────

def pct(x: float, dec_: int = 1) -> str:
    return f"{100 * x:.{dec_}f}".replace(".", ",")


def dec(x: float, n: int = 2) -> str:
    return f"{x:.{n}f}".replace(".", ",")


def num(n: float) -> str:
    return f"{int(n):,}".replace(",", NBSP)


MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def jour(iso: str) -> str:
    a, m, j = str(iso).split("-")
    return f"{int(j)}{NBSP}{MOIS[int(m) - 1]}{NBSP}{a}"


def ordinal(n: str | int) -> str:
    return "re" if str(n) == "1" else "e"


def echapper(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── distributions : la matière de la bande des 577 ─────────────────────────

def distribution(valeurs) -> dict:
    v = sorted(round(float(x), 4) for x in valeurs)
    return {"valeurs": v, "min": v[0], "max": v[-1], "mediane": v[len(v) // 2], "n": len(v)}


def rang_parmi(valeurs: list[float], x: float) -> int:
    """Combien de députés se situent au-dessus."""
    return sum(1 for v in valeurs if v > x)


def participation_engageants(donnees: Donnees) -> dict[str, tuple[float, int, int]]:
    """Présence aux seuls votes qui engagent, pour tous les députés.

    Reprend la définition de `site._statistiques_deputes` : le dénominateur ne
    compte que les scrutins où le mandat courait. Le cube « texte » est déjà
    construit par `Donnees.construire` — on ne le recalcule pas.
    """
    cube = donnees.cube_texte
    exprimes = (cube.exprime & cube.eligible).sum(axis=1)
    eligibles = cube.eligible.sum(axis=1).astype(float)
    taux = np.divide(exprimes, eligibles,
                     out=np.full(cube.n_deputes, np.nan), where=eligibles > 0)
    return {
        uid: (float(t), int(e), int(el))
        for uid, t, e, el in zip(cube.deputes["acteur_uid"].to_list(), taux, exprimes, eligibles)
        if not np.isnan(t)
    }


# ── couche éditoriale : les phrases se choisissent ─────────────────────────

def accords(identite: dict) -> dict[str, str]:
    """Les accords en genre, pris de la civilité publiée par l'Assemblée.

    243 des 648 députés de la législature sont des femmes. Écrire « il » partout
    serait faux sur près de quatre fiches sur dix — un site qui prétend à
    l'exactitude des chiffres ne peut pas se permettre l'à-peu-près sur les mots.
    """
    f = identite.get("civilite") == "Mme"
    return {
        "il": "elle" if f else "il",
        "Il": "Elle" if f else "Il",
        "lui": "elle" if f else "lui",
        "depute": "députée" if f else "député",
        "present": "présente" if f else "présent",
        "assidu": "assidue" if f else "assidu",
        "elu": "élue" if f else "élu",
        "e": "e" if f else "",
    }


def combien(n: int, total: int, singulier: str, pluriel: str) -> str:
    """« Seuls 12 députés » se dit, « seuls 184 députés » non.

    Le superlatif doit rester proportionnel : au-delà d'un dixième de
    l'Assemblée, le fait n'est plus rare et la phrase ne doit plus le prétendre.
    """
    if n == 0:
        return f"aucun autre député ne {singulier}"
    if n == 1:
        return f"un seul autre député {singulier}"
    if n <= 0.1 * total:
        return f"seuls {num(n)} députés sur {num(total)} {pluriel}"
    if n <= 0.35 * total:
        return f"{num(n)} députés sur {num(total)} {pluriel}"
    return f"{num(n)} députés sur {num(total)} {pluriel} également"


def situer(x: float, mediane: float, au_dessus: str, au_dessous: str, autour: str,
           seuil: float = 0.1) -> str:
    """Un chiffre ne se juge pas, il se situe. Trois formulations, jamais un verdict."""
    ecart = (x - mediane) / (abs(mediane) or 1)
    if ecart > seuil:
        return au_dessus
    if ecart < -seuil:
        return au_dessous
    return autour


def phrase_participation(a: dict, i: dict, dist: dict, rang_sup: int,
                         n_texte: int) -> tuple[str, str]:
    g = accords(i)
    if not a["votes_exprimes"]:
        return ("Aucun vote nominatif de ce député ne figure dans les données publiées "
                "par l'Assemblée nationale&nbsp;: il n'y a pas de taux à calculer.",
                "Nous préférons une case vide à un zéro trompeur. Un taux de 0&nbsp;% "
                "affirmerait une absence constatée&nbsp;; or nous ne constatons ici "
                "qu'un silence de la source.")
    situation = situer(
        a["participation_engageants"], dist["mediane"],
        f"C'est <b>au-dessus</b> de la médiane de l'Assemblée, qui est de {pct(dist['mediane'])}{NBSP}%.",
        f"C'est <b>en dessous</b> de la médiane de l'Assemblée, qui est de {pct(dist['mediane'])}{NBSP}%{NBSP}: "
        f"{num(rang_sup)} députés votent plus souvent qu'{g['il']}.",
        f"C'est <b>dans la moyenne</b> de l'Assemblée, dont la médiane est à {pct(dist['mediane'])}{NBSP}%.",
    )
    elig = a["engageants_eligibles"]
    assiette = (f"Sur les {num(elig)} votes qui engagent réellement — l'ensemble d'un texte, "
                f"une motion de censure — tenus depuis son entrée en fonction"
                if elig < n_texte else
                f"Sur les {num(elig)} votes qui engagent réellement — l'ensemble d'un texte, "
                f"une motion de censure —")
    p1 = (f"{assiette}, {g['il']} en a exprimé <b>{num(a['votes_engageants'])}</b>. {situation}")
    p2 = (f"L'étalon n'est pas 100{NBSP}%. Le député le plus assidu de la législature "
          f"atteint {pct(dist['max'])}{NBSP}%, et le député médian manque "
          f"{dec(10 * (1 - dist['mediane']), 0)} votes qui engagent sur dix. "
          f"Un taux se lit contre cette réalité, pas contre un idéal.")
    return p1, p2


def phrase_dissidence(a: dict, i: dict, dist: dict, au_dessus: int,
                      groupe: dict | None) -> tuple[str, str]:
    g = accords(i)
    if not a["votes_avec_ligne"] or a["taux_dissidence"] is None:
        return (f"Son groupe n'a eu de ligne identifiable sur aucun scrutin où {g['il']} a voté&nbsp;: "
                "il n'y a donc rien à compter ici.", "")
    facteur = a["taux_dissidence"] / dist["mediane"] if dist["mediane"] else 0
    p1 = (f"<b>{num(a['votes_dissidents'])} fois sur {num(a['votes_avec_ligne'])}</b>, "
          f"{g['il']} a voté autrement que la majorité de son groupe. La médiane de l'Assemblée est à "
          f"{pct(dist['mediane'])}{NBSP}%{NBSP}: {g['il']} s'en écarte donc environ "
          f"<b>{dec(facteur, 1)} fois plus souvent</b> que le député médian, et "
          f"{combien(au_dessus, dist['n'], 'le fait davantage', 'le font davantage')}.")

    # Le contexte du groupe renverse souvent la lecture : on le donne toujours.
    if groupe and groupe["dissidence_moyenne"] > a["taux_dissidence"]:
        p2 = (f"Ce chiffre demande aussitôt une correction. Dans son groupe, {i['groupe']}, "
              f"la dissidence atteint {pct(groupe['dissidence_moyenne'])}{NBSP}% en moyenne "
              f"et la cohésion interne tombe à {pct(groupe['cohesion'])}{NBSP}%{NBSP}: "
              f"<b>chez {g['lui']}, {echapper(i['nom'])} est en dessous de la moyenne.</b>")
    elif groupe:
        p2 = (f"Son groupe, {i['groupe']}, affiche {pct(groupe['dissidence_moyenne'])}{NBSP}% de "
              f"dissidence moyenne et {pct(groupe['cohesion'])}{NBSP}% de cohésion interne{NBSP}: "
              f"l'écart se joue donc bien à l'échelle de la personne, pas du groupe.")
    else:
        p2 = ""
    return p1, p2


def phrase_position(p: dict, a: dict, i: dict, dist: dict, recouvre: int,
                    encadrants: list[dict]) -> tuple[str, str]:
    g = accords(i)
    reperes = ", ".join(f"{g['groupe']} ({dec(g['position_mediane'])})" for g in encadrants)
    situation = (f"{g['Il']} se situe entre les médianes de {reperes}." if len(encadrants) > 1
                 else f"{g['Il']} se situe au-delà de la médiane de {reperes}." if encadrants
                 else f"{g['Il']} se situe à l'extrémité de l'axe.")
    p1 = (f"Ses votes sur les textes qui engagent le placent à <b>{dec(p['axe1'])}</b> sur l'axe "
          f"principal, quand la médiane de l'Assemblée est à {dec(dist['mediane'])}. {situation}")

    largeur = p["borne_haute"] - p["borne_basse"]
    etendue = dist["max"] - dist["min"]
    qualificatif = ("large" if largeur > 0.22 * etendue
                    else "étroit" if largeur < 0.08 * etendue else "moyen")
    p2 = (f"Son intervalle est {qualificatif} — de {dec(p['borne_basse'])} à "
          f"{dec(p['borne_haute'])} — parce qu'{g['il']} a exprimé {num(a['votes_engageants'])} votes "
          f"qui engagent. <b>{g['Il']} est indiscernable de {num(recouvre)} autres députés</b>, dont "
          f"l'intervalle recouvre le sien. Le rang n'a donc de sens qu'à cette réserve près.")
    return p1, p2


def phrase_ecart_groupe(p: dict, i: dict, groupe: dict | None) -> str:
    """Situe le député dans son propre groupe : le raccourci « il pense comme son groupe »."""
    g = accords(i)
    if not groupe or groupe.get("position_mediane") is None:
        return ""
    d = p["axe1"] - groupe["position_mediane"]
    if abs(d) < 0.05:
        return f"{g['Il']} est <b>au centre de son groupe</b>, à {dec(abs(d))} de sa médiane."
    cote = "au-delà" if d > 0 else "en deçà"
    dehors = (p["axe1"] > groupe["position_max"] or p["axe1"] < groupe["position_min"])
    if dehors:
        return (f"{g['Il']} est <b>hors de l'étendue de son propre groupe</b>, à {dec(abs(d))} "
                f"{cote} de sa médiane.")
    return f"{g['Il']} se tient à <b>{dec(abs(d))} {cote}</b> de la médiane de son groupe."


def phrase_incertitude(p: dict, i: dict, a: dict, dist: dict) -> str:
    g = accords(i)
    largeur = p["borne_haute"] - p["borne_basse"]
    etendue = dist["max"] - dist["min"]
    if largeur > 0.22 * etendue:
        return (f"L'intervalle de {echapper(i['nom'])} est large ({dec(p['borne_basse'])} à "
                f"{dec(p['borne_haute'])})&nbsp;: {g['il']} a exprimé {num(a['votes_engageants'])} votes "
                f"qui engagent, ce qui laisse au modèle peu de matière. Le rang {num(p['rang'])} "
                f"doit être lu avec cette réserve.")
    return (f"L'intervalle de {echapper(i['nom'])} est resserré ({dec(p['borne_basse'])} à "
            f"{dec(p['borne_haute'])}), parce qu'{g['il']} a exprimé {num(a['votes_engageants'])} votes "
            f"qui engagent&nbsp;: le modèle a de la matière. Le rang {num(p['rang'])} reste "
            f"néanmoins indicatif.")


def phrase_these(f: dict, d_diss: dict, d_part: dict, rang_diss: int, rang_part: int,
                 groupe: dict | None, groupes: list[dict], n_texte: int) -> tuple[str, str]:
    """La thèse porte sur ce que ce député a de plus remarquable — pas toujours la même mesure.

    Un site qui ouvrirait toujours sur la dissidence produirait 577 fiches identiques,
    et raconterait n'importe quoi pour les députés parfaitement disciplinés. On classe
    donc les angles par écart à la médiane, et on prend le plus fort.
    """
    a, i = f["activite"], f["identite"]
    g = accords(i)
    n = d_diss["n"]
    nom = echapper(i["nom_complet"])

    # Aucun vote nominatif dans les données. Afficher « 0 % de présence » serait
    # l'accusation la plus grave du site, portée sur ce qui est peut-être une
    # lacune de la source. On décrit l'absence de donnée, pas un comportement.
    if not a["votes_exprimes"]:
        return (f"Les données publiées par l'Assemblée ne contiennent "
                f"<em>aucun vote nominatif</em> pour {nom}, alors que son mandat court "
                f"depuis le {jour(i['mandat_debut'])}.",
                "Ce site ne peut donc rien en dire. Une absence de donnée n'est pas une "
                "absence de travail&nbsp;: elle peut venir d'une lacune de la source, et "
                "en aucun cas nous ne la présenterons comme une présence nulle.")

    # `taux_dissidence` vaut None — et non zéro — quand le groupe n'a eu de ligne
    # sur aucun scrutin où le député a voté. Il n'y a alors pas d'angle à tirer
    # de la dissidence : ce n'est pas une discipline parfaite, c'est une absence
    # de mesure. On bascule sur la présence.
    diss_mesurable = a["taux_dissidence"] is not None and bool(a["votes_avec_ligne"])
    ecart_diss = (a["taux_dissidence"] / d_diss["mediane"]
                  if diss_mesurable and d_diss["mediane"] else 1.0)
    ecart_part = (a["participation_engageants"] / d_part["mediane"]
                  if d_part["mediane"] else 1.0)

    def force(r: float) -> float:
        return max(r, 1 / r) if r > 0 else 99.0

    # ── angle « il s'écarte beaucoup de son groupe » ──────────────────────
    if diss_mesurable and force(ecart_diss) >= force(ecart_part) and ecart_diss >= 1.6:
        t1 = (f"{nom} s'écarte de la ligne de son groupe "
              f"<em>{dec(ecart_diss, 1)} fois plus souvent</em> que le député médian. "
              f"À l'Assemblée, {combien(rang_diss, n, 'le fait davantage', 'le font davantage')}.")
        if groupe and groupe["dissidence_moyenne"] > a["taux_dissidence"]:
            # « NI » rassemble les non-inscrits : ce n'est pas un groupe, et sa cohésion
            # n'a pas de sens. Il est donc hors des superlatifs.
            vrais = [g for g in groupes if g["groupe"] != "NI"]
            moins_uni = min(vrais, key=lambda g: g["cohesion"])["groupe"] == i["groupe"]
            precision = ("est le groupe le moins uni de l'Assemblée"
                         if moins_uni else
                         f"n'a que {pct(groupe['cohesion'])}{NBSP}% de cohésion interne")
            t2 = (f"Sauf que son groupe {precision}. Rapporté à {i['groupe']}, "
                  f"le même chiffre devient ordinaire. Un nombre ne dit rien sans ce à quoi "
                  f"on le compare — c'est la raison d'être de ce site.")
        elif groupe:
            t2 = (f"Et cette fois le contexte ne l'atténue pas{NBSP}: son groupe tient à "
                  f"{pct(groupe['cohesion'])}{NBSP}% de cohésion interne, avec "
                  f"{pct(groupe['dissidence_moyenne'])}{NBSP}% de dissidence en moyenne. "
                  f"L'écart se joue bien à l'échelle de la personne.")
        else:
            t2 = ("Il ne siège dans aucun groupe constitué&nbsp;: la notion de ligne à suivre "
                  "ne s'applique donc qu'aux scrutins où les non-inscrits ont voté de concert.")
        return t1, t2

    # ── angle « il vote presque toujours avec son groupe » ────────────────
    if diss_mesurable and force(ecart_diss) >= force(ecart_part) and ecart_diss <= 0.6:
        plus_disciplines = max(n - rang_diss - 1, 0)
        t1 = (f"{nom} suit la ligne de son groupe dans "
              f"<em>{pct(1 - a['taux_dissidence'])}{NBSP}% des cas</em> où celui-ci en avait "
              f"une. {g['Il']} ne s'en écarte que {num(a['votes_dissidents'])} fois sur "
              f"{num(a['votes_avec_ligne'])}.")
        coh = (f" et son groupe affiche {pct(groupe['cohesion'])}{NBSP}% de cohésion interne"
               if groupe else "")
        t2 = (f"C'est peu, mais ce n'est pas rare{NBSP}: {num(plus_disciplines)} députés sur "
              f"{num(n)} sont encore plus disciplinés{coh}. "
              f"<b>La discipline est la règle à l'Assemblée, pas l'exception</b> — c'est le "
              f"contexte qui manque à la plupart des chiffres qu'on lit ailleurs.")
        return t1, t2

    # ── angle « présence aux votes qui engagent » ─────────────────────────
    if ecart_part >= 1:
        t1 = (f"{nom} est {g['present']} à <em>{pct(a['participation_engageants'])}{NBSP}% "
              f"des votes qui engagent</em>, quand le député médian n'atteint que "
              f"{pct(d_part['mediane'])}{NBSP}%.")
        t2 = (f"{combien(rang_part, d_part['n'], 'fait mieux', 'font mieux').capitalize()}. "
              f"Et personne ne dépasse {pct(d_part['max'])}{NBSP}%{NBSP}: à l'Assemblée, "
              f"la présence intégrale n'existe pas. Un taux se lit contre cette réalité, "
              f"pas contre un idéal à 100{NBSP}%.")
        return t1, t2

    elig = a["engageants_eligibles"]
    quand = ("depuis son entrée en fonction" if elig < n_texte
             else "depuis le début de la législature")
    t1 = (f"{nom} a exprimé <em>{num(a['votes_engageants'])} des "
          f"{num(elig)} votes qui engagent</em> {quand}, "
          f"soit {pct(a['participation_engageants'])}{NBSP}%.")
    t2 = (f"C'est en dessous de la médiane de l'Assemblée ({pct(d_part['mediane'])}{NBSP}%), "
          f"mais l'étalon n'est pas 100{NBSP}%{NBSP}: le député le plus assidu de la "
          f"législature atteint {pct(d_part['max'])}{NBSP}%. Aucun chiffre de ce site ne mesure "
          f"le travail en commission, en circonscription ou en séance sans vote.")
    return t1, t2


def phrase_ecart(f: dict, communs: int) -> tuple[str, str]:
    tous, texte = f["proches"]["tous"], f["proches"]["texte"]
    if not tous or not texte:
        return ("Il n'a pas assez de scrutins en commun avec ses collègues pour que la "
                "comparaison ait un sens.",
                "Un taux d'accord calculé sur une poignée de votes n'est pas une mesure, "
                "c'est un accident d'échantillon. Nous ne l'affichons donc pas.")
    if communs == 0:
        v1 = "Aucun nom ne figure dans les deux colonnes."
    elif communs == 1:
        v1 = "Un seul nom sur huit figure dans les deux colonnes."
    else:
        v1 = f"Seulement {num(communs)} noms sur huit figurent dans les deux colonnes."
    v1 = f"{v1} Changer l'assiette des scrutins change presque entièrement la réponse."
    v2 = (f"Ce n'est pas une erreur de calcul, c'est le fond du problème. En haut de la colonne de "
          f"gauche, {echapper(tous[0]['nom_complet'])} atteint {pct(tous[0]['accord'])}{NBSP}% — sur "
          f"{num(tous[0]['scrutins_communs'])} scrutins seulement. À droite, "
          f"{echapper(texte[0]['nom_complet'])} atteint {pct(texte[0]['accord'])}{NBSP}% sur "
          f"{num(texte[0]['scrutins_communs'])} scrutins. "
          f"<b>C'est l'écart entre les deux colonnes qui informe</b>, jamais une colonne prise seule. "
          f"Un site qui n'en publierait qu'une vous laisserait croire à une précision qui n'existe pas.")
    return v1, v2


# ── fragments HTML ─────────────────────────────────────────────────────────

def rangs_html(voisins: list[dict], surligne: str | None = None) -> str:
    lignes = []
    for v in voisins:
        classe = "rang souligne" if v["nom_complet"] == surligne else "rang"
        lignes.append(
            f'<div class="{classe}">'
            f'<a class="nom" href="{v["acteur_uid"]}.html">{echapper(v["nom_complet"])}</a>'
            f'<span class="grp">{echapper(v["groupe"])}</span>'
            f'<span class="tx">{pct(v["accord"])}{NBSP}%</span>'
            f'<span class="n">n={num(v["scrutins_communs"])}</span>'
            f"</div>"
        )
    return "\n        ".join(lignes)


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

    def __init__(self, donnees: Donnees):
        self.donnees = donnees
        self.apercu = donnees.apercu()
        self.tous = donnees.liste_deputes()
        self.deputes = [d for d in self.tous if d["en_exercice"]]
        self.groupes = {g["groupe"]: g for g in self.apercu["groupes"]}
        self.portees = self.apercu["scrutins_par_portee"]
        self.n_texte = self.portees.get("texte", 0)

        pe = participation_engageants(donnees)
        self.pe = pe
        uids = {d["acteur_uid"] for d in self.deputes}
        self.d_part = distribution([t for u, (t, _, _) in pe.items() if u in uids])
        self.d_diss = distribution(
            [d["taux_dissidence"] for d in self.deputes if d["taux_dissidence"] is not None])
        self.d_pos = distribution(
            [d["axe1"] for d in self.deputes if d["axe1"] is not None])
        self.donnees_json = json.dumps(
            {"participation": self.d_part, "dissidence": self.d_diss, "positions": self.d_pos},
            ensure_ascii=False, separators=(",", ":"))

        self.groupes_tries = sorted(
            [g for g in self.apercu["groupes"] if g.get("position_mediane") is not None],
            key=lambda g: g["position_mediane"])
        self.genere_le = jour(date.today().isoformat())

        self.base = (GABARITS / "base.html").read_text()
        self.commun = {
            "LEGISLATURE": self.apercu["legislature"],
            "DEBUT": jour(self.apercu["debut"]),
            "FIN": jour(self.apercu["fin"]),
            "SCRUTINS": num(self.apercu["scrutins"]),
            "VOTES": num(self.apercu["votes"]),
            "DEPUTES": num(self.apercu["deputes"]),
            "EN_EXERCICE": num(self.apercu["en_exercice"]),
            "PORTEE_TEXTE": num(self.portees.get("texte", 0)),
            "PORTEE_DETAIL": num(self.portees.get("detail", 0)),
            "PART_DETAIL_PCT": pct(self.portees.get("detail", 0) / self.apercu["scrutins"], 0),
            "GENERE_LE": self.genere_le,
        }

    # -- rendu ------------------------------------------------------------

    def page(self, corps: str, jetons: dict, *, titre: str, description: str,
             onglet: str = "") -> str:
        html = self.base.replace("{{CORPS}}", corps)
        tout = {
            **self.commun, **jetons,
            "TITRE": echapper(titre),
            "DESCRIPTION": echapper(description),
            "NAV_ACCUEIL": ' aria-current="page"' if onglet == "accueil" else "",
            "NAV_DEPUTES": ' aria-current="page"' if onglet == "deputes" else "",
        }
        manquants = set(re.findall(r"\{\{(\w+)\}\}", html)) - set(tout)
        if manquants:
            raise SystemExit(f"jetons absents du générateur : {sorted(manquants)}")
        for cle, valeur in tout.items():
            html = html.replace("{{" + cle + "}}", str(valeur))
        return html

    # -- fiche ------------------------------------------------------------

    def fiche(self, uid: str) -> str:
        f = self.donnees.fiche(uid)
        i, a, p = f["identite"], f["activite"], f["position"]
        groupe = self.groupes.get(i["groupe"])

        taux, exprimes, eligibles = self.pe.get(uid, (0.0, 0, 0))
        a["participation_engageants"] = taux
        a["votes_engageants"] = exprimes
        # Le dénominateur est propre au député : un élu en cours de législature
        # n'a pas pu voter les scrutins tenus avant son entrée en fonction.
        a["engageants_eligibles"] = eligibles

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
        diss1, diss2 = phrase_dissidence(a, i, self.d_diss, rang_diss, groupe)
        ec1, ec2 = phrase_ecart(f, communs)

        # On surligne dans les deux colonnes le binôme de tête des votes qui engagent :
        # c'est lui qui rend l'écart entre les deux assiettes visible d'un coup d'œil.
        pt = f["proches"]["texte"]
        surligne = pt[0]["nom_complet"] if pt else None

        profession = i.get("profession") or ""
        identite = ", ".join(x for x in [
            f"{i['age']} ans" if i.get("age") else "",
            profession[0].lower() + profession[1:] if profession else "",
        ] if x)
        identite = (identite + ", " if identite else "") + \
            f"député depuis le {jour(i['mandat_debut'])}."

        corps = (GABARITS / "fiche.html").read_text().replace("{{BLOC_POSITION}}", bloc)

        jetons = {
            "FIL": echapper(f"Députés · {i['departement']} · "
                            f"{i['num_circo']}{ordinal(i['num_circo'])} circonscription"),
            "NOM": echapper(i["nom_complet"]),
            "NOM_COURT": echapper(i["nom"]),
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
            "HATVP": i.get("uri_hatvp") or "https://www.hatvp.fr",
            "BOOTSTRAP": num(self.apercu.get("bootstrap") or 40),

            "PART_ENG_PCT": pct(taux),
            "PART_ENG_N": num(exprimes),
            "PART_ENG_DENOM": num(eligibles),
            # Quand le mandat n'a pas couvert toute la législature, le dénominateur
            # diffère du total : on le dit, sinon le chiffre paraît faux.
            "PART_ENG_RESERVE": ("<br>tenus depuis son entrée en fonction"
                                 if eligibles < self.n_texte else ""),
            "PART_TOUS": f"{taux:.4f}",
            "PART_TOUS_PCT": pct(a["participation"] or 0),
            "PART_PHRASE": part1,
            "PART_PHRASE_2": part2,

            "DISS_PCT": pct(a["taux_dissidence"] or 0),
            "DISS_N": num(a["votes_dissidents"] or 0),
            "DISS_DENOM": num(a["votes_avec_ligne"] or 0),
            "DISS_VAL": f"{a['taux_dissidence'] or 0:.4f}",
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
            "POS_PHRASE": pos1,
            "POS_PHRASE_2": pos2,
            "POS_ECART_GROUPE": phrase_ecart_groupe(p, i, groupe) if estimee else "",
            "POS_INCERTITUDE": phrase_incertitude(p, i, a, self.d_pos) if estimee else "",

            "PROCHES_TOUS": rangs_html(f["proches"]["tous"], surligne),
            "PROCHES_TEXTE": rangs_html(f["proches"]["texte"], surligne),
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
            onglet="deputes")

    # -- accueil et annuaire ----------------------------------------------

    def accueil(self) -> str:
        corps = (GABARITS / "accueil.html").read_text()
        return self.page(
            corps, {"LIGNES_GROUPES": lignes_groupes_html(self.apercu["groupes"])},
            titre="Radar parlementaire — ce que fait votre député, avec le dénominateur",
            description=("Chaque chiffre sur l'activité des députés, recalculé depuis les votes "
                         "nominatifs de l'Assemblée nationale et affiché avec son dénominateur."),
            onglet="accueil")

    def annuaire(self) -> str:
        index = [{
            "u": d["acteur_uid"],
            "n": d["nom_complet"],
            "d": d["departement"] or "",
            "dn": str(d["num_departement"] or ""),
            "c": str(d["num_circo"] or ""),
            "r": self.region.get(d["acteur_uid"], ""),
            "g": d["groupe"] or "",
            "gl": d["groupe_libelle"] or "",
            "p": f"{pct(self.pe.get(d['acteur_uid'], (0.0, 0, 0))[0], 0)}{NBSP}%",
        } for d in sorted(self.deputes, key=lambda x: x["nom_complet"])]
        corps = (GABARITS / "annuaire.html").read_text()
        return self.page(
            corps,
            {"ANNUAIRE_JSON": json.dumps(index, ensure_ascii=False, separators=(",", ":"))},
            titre="Trouver un député — Radar parlementaire",
            description=("Cherchez votre député par nom, département, région ou groupe "
                         "parlementaire, et lisez ses chiffres avec leur dénominateur."),
            onglet="deputes")


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--bootstrap", type=int, default=40,
                         help="rééchantillonnages pour les intervalles (0 = sans intervalle)")
    parseur.add_argument("--limite", type=int, default=0,
                         help="ne générer que N fiches, pour une mise au point rapide")
    args = parseur.parse_args()

    debut = time.monotonic()
    donnees = Donnees.construire(bootstrap=args.bootstrap, journal=lambda m: print(f"  · {m}"))
    site = Site(donnees)

    # La région n'est pas dans `liste_deputes` : on la prend à la source.
    site.region = dict(donnees.deputes.select(["acteur_uid", "region"]).iter_rows())

    SORTIE.mkdir(exist_ok=True)
    if (SORTIE / "statique").exists():
        shutil.rmtree(SORTIE / "statique")
    shutil.copytree(STATIQUE, SORTIE / "statique")

    (SORTIE / "index.html").write_text(site.accueil())
    (SORTIE / "deputes.html").write_text(site.annuaire())
    print(f"  · accueil et annuaire")

    cibles = site.deputes[: args.limite] if args.limite else site.deputes
    for n, d in enumerate(cibles, 1):
        (SORTIE / f"{d['acteur_uid']}.html").write_text(site.fiche(d["acteur_uid"]))
        if n % 100 == 0 or n == len(cibles):
            print(f"  · fiches {n}/{len(cibles)}")

    poids = sum(f.stat().st_size for f in SORTIE.rglob("*") if f.is_file())
    print(f"\n{len(cibles) + 2} pages écrites dans {SORTIE} "
          f"({poids / 1e6:.1f} Mo, {time.monotonic() - debut:.0f} s)")
    print(f"→ ouvrir {SORTIE / 'index.html'}")


if __name__ == "__main__":
    main()
