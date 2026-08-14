"""Tests du dénominateur de présence — celui que le site affiche le plus.

Le biais verrouillé ici a coûté cher : la cause de non-vote `PAN` (président de
l'Assemblée nationale) manquait à `STRUCTURAL_NONVOTE_CAUSES`. Ses 7 508
non-votes tombaient donc au dénominateur de présence d'une seule personne, et le
site publiait la présidente de l'Assemblée à 8,6 % de présence aux votes qui
engagent — 19 sur 221 — au lieu de 100 % — 19 sur 19. Le perchoir était compté
comme de l'absentéisme.

Deux natures de tests, et elles ne se remplacent pas :

- un test sur cube synthétique, qui vérifie la **règle** : un non-vote dont la
  source donne une cause institutionnelle sort du dénominateur ;
- un test sur les données réelles, qui vérifie la **liste** : toute cause
  présente dans la source est soit déclarée structurelle, soit délibérément
  laissée au dénominateur. C'est ce second test qui aurait attrapé `PAN`, car
  aucune règle abstraite ne pouvait deviner qu'un code manquait à l'appel.
"""

import numpy as np
import polars as pl
import pytest

from radar import analyze
from radar.analyze import VoteCube
from radar.config import STRUCTURAL_NONVOTE_CAUSES
from radar.parse import load

#: Causes de non-vote connues qui ne sont **pas** structurelles, c'est-à-dire
#: qui doivent rester au dénominateur. Vide aujourd'hui : les trois causes que
#: publie la source sont toutes institutionnelles. Une cause nouvelle fera
#: échouer le test tant qu'elle n'aura pas été rangée dans l'une des deux
#: listes — ce qui est le comportement voulu, puisque le choix est éditorial.
CAUSES_NON_STRUCTURELLES: frozenset[str] = frozenset()


def cube_presidence() -> VoteCube:
    """Deux députés, quatre scrutins.

        PRESIDENT : préside trois fois, vote une fois
        ORDINAIRE : vote deux fois, absent deux fois sans cause déclarée
    """
    deputes = pl.DataFrame(
        {
            "acteur_uid": ["PRESIDENT", "ORDINAIRE"],
            "nom_complet": ["La Présidente", "Le Député"],
            "groupe": ["G", "G"],
        }
    )
    scrutins = pl.DataFrame({"scrutin_uid": ["s1", "s2", "s3", "s4"]})
    faux = np.zeros((2, 4), dtype=bool)
    pour = np.array(
        [
            [1, 0, 0, 0],   # la présidente vote une fois
            [1, 1, 0, 0],   # le député vote deux fois
        ],
        dtype=bool,
    )
    non_votant = np.array(
        [
            [0, 1, 1, 1],   # trois non-votes, tous déclarés « au perchoir »
            [0, 0, 0, 0],   # absences non déclarées : pas de ligne dans `votes`
        ],
        dtype=bool,
    )
    return VoteCube(
        deputes=deputes,
        scrutins=scrutins,
        pour=pour,
        contre=faux.copy(),
        abstention=faux.copy(),
        non_votant=non_votant,
        eligible=np.ones((2, 4), dtype=bool),
    )


def votes_presidence() -> pl.DataFrame:
    """La table `votes` correspondante, avec la cause institutionnelle."""
    return pl.DataFrame(
        {
            "scrutin_uid": ["s1", "s2", "s3", "s4", "s1", "s2"],
            "acteur_uid": ["PRESIDENT"] * 4 + ["ORDINAIRE"] * 2,
            "position": ["pour", "nonVotant", "nonVotant", "nonVotant", "pour", "pour"],
            "cause": [None, "PAN", "PAN", "PAN", None, None],
        }
    )


def test_le_perchoir_sort_du_denominateur(monkeypatch):
    """Présider n'est pas s'absenter : les non-votes déclarés sortent du bas.

    Sans ce retrait, la présidente afficherait 1 vote sur 4 — soit 25 % — alors
    qu'elle a voté à chacun des scrutins où elle n'était pas empêchée.
    """
    monkeypatch.setattr(analyze, "load", lambda nom: votes_presidence())
    p = analyze.participation(cube_presidence())
    ligne = p.filter(pl.col("acteur_uid") == "PRESIDENT").to_dicts()[0]

    assert ligne["scrutins_eligibles"] == 4
    assert ligne["non_votants_structurels"] == 3
    assert ligne["denominateur"] == 1
    assert ligne["votes_exprimes"] == 1
    assert ligne["participation"] == pytest.approx(1.0)


def test_une_absence_sans_cause_reste_au_denominateur(monkeypatch):
    """Le retrait ne vaut que pour ce que la source justifie explicitement.

    Le pendant du test précédent : sans lui, on pourrait faire disparaître
    n'importe quelle absence en élargissant la liste des causes.
    """
    monkeypatch.setattr(analyze, "load", lambda nom: votes_presidence())
    p = analyze.participation(cube_presidence())
    ligne = p.filter(pl.col("acteur_uid") == "ORDINAIRE").to_dicts()[0]

    assert ligne["non_votants_structurels"] == 0
    assert ligne["denominateur"] == 4
    assert ligne["participation"] == pytest.approx(0.5)


def test_toute_cause_de_la_source_est_arbitree():
    """Aucune cause de non-vote ne doit rester non classée.

    C'est le test qui manquait. `PAN` était absente de la liste des causes
    structurelles sans que rien ne le signale : la constante avait été écrite
    d'après une supposition sur les codes de l'Assemblée, pas d'après les codes
    réellement présents dans les fichiers.
    """
    try:
        votes = load("votes")
    except FileNotFoundError:
        pytest.skip("tables non construites : lancer `radar update`")

    presentes = set(
        votes.filter(pl.col("cause").is_not_null())["cause"].unique().to_list()
    )
    non_classees = presentes - set(STRUCTURAL_NONVOTE_CAUSES) - CAUSES_NON_STRUCTURELLES
    assert not non_classees, (
        f"causes de non-vote non arbitrées : {sorted(non_classees)}. "
        "Les ranger dans STRUCTURAL_NONVOTE_CAUSES si la fonction empêche de "
        "voter, dans CAUSES_NON_STRUCTURELLES sinon."
    )


def test_la_liste_ne_contient_pas_de_code_mort():
    """Une cause déclarée structurelle qui ne correspond à rien est un signal.

    `PDS` figurait dans la constante et n'apparaît dans aucune ligne : le
    commentaire glosait deux codes différents par « président de séance ». Un
    code mort dans cette liste ne casse rien, mais il signale que la liste n'a
    jamais été confrontée aux données — et c'est ainsi que `PAN` a pu manquer.
    """
    try:
        votes = load("votes")
    except FileNotFoundError:
        pytest.skip("tables non construites : lancer `radar update`")

    presentes = set(
        votes.filter(pl.col("cause").is_not_null())["cause"].unique().to_list()
    )
    mortes = set(STRUCTURAL_NONVOTE_CAUSES) - presentes
    assert not mortes, (
        f"causes déclarées structurelles et absentes de la source : {sorted(mortes)}"
    )
