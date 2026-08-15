"""L'onglet « Les votes » : les phrases d'un scrutin, et les gardes du gabarit.

Ces tests portent sur la couche rédactionnelle et sur le rendu, pas sur les
calculs — qui sont ceux de `radar.vues`, testés à part. Ils verrouillent trois
choses que le site ne peut pas se permettre de perdre :

1. Un vote dont la source ne donne pas le dossier législatif produit une phrase
   qui le dit, et non un lien manquant en silence.
2. Un nombre servi sans son dénominateur ne se produit pas, même quand la
   phrase est plus courte sans lui.
3. Un jeton de page qui porte le nom d'un jeton réservé par `page()` fait
   échouer la génération, au lieu d'être écrasé sans bruit.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "site"))

import redaction  # noqa: E402


@pytest.fixture(scope="module")
def generer():
    """Charge `site/generer.py` par son chemin. Cf. `test_note.generer`."""
    chemin = RACINE / "site" / "generer.py"
    sys.path.insert(0, str(chemin.parent))
    try:
        spec = importlib.util.spec_from_file_location("site_generer_votes", chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


APERCU = {
    "dossiers": {
        "scrutins_avec": 2608, "scrutins_sans": 5826, "distincts": 75,
        "depuis": "2026-03-26", "engageants_avec": 73, "engageants_sans": 172,
    },
    "solennels": {
        "total": 72, "mois": 21, "par_mois": 72 / 21,
        "poids_d_une_absence": 1 / 72, "poids_de_dix_absences": 10 / 72,
        "poids_engageant": 1 / 245, "poids_engageant_dix": 10 / 245,
        "engageants": 65, "hors_engageants": 7,
    },
}

RESUME = {
    "pour": 276, "contre": 86, "abstention": 2, "absent": 213,
    "empeche": 2, "hors_mandat": 69, "total": 648, "exprimes": 364,
    "delegues": 169, "dissidents": 2,
}


class TestPhraseLoi:
    """Le rattachement à la loi, ou l'aveu qu'il manque."""

    def test_un_dossier_connu_donne_son_titre_et_son_lien(self):
        liens = {
            "dossier": "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR1",
            "dossier_titre": "Moderniser la gestion du patrimoine",
            "amendements_du_dossier": None,
        }
        p = redaction.phrase_loi(liens, {}, APERCU)
        assert "Moderniser la gestion du patrimoine" in p
        assert "dossiers/DLR1" in p

    def test_un_dossier_absent_produit_une_phrase_et_non_un_silence(self):
        """Un lien manquant se lirait « ce vote ne porte sur aucune loi »."""
        p = redaction.phrase_loi({"dossier": None}, {}, APERCU)
        assert "ne rattache pas ce scrutin" in p
        # La lacune est datée et chiffrée, sinon elle passe pour la nôtre.
        assert "26" in p and "mars" in p
        assert "5 826".replace(" ", " ") in p

    def test_le_compte_d_amendements_dit_qu_il_est_celui_de_la_loi(self):
        """Sans cette réserve, on croit que ces amendements ont été mis aux voix."""
        liens = {
            "dossier": "https://exemple/DLR1", "dossier_titre": "Une loi",
            "amendements_du_dossier": 104, "amendements_adoptes": 30,
            "amendements_examines": 79,
        }
        p = redaction.phrase_loi(liens, {}, APERCU)
        assert "de la loi entière et non de ce scrutin" in p


class TestPhraseDelegation:
    """La délégation d'un scrutin ne se publie pas sans son dénominateur."""

    def test_les_deux_nombres_sont_dans_la_phrase(self):
        p = redaction.phrase_delegation_scrutin(RESUME)
        assert "169" in p and "364" in p

    def test_elle_dit_emis_et_non_exprimes(self):
        """« Suffrages exprimés » a un sens réglementaire, écrit deux lignes plus haut."""
        p = redaction.phrase_delegation_scrutin(RESUME)
        assert "suffrages émis" in p
        assert "suffrages exprimés" not in p

    def test_aucune_delegation_ne_produit_aucune_phrase(self):
        assert redaction.phrase_delegation_scrutin({**RESUME, "delegues": 0}) == ""

    def test_aucun_suffrage_ne_divise_pas(self):
        assert redaction.phrase_delegation_scrutin({**RESUME, "exprimes": 0}) == ""


class TestPhraseGroupes:
    """Le partage des groupes, sans jamais nommer un camp."""

    def groupes(self):
        return [
            {"groupe": "EPR", "majoritaire": "pour", "partage": False},
            {"groupe": "RN", "majoritaire": "pour", "partage": False},
            {"groupe": "LFI-NFP", "majoritaire": "contre", "partage": False},
            {"groupe": "NI", "majoritaire": None, "partage": True},
        ]

    def test_aucune_etiquette_politique_n_apparait(self):
        """« La gauche a voté contre » serait une lecture, pas une mesure."""
        p = redaction.phrase_groupes_scrutin(self.groupes(), RESUME)
        for mot in ("gauche", "droite", "centre", "majorité présidentielle"):
            assert mot not in p.lower()

    def test_un_groupe_partage_n_a_pas_de_position(self):
        p = redaction.phrase_groupes_scrutin(self.groupes(), RESUME)
        assert "pas de position" in p

    def test_sans_aucune_position_la_phrase_ne_ment_pas(self):
        gs = [{"groupe": "NI", "majoritaire": None, "partage": True}]
        p = redaction.phrase_groupes_scrutin(gs, {"dissidents": 0})
        assert "Aucun groupe" in p


class TestPhraseReleve:
    """Le compte des absents, avec ce qui le relativise dans la même phrase."""

    def test_les_absents_ne_voyagent_pas_sans_leur_reserve(self):
        p = redaction.phrase_releve(RESUME, APERCU)
        assert "213" in p
        assert "aucun motif" in p

    def test_les_hors_mandat_sont_distingues_des_absents(self):
        """Un député élu plus tard n'a pas manqué le vote : la question ne se posait pas."""
        p = redaction.phrase_releve(RESUME, APERCU)
        assert "n'étaient pas en fonction" in p

    def test_le_denominateur_retire_les_hors_mandat(self):
        p = redaction.phrase_releve(RESUME, APERCU)
        assert "579" in p.replace(" ", "")


class TestGardeDesJetons:
    """Un jeton réservé posé par une page est écrasé sans bruit : on l'interdit."""

    def test_tous_les_jetons_reserves_sont_bien_ceux_que_page_remplit(self, generer):
        """Si `page()` en ajoute un et l'oublie ici, la garde devient un mensonge."""
        source = (RACINE / "site" / "generer.py").read_text()
        debut = source.index("def page(")
        corps = source[debut:source.index("manquants =", debut)]
        poses = set(__import__("re").findall(r'"([A-Z_]+)":', corps))
        assert poses == set(generer.RESERVES)

    def test_titre_est_reserve(self, generer):
        """C'est le jeton qui a mordu : le `h1` affichait le titre de l'onglet."""
        assert "TITRE" in generer.RESERVES


class TestAdresseScrutin:
    """L'adresse d'une page de vote est le numéro que l'Assemblée elle-même cite."""

    def test_l_adresse_porte_le_numero_du_scrutin(self, generer):
        assert generer.adresse_scrutin({"numero": 8434}) == "scrutin-8434.html"
