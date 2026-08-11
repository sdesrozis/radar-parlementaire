"""Tests de l'analyse de l'abstention."""

import numpy as np
import polars as pl
import pytest

from radar.abstention import resume_intermediaire, situer_abstentionnistes
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
