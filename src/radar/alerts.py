"""Alertes hebdomadaires : ce qu'il s'est passé d'inhabituel à l'Assemblée.

Chaque détecteur renvoie une liste d'`Alerte`. L'ensemble constitue la matière
première d'une newsletter, d'un fil social ou d'un tableau de bord. Rien ici
n'interprète politiquement : on signale des écarts à la normale, la lecture
reste au lecteur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import polars as pl

from . import analyze, topics
from .analyze import VoteCube
from .parse import load


@dataclass
class Alerte:
    categorie: str
    titre: str
    detail: str
    #: 0 à 1 — sert à trier, pas à juger.
    intensite: float
    donnees: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.categorie}] {self.titre}\n    {self.detail}"


def _lundi(jour: date) -> date:
    return jour - timedelta(days=jour.weekday())


def derniere_semaine() -> date:
    """Lundi de la dernière semaine pour laquelle on a des scrutins."""
    d = load("scrutins")["date_d"].max()
    return _lundi(d)


# --------------------------------------------------------------------------
# Détecteurs
# --------------------------------------------------------------------------


def votes_serres(semaine: date, *, ecart_max: int = 15) -> list[Alerte]:
    """Scrutins de la semaine qui se sont joués à quelques voix."""
    fin = semaine + timedelta(days=7)
    s = (
        load("scrutins")
        .filter((pl.col("date_d") >= semaine) & (pl.col("date_d") < fin))
        .with_columns((pl.col("n_pour") - pl.col("n_contre")).abs().alias("ecart"))
        .filter((pl.col("ecart") <= ecart_max) & (pl.col("nb_votants") > 50))
        .sort("ecart")
    )
    return [
        Alerte(
            categorie="vote serré",
            titre=f"{r['ecart']} voix d'écart le {r['date']}",
            detail=f"{r['n_pour']} pour / {r['n_contre']} contre — {(r['titre'] or '')[:180]}",
            intensite=1.0 - r["ecart"] / max(ecart_max, 1),
            donnees={"scrutin_uid": r["scrutin_uid"], "sort": r["sort_code"]},
        )
        for r in s.iter_rows(named=True)
    ]


def _groupes_du_scrutin(semaine: date) -> pl.DataFrame:
    """Effectifs par position, pour chaque groupe et chaque scrutin de la semaine."""
    fin = semaine + timedelta(days=7)
    scrutins = load("scrutins").filter(
        (pl.col("date_d") >= semaine) & (pl.col("date_d") < fin)
    )
    noms = load("organes").select(
        pl.col("organe_uid").alias("groupe_uid"), pl.col("libelle_abrev").alias("groupe")
    )
    return (
        load("positions_groupe")
        .join(scrutins.select("scrutin_uid", "date", "titre"), on="scrutin_uid", how="inner")
        .join(noms, on="groupe_uid", how="left")
        .filter(pl.col("votants_groupe") >= 15)
    )


def fractures_de_groupe(semaine: date, *, seuil: float = 0.08) -> list[Alerte]:
    """Scrutins où des députés se sont écartés d'une **ligne nette** de leur groupe.

    Le détecteur ne se déclenche que si une position réunit la majorité absolue
    des suffrages du groupe (`analyze.SEUIL_LIGNE`). Sans ce garde-fou, il
    remontait surtout des groupes sans ligne du tout : le seuil de 8 % de
    « hors ligne » sélectionne mécaniquement les scrutins les plus éclatés,
    c'est-à-dire précisément ceux où la position dominante ne domine rien. Ces
    cas-là sont désormais traités par `groupes_partages`, qui les nomme pour ce
    qu'ils sont.
    """
    d = (
        _groupes_du_scrutin(semaine)
        .filter(pl.col("part_majoritaire") > analyze.SEUIL_LIGNE)
        .with_columns(
            (pl.col("votants_groupe") - pl.col("n_majoritaire")).alias("dissidents")
        )
        .with_columns((pl.col("dissidents") / pl.col("votants_groupe")).alias("taux"))
        .filter(pl.col("taux") >= seuil)
        .sort("taux", descending=True)
    )
    return [
        Alerte(
            categorie="fracture",
            titre=(
                f"{r['groupe']} : {r['dissidents']}/{r['votants_groupe']} votes "
                f"s'écartent de la ligne « {r['majoritaire']} »"
            ),
            detail=(
                f"{r['date']} — {r['n_pour']} pour / {r['n_contre']} contre / "
                f"{r['n_abstention']} abstentions — {(r['titre'] or '')[:160]}"
            ),
            intensite=min(1.0, r["taux"] / 0.5),
            donnees={"scrutin_uid": r["scrutin_uid"], "groupe": r["groupe"]},
        )
        for r in d.iter_rows(named=True)
    ]


def groupes_partages(semaine: date, *, seuil: float = analyze.SEUIL_LIGNE) -> list[Alerte]:
    """Scrutins où un groupe n'a **pas** dégagé de position majoritaire.

    À ne pas confondre avec une fracture : personne ne s'écarte d'une consigne
    ici, il n'y a simplement pas de consigne dégagée. On rapporte donc les trois
    effectifs et la part de la position dominante, sans parler de dissidence.
    """
    d = (
        _groupes_du_scrutin(semaine)
        .filter(pl.col("part_majoritaire") <= seuil)
        .sort("part_majoritaire")
    )
    return [
        Alerte(
            categorie="groupe partagé",
            titre=(
                f"{r['groupe']} sans ligne majoritaire "
                f"(position dominante à {r['part_majoritaire']:.0%})"
            ),
            detail=(
                f"{r['date']} — {r['n_pour']} pour / {r['n_contre']} contre / "
                f"{r['n_abstention']} abstentions sur {r['votants_groupe']} votants "
                f"— {(r['titre'] or '')[:160]}"
            ),
            intensite=min(1.0, (seuil - r["part_majoritaire"]) / seuil + 0.5),
            donnees={"scrutin_uid": r["scrutin_uid"], "groupe": r["groupe"]},
        )
        for r in d.iter_rows(named=True)
    ]


def poussees_thematiques(
    semaine: date, *, k: int = 8, freqs: pl.DataFrame | None = None
) -> list[Alerte]:
    """Sujets dont la fréquence explose cette semaine."""
    try:
        montants = topics.sujets_qui_montent(
            freqs, semaine=semaine.isoformat(), k=k
        )
    except ValueError:
        return []
    return [
        Alerte(
            categorie="sujet",
            titre=f"« {r['terme']} » en forte hausse",
            detail=(
                f"cité dans {r['n_docs']} des {r['n_documents']} documents de la "
                f"semaine, contre {r['attendu']:.1f} attendus au vu des semaines "
                f"précédentes (q = {r['q_valeur']:.3f})"
            ),
            intensite=min(1.0, r["score"] / 20.0),
            donnees={"terme": r["terme"], "score": r["score"]},
        )
        for r in montants.iter_rows(named=True)
    ]


def deputes_en_rupture(
    cube: VoteCube, semaine: date, *, min_votes: int = 10, seuil: float = 0.25
) -> list[Alerte]:
    """Députés qui ont voté contre leur groupe de façon inhabituelle cette semaine.

    On compare leur taux de dissidence de la semaine à leur propre taux sur
    l'ensemble de la législature : c'est l'écart au comportement habituel du
    député qui compte, pas son niveau absolu.
    """
    fin = semaine + timedelta(days=7)
    scrutins = load("scrutins").filter(
        (pl.col("date_d") >= semaine) & (pl.col("date_d") < fin)
    )
    deputes = cube.deputes.select("acteur_uid", "nom_complet", "groupe")

    base = analyze.dissidence(cube, min_votes=1).select(
        "acteur_uid", pl.col("taux_dissidence").alias("taux_habituel")
    )
    semaine_df = (
        analyze.votes_vs_ligne(scrutins)
        .join(deputes, on="acteur_uid", how="inner")
        .group_by("acteur_uid", "nom_complet", "groupe")
        .agg(pl.len().alias("votes"), pl.col("dissident").sum().alias("dissidents"))
        .filter(pl.col("votes") >= min_votes)
        .with_columns((pl.col("dissidents") / pl.col("votes")).alias("taux_semaine"))
        .join(base, on="acteur_uid", how="left")
        .with_columns(
            (pl.col("taux_semaine") - pl.col("taux_habituel").fill_null(0.0)).alias("ecart")
        )
        .filter((pl.col("taux_semaine") >= seuil) & (pl.col("ecart") > 0.10))
        .sort("ecart", descending=True)
    )
    return [
        Alerte(
            categorie="rupture",
            titre=f"{r['nom_complet']} ({r['groupe']}) s'écarte de son groupe",
            detail=(
                f"{r['dissidents']}/{r['votes']} votes hors ligne cette semaine "
                f"({r['taux_semaine']:.0%}), contre {r['taux_habituel']:.0%} habituellement"
            ),
            intensite=min(1.0, r["ecart"] / 0.5),
            donnees={"acteur_uid": r["acteur_uid"]},
        )
        for r in semaine_df.iter_rows(named=True)
    ]


def activite_hebdo(semaine: date) -> list[Alerte]:
    """Volume d'activité de la semaine, comparé aux huit semaines précédentes."""
    fin = semaine + timedelta(days=7)
    scrutins = load("scrutins").filter(pl.col("date_d").is_not_null())
    n = scrutins.filter((pl.col("date_d") >= semaine) & (pl.col("date_d") < fin)).height
    ref = scrutins.filter(
        (pl.col("date_d") >= semaine - timedelta(weeks=8)) & (pl.col("date_d") < semaine)
    ).height / 8.0

    if n == 0 or ref < 1:
        return []
    ratio = n / ref
    if 0.5 < ratio < 2.0:
        return []
    sens = "intense" if ratio >= 2.0 else "calme"
    return [
        Alerte(
            categorie="activité",
            titre=f"Semaine {sens} : {n} scrutins",
            detail=f"contre {ref:.0f} par semaine en moyenne sur les huit semaines précédentes",
            intensite=min(1.0, abs(ratio - 1) / 2),
            donnees={"scrutins": n, "moyenne": ref},
        )
    ]


# --------------------------------------------------------------------------
# Assemblage
# --------------------------------------------------------------------------


def toutes_les_alertes(
    semaine: date | str | None = None,
    *,
    cube: VoteCube | None = None,
    avec_sujets: bool = True,
    max_par_categorie: int = 5,
) -> list[Alerte]:
    """Lance tous les détecteurs et retourne les alertes triées par intensité."""
    if semaine is None:
        semaine = derniere_semaine()
    elif isinstance(semaine, str):
        semaine = _lundi(date.fromisoformat(semaine))

    cube = cube or analyze.build_cube()

    alertes: list[Alerte] = []
    alertes += activite_hebdo(semaine)
    alertes += votes_serres(semaine)
    alertes += fractures_de_groupe(semaine)
    alertes += groupes_partages(semaine)
    alertes += deputes_en_rupture(cube, semaine)
    if avec_sujets:
        alertes += poussees_thematiques(semaine)

    retenues: list[Alerte] = []
    for categorie in {a.categorie for a in alertes}:
        lot = sorted(
            (a for a in alertes if a.categorie == categorie),
            key=lambda a: a.intensite,
            reverse=True,
        )
        retenues += lot[:max_par_categorie]
    return sorted(retenues, key=lambda a: a.intensite, reverse=True)
