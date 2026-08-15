"""L'annexe du site ne doit jamais être en retard sur ce qu'elle spécifie.

La page Méthode présente la note méthodologique comme le document de référence,
et dit littéralement que « si le site et la note divergent, c'est le site qui a
tort ». Cette phrase a une conséquence mécanique : servir un PDF plus ancien que
son source LaTeX, c'est publier soi-même la preuve qu'on a tort — sous
l'autorité du document dont on se réclame.

Le cas s'est présenté le jour où le dénominateur du taux d'adoption est passé
des dépôts aux examens. Le `.tex` était corrigé dans le même commit ; le PDF,
lui, est un artefact ignoré par git, compilé à la main, et rien n'obligeait
personne à le refaire avant de publier.

**Absente et périmée ne sont pas la même chose.** Une note absente est honnête :
la page Méthode retire son encart, et le lecteur ne se voit rien promettre. Une
note périmée ment. Le générateur traite donc les deux cas différemment, et
c'est ce que ces tests verrouillent.
"""

import importlib.util
import re
import os
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def generer():
    """Charge `site/generer.py` par son chemin.

    Le site n'est pas un paquet et ne doit pas en devenir un — `site` est un nom
    de module de la bibliothèque standard. On ajoute son dossier au chemin le
    temps du chargement, parce que le générateur importe `redaction` en voisin.
    """
    chemin = RACINE / "site" / "generer.py"
    sys.path.insert(0, str(chemin.parent))
    try:
        spec = importlib.util.spec_from_file_location("site_generer", chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def _dater(chemin: Path, horodatage: float) -> None:
    """Crée le fichier et lui impose sa date : c'est elle seule qu'on teste."""
    chemin.write_bytes(b"contenu")
    os.utime(chemin, (horodatage, horodatage))


@pytest.fixture
def notes(generer, tmp_path, monkeypatch):
    """Un couple source/PDF isolé, dont on pilote les dates."""
    tex, pdf = tmp_path / "note.tex", tmp_path / "note.pdf"
    monkeypatch.setattr(generer, "NOTE_TEX", tex)
    monkeypatch.setattr(generer, "NOTE_SOURCE", pdf)
    return tex, pdf


def test_un_pdf_plus_ancien_que_son_source_est_perime(generer, notes):
    tex, pdf = notes
    _dater(pdf, 1_000)
    _dater(tex, 2_000)
    assert generer.note_perimee()


def test_un_pdf_recompile_ne_l_est_pas(generer, notes):
    tex, pdf = notes
    _dater(tex, 1_000)
    _dater(pdf, 2_000)
    assert not generer.note_perimee()


def test_l_egalite_des_dates_passe(generer, notes):
    """Une copie qui préserve l'horodatage — `shutil.copy2` — ne doit pas alerter."""
    tex, pdf = notes
    _dater(tex, 1_000)
    _dater(pdf, 1_000)
    assert not generer.note_perimee()


def test_une_note_absente_n_est_pas_une_note_perimee(generer, notes):
    """Le site se construit sans son annexe ; il ne se construit pas contre elle."""
    tex, _ = notes
    _dater(tex, 2_000)
    assert not generer.note_perimee()


def test_le_source_absent_ne_bloque_rien(generer, notes):
    """Publier depuis une copie sans `docs/` reste possible."""
    _, pdf = notes
    _dater(pdf, 1_000)
    assert not generer.note_perimee()


def test_la_note_du_depot_est_a_jour(generer):
    """Le contrôle, appliqué au dépôt lui-même — quand le PDF a été compilé.

    Il n'existe pas dans un clone frais : `docs/*.pdf` est ignoré par git. Le
    test ne le réclame donc pas, il vérifie seulement qu'il n'est pas en retard
    quand il est là.
    """
    if not generer.NOTE_SOURCE.exists():
        pytest.skip("note non compilée dans cette copie de travail")
    assert not generer.note_perimee(), (
        "docs/note-methodologique.pdf est plus ancien que son .tex — "
        "recompiler avant de publier")


# ══════════════════════════════════════════════════════════════════════════
# Les seuils que la note énonce doivent être ceux que le code applique
# ══════════════════════════════════════════════════════════════════════════

#: Le source LaTeX, replié sur une seule ligne. Sans cette normalisation, le
#: contrôle dépendrait de l'endroit où l'éditeur a coupé la phrase : « minimum
#: de 50 » cesse d'être trouvable dès qu'un retour à la ligne tombe entre les
#: deux mots, et le test échouerait sur une mise en forme au lieu d'un chiffre.
SOURCE_NOTE = re.sub(
    r"\s+", " ", (RACINE / "docs" / "note-methodologique.tex").read_text(encoding="utf-8")
)


class TestLesSeuilsDeLaNoteSontCeuxDuCode:
    """La note est la spécification ; un seuil qui y dort n'engage personne.

    Ce contrôle est né d'une divergence réelle et durable : la note annonçait
    un minimum de 50 votes comparables pour entrer au classement de dissidence,
    le site n'en appliquait aucun, et personne ne pouvait le voir sans lire les
    deux côte à côte. Le résultat était qu'un taux de 0 % mesuré sur quatre
    scrutins fixait l'extrémité de la distribution publiée.

    On vérifie donc que chaque seuil cité dans le document apparaît **avec la
    valeur de la constante**. Le test échoue dans les deux sens : changer le
    code sans rouvrir le `.tex` casse la construction, et c'est le but. Il ne
    prétend pas lire le sens des phrases — seulement empêcher qu'un nombre
    figure d'un côté et pas de l'autre.
    """

    def test_le_seuil_de_dissidence(self):
        from radar.analyze import MIN_VOTES_LIGNE

        assert f"minimum de {MIN_VOTES_LIGNE} par défaut" in SOURCE_NOTE

    def test_les_seuils_d_accord(self):
        from radar.vues import MIN_COMMUNS, MIN_COMMUNS_TEXTE

        assert f"à {MIN_COMMUNS} scrutins communs" in SOURCE_NOTE
        assert f"et à {MIN_COMMUNS_TEXTE} pour les seuls votes" in SOURCE_NOTE

    def test_le_seuil_de_comparabilite_de_la_presence(self):
        from radar.vues import MIN_VOTABLES

        assert f"inférieur à un seuil $D_{{\\min}}$ — fixé à {MIN_VOTABLES}" in SOURCE_NOTE

    def test_le_niveau_des_intervalles(self):
        """Un seul niveau sur tout le site, et la note doit le porter."""
        from radar.vues import NIVEAU

        assert f"{int(NIVEAU * 100)}~\\%" in SOURCE_NOTE or "1-\\gamma" in SOURCE_NOTE

    def test_le_filtre_de_contestation_du_modele(self):
        import inspect

        from radar.ideal import estimer

        seuil = inspect.signature(estimer).parameters["contestation_min"].default
        # Le `.tex` écrit les décimaux à la française, virgule protégée.
        assert f"{seuil:.3f}".replace(".", "{,}") in SOURCE_NOTE

    def test_le_groupe_d_ancrage_est_nomme(self):
        """La note doit pouvoir citer le seul groupe qui touche le calcul."""
        from radar.ideal import GROUPE_ANCRAGE

        assert "ancrage" in SOURCE_NOTE.lower()
        assert GROUPE_ANCRAGE.split("-")[0] in SOURCE_NOTE
