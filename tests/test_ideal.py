"""Tests du modèle de points idéaux.

Un modèle itératif se trompe silencieusement : il rend des nombres plausibles
même quand l'optimisation diverge. Ces tests vérifient les propriétés qui
doivent tenir par construction — et deux d'entre eux ont effectivement attrapé
des bugs pendant l'écriture.
"""

import numpy as np
import polars as pl
import pytest

from radar.analyze import VoteCube
from radar.ideal import _ajustement, _sigmoide, estimer


def cube_synthetique(
    n: int = 60, m: int = 80, graine: int = 0, dimensions: int = 1
) -> tuple[VoteCube, np.ndarray]:
    """Fabrique des votes *à partir du modèle*, avec des positions connues.

    C'est le seul moyen de vérifier qu'une estimation retrouve la vérité : sur
    des données réelles, on n'a rien à quoi comparer.
    """
    rng = np.random.default_rng(graine)
    x_vrai = rng.normal(0, 1, (n, dimensions))
    beta = rng.normal(0, 1.5, (m, dimensions))
    alpha = rng.normal(0, 1, m)

    p = _sigmoide(x_vrai @ beta.T - alpha)
    pour = rng.random((n, m)) < p
    contre = ~pour

    groupes = ["G_bas" if v < 0 else "G_haut" for v in x_vrai[:, 0]]
    deputes = pl.DataFrame(
        {
            "acteur_uid": [f"PA{i}" for i in range(n)],
            "nom_complet": [f"Député {i}" for i in range(n)],
            "groupe": groupes,
        }
    )
    scrutins = pl.DataFrame(
        {
            "scrutin_uid": [f"S{j}" for j in range(m)],
            "date": ["2025-01-01"] * m,
            "categorie": ["ensemble"] * m,
            "titre": [f"scrutin {j}" for j in range(m)],
        }
    )
    cube = VoteCube(
        deputes=deputes,
        scrutins=scrutins,
        pour=pour,
        contre=contre,
        abstention=np.zeros((n, m), dtype=bool),
        non_votant=np.zeros((n, m), dtype=bool),
        eligible=np.ones((n, m), dtype=bool),
    )
    return cube, x_vrai


class TestRecuperationDesPositions:
    def test_retrouve_l_ordre_des_positions_connues(self):
        cube, x_vrai = cube_synthetique(n=80, m=150)
        m = estimer(cube, dimensions=1, groupe_ancrage="G_bas", contestation_min=0.0)
        ordre = {u: i for i, u in enumerate(m.deputes["acteur_uid"].to_list())}
        vrai = np.array([x_vrai[int(u[2:]), 0] for u in ordre])
        # Corrélation forte avec les positions ayant servi à générer les votes.
        r = np.corrcoef(vrai, m.positions[:, 0])[0, 1]
        assert r > 0.9, f"corrélation trop faible : {r:.2f}"

    def test_orientation_stable(self):
        """L'ancrage doit garantir la même orientation d'une exécution à l'autre.

        Sans lui, le modèle est libre de renvoyer l'axe inversé — pas faux, mais
        illisible d'une exécution sur l'autre.
        """
        cube, _ = cube_synthetique()
        a = estimer(cube, dimensions=1, groupe_ancrage="G_bas", contestation_min=0.0)
        b = estimer(cube, dimensions=1, groupe_ancrage="G_bas", contestation_min=0.0)
        assert np.allclose(a.positions, b.positions)
        bas = np.array([g == "G_bas" for g in a.deputes["groupe"].to_list()])
        assert a.positions[bas, 0].mean() < 0


class TestConvergence:
    def test_la_vraisemblance_ne_se_degrade_pas_avec_les_iterations(self):
        """Le test qui a révélé le bug principal.

        L'étape qui ajuste les positions oubliait le terme de difficulté −α et
        donnait à chaque député sa propre constante : elle n'optimisait donc pas
        la même fonction que l'étape précédente, et la vraisemblance *baissait*
        au fil des itérations.
        """
        cube, _ = cube_synthetique(n=60, m=120)
        lls = [
            estimer(cube, dimensions=1, iterations=it, contestation_min=0.0,
                    groupe_ancrage="G_bas").log_vraisemblance
            for it in (5, 20, 60)
        ]
        assert lls[-1] > lls[0] - abs(lls[0]) * 0.05

    def test_une_dimension_de_plus_ajuste_mieux_en_echantillon(self):
        """Propriété d'emboîtement : ajouter un axe ne peut pas dégrader l'ajustement.

        Une violation signalerait une divergence de l'optimisation, pas un
        résultat sur les données.
        """
        cube, _ = cube_synthetique(n=60, m=120, dimensions=2)
        a = estimer(cube, dimensions=1, contestation_min=0.0, groupe_ancrage="G_bas")
        b = estimer(cube, dimensions=2, contestation_min=0.0, groupe_ancrage="G_bas")
        assert b.apre >= a.apre - 1e-3


class TestPenalisation:
    def test_penaliser_borne_les_discriminations(self):
        # Sans pénalisation, la séparation quasi parfaite fait exploser β.
        cube, _ = cube_synthetique(n=60, m=100)
        faible = estimer(cube, ridge_discrimination=1e-4, contestation_min=0.0,
                         groupe_ancrage="G_bas")
        forte = estimer(cube, ridge_discrimination=5.0, contestation_min=0.0,
                        groupe_ancrage="G_bas")
        assert np.abs(forte.discrimination).max() < np.abs(faible.discrimination).max()

    def test_positions_normalisees(self):
        cube, _ = cube_synthetique()
        m = estimer(cube, dimensions=1, contestation_min=0.0, groupe_ancrage="G_bas")
        assert m.positions[:, 0].mean() == pytest.approx(0, abs=1e-6)
        assert m.positions[:, 0].std() == pytest.approx(1, abs=1e-6)


class TestValidation:
    def test_les_votes_de_test_sont_exclus_de_l_apprentissage(self):
        cube, _ = cube_synthetique(n=60, m=120)
        m = estimer(cube, part_test=0.25, contestation_min=0.0, groupe_ancrage="G_bas")
        assert m.n_votes_test > 0
        assert m.apre_test is not None
        # L'ajustement hors échantillon ne peut pas dépasser celui en échantillon
        # de beaucoup ; s'il le faisait, c'est que la séparation a fuité.
        assert m.apre_test <= m.apre + 0.05

    def test_apre_vaut_un_quand_tout_est_predit(self):
        Y = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        M = np.ones_like(Y)
        correct, apre = _ajustement(Y, M, Y * 0.99 + (1 - Y) * 0.01)
        assert correct == pytest.approx(1.0)
        assert apre == pytest.approx(1.0)

    def test_apre_vaut_zero_quand_on_ne_fait_pas_mieux_que_la_majorite(self):
        # Trois « pour », un « contre » : prédire toujours « pour » laisse
        # exactement l'erreur de la minorité, donc APRE nul.
        Y = np.array([[1.0], [1.0], [1.0], [0.0]])
        M = np.ones_like(Y)
        _, apre = _ajustement(Y, M, np.full_like(Y, 0.9))
        assert apre == pytest.approx(0.0)


class TestGardeFous:
    def test_leve_si_trop_peu_de_votes(self):
        cube, _ = cube_synthetique(n=5, m=3)
        with pytest.raises(ValueError, match="trop peu de votes"):
            estimer(cube, min_votes_scrutin=100, groupe_ancrage="G_bas")
