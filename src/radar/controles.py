"""Les invariants que les données doivent vérifier pour être publiables.

**Pourquoi ce module existe.** Le site a publié, pendant toute sa vie, des pages
de scrutin annonçant « les 581 députés dont le mandat courait le 2 juin 2026 »
sous un régime qui n'en compte que 577. Aucun test ne s'en est aperçu, parce que
les 230 tests vérifiaient des calculs et jamais un *effectif*. Un chiffre faux
qui ne contredit aucune formule ne se voit pas : il se compte.

Les contrôles ci-dessous ne testent donc pas du code, ils testent **les données
produites**, et ils sont exécutés avant la génération du site. Un site qui ne
sait pas compter ses députés n'est pas discutable, il est invalidé ; mieux vaut
ne rien publier que publier un total que le premier lecteur venu peut réfuter
avec la Constitution.

Chaque contrôle répond à une question qu'un contradicteur poserait :

- `effectifs` — « vous affichez plus de 577 députés, comment est-ce possible ? »
- `sieges_uniques` — « ces deux personnes occupent la même circonscription. »
- `mandats` — « d'où sortent vos dates d'entrée en fonction ? »
- `votes_hors_mandat` — « ce vote est attribué à quelqu'un qui ne siégeait pas. »
- `releve_complet` — « votre relevé n'additionne pas. »
- `totaux_source` — « vos totaux ne sont pas ceux de l'Assemblée. »

Un contrôle qui échoue rend une `Anomalie` avec des exemples nommés : une alerte
qui dit « 4 anomalies » sans dire lesquelles ne se corrige pas.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from .analyze import VoteCube, build_cube
from .config import EXPRESSED, SIEGES
from .parse import load

#: Au-delà, la liste d'exemples devient un listing et cesse d'être lue.
MAX_EXEMPLES = 8


@dataclass(frozen=True)
class Anomalie:
    """Un invariant violé, avec de quoi aller le corriger."""

    controle: str
    message: str
    exemples: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lignes = [f"[{self.controle}] {self.message}"]
        lignes += [f"    · {e}" for e in self.exemples[:MAX_EXEMPLES]]
        if len(self.exemples) > MAX_EXEMPLES:
            lignes.append(f"    · … et {len(self.exemples) - MAX_EXEMPLES} autres")
        return "\n".join(lignes)


# --------------------------------------------------------------------------
# Les mandats : d'où viennent les dates
# --------------------------------------------------------------------------


def mandats(cube: VoteCube, legislature: int) -> Iterator[Anomalie]:
    """Les périodes de mandat sont-elles lisibles, ordonnées et disjointes ?

    Trois questions, et la première est la plus importante : `date_debut` de la
    source vaut l'ouverture de la législature pour tout le monde, y compris pour
    les remplaçants entrés deux ans plus tard. L'éligibilité se fonde donc sur
    `datePriseFonction`, avec un repli sur `date_debut` — et ce repli est
    exactement ce qu'il faut surveiller : s'il servait, le site republierait
    l'erreur sans rien changer au code.
    """
    brut = load("mandats").filter(
        (pl.col("type_organe") == "ASSEMBLEE")
        & (pl.col("legislature") == str(legislature))
    )
    sans_prise = brut.filter(pl.col("date_prise_fonction").is_null())
    if sans_prise.height:
        yield Anomalie(
            "mandats",
            f"{sans_prise.height} mandat(s) sans date de prise de fonction : "
            "l'éligibilité retomberait sur `date_debut`, qui vaut l'ouverture de "
            "la législature pour tout le monde et ferait siéger des remplaçants "
            "avant leur arrivée",
            [f"mandat {r}" for r in sans_prise["mandat_uid"].to_list()],
        )

    vides, desordre, chevauchements = [], [], []
    for r in cube.deputes.select("nom_complet", "periodes_mandat").iter_rows():
        nom, periodes = r
        if not periodes:
            vides.append(nom)
            continue
        bornes = []
        for p in periodes:
            if not p["debut"]:
                vides.append(nom)
                break
            fin = dt.date.fromisoformat(p["fin"]) if p["fin"] else dt.date.max
            debut = dt.date.fromisoformat(p["debut"])
            if debut > fin:
                desordre.append(f"{nom} : {p['debut']} → {p['fin']}")
            bornes.append((debut, fin))
        for (_, fin), (debut, _) in zip(sorted(bornes), sorted(bornes)[1:]):
            if debut <= fin:
                chevauchements.append(f"{nom} : période ouverte au {debut} alors "
                                      f"que la précédente court jusqu'au {fin}")

    if vides:
        yield Anomalie("mandats", f"{len(vides)} député(s) sans période de mandat "
                       "exploitable", vides)
    if desordre:
        yield Anomalie("mandats", f"{len(desordre)} période(s) dont la fin précède "
                       "le début", desordre)
    if chevauchements:
        yield Anomalie("mandats", f"{len(chevauchements)} député(s) dont deux "
                       "périodes de mandat se recouvrent — un même siège ne "
                       "s'occupe pas deux fois le même jour", chevauchements)


# --------------------------------------------------------------------------
# Les effectifs : 577 sièges, et pas un de plus
# --------------------------------------------------------------------------


def effectifs(cube: VoteCube, legislature: int = 0) -> Iterator[Anomalie]:
    """Combien de députés siégeaient, le jour de chaque scrutin ?

    L'invariant est constitutionnel, pas statistique : au plus `SIEGES`. Un
    effectif **inférieur** est normal et se publie tel quel — un siège vacant
    entre une démission et la partielle qui la suit est un fait, pas une
    anomalie. Seul le dépassement est une impossibilité.
    """
    par_scrutin = cube.eligible.sum(axis=0)
    trop = np.flatnonzero(par_scrutin > SIEGES)
    if not trop.size:
        return
    dates = cube.scrutins["date"].to_list()
    numeros = cube.scrutins["numero"].to_list()
    yield Anomalie(
        "effectifs",
        f"{trop.size} scrutin(s) sur {cube.n_scrutins} comptent plus de "
        f"{SIEGES} députés en fonction, ce que l'article 24 de la Constitution "
        "interdit — une page de scrutin publierait un effectif réfutable",
        [f"scrutin nº {numeros[j]} du {dates[j]} : {par_scrutin[j]} députés"
         for j in trop.tolist()],
    )


def sieges_uniques(cube: VoteCube, legislature: int = 0) -> Iterator[Anomalie]:
    """Deux personnes peuvent-elles occuper la même circonscription le même jour ?

    C'est le même défaut que `effectifs`, pris par l'autre bout, et il le
    nomme : le dépassement d'effectif ne dit pas *qui* est en trop, le doublon
    de circonscription donne les deux noms et le siège disputé.
    """
    noms = cube.deputes["nom_complet"].to_list()
    dates = cube.scrutins["date"].to_list()
    par_circo: dict[str, list[int]] = {}
    for i, (d, c) in enumerate(zip(cube.deputes["num_departement"].to_list(),
                                   cube.deputes["num_circo"].to_list())):
        par_circo.setdefault(f"{d or '?'}-{c or '?'}", []).append(i)

    # Le test se fait par circonscription, pas par scrutin : deux occupants
    # d'un même siège se détectent en croisant leurs deux lignes d'éligibilité,
    # là où balayer les 8 434 colonnes coûterait cinq millions d'itérations.
    doublons = []
    for circo, indices in sorted(par_circo.items()):
        for rang, i in enumerate(indices):
            for k in indices[rang + 1:]:
                ensemble = np.flatnonzero(cube.eligible[i] & cube.eligible[k])
                if ensemble.size:
                    doublons.append(
                        f"circonscription {circo} : {noms[i]} et {noms[k]} comptés "
                        f"ensemble sur {ensemble.size} scrutin(s), dont celui du "
                        f"{dates[int(ensemble[0])]}"
                    )
    if doublons:
        yield Anomalie(
            "sieges_uniques",
            f"{len(doublons)} circonscription(s) occupée(s) par deux députés à la "
            "même date — un remplacement traité comme un cumul",
            doublons,
        )


# --------------------------------------------------------------------------
# Le relevé : ce que la page affiche doit s'additionner
# --------------------------------------------------------------------------


def votes_hors_mandat(cube: VoteCube, legislature: int = 0) -> Iterator[Anomalie]:
    """Un suffrage a-t-il été attribué à un député qui ne siégeait pas ?

    C'est l'invariant réciproque de `effectifs`, et il vaut mieux que lui : il
    confronte nos dates de mandat au dépouillement de l'Assemblée. Un député qui
    vote un jour où nos données le disent hors mandat prouve que **nos dates**
    sont fausses, sans qu'aucun effectif n'ait besoin d'être dépassé.
    """
    exprime = cube.pour | cube.contre | cube.abstention | cube.non_votant
    faux = np.argwhere(exprime & ~cube.eligible)
    if not faux.size:
        return
    noms = cube.deputes["nom_complet"].to_list()
    dates = cube.scrutins["date"].to_list()
    numeros = cube.scrutins["numero"].to_list()
    yield Anomalie(
        "votes_hors_mandat",
        f"{len(faux)} vote(s) attribué(s) par l'Assemblée à un député que nos "
        "dates de mandat placent hors de son mandat — ce sont nos dates qui "
        "sont fausses, la source publie le dépouillement",
        [f"{noms[i]} au scrutin nº {numeros[j]} du {dates[j]}"
         for i, j in faux.tolist()],
    )


def releve_complet(cube: VoteCube, legislature: int = 0) -> Iterator[Anomalie]:
    """Les six statuts du relevé couvrent-ils exactement l'Assemblée ?

    La page de scrutin promet que ses quatre nombres « se rapportent aux N
    députés dont le mandat courait » : cette phrase n'est vraie que si les
    statuts publiés partitionnent l'effectif. Un député à la fois « pour » et
    « contre » — la source en publierait deux lignes — casserait la partition
    sans casser aucun total.
    """
    doubles = (cube.pour.astype(np.int8) + cube.contre + cube.abstention
               + cube.non_votant)
    faux = np.argwhere(doubles > 1)
    if not faux.size:
        return
    noms = cube.deputes["nom_complet"].to_list()
    numeros = cube.scrutins["numero"].to_list()
    yield Anomalie(
        "releve_complet",
        f"{len(faux)} couple(s) député × scrutin portant plusieurs positions : "
        "le relevé nominatif ne s'additionnerait plus",
        [f"{noms[i]} au scrutin nº {numeros[j]}" for i, j in faux.tolist()],
    )


def totaux_source(cube: VoteCube, legislature: int) -> Iterator[Anomalie]:
    """Nos comptes sont-ils ceux que l'Assemblée publie ?

    Le site écrit, sur chaque page de scrutin, que ses chiffres sont recalculés
    depuis le dépouillement nominatif et que « si les deux divergent, c'est
    cette page qui a tort ». La phrase engage : elle n'est tenable que si un
    contrôle la vérifie avant publication, sur les 8 434 scrutins et non sur un
    échantillon.
    """
    scrutins, votes = load("scrutins"), load("votes")
    recompte = votes.group_by("scrutin_uid").agg(
        [(pl.col("position") == p).sum().alias(f"r_{p}") for p in EXPRESSED]
    )
    j = scrutins.join(recompte, on="scrutin_uid", how="left").with_columns(
        [pl.col(f"r_{p}").fill_null(0) for p in EXPRESSED]
    )
    for p in EXPRESSED:
        ecarts = j.filter(pl.col(f"n_{p}").fill_null(0) != pl.col(f"r_{p}"))
        if ecarts.height:
            yield Anomalie(
                "totaux_source",
                f"{ecarts.height} scrutin(s) dont le nombre de « {p} » publié par "
                "l'Assemblée diffère du recomptage nominatif",
                [f"scrutin nº {r['numero']} du {r['date']} : source {r[f'n_{p}']}, "
                 f"recompté {r[f'r_{p}']}"
                 for r in ecarts.head(MAX_EXEMPLES + 1).to_dicts()],
            )


# --------------------------------------------------------------------------
# Tout, d'un coup
# --------------------------------------------------------------------------


#: Les contrôles, dans l'ordre où ils s'exécutent et où leurs échecs se lisent :
#: d'abord d'où viennent les dates, ensuite ce qu'elles impliquent, enfin la
#: confrontation à la source. Ajouter un contrôle, c'est ajouter une ligne ici —
#: `generer.py` en publie le nombre, et le site ne promet donc jamais plus de
#: vérifications qu'il n'en fait.
CONTROLES = (
    ("mandats", mandats),
    ("effectifs", effectifs),
    ("sieges_uniques", sieges_uniques),
    ("votes_hors_mandat", votes_hors_mandat),
    ("releve_complet", releve_complet),
    ("totaux_source", totaux_source),
)


def verifier(cube: VoteCube | None = None, *, legislature: int | None = None) -> list[Anomalie]:
    """Passe tous les contrôles et rend les anomalies, la liste vide si tout va.

    Le cube est celui de **tous** les députés de la législature, en exercice ou
    non : c'est celui dont sortent les pages de scrutin, donc celui dont les
    effectifs sont publiés.
    """
    from .config import LEGISLATURE

    leg = LEGISLATURE if legislature is None else legislature
    cube = build_cube(en_exercice_seulement=False) if cube is None else cube
    return [a for _, controle in CONTROLES for a in controle(cube, leg)]


def exiger(cube: VoteCube | None = None, *, legislature: int | None = None) -> None:
    """Lève `DonneesInvalides` s'il reste une anomalie. À appeler avant de publier."""
    anomalies = verifier(cube, legislature=legislature)
    if anomalies:
        raise DonneesInvalides(anomalies)


class DonneesInvalides(RuntimeError):
    """Les données ne satisfont pas un invariant : rien ne doit être publié."""

    def __init__(self, anomalies: list[Anomalie]):
        self.anomalies = anomalies
        super().__init__(
            f"{len(anomalies)} contrôle(s) en échec — publication refusée :\n"
            + "\n".join(str(a) for a in anomalies)
        )
