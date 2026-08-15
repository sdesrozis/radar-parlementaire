"""Tests des mandats et de l'éligibilité — qui siégeait, le jour du scrutin.

Le défaut verrouillé ici a été publié sur toutes les pages de scrutin du site.
L'éligibilité était reconstruite comme **un** intervalle, de la première entrée
à la dernière sortie, à partir du champ `dateDebut` de la source. Or :

1. `dateDebut` ne dit pas quand un député entre en fonction — l'Assemblée y met
   la date d'ouverture de la législature pour tout le monde, y compris pour les
   remplaçants arrivés deux ans plus tard. La vraie date est
   `mandature/datePriseFonction`, que le parseur ne lisait pas ;
2. un mandat peut être **interrompu** — le titulaire nommé au Gouvernement rend
   son siège à son suppléant, puis le reprend —, et un intervalle unique comble
   le trou.

Conséquence publiée : 261 214 couples député × scrutin déclarés éligibles à
tort, 86 députés crédités d'absences qu'ils ne pouvaient pas commettre, et des
pages de scrutin annonçant « les 581 députés dont le mandat courait » sous un
régime qui en compte 577.

Trois natures de tests, et aucune ne remplace les autres :

- sur données synthétiques, la **règle** : une interruption n'est pas éligible,
  et la prise de fonction l'emporte sur `dateDebut` ;
- sur données réelles, les **invariants** : `controles.verifier()` ne trouve
  rien, ce qu'aucun test de formule ne peut établir ;
- en régression, la **morsure du garde-fou** : l'ancien calcul, réinjecté, doit
  faire échouer les contrôles. Un contrôle qu'on n'a jamais vu échouer ne
  protège de rien.
"""

import dataclasses

import numpy as np
import polars as pl
import pytest

from radar import controles
from radar.analyze import _eligibilite, build_cube
from radar.config import SIEGES
from radar.parse import MANDAT_SCHEMA, build_deputes, load


def _scrutins(dates: list[str]) -> pl.DataFrame:
    return pl.DataFrame({"date_d": [pl.Series([d]).str.to_date()[0] for d in dates]})


def _deputes(periodes: list[list[tuple[str, str | None]]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "periodes_mandat": [
                [{"debut": a, "fin": b} for a, b in p] for p in periodes
            ]
        }
    )


class TestEligibilite:
    """La règle : une union d'intervalles, jamais un intervalle."""

    def test_une_interruption_n_est_pas_eligible(self):
        # Le cas réel d'Astrid Panosyan-Bouvet : élue, nommée ministre, revenue.
        # Entre les deux, c'est sa suppléante qui siégeait.
        e = _eligibilite(
            _deputes([[("2024-07-08", "2024-10-21"), ("2025-11-06", None)]]),
            _scrutins(["2024-09-01", "2024-12-01", "2025-06-01", "2026-06-02"]),
        )
        assert e[0].tolist() == [True, False, False, True]

    def test_les_bornes_sont_closes_des_deux_cotes(self):
        # La source date la sortie du titulaire au jour J et l'entrée de son
        # suppléant au jour J+1 : les deux bornes comptent, sans se recouvrir.
        e = _eligibilite(
            _deputes([[("2024-07-08", "2024-10-21")]]),
            _scrutins(["2024-07-07", "2024-07-08", "2024-10-21", "2024-10-22"]),
        )
        assert e[0].tolist() == [False, True, True, False]

    def test_une_fin_nulle_vaut_mandat_en_cours(self):
        e = _eligibilite(
            _deputes([[("2026-04-27", None)]]),
            _scrutins(["2026-04-26", "2026-04-27", "2099-01-01"]),
        )
        assert e[0].tolist() == [False, True, True]

    def test_sans_periode_aucun_scrutin_n_est_eligible(self):
        # Ni une erreur ni un zéro de présence : simplement rien à mesurer.
        e = _eligibilite(_deputes([[]]), _scrutins(["2025-01-01"]))
        assert not e.any()


class TestBuildDeputes:
    """La lecture : la prise de fonction, et les périodes conservées."""

    def _tables(self, mandats: list[dict]):
        acteurs = pl.DataFrame(
            {
                "acteur_uid": ["PA1"],
                "civilite": ["Mme"],
                "prenom": ["A"],
                "nom": ["B"],
                "date_naissance": [None],
                "date_deces": [None],
                "profession": [None],
                "cat_socio_pro": [None],
                "uri_hatvp": [None],
            }
        )
        lignes = [
            {**{c: None for c in MANDAT_SCHEMA}, "acteur_uid": "PA1",
             "type_organe": "ASSEMBLEE", "legislature": "17", **m}
            for m in mandats
        ]
        organes = pl.DataFrame(
            {"organe_uid": ["PO1"], "libelle": ["Groupe"], "libelle_abrev": ["G"],
             "position_politique": [None]}
        )
        mandats_df = pl.DataFrame(lignes, schema=MANDAT_SCHEMA)
        return build_deputes(acteurs, mandats_df, organes, 17)

    def test_la_prise_de_fonction_l_emporte_sur_date_debut(self):
        # Le défaut d'origine : la source met l'ouverture de la législature dans
        # `dateDebut` pour tout le monde, remplaçants compris.
        d = self._tables([{"mandat_uid": "PM1", "date_debut": "2024-07-07",
                           "date_prise_fonction": "2026-04-27", "date_fin": None}])
        assert d["mandat_debut"][0] == "2026-04-27"
        assert d["periodes_mandat"][0].to_list() == [
            {"debut": "2026-04-27", "fin": None}]

    def test_deux_mandats_font_deux_periodes_et_non_un_intervalle(self):
        d = self._tables([
            {"mandat_uid": "PM1", "date_debut": "2024-07-07",
             "date_prise_fonction": "2024-07-08", "date_fin": "2024-10-21"},
            {"mandat_uid": "PM2", "date_debut": "2024-07-07",
             "date_prise_fonction": "2025-11-06", "date_fin": None},
        ])
        assert d["periodes_mandat"][0].to_list() == [
            {"debut": "2024-07-08", "fin": "2024-10-21"},
            {"debut": "2025-11-06", "fin": None},
        ]
        assert d["mandat_interrompu"][0] is True
        # Les bornes extrêmes restent servies — elles affichent et décident de
        # `en_exercice` — mais elles ne décident plus de l'éligibilité.
        assert d["mandat_debut"][0] == "2024-07-08"
        assert d["mandat_fin"][0] is None
        assert d["en_exercice"][0] is True

    def test_deux_periodes_qui_se_touchent_ne_sont_pas_une_interruption(self):
        d = self._tables([
            {"mandat_uid": "PM1", "date_debut": "2024-07-07",
             "date_prise_fonction": "2024-07-08", "date_fin": "2025-01-31"},
            {"mandat_uid": "PM2", "date_debut": "2024-07-07",
             "date_prise_fonction": "2025-02-01", "date_fin": None},
        ])
        assert d["mandat_interrompu"][0] is False


# --------------------------------------------------------------------------
# Sur les données réelles
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cube():
    return build_cube(en_exercice_seulement=False)


class TestDonneesReelles:
    def test_aucune_anomalie(self, cube):
        # Le test qui aurait attrapé le défaut, et qu'aucun test de formule ne
        # remplace : il ne vérifie pas un calcul, il compte des députés.
        assert [str(a) for a in controles.verifier(cube)] == []

    def test_l_effectif_ne_depasse_jamais_le_nombre_de_sieges(self, cube):
        assert int(cube.eligible.sum(axis=0).max()) <= SIEGES

    def test_aucun_vote_hors_mandat(self, cube):
        # Confronte nos dates au dépouillement de l'Assemblée : un député qui
        # vote un jour où nous le disons hors mandat prouve que nos dates sont
        # fausses, sans qu'aucun effectif n'ait à être dépassé.
        exprime = cube.pour | cube.contre | cube.abstention | cube.non_votant
        assert not (exprime & ~cube.eligible).any()

    def test_les_mandats_interrompus_de_la_legislature_sont_conserves(self):
        # 29 mandats de la 17ᵉ comportent une interruption. Le jour où la source
        # n'en publiera plus aucun, ce test le dira au lieu de laisser croire
        # que le cas n'existe pas.
        d = load("deputes")
        interrompus = d.filter(pl.col("mandat_interrompu"))
        assert interrompus.height > 0
        for periodes in interrompus["periodes_mandat"].to_list():
            assert len(periodes) > 1

    def test_le_releve_d_un_scrutin_s_additionne(self, cube):
        # La phrase publiée sur chaque page — « les N députés dont le mandat
        # courait » — n'est vraie que si les six statuts partitionnent
        # l'effectif : personne ne doit porter deux positions sur un scrutin.
        positions = (cube.pour.astype(np.int8) + cube.contre + cube.abstention
                     + cube.non_votant)
        assert int(positions.max()) <= 1


class TestLeGardeFouMord:
    """L'ancien calcul, réinjecté, doit faire échouer les contrôles.

    Sans ce test, rien ne distingue un contrôle qui protège d'un contrôle qui
    ne s'exécute pas : les six invariants passeraient tout aussi silencieusement
    sur un `verifier()` qui rendrait toujours la liste vide.
    """

    def _cube_ancien(self, cube):
        """Le cube tel qu'il était : un intervalle unique assis sur `dateDebut`."""
        dates = cube.scrutins["date_d"].to_numpy().astype("datetime64[D]")
        debuts = (
            load("mandats")
            .filter((pl.col("type_organe") == "ASSEMBLEE")
                    & (pl.col("legislature") == "17"))
            .group_by("acteur_uid")
            .agg(pl.col("date_debut").min())
        )
        par_uid = dict(zip(debuts["acteur_uid"].to_list(),
                           debuts["date_debut"].to_list()))
        debut = np.array([np.datetime64(par_uid[u])
                          for u in cube.deputes["acteur_uid"].to_list()],
                         dtype="datetime64[D]")
        fin = np.array(
            [np.datetime64("2999-12-31") if any(p["fin"] is None for p in ps)
             else np.datetime64(max(p["fin"] for p in ps))
             for ps in cube.deputes["periodes_mandat"].to_list()],
            dtype="datetime64[D]",
        )
        eligible = (dates[None, :] >= debut[:, None]) & (dates[None, :] <= fin[:, None])
        return dataclasses.replace(cube, eligible=eligible)

    def test_l_ancien_calcul_est_refuse(self, cube):
        anomalies = controles.verifier(self._cube_ancien(cube))
        noms = {a.controle for a in anomalies}
        assert "effectifs" in noms, "un effectif supérieur à 577 doit être refusé"
        assert "sieges_uniques" in noms, "deux députés sur un siège doivent être vus"

    def test_l_ancien_calcul_arrete_la_publication(self, cube):
        with pytest.raises(controles.DonneesInvalides):
            controles.exiger(self._cube_ancien(cube))
