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
                # L'assiette réelle du modèle voyage avec la position : c'est
                # elle que la fiche annonce sous l'intervalle.
                "votes_modele": [120, 45, 200],
                "modele_scrutins": [127, 127, 127],
                "modele_scrutins_offerts": [245, 245, 245],
            }
        )
        monkeypatch.setattr(vues.ideal, "intervalles", lambda *a, **k: table)
        p = vues._positions(cube=None, bootstrap=10)
        rangs = dict(zip(p["acteur_uid"].to_list(), p["rang_axe1"].to_list()))
        assert rangs == {"PA2": 1, "PA1": 2, "PA3": 3}
        # Le nombre de votes réellement lus par le modèle doit rester attaché au
        # bon député après le tri : c'est lui que la fiche publie à côté de
        # l'intervalle, et un décalage y ferait expliquer une largeur par la
        # matière d'un autre.
        lus = dict(zip(p["acteur_uid"].to_list(), p["votes_modele"].to_list()))
        assert lus == {"PA1": 120, "PA2": 45, "PA3": 200}


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


class TestStatutDeVote:
    """Les cinq façons de ne pas voter « pour », et pourquoi elles diffèrent.

    Le relevé de la fiche part des scrutins et non des votes : un député absent
    n'a pas de ligne dans la table des votes, et une liste construite depuis
    les votes tuerait silencieusement ce qu'elle prétend montrer. Le statut est
    donc calculé sur une jointure à gauche, où presque tout peut être `null`.

    L'ordre des cas est ce qui se vérifie ici. Il n'est pas commutatif : un
    ministre est empêché avant d'être absent, et un scrutin tenu avant son
    élection n'est ni l'un ni l'autre.
    """

    def cas(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "eligible": [True, True, True, True, True, False, False],
                "position": ["pour", "contre", "abstention", None, "nonVotant",
                             None, "pour"],
                "cause": [None, None, None, None, "MG", None, None],
            }
        ).with_columns(vues._statut_vote())

    def test_les_six_statuts(self):
        assert self.cas()["statut"].to_list() == [
            "pour", "contre", "abstention",
            "absent",       # mandat courant, aucune ligne de vote
            "empeche",      # membre du Gouvernement : la source le dit
            "hors_mandat",  # scrutin antérieur à son élection
            "hors_mandat",  # et l'inéligibilité prime sur tout le reste
        ]

    def test_aucun_statut_hors_du_vocabulaire_publie(self):
        """Le gabarit traduit chaque statut : un statut non prévu casserait la page."""
        assert set(self.cas()["statut"]) <= set(vues.STATUTS_VOTE)


class TestBilan:
    """L'activité en un coup d'œil : trois assiettes qui doivent s'additionner."""

    def depute(self, **surcharges) -> dict:
        base = {
            "votes_exprimes": 483, "scrutins_votables": 8434,
            "scrutins_eligibles": 8434, "votes_delegues": 29,
            "votes_engageants": 77, "engageants_votables": 245,
            "engageants_eligibles": 245, "engageants_delegues": 19,
        }
        return {**base, **surcharges}

    def test_les_autres_scrutins_sont_la_soustraction_exacte(self):
        """Un troisième calcul serait une troisième occasion de diverger."""
        eng, autres, tous = vues._bilan(self.depute())
        for champ in ("exprimes", "votables", "eligibles", "delegues"):
            assert eng[champ] + autres[champ] == tous[champ]

    def test_chaque_taux_est_le_sien(self):
        eng, autres, tous = vues._bilan(self.depute())
        assert eng["taux"] == pytest.approx(77 / 245)
        assert autres["taux"] == pytest.approx((483 - 77) / (8434 - 245))
        assert tous["taux"] == pytest.approx(483 / 8434)

    def test_un_depute_sans_aucun_suffrage_n_a_pas_un_taux_de_zero(self):
        """C'est l'accusation la plus grave du site, portée sur un trou de source."""
        muet = self.depute(votes_exprimes=0, votes_engageants=0,
                           votes_delegues=0, engageants_delegues=0)
        assert [r["taux"] for r in vues._bilan(muet)] == [None, None, None]

    def test_un_denominateur_nul_ne_divise_pas(self):
        entrant = self.depute(engageants_votables=0, engageants_eligibles=0,
                              votes_engageants=0, engageants_delegues=0)
        assert vues._bilan(entrant)[0]["taux"] is None

    def test_les_colonnes_absentes_valent_zero_et_non_none(self):
        """`fiche()` sert des `null` : la soustraction ne doit pas exploser."""
        vide = {k: None for k in self.depute()}
        assert [r["exprimes"] for r in vues._bilan(vide)] == [0, 0, 0]


class TestTauxAdoption:
    """Le taux d'adoption se divise par les amendements examinés."""

    def table(self) -> pl.DataFrame:
        return pl.DataFrame(
            {"amendements": [100, 10, 5], "examines": [50, 10, 0], "adoptes": [25, 1, 0]}
        ).with_columns(vues.analyze.taux_adoption())

    def test_le_denominateur_est_l_examen_pas_le_depot(self):
        """Diviser par les dépôts ferait baisser le taux à chaque dépôt."""
        assert self.table()["taux_adoption"].to_list()[:2] == [0.5, 0.1]

    def test_rien_d_examine_ne_donne_pas_zero(self):
        """Un texte encore en navette n'est pas un échec : c'est une attente."""
        assert self.table()["taux_adoption"].to_list()[2] is None


class TestSolennels:
    """L'assiette des scrutins solennels : servie, mais jamais additionnable."""

    def depute(self, **surcharges) -> dict:
        base = {
            "votes_exprimes": 1508, "votes_solennels": 62,
            "solennels_votables": 72, "solennels_eligibles": 72,
            "solennels_delegues": 20, "participation_solennels": 62 / 72,
            "part_delegation_solennels": 20 / 62,
        }
        return {**base, **surcharges}

    def test_le_taux_est_celui_calcule_en_amont(self):
        """Le site ne refait aucune division : un seul chiffre par mesure."""
        assert vues._solennels(self.depute())["taux"] == pytest.approx(62 / 72)

    def test_un_depute_sans_aucun_suffrage_n_a_pas_un_taux_de_zero(self):
        # Même règle que partout : une absence de donnée n'est pas un zéro, et
        # elle se juge sur l'ensemble des scrutins, pas sur cette assiette-ci.
        muet = self.depute(votes_exprimes=0, votes_solennels=0,
                           participation_solennels=None)
        assert vues._solennels(muet)["taux"] is None

    def test_un_denominateur_nul_ne_divise_pas(self):
        assert vues._solennels(self.depute(solennels_votables=0))["taux"] is None

    def test_les_colonnes_absentes_valent_zero_et_non_none(self):
        vide = {k: None for k in self.depute()}
        r = vues._solennels(vide)
        assert (r["exprimes"], r["votables"], r["delegues"]) == (0, 0, 0)

    def test_la_delegation_est_servie_avec_l_assiette(self):
        """C'est sur ces votes-là qu'elle se lit le mieux : on est censé venir."""
        assert vues._solennels(self.depute())["part_delegation"] == pytest.approx(20 / 62)


class TestLiensAssemblee:
    """Les pièces justificatives : celle qui existe toujours, celle qui manque."""

    def test_le_lien_du_scrutin_se_construit_sur_le_numero(self):
        assert vues.lien_scrutin("17", 7946) == (
            "https://www.assemblee-nationale.fr/dyn/17/scrutins/7946")

    def test_le_lien_du_dossier_se_construit_sur_sa_reference(self):
        assert vues.lien_dossier(17, "DLR5L17N53940") == (
            "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N53940")

    def test_un_dossier_absent_rend_none_et_non_une_adresse_tronquee(self):
        """Une adresse en `/dossiers/None` serait un lien mort publié 172 fois."""
        assert vues.lien_dossier("17", None) is None
        assert vues.lien_dossier("17", "") is None

    def test_un_numero_absent_rend_none(self):
        assert vues.lien_scrutin("17", None) is None


class TestDepuisQuandComplet:
    """La date de bascule d'un champ, et pas la date de sa première valeur."""

    def table(self, dates, valeurs) -> pl.DataFrame:
        return pl.DataFrame({
            "date": dates,
            "date_d": [pl.Series([d]).str.to_date()[0] for d in dates],
            "dossier_uid": valeurs,
        })

    def test_le_premier_renseigne_ne_fait_pas_la_bascule(self):
        """Une valeur isolée en avril 2025 ne promet rien pour les onze mois qui suivent."""
        t = self.table(
            ["2025-04-07", "2025-06-01", "2026-03-26", "2026-04-01"],
            ["DLR1", None, "DLR2", "DLR3"],
        )
        assert vues._depuis_quand_complet(t, "dossier_uid") == "2026-03-26"

    def test_une_colonne_entierement_remplie_bascule_a_sa_premiere_ligne(self):
        t = self.table(["2025-01-01", "2025-02-01"], ["DLR1", "DLR2"])
        assert vues._depuis_quand_complet(t, "dossier_uid") == "2025-01-01"

    def test_une_lacune_sur_la_derniere_ligne_n_annonce_aucune_date(self):
        """La source n'a pas basculé : promettre une date serait mentir."""
        t = self.table(["2025-01-01", "2026-07-01"], ["DLR1", None])
        assert vues._depuis_quand_complet(t, "dossier_uid") is None
