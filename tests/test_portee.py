"""Tests du classement des scrutins par portée politique.

Ce classement conditionne toutes les conclusions du notebook 02 : s'il range mal
les scrutins, l'écart mesuré entre « amendements » et « votes sur l'ensemble »
ne veut plus rien dire.
"""

import polars as pl
import pytest

from radar.parse import CATEGORIES, ORDRE_PORTEES, PORTEES, _categoriser


def categoriser(titre: str) -> str:
    return (
        pl.DataFrame({"titre": [titre]})
        .select(_categoriser(pl.col("titre")).alias("c"))["c"][0]
    )


class TestCategorisation:
    @pytest.mark.parametrize(
        "titre,attendu",
        [
            ("l'ensemble de la proposition de loi pour une montagne vivante", "ensemble"),
            ("L'ensemble du projet de loi de finances", "ensemble"),
            ("l'amendement n° 7 du Gouvernement au projet de loi", "amendement"),
            ("le sous-amendement n° 2 de Mme Goulet à l'amendement n° 1", "amendement"),
            ("les amendements identiques n° 12 et 13", "amendement"),
            ("l'article premier du projet de loi", "article"),
            ("la motion de censure déposée en application de l'article 49", "motion_censure"),
            ("la motion de rejet préalable déposée par M. X", "motion_procedure"),
            ("la déclaration du Gouvernement sur la politique étrangère", "declaration"),
            ("la première partie du projet de loi de finances pour 2025", "autre"),
        ],
    )
    def test_titres_reels(self, titre, attendu):
        assert categoriser(titre) == attendu

    def test_sous_amendement_avant_amendement(self):
        # Piège rencontré : « le sous-amendement » ne doit pas tomber dans
        # « autre » faute d'avoir prévu l'espace après « le ».
        assert categoriser("le sous-amendement n° 3692 de M. Cazeneuve") == "amendement"

    def test_motion_de_censure_avant_motion_procedure(self):
        # L'ordre des motifs compte : une motion de censure ne doit pas être
        # classée comme une simple motion de procédure.
        titre = "la motion de censure déposée par M. Vallaud"
        assert categoriser(titre) == "motion_censure"
        assert [n for n, _ in CATEGORIES].index("motion_censure") < [
            n for n, _ in CATEGORIES
        ].index("motion_procedure")

    def test_titre_vide(self):
        assert categoriser("") == "autre"


class TestPortees:
    def test_toute_categorie_a_une_portee(self):
        for nom, _ in CATEGORIES:
            assert nom in PORTEES
        assert "autre" in PORTEES

    def test_portees_sont_dans_l_ordre_connu(self):
        assert set(PORTEES.values()) <= set(ORDRE_PORTEES)

    def test_l_enjeu_croit_avec_l_ordre(self):
        # `texte` doit être le dernier : c'est sur lui que porte la conclusion
        # « l'accord change quand l'enjeu monte ».
        assert ORDRE_PORTEES[-1] == "texte"
        assert PORTEES["ensemble"] == "texte"
        assert PORTEES["motion_censure"] == "texte"
        assert PORTEES["amendement"] == "detail"
