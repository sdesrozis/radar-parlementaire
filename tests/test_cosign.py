"""Tests du réseau de cosignatures.

Trois biais ont été rencontrés en construisant ce réseau, et chacun produisait
un classement plausible mais faux. Ces tests les verrouillent.
"""

import numpy as np
import polars as pl
import pytest

from radar.cosign import ReseauCosignatures, courtiers, paires_cosignataires
from radar.parse import _strip_html


def reseau_jouet() -> ReseauCosignatures:
    """Quatre députés, deux groupes de tailles très différentes.

    A, B, C forment un groupe de trois ; D est seul dans le sien. A et B
    cosignent beaucoup ensemble ; D cosigne peu, mais forcément hors de son
    groupe puisqu'il n'a pas de collègue.
    """
    deputes = pl.DataFrame(
        {
            "acteur_uid": ["PA1", "PA2", "PA3", "PA4"],
            "nom_complet": ["A", "B", "C", "D"],
            "groupe": ["G1", "G1", "G1", "G2"],
        }
    )
    communs = np.array(
        [
            [100, 80, 10, 5],
            [80, 100, 10, 5],
            [10, 10, 40, 2],
            [5, 5, 2, 20],
        ],
        dtype=float,
    )
    return ReseauCosignatures(
        deputes=deputes,
        communs=communs,
        signatures=np.array([100, 100, 40, 20]),
        n_amendements=200,
    )


class TestJaccard:
    def test_normalise_par_le_volume(self):
        # A et B : 80 communs sur (100 + 100 - 80) = 120.
        j = reseau_jouet().jaccard(min_signatures=1)
        assert j[0, 1] == pytest.approx(80 / 120)

    def test_diagonale_neutralisee(self):
        j = reseau_jouet().jaccard(min_signatures=1)
        assert np.isnan(np.diag(j)).all()

    def test_seuil_ecarte_les_deputes_peu_actifs(self):
        # Sans seuil, un député à trois amendements peut afficher une
        # affinité de 1 sur un hasard de calendrier.
        j = reseau_jouet().jaccard(min_signatures=50)
        assert np.isnan(j[3]).all()   # D n'a que 20 signatures
        assert not np.isnan(j[0, 1])

    def test_symetrie(self):
        j = reseau_jouet().jaccard(min_signatures=1)
        assert np.allclose(j, j.T, equal_nan=True)


class TestCourtiers:
    def test_le_ratio_corrige_la_taille_du_groupe(self):
        """Le biais principal : appartenir à un petit groupe gonfle la part brute.

        D est seul dans G2 : 100 % de ses cosignatures sont « hors groupe », ce
        qui est mécanique et ne dit rien. Son ratio observé/attendu doit le
        remettre à sa place, alors que la part brute le sacrerait champion.
        """
        r = reseau_jouet()
        d = courtiers(r, k=10, min_signatures=1)
        ligne_d = d.filter(pl.col("nom_complet") == "D")
        # D est écarté (groupe d'un seul membre) ou, s'il est présent, son
        # attendu vaut 1 et son ratio ne peut pas dépasser 1.
        if ligne_d.height:
            assert ligne_d["part_hors_groupe"][0] == pytest.approx(1.0)
            assert ligne_d["ratio"][0] <= 1.0 + 1e-9

    def test_part_attendue_depend_de_l_effectif(self):
        d = courtiers(reseau_jouet(), k=10, min_signatures=1)
        attendus = dict(zip(d["groupe"], d["part_attendue"]))
        if "G1" in attendus:
            # n=4, effectif G1 = 3 → (4-3)/(4-1) = 1/3
            assert attendus["G1"] == pytest.approx(1 / 3)

    def test_non_inscrits_exclus(self):
        r = reseau_jouet()
        r.deputes = r.deputes.with_columns(
            pl.when(pl.col("nom_complet") == "C").then(pl.lit("NI"))
            .otherwise(pl.col("groupe")).alias("groupe")
        )
        d = courtiers(r, k=10, min_signatures=1)
        assert "NI" not in d["groupe"].to_list()


class TestPaires:
    def test_inter_groupes_exclut_les_binomes_internes(self):
        d = paires_cosignataires(
            reseau_jouet(), k=10, min_signatures=1, min_communs=1, inter_groupes=True
        )
        assert all(a != b for a, b in zip(d["groupe_a"], d["groupe_b"]))

    def test_min_communs_filtre_les_liens_anecdotiques(self):
        d = paires_cosignataires(
            reseau_jouet(), k=10, min_signatures=1, min_communs=50, inter_groupes=False
        )
        assert (d["amendements_communs"] >= 50).all()


class TestNettoyageHtml:
    def test_entites_decodees(self):
        # 99 % des exposés sommaires arrivaient encodés : sans ce décodage, la
        # détection de sujets tokenisait « dactionnel ».
        assert _strip_html("R&#x00E9;dactionnel") == "Rédactionnel"

    def test_balises_retirees(self):
        assert _strip_html("<p>Cet amendement <b>vise</b> à</p>") == "Cet amendement vise à"

    def test_double_encodage(self):
        assert _strip_html("&amp;#x00E9;t&#x00E9;") == "été"

    def test_espaces_normalises(self):
        assert _strip_html("a&nbsp;&nbsp;  b") == "a b"

    def test_vide_devient_nul(self):
        assert _strip_html("<p></p>") is None
        assert _strip_html(None) is None
