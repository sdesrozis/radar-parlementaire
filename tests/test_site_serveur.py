"""Tests du serveur de développement du site.

Le site n'est pas un paquet Python — et ne doit pas en devenir un : `site` est
un nom de module de la bibliothèque standard, et le rendre importable le
masquerait. On le charge donc par son chemin, ce qui est aussi une vérification
en soi : le serveur ne doit dépendre de rien du paquet `radar`.

Ce qui compte ici tient en une garantie. L'ancien serveur résolvait lui-même
les chemins de fichiers, et devait donc se garder explicitement d'un `../` —
c'était la ligne la plus délicate des 855. Le nouveau délègue à
`SimpleHTTPRequestHandler`. Ce test vérifie que la délégation tient.
"""

import importlib.util
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def _charger():
    chemin = RACINE / "site" / "serveur.py"
    spec = importlib.util.spec_from_file_location("site_serveur", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serveur_site = _charger()


@pytest.fixture
def adresse(tmp_path):
    (tmp_path / "index.html").write_text("<title>Radar parlementaire</title>", encoding="utf-8")
    (tmp_path / "statique").mkdir()
    (tmp_path / "statique" / "style.css").write_text("body{}", encoding="utf-8")

    s = serveur_site.servir(tmp_path, port=0)
    fil = threading.Thread(target=s.serve_forever, daemon=True)
    fil.start()
    yield "http://%s:%d" % s.server_address[:2]
    s.shutdown()
    s.server_close()


def obtenir(adresse: str, chemin: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(adresse + chemin) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_la_racine_sert_la_page(adresse):
    code, corps = obtenir(adresse, "/")
    assert code == 200
    assert b"Radar parlementaire" in corps


def test_les_fichiers_statiques_sont_servis(adresse):
    assert obtenir(adresse, "/statique/style.css")[0] == 200


def test_une_page_absente_donne_404(adresse):
    """Le site est statique : une URL inconnue est une erreur, pas un repli."""
    assert obtenir(adresse, "/PA000000.html")[0] == 404


def test_la_remontee_de_chemin_ne_sert_rien(adresse):
    """`..` ne doit pas atteindre un fichier du projet, même en local."""
    code, corps = obtenir(adresse, "/../../pyproject.toml")
    assert b"[project]" not in corps
    assert code in (400, 403, 404)


def test_le_serveur_n_ecoute_que_en_local_par_defaut(tmp_path):
    """Un site de travail ne s'expose pas au réseau sans qu'on le demande."""
    s = serveur_site.servir(tmp_path, port=0)
    try:
        assert s.server_address[0] == "127.0.0.1"
    finally:
        s.server_close()
