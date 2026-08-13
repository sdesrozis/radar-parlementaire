"""Tests de la frontière entre les algorithmes et ce qui les affiche.

`vues.py` ne calcule presque rien : ce qui peut y casser n'est pas le calcul,
c'est le passage du calcul à la page. Trois choses s'y perdent silencieusement,
et ces tests les verrouillent :

1. **Un NaN sérialisé** casse `JSON.parse` côté navigateur — la page reste
   blanche sans message. Une mesure absente doit sortir en `null`. Cela valait
   pour l'API disparue ; cela vaut identiquement pour le JSON que le générateur
   incruste dans chaque page.
2. **Une dissidence comptée sans ligne de groupe** : quand aucune position ne
   réunit la majorité des suffrages du groupe, le vote n'est ni dissident ni
   conforme. `null`, pas `false`.
3. **Un rang qui suit l'ordre d'arrivée** plutôt que l'axe estimé.

Ce qui touche au serveur de développement est dans `test_site_serveur.py` : ce
sont deux étages différents, et ils ne se testent plus ensemble.
"""

import json
import math

import numpy as np
import polars as pl
import pytest

from radar import vues


class TestSerialisation:
    def test_nan_devient_null(self):
        assert vues._propre(float("nan")) is None
        assert vues._propre(float("inf")) is None

    def test_nan_numpy_aussi(self):
        assert vues._propre(np.float64("nan")) is None
        assert vues._propre(np.int64(3)) == 3
        assert vues._propre(np.bool_(True)) is True

    def test_recursion_dans_les_structures(self):
        valeur = {"a": [1.0, float("nan")], "b": {"c": float("nan")}}
        assert vues._propre(valeur) == {"a": [1.0, None], "b": {"c": None}}

    def test_le_resultat_passe_json_strict(self):
        """`allow_nan=False` est ce que fait le serveur : rien ne doit lever."""
        df = pl.DataFrame({"x": [1.0, float("nan"), None], "n": [1, 2, 3]})
        json.dumps(vues.lignes(df), allow_nan=False)

    def test_les_dates_deviennent_des_chaines(self):
        df = pl.DataFrame({"d": ["2026-01-02"]}).with_columns(
            pl.col("d").str.to_date().alias("d")
        )
        assert vues.lignes(df) == [{"d": "2026-01-02"}]


class TestOuverture:
    """La part de cosignatures hors groupe, corrigée de la taille du groupe."""

    def reseau(self):
        from radar.cosign import ReseauCosignatures

        deputes = pl.DataFrame(
            {
                "acteur_uid": ["PA1", "PA2", "PA3", "PA4"],
                "nom_complet": ["A", "B", "C", "D"],
                "groupe": ["G1", "G1", "G1", "G2"],
            }
        )
        communs = np.array(
            [[100, 80, 10, 5], [80, 100, 10, 5], [10, 10, 40, 2], [5, 5, 2, 20]],
            dtype=float,
        )
        return ReseauCosignatures(
            deputes=deputes, communs=communs,
            signatures=np.array([100, 100, 40, 20]), n_amendements=200,
        )

    def test_le_ratio_corrige_la_taille_du_groupe(self):
        """D est seul dans son groupe : 100 % hors groupe ne prouve rien.

        Sans correction, il caracolerait en tête. Rapporté au hasard — pour lui,
        tout l'hémicycle est « hors groupe » — son ratio retombe à 1.
        """
        d = self.reseau()
        o = vues._ouverture(d).sort("acteur_uid")
        seul = o.filter(pl.col("acteur_uid") == "PA4").to_dicts()[0]
        assert seul["part_hors_groupe"] == pytest.approx(1.0)
        assert seul["ouverture"] == pytest.approx(1.0)

    def test_un_depute_replie_sur_son_groupe_descend_sous_un(self):
        o = vues._ouverture(self.reseau())
        a = o.filter(pl.col("acteur_uid") == "PA1").to_dicts()[0]
        # A : 5 liens hors groupe sur 95 → bien moins que le hasard ne prédirait.
        assert a["ouverture"] < 1.0

    def test_sans_aucune_cosignature_la_mesure_est_absente(self):
        """Zéro lien ne fait pas une ouverture de zéro : ça ne se mesure pas."""
        from radar.cosign import ReseauCosignatures

        reseau = ReseauCosignatures(
            deputes=pl.DataFrame(
                {"acteur_uid": ["PA1", "PA2"], "nom_complet": ["A", "B"],
                 "groupe": ["G1", "G2"]}
            ),
            communs=np.zeros((2, 2)),
            signatures=np.array([0, 0]),
            n_amendements=0,
        )
        o = vues._ouverture(reseau)
        assert o["part_hors_groupe"].to_list() == [None, None]


class TestPositions:
    def test_le_rang_suit_l_axe(self, monkeypatch):
        """Le rang doit être celui de l'axe, pas l'ordre d'arrivée des lignes."""
        table = pl.DataFrame(
            {
                "acteur_uid": ["PA1", "PA2", "PA3"],
                "nom_complet": ["A", "B", "C"],
                "groupe": ["G1", "G1", "G2"],
                "axe1": [0.5, -1.5, 2.0],
                "borne_basse": [0.3, -1.7, 1.8],
                "borne_haute": [0.7, -1.3, 2.2],
            }
        )
        monkeypatch.setattr(vues.ideal, "intervalles", lambda *a, **k: table)
        p = vues._positions(cube=None, bootstrap=10)
        rangs = dict(zip(p["acteur_uid"].to_list(), p["rang_axe1"].to_list()))
        assert rangs == {"PA2": 1, "PA1": 2, "PA3": 3}


class TestLigneDeGroupe:
    """La dissidence n'existe que là où le groupe a une ligne.

    Reproduit la logique servie par `votes_du_depute` sur un cas fabriqué : un
    groupe partagé (aucune position au-dessus de la moitié des suffrages) ne
    produit ni dissidence ni conformité.
    """

    def cas(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "position": ["pour", "contre", "abstention", "pour"],
                "majoritaire": ["pour", "pour", "pour", None],
                "part_majoritaire": [0.9, 0.9, 0.41, None],
            }
        ).with_columns(
            dissident=pl.when(
                pl.col("majoritaire").is_not_null()
                & (pl.col("part_majoritaire") > 0.5)
                & pl.col("position").is_in(["pour", "contre", "abstention"])
            )
            .then(pl.col("position") != pl.col("majoritaire"))
            .otherwise(None)
        )

    def test_conforme_dissident_et_indetermine(self):
        assert self.cas()["dissident"].to_list() == [False, True, None, None]

    def test_le_groupe_partage_sort_du_denominateur(self):
        d = self.cas()["dissident"]
        assert d.null_count() == 2  # groupe partagé + ligne absente
        assert not math.isnan(d.drop_nulls().mean())
