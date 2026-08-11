"""Tests du calcul d'accord : c'est là que se cachent les faux 100 %."""

import numpy as np
import polars as pl
import pytest

from radar.analyze import VoteCube, agreement, plus_proches


def cube_jouet() -> VoteCube:
    """Quatre députés, six scrutins, positions choisies à la main.

        A : pour   pour   contre contre abst   pour
        B : pour   pour   contre contre abst   pour     (identique à A)
        C : contre contre pour   pour   abst   contre   (opposé à A sauf s5)
        D : pour   —      —      —      —      —        (un seul vote)
    """
    noms = ["A", "B", "C", "D"]
    pour = np.array(
        [
            [1, 1, 0, 0, 0, 1],
            [1, 1, 0, 0, 0, 1],
            [0, 0, 1, 1, 0, 0],
            [1, 0, 0, 0, 0, 0],
        ], dtype=bool,
    )
    contre = np.array(
        [
            [0, 0, 1, 1, 0, 0],
            [0, 0, 1, 1, 0, 0],
            [1, 1, 0, 0, 0, 1],
            [0, 0, 0, 0, 0, 0],
        ], dtype=bool,
    )
    abst = np.zeros((4, 6), dtype=bool)
    abst[:3, 4] = True

    deputes = pl.DataFrame(
        {
            "acteur_uid": ["PA1", "PA2", "PA3", "PA4"],
            "nom_complet": noms,
            "groupe": ["G1", "G1", "G2", "G2"],
        }
    )
    scrutins = pl.DataFrame({"scrutin_uid": [f"S{i}" for i in range(6)]})
    return VoteCube(
        deputes=deputes,
        scrutins=scrutins,
        pour=pour,
        contre=contre,
        abstention=abst,
        non_votant=np.zeros((4, 6), dtype=bool),
        eligible=np.ones((4, 6), dtype=bool),
    )


class TestAgreement:
    def test_deux_deputes_identiques_sont_a_cent_pour_cent(self):
        taux, _ = agreement(cube_jouet(), min_communs=1)
        assert taux[0, 1] == pytest.approx(1.0)

    def test_opposition_ne_compte_que_les_positions_communes(self):
        # A et C ne partagent que l'abstention du scrutin 5 : 1 accord sur 6.
        taux, communs = agreement(cube_jouet(), min_communs=1)
        assert communs[0, 2] == 6
        assert taux[0, 2] == pytest.approx(1 / 6)

    def test_absence_n_est_ni_accord_ni_desaccord(self):
        # D n'a voté qu'une fois, et comme A : 1 scrutin commun, 100 % d'accord.
        taux, communs = agreement(cube_jouet(), min_communs=1)
        assert communs[0, 3] == 1
        assert taux[0, 3] == pytest.approx(1.0)

    def test_le_seuil_ecarte_les_faux_cent_pour_cent(self):
        # C'est tout l'intérêt du seuil : un 100 % sur un vote n'est pas un signal.
        taux, _ = agreement(cube_jouet(), min_communs=3)
        assert np.isnan(taux[0, 3])
        assert not np.isnan(taux[0, 1])

    def test_diagonale_neutralisee(self):
        taux, _ = agreement(cube_jouet(), min_communs=1)
        assert np.isnan(np.diag(taux)).all()

    def test_matrice_symetrique(self):
        taux, _ = agreement(cube_jouet(), min_communs=1)
        assert np.allclose(taux, taux.T, equal_nan=True)


class TestPlusProches:
    def test_classement_par_accord_decroissant(self):
        d = plus_proches(cube_jouet(), "A", k=3, min_communs=1)
        assert d["depute"][0] == "B"
        assert d["accord"].to_list() == sorted(d["accord"].to_list(), reverse=True)

    def test_hors_groupe_exclut_son_propre_groupe(self):
        d = plus_proches(cube_jouet(), "A", k=3, hors_groupe=True, min_communs=1)
        assert "B" not in d["depute"].to_list()
        assert set(d["groupe"].to_list()) == {"G2"}

    def test_inverse_donne_les_plus_eloignes(self):
        d = plus_proches(cube_jouet(), "A", k=1, inverse=True, min_communs=1)
        assert d["depute"][0] == "C"

    def test_recherche_partielle_et_ambiguite(self):
        cube = cube_jouet()
        assert cube.index_depute("a") == 0
        with pytest.raises(KeyError, match="aucun député"):
            cube.index_depute("Zorglub")
