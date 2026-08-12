"""Tests du site local.

Le site est une couche de présentation : ce qui peut y casser n'est pas le
calcul, c'est le passage du calcul à la page. Trois choses s'y perdent
silencieusement, et ces tests les verrouillent :

1. **Un NaN sérialisé** casse `JSON.parse` côté navigateur — la page reste
   blanche sans message. Une mesure absente doit sortir en `null`.
2. **Une dissidence comptée sans ligne de groupe** : quand aucune position ne
   réunit la majorité des suffrages du groupe, le vote n'est ni dissident ni
   conforme. `null`, pas `false`.
3. **Un chemin qui remonte hors de `web/`** ne doit rien servir, même sur un
   serveur local.
"""

import json
import math

import numpy as np
import polars as pl
import pytest

from radar import site


class TestSerialisation:
    def test_nan_devient_null(self):
        assert site._propre(float("nan")) is None
        assert site._propre(float("inf")) is None

    def test_nan_numpy_aussi(self):
        assert site._propre(np.float64("nan")) is None
        assert site._propre(np.int64(3)) == 3
        assert site._propre(np.bool_(True)) is True

    def test_recursion_dans_les_structures(self):
        valeur = {"a": [1.0, float("nan")], "b": {"c": float("nan")}}
        assert site._propre(valeur) == {"a": [1.0, None], "b": {"c": None}}

    def test_le_resultat_passe_json_strict(self):
        """`allow_nan=False` est ce que fait le serveur : rien ne doit lever."""
        df = pl.DataFrame({"x": [1.0, float("nan"), None], "n": [1, 2, 3]})
        json.dumps(site.lignes(df), allow_nan=False)

    def test_les_dates_deviennent_des_chaines(self):
        df = pl.DataFrame({"d": ["2026-01-02"]}).with_columns(
            pl.col("d").str.to_date().alias("d")
        )
        assert site.lignes(df) == [{"d": "2026-01-02"}]


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
        o = site._ouverture(d).sort("acteur_uid")
        seul = o.filter(pl.col("acteur_uid") == "PA4").to_dicts()[0]
        assert seul["part_hors_groupe"] == pytest.approx(1.0)
        assert seul["ouverture"] == pytest.approx(1.0)

    def test_un_depute_replie_sur_son_groupe_descend_sous_un(self):
        o = site._ouverture(self.reseau())
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
        o = site._ouverture(reseau)
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
        monkeypatch.setattr(site.ideal, "intervalles", lambda *a, **k: table)
        p = site._positions(cube=None, bootstrap=10)
        rangs = dict(zip(p["acteur_uid"].to_list(), p["rang_axe1"].to_list()))
        assert rangs == {"PA2": 1, "PA1": 2, "PA3": 3}


class TestStatiqueEtRoutage:
    def test_les_fichiers_du_site_existent(self):
        for nom in ("index.html", "style.css", "app.js"):
            assert (site.WEB / nom).is_file()

    def test_aucun_chemin_ne_sort_du_dossier_web(self):
        """`..` ne doit pas servir un fichier du projet, même en local."""
        cible = (site.WEB / "../../../pyproject.toml").resolve()
        assert site.WEB.resolve() not in cible.parents


class TestServeur:
    """Le routeur, sur des données factices : aucune table Parquet requise."""

    @pytest.fixture
    def adresse(self):
        import threading
        from types import SimpleNamespace

        faux = SimpleNamespace(
            apercu=lambda: {"deputes": 2, "manquant": None},
            liste_deputes=lambda: [{"acteur_uid": "PA1"}],
            fiche=lambda uid: (_ for _ in ()).throw(KeyError(uid))
            if uid == "PA404"
            else {"identite": {"acteur_uid": uid}},
            votes_du_depute=lambda uid, **k: {"total": 0, "votes": [], "filtres": k},
            liste_scrutins=lambda **k: {"total": 0, "scrutins": [], "filtres": k},
            scrutin=lambda uid: {"scrutin": {"scrutin_uid": uid}},
            liste_groupes=lambda: [{"groupe": "G1"}],
        )
        serveur = site.servir(faux, port=0)
        fil = threading.Thread(target=serveur.serve_forever, daemon=True)
        fil.start()
        yield "http://%s:%d" % serveur.server_address[:2]
        serveur.shutdown()
        serveur.server_close()

    def obtenir(self, adresse: str, chemin: str):
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(adresse + chemin) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_les_routes_json_repondent(self, adresse):
        for chemin in ("/api/apercu", "/api/deputes", "/api/deputes/PA1",
                       "/api/deputes/PA1/votes", "/api/scrutins",
                       "/api/scrutins/VT1", "/api/groupes"):
            code, corps = self.obtenir(adresse, chemin)
            assert code == 200, chemin
            json.loads(corps)

    def test_les_parametres_sont_convertis(self, adresse):
        _, corps = self.obtenir(adresse, "/api/scrutins?limite=5&portee=texte&q=loi")
        filtres = json.loads(corps)["filtres"]
        assert filtres["limite"] == 5 and filtres["portee"] == "texte"
        assert filtres["q"] == "loi"

    def test_un_filtre_vide_vaut_pas_de_filtre(self, adresse):
        """`?portee=` doit valoir « toutes les portées », pas « portée vide »."""
        _, corps = self.obtenir(adresse, "/api/scrutins?portee=")
        assert json.loads(corps)["filtres"]["portee"] is None

    def test_une_route_inconnue_donne_404(self, adresse):
        code, corps = self.obtenir(adresse, "/api/inexistant")
        assert code == 404
        assert "erreur" in json.loads(corps)

    def test_un_identifiant_inconnu_donne_404(self, adresse):
        assert self.obtenir(adresse, "/api/deputes/PA404")[0] == 404

    def test_la_racine_sert_la_page(self, adresse):
        code, corps = self.obtenir(adresse, "/")
        assert code == 200
        assert b"Radar parlementaire" in corps

    def test_une_route_client_retombe_sur_la_page(self, adresse):
        """`#/depute/...` est côté client, mais un rechargement doit marcher."""
        code, corps = self.obtenir(adresse, "/depute/PA1")
        assert code == 200 and b"<title>" in corps

    def test_la_remontee_de_chemin_ne_sert_rien(self, adresse):
        _, corps = self.obtenir(adresse, "/../../pyproject.toml")
        assert b"[project]" not in corps


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
