"""Tests de l'analyse de l'abstention."""

import numpy as np
import polars as pl
import pytest

from radar import abstention
from radar.abstention import (
    decomposition,
    resume_intermediaire,
    scrutins_bascule,
    situer_abstentionnistes,
)
from radar.analyze import VoteCube


class ModeleFactice:
    """Juste ce dont `situer_abstentionnistes` a besoin : des positions."""

    def __init__(self, uids, positions):
        self.deputes = pl.DataFrame({"acteur_uid": uids})
        self.positions = np.asarray(positions, dtype=float).reshape(-1, 1)


def cube_positionne(motifs: list[tuple[str, ...]]) -> tuple[VoteCube, ModeleFactice]:
    """Construit un cube où l'on décide qui vote quoi, et où chacun se situe.

    `motifs` donne, pour chaque scrutin, la position de chacun des 30 députés,
    rangés par position croissante sur l'axe (le premier est le plus à gauche).
    """
    n = 30
    m = len(motifs)
    pour = np.zeros((n, m), dtype=bool)
    contre = np.zeros((n, m), dtype=bool)
    abst = np.zeros((n, m), dtype=bool)
    for j, motif in enumerate(motifs):
        for i, p in enumerate(motif):
            {"p": pour, "c": contre, "a": abst}[p][i, j] = True

    uids = [f"PA{i:02d}" for i in range(n)]
    cube = VoteCube(
        deputes=pl.DataFrame(
            {
                "acteur_uid": uids,
                "nom_complet": uids,
                "groupe": ["G"] * n,
            }
        ),
        scrutins=pl.DataFrame(
            {
                "scrutin_uid": [f"S{j}" for j in range(m)],
                "date": ["2025-01-01"] * m,
                "titre": [f"scrutin {j}" for j in range(m)],
            }
        ),
        pour=pour,
        contre=contre,
        abstention=abst,
        non_votant=np.zeros((n, m), dtype=bool),
        eligible=np.ones((n, m), dtype=bool),
    )
    return cube, ModeleFactice(uids, np.linspace(-1, 1, n))


class TestPositionIntermediaire:
    def test_detecte_une_abstention_au_milieu(self):
        # 10 « contre » à gauche, 10 abstentions au centre, 10 « pour » à droite.
        motif = tuple("c" * 10 + "a" * 10 + "p" * 10)
        cube, modele = cube_positionne([motif])
        t = situer_abstentionnistes(cube, modele)
        assert t.height == 1
        assert t["entre_les_camps"][0]
        assert t["position_relative"][0] == pytest.approx(0.5, abs=0.05)

    def test_detecte_une_abstention_collee_a_un_camp(self):
        # Les abstentionnistes sont mêlés au camp « contre », à gauche.
        motif = tuple("c" * 5 + "a" * 10 + "c" * 5 + "p" * 10)
        cube, modele = cube_positionne([motif])
        t = situer_abstentionnistes(cube, modele)
        # Leur médiane doit être bien plus proche du camp « contre ».
        assert t["position_relative"][0] < 0.35

    def test_ignore_les_scrutins_a_camps_trop_petits(self):
        # Deux abstentionnistes seulement : sous le seuil, le scrutin est écarté.
        motif = tuple("c" * 14 + "a" * 2 + "p" * 14)
        cube, modele = cube_positionne([motif])
        assert situer_abstentionnistes(cube, modele, min_par_camp=10).height == 0
        assert situer_abstentionnistes(cube, modele, min_par_camp=2).height == 1

    def test_l_orientation_du_scrutin_ne_change_rien(self):
        """La mesure doit être la même que le camp « pour » soit à gauche ou à droite.

        `position_relative` est calculée depuis le camp le plus à gauche, pas
        depuis le camp « pour » : inverser le sens du vote ne doit donc pas
        déplacer les abstentionnistes.
        """
        direct = tuple("c" * 10 + "a" * 10 + "p" * 10)
        inverse = tuple("p" * 10 + "a" * 10 + "c" * 10)
        t = situer_abstentionnistes(*cube_positionne([direct, inverse]))
        assert t["position_relative"][0] == pytest.approx(t["position_relative"][1])

    def test_resume(self):
        motifs = [
            tuple("c" * 10 + "a" * 10 + "p" * 10),   # au milieu
            tuple("a" * 10 + "c" * 10 + "p" * 10),   # au-delà d'un camp
        ]
        t = situer_abstentionnistes(*cube_positionne(motifs))
        r = resume_intermediaire(t)
        assert r["scrutins_testes"] == 2
        assert r["part_entre_les_camps"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# Quand l'abstention décide : le seuil n'est pas le même selon le résultat
# --------------------------------------------------------------------------


def _tables(monkeypatch, scrutins: pl.DataFrame, votes=None, positions=None):
    """Sert des tables en mémoire à la place des Parquet."""
    tables = {"scrutins": scrutins, "votes": votes, "positions_groupe": positions}
    monkeypatch.setattr(abstention, "load", lambda nom: tables[nom])


def _scrutin(**kw) -> dict:
    base = {
        "scrutin_uid": "S", "date": "2025-01-01", "portee": "texte",
        "categorie": "ensemble", "titre": "un texte", "sort_code": "adopté",
        "nb_votants": 400, "n_pour": 0, "n_contre": 0, "n_abstention": 0,
    }
    return {**base, **kw}


class TestScrutinsBascule:
    """Un texte est adopté quand les « pour » l'emportent *strictement*.

    L'égalité vaut rejet — c'est ce qui rend les deux seuils asymétriques, et
    c'est exactement ce que le critère naïf « abstentions ≥ écart » manquait.
    """

    def test_adopte_l_egalite_suffit_a_faire_tomber(self, monkeypatch):
        # 200 pour, 190 contre : 10 abstentionnistes passant au contre font 200
        # contre 200, donc rejet. `a = e` suffit.
        _tables(monkeypatch,
                pl.DataFrame([_scrutin(n_pour=200, n_contre=190, n_abstention=10)]))
        d = scrutins_bascule()
        assert d.height == 1
        assert d["bascule_vers"][0] == "rejet"
        assert d["abstentions_requises"][0] == 10

    def test_rejete_l_egalite_ne_suffit_pas(self, monkeypatch):
        # 190 pour, 200 contre : 10 abstentionnistes passant au pour font 200
        # contre 200 — toujours rejeté. Il en faut 11. C'est le cas que
        # l'ancien critère comptait à tort comme une bascule.
        _tables(monkeypatch,
                pl.DataFrame([_scrutin(sort_code="rejeté", n_pour=190,
                                       n_contre=200, n_abstention=10)]))
        assert scrutins_bascule().height == 0

    def test_rejete_une_voix_de_plus_bascule(self, monkeypatch):
        _tables(monkeypatch,
                pl.DataFrame([_scrutin(sort_code="rejeté", n_pour=190,
                                       n_contre=200, n_abstention=11)]))
        d = scrutins_bascule()
        assert d.height == 1
        assert d["bascule_vers"][0] == "adoption"
        assert d["abstentions_requises"][0] == 11

    def test_egalite_pour_contre_bascule_avec_une_seule_abstention(self, monkeypatch):
        # 200 partout : le scrutin est rejeté, une voix de plus l'emporte.
        _tables(monkeypatch,
                pl.DataFrame([_scrutin(sort_code="rejeté", n_pour=200,
                                       n_contre=200, n_abstention=1)]))
        d = scrutins_bascule()
        assert d.height == 1
        assert d["abstentions_requises"][0] == 1


# --------------------------------------------------------------------------
# Décomposition : ce que la méthode ne sait pas classer reste visible
# --------------------------------------------------------------------------


class TestDecomposition:
    def _tables(self):
        """Quatre abstentions : consigne, retrait, groupe partagé, groupe absent."""
        votes = pl.DataFrame(
            {
                "scrutin_uid": ["S1", "S2", "S3", "S4"],
                "acteur_uid": ["PA1", "PA2", "PA3", "PA4"],
                "groupe_uid": ["G1", "G1", "G2", "G3"],
                "position": ["abstention"] * 4,
            }
        )
        scrutins = pl.DataFrame(
            {"scrutin_uid": ["S1", "S2", "S3", "S4"], "portee": ["texte"] * 4}
        )
        positions = pl.DataFrame(
            {
                "scrutin_uid": ["S1", "S2", "S3"],
                "groupe_uid": ["G1", "G1", "G2"],
                "majoritaire": ["abstention", "pour", "pour"],
                # S3 : position dominante à 41 %, le groupe est partagé.
                "part_majoritaire": [0.9, 0.9, 0.41],
                "votants_groupe": [30, 30, 30],
            }
        )
        return scrutins, votes, positions

    def _parts(self, monkeypatch) -> dict:
        _tables(monkeypatch, *self._tables())
        d = decomposition()
        return dict(zip(d["nature"].to_list(), d["abstentions"].to_list()))

    def test_les_trois_natures_sont_comptees(self, monkeypatch):
        parts = self._parts(monkeypatch)
        assert parts == {"consigne": 1, "retrait": 1, "indeterminee": 2}

    def test_le_denominateur_est_le_total_des_abstentions(self, monkeypatch):
        """Le point du reproche : les parts portaient sur le sous-ensemble classable.

        Un groupe partagé ou absent de la table des lignes n'est pas une
        abstention à ignorer, c'est une abstention qu'on ne sait pas trancher.
        """
        _tables(monkeypatch, *self._tables())
        d = decomposition()
        assert int(d["abstentions"].sum()) == 4
        assert float(d["part"].sum()) == pytest.approx(1.0)
