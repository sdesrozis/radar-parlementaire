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
