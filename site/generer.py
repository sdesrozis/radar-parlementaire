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
import shutil
import time
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from radar.vues import MIN_COMMUNS, Donnees
from redaction import (
    NBSP,
    accords,
    combien,
    dec,
    echapper,
    jour,
    num,
    ordinal,
    pct,
    phrase_carte,
    phrase_dissidence,
    phrase_ecart,
    phrase_ecart_groupe,
    phrase_incertitude,
    phrase_participation,
    phrase_portee,
    phrase_position,
    phrase_these,
    reserve_denominateur,
    situer,
)

ICI = Path(__file__).parent
GABARITS = ICI / "gabarits"
STATIQUE = ICI / "statique"
ASSETS = ICI / "assets"
SORTIE = ICI / "sortie"


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

        pe = participation_engageants(self.tous)
        self.pe = pe
        uids = {d["acteur_uid"] for d in self.deputes}
        self.d_part = distribution([p["taux"] for u, p in pe.items() if u in uids])
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
            "NAV_CARTE": ' aria-current="page"' if onglet == "carte" else "",
            "NAV_METHODE": ' aria-current="page"' if onglet == "methode" else "",
        }
        manquants = set(re.findall(r"\{\{(\w+)\}\}", html)) - set(tout)
        if manquants:
            raise SystemExit(f"jetons absents du générateur : {sorted(manquants)}")
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
                "CARTE_JSON": json.dumps(m, ensure_ascii=False, separators=(",", ":")),
            },
            titre="Qui vote avec qui ? — Radar parlementaire",
            description=(
                f"Les {num(m['paires'])} paires de députés de l'Assemblée nationale, "
                f"chacune mesurée sur les scrutins où les deux ont voté."
            ),
            onglet="carte")

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
                "BOOTSTRAP": num(self.apercu["bootstrap"]),
                "BLOCS": num(self.apercu["blocs_bootstrap"]),
                "MIN_COMMUNS": num(MIN_COMMUNS),
            },
            titre="Méthode — Radar parlementaire",
            description=("La définition exacte de chaque mesure du Radar parlementaire, "
                         "son dénominateur, et ce qu'elle n'est pas capable de dire."),
            onglet="methode")

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
            "PART_ENG_DENOM": num(votables),
            # Le dénominateur affiché est celui qui a été divisé, et les deux
            # retraits qui l'ont formé sont nommés : sans cela le chiffre paraît
            # faux à quiconque compare aux totaux de la législature.
            "PART_ENG_RESERVE": reserve_denominateur(
                eligibles, votables, self.n_texte),
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
            onglet="accueil")

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
            "u": d["acteur_uid"],
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
            {"ANNUAIRE_JSON": self.index_annuaire()},
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
    parseur.add_argument("--servir", action="store_true",
                         help="servir sortie/ après génération, comme le fera l'hébergeur")
    parseur.add_argument("--port", type=int, default=8000, help="port du mode --servir")
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
    # Les images de marque vivent dans `assets/` — elles ne sont ni du style ni
    # du comportement — mais elles sont servies depuis `statique/`, parce que
    # c'est le seul dossier que l'hébergeur voit.
    for image in sorted(ASSETS.glob("*")):
        if image.is_file():
            shutil.copy2(image, SORTIE / "statique" / image.name)

    (SORTIE / "index.html").write_text(site.accueil())
    (SORTIE / "deputes.html").write_text(site.annuaire())
    (SORTIE / "carte.html").write_text(site.carte())
    (SORTIE / "methode.html").write_text(site.methode())
    print("  · accueil, annuaire, carte et méthode")

    cibles = site.deputes[: args.limite] if args.limite else site.deputes
    for n, d in enumerate(cibles, 1):
        (SORTIE / f"{d['acteur_uid']}.html").write_text(site.fiche(d["acteur_uid"]))
        if n % 100 == 0 or n == len(cibles):
            print(f"  · fiches {n}/{len(cibles)}")

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
