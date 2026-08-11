"""Tests des parsers : ce sont les bizarreries XML→JSON qui cassent tout."""

import polars as pl
import pytest

from radar.parse import as_list, build_positions_groupe, dig, text


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

    def test_les_non_votants_sont_exclus(self):
        v = self._votes([("A", "pour"), ("B", "nonVotant"), ("C", "nonVotant")])
        r = build_positions_groupe(v)
        assert r["majoritaire"][0] == "pour"
        assert r["votants_groupe"][0] == 1
