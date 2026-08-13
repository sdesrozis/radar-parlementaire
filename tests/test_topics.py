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
        """« retraites » stable, « agricole » qui explose en dernière semaine.

        `n_docs` porte le taux, `n` reste le comptage brut d'occurrences : les
        deux sont dissociés ici exprès, pour vérifier que c'est bien le premier
        qui décide.
        """
        lignes = []
        for i, semaine in enumerate(["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"]):
            lignes.append({"semaine": semaine, "terme": "retraites",
                           "n": 60, "n_docs": 20, "n_documents": 100})
            lignes.append({"semaine": semaine, "terme": "agricole",
                           "n": 2 if i < 3 else 40, "n_docs": 2 if i < 3 else 40,
                           "n_documents": 100})
        return pl.DataFrame(lignes).with_columns(
            pl.col("semaine").str.to_date(),
            pl.col("n").cast(pl.UInt32),
            pl.col("n_docs").cast(pl.UInt32),
        )

    def test_detecte_la_poussee_pas_le_volume(self):
        d = sujets_qui_montent(self._freqs(), semaines_reference=3, min_documents=5, k=10)
        assert d["terme"][0] == "agricole"

    def test_terme_stable_non_signale(self):
        d = sujets_qui_montent(self._freqs(), semaines_reference=3, min_documents=5, k=10)
        # « retraites » est trois fois plus fréquent en occurrences, mais sa part
        # de documents ne bouge pas.
        assert "retraites" not in d["terme"].to_list()

    def test_le_taux_porte_sur_les_documents_pas_les_occurrences(self):
        """Un terme martelé dans les mêmes documents ne « monte » pas.

        C'est le piège que corrige cette mesure : `n` triple, `n_docs` reste
        identique. Diviser des occurrences par des documents aurait signalé une
        poussée là où le même petit groupe de textes répète le même mot.
        """
        lignes = []
        for i, semaine in enumerate(["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"]):
            lignes.append({"semaine": semaine, "terme": "alinea",
                           "n": 30 if i < 3 else 300, "n_docs": 30,
                           "n_documents": 100})
        freqs = pl.DataFrame(lignes).with_columns(
            pl.col("semaine").str.to_date(),
            pl.col("n").cast(pl.UInt32),
            pl.col("n_docs").cast(pl.UInt32),
        )
        d = sujets_qui_montent(freqs, semaines_reference=3, min_documents=5, k=10)
        assert d.height == 0

    def test_termes_en_recul_ecartes_par_defaut(self):
        d = sujets_qui_montent(self._freqs(), semaines_reference=3, min_documents=1, k=10)
        assert (d["score"] > 0).all()

    def test_q_valeur_bornee_et_filtrante(self):
        d = sujets_qui_montent(self._freqs(), semaines_reference=3, min_documents=1,
                               k=10, fdr=None)
        assert d["q_valeur"].max() <= 1.0
        assert (d["q_valeur"] >= d["p_valeur"] - 1e-12).all()

    def test_semaine_inconnue_leve(self):
        with pytest.raises(ValueError, match="aucune donnée"):
            sujets_qui_montent(self._freqs(), semaine="2030-01-07")
