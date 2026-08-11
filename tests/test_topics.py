"""Tests de la détection de sujets."""

import polars as pl
import pytest

from radar.topics import normaliser, sujets_qui_montent, tokeniser


class TestTokeniser:
    def test_accents_et_casse_fusionnent(self):
        assert normaliser("Écologie") == "ecologie"
        assert tokeniser("Écologie") == tokeniser("ecologie")

    def test_mots_vides_et_jargon_ecartes(self):
        # « article » et « gouvernement » sont partout : ils ne disent rien.
        termes = tokeniser("l'article du gouvernement sur les retraites")
        assert "article" not in termes
        assert "gouvernement" not in termes
        assert "retraites" in termes

    def test_bigrammes_produits(self):
        termes = tokeniser("souveraineté agricole")
        assert "souverainete" in termes
        assert "souverainete agricole" in termes

    def test_mots_courts_ignores(self):
        assert tokeniser("un go et ub") == []

    def test_texte_vide(self):
        assert tokeniser(None) == []
        assert tokeniser("") == []


class TestSujetsQuiMontent:
    def _freqs(self) -> pl.DataFrame:
        """« retraites » stable, « agricole » qui explose en dernière semaine."""
        lignes = []
        for i, semaine in enumerate(["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"]):
            lignes.append({"semaine": semaine, "terme": "retraites", "n": 20, "n_documents": 100})
            lignes.append({"semaine": semaine, "terme": "agricole",
                           "n": 2 if i < 3 else 40, "n_documents": 100})
        return pl.DataFrame(lignes).with_columns(
            pl.col("semaine").str.to_date(), pl.col("n").cast(pl.UInt32)
        )

    def test_detecte_la_poussee_pas_le_volume(self):
        d = sujets_qui_montent(self._freqs(), semaines_reference=3, min_occurrences=5, k=10)
        assert d["terme"][0] == "agricole"

    def test_terme_stable_non_signale(self):
        d = sujets_qui_montent(self._freqs(), semaines_reference=3, min_occurrences=5, k=10)
        # « retraites » est deux fois plus fréquent en volume, mais ne bouge pas.
        assert "retraites" not in d["terme"].to_list()

    def test_termes_en_recul_ecartes_par_defaut(self):
        d = sujets_qui_montent(self._freqs(), semaines_reference=3, min_occurrences=1, k=10)
        assert (d["score"] > 0).all()

    def test_semaine_inconnue_leve(self):
        with pytest.raises(ValueError, match="aucune donnée"):
            sujets_qui_montent(self._freqs(), semaine="2030-01-07")
