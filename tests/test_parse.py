"""Tests des parsers : ce sont les bizarreries XML→JSON qui cassent tout."""

import polars as pl
import pytest

from pathlib import Path

from radar.parse import (
    _dossier_du_chemin,
    _scrutin_row,
    as_list,
    build_positions_groupe,
    dig,
    text,
)


class TestAplatissement:
    def test_as_list_normalise_les_trois_formes(self):
        # Le JSON de l'AN écrit un dict quand la liste n'a qu'un élément.
        assert as_list(None) == []
        assert as_list({"a": 1}) == [{"a": 1}]
        assert as_list([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]

    def test_text_gere_les_nuls_xsi(self):
        assert text({"@xsi:nil": "true"}) is None
        assert text({"#text": "PA1001"}) == "PA1001"
        assert text("PA1001") == "PA1001"
        assert text("") is None
        assert text(None) is None

    def test_dig_ne_casse_pas_sur_chemin_absent(self):
        d = {"a": {"b": {"c": 42}}}
        assert dig(d, "a", "b", "c") == 42
        assert dig(d, "a", "z", "c") is None
        assert dig(None, "a") is None
        # Un scalaire au milieu du chemin ne doit pas lever.
        assert dig({"a": "texte"}, "a", "b") is None


class TestPositionsGroupe:
    def _votes(self, positions: list[tuple[str, str]]) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "scrutin_uid": ["S1"] * len(positions),
                "groupe_uid": ["G1"] * len(positions),
                "acteur_uid": [a for a, _ in positions],
                "position": [p for _, p in positions],
            }
        )

    def test_majorite_simple(self):
        v = self._votes([("A", "pour"), ("B", "pour"), ("C", "contre")])
        r = build_positions_groupe(v)
        assert r["majoritaire"][0] == "pour"
        assert r["votants_groupe"][0] == 3
        assert r["part_majoritaire"][0] == pytest.approx(2 / 3)

    def test_egalite_parfaite_donne_aucune_ligne(self):
        # 2 pour / 2 contre : le groupe n'a pas de ligne majoritaire.
        v = self._votes([("A", "pour"), ("B", "pour"), ("C", "contre"), ("D", "contre")])
        r = build_positions_groupe(v)
        assert r["partage"][0] is True
        assert r["majoritaire"][0] is None

    def test_majorite_relative_sur_trois_positions(self):
        v = self._votes(
            [("A", "pour")] * 3 + [("B", "contre")] * 2 + [("C", "abstention")] * 2
        )
        r = build_positions_groupe(v)
        assert r["majoritaire"][0] == "pour"
        assert r["partage"][0] is False

    def test_une_pluralite_faible_est_reconnaissable(self):
        """7/5/5 : « pour » domine, mais à 41 % — ce n'est pas une ligne.

        La table ne tranche pas, elle donne de quoi trancher : c'est
        `analyze.votes_vs_ligne` et son `seuil_ligne` qui écartent ce cas, pour
        ne pas compter dix « dissidents » dans un groupe qui n'a pas de ligne.
        """
        v = self._votes(
            [("A", "pour")] * 7 + [("B", "contre")] * 5 + [("C", "abstention")] * 5
        )
        r = build_positions_groupe(v)
        assert r["majoritaire"][0] == "pour"
        assert r["partage"][0] is False       # pas une égalité parfaite…
        assert r["part_majoritaire"][0] == pytest.approx(7 / 17)   # …mais 41 %
        assert r["part_majoritaire"][0] < 0.5

    def test_les_trois_effectifs_sont_publies(self):
        v = self._votes(
            [("A", "pour")] * 3 + [("B", "contre")] * 2 + [("C", "abstention")] * 1
        )
        r = build_positions_groupe(v)
        assert (r["n_pour"][0], r["n_contre"][0], r["n_abstention"][0]) == (3, 2, 1)
        assert r["votants_groupe"][0] == 6

    def test_les_non_votants_sont_exclus(self):
        v = self._votes([("A", "pour"), ("B", "nonVotant"), ("C", "nonVotant")])
        r = build_positions_groupe(v)
        assert r["majoritaire"][0] == "pour"
        assert r["votants_groupe"][0] == 1


class TestDossierLegislatif:
    """Le rattachement d'un scrutin à sa loi.

    Ce champ a été nul sur les 8 434 scrutins pendant toute la vie du dépôt, et
    le code en avait conclu par écrit que « la source ne le remplit jamais ».
    Ces tests verrouillent la forme réelle de la source.
    """

    def test_dossier_legislatif_est_un_objet_pas_une_chaine(self):
        # La forme exacte que publie l'Assemblée. `text()` appliqué à cet objet
        # rend None : c'était le bug, et c'est pourquoi on descend d'un cran.
        objet = {
            "libelle": "Projet de loi sur la justice criminelle",
            "dossierRef": "DLR5L17N53940",
        }
        assert text(objet) is None
        assert text(dig(objet, "dossierRef")) == "DLR5L17N53940"

    def test_scrutin_row_extrait_le_dossier_et_son_titre(self):
        s = {
            "uid": "VTANR5L17V7946",
            "numero": "7946",
            "dateScrutin": "2026-07-02",
            "legislature": "17",
            "titre": "l'amendement nº 80",
            "objet": {
                "libelle": "l'amendement nº 80",
                "dossierLegislatif": {
                    "libelle": "Projet de loi sur la justice criminelle",
                    "dossierRef": "DLR5L17N53940",
                },
            },
        }
        r = _scrutin_row(s)
        assert r["dossier_uid"] == "DLR5L17N53940"
        assert r["dossier_titre"] == "Projet de loi sur la justice criminelle"

    def test_scrutin_sans_dossier_rend_none_pas_une_chaine_vide(self):
        # 5 826 scrutins sont dans ce cas. Une chaîne vide se joindrait à une
        # autre chaîne vide et fabriquerait un dossier fantôme partagé.
        r = _scrutin_row({"uid": "VTANR5L17V1", "numero": "1",
                          "objet": {"libelle": "la motion de censure"}})
        assert r["dossier_uid"] is None
        assert r["dossier_titre"] is None

    def test_dossier_lu_dans_le_chemin_de_l_amendement(self):
        # Le JSON d'un amendement ne porte que son texte législatif : le
        # dossier n'existe que dans l'arborescence de l'archive.
        f = Path("data/raw/amendements/json/DLR5L16N48701/PIONANR5L17B0132/AM1.json")
        assert _dossier_du_chemin(f) == "DLR5L16N48701"

    def test_amendement_hors_dossier_reste_sans_dossier(self):
        # `incorrect_data/` est le répertoire où l'Assemblée gare ce qu'elle
        # sait erroné : 149 amendements. Leur rattachement n'est pas inconnu,
        # il est démenti — ils ne doivent entrer dans aucun compte par loi.
        f = Path("data/raw/amendements/json/incorrect_data/PIONANR5L17BTC2202/AM1.json")
        assert _dossier_du_chemin(f) is None
