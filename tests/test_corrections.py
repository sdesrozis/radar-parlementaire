"""Tests du registre des corrections.

Le pied de page de chaque page du site promet qu'un chiffre faux « est corrigé
publiquement », et renvoie au registre. Cette promesse ne vaut que si le
registre reste tenu : une entrée à laquelle il manque l'avant/après ne permet
pas à quelqu'un qui aurait cité l'ancienne valeur de savoir s'il doit se
corriger — c'est-à-dire ne sert à rien.

Ces tests sont donc le contrôle de la promesse, pas du code. Ils portent sur un
fichier de données, `site/corrections.toml`, et ne chargent ni le générateur ni
le paquet `radar`.
"""

import subprocess
import tomllib
from datetime import date
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
REGISTRE = RACINE / "site" / "corrections.toml"

CHAMPS = ("date", "titre", "portee", "erreur", "cause", "effet", "pages", "commit")
PORTEES = {"chiffre", "methode", "site"}

ENTREES = tomllib.loads(REGISTRE.read_text(encoding="utf-8"))["correction"]


def test_le_registre_n_est_pas_vide():
    """Un registre vide n'est pas neutre : il dit qu'on n'a jamais rien vérifié."""
    assert ENTREES


@pytest.mark.parametrize("entree", ENTREES, ids=lambda e: e["titre"])
def test_chaque_entree_est_complete(entree):
    for champ in CHAMPS:
        assert str(entree.get(champ, "")).strip(), f"champ « {champ} » vide"


@pytest.mark.parametrize("entree", ENTREES, ids=lambda e: e["titre"])
def test_la_portee_est_l_une_des_trois(entree):
    assert entree["portee"] in PORTEES


@pytest.mark.parametrize("entree", ENTREES, ids=lambda e: e["titre"])
def test_la_date_est_lisible_et_passee(entree):
    """Une correction est un fait daté, pas une intention."""
    jour = date.fromisoformat(entree["date"])
    assert jour <= date.today()


@pytest.mark.parametrize("entree", ENTREES, ids=lambda e: e["titre"])
def test_l_effet_donne_un_avant_et_un_apres(entree):
    """La partie la plus facile à bâcler, et la seule qui serve vraiment.

    On ne peut pas vérifier automatiquement qu'un avant/après est *juste*. On
    peut exiger qu'il soit *écrit* : soit chiffré — « 752 avant, 717 après »
    suffit et tient en cinq mots —, soit assez développé pour dire en toutes
    lettres ce qui a changé quand aucun nombre ne s'y prête.
    """
    effet = entree["effet"].strip()
    chiffre = any(c.isdigit() for c in effet)
    assert chiffre or len(effet.split()) >= 15, (
        "un effet non chiffré doit être explicité")


@pytest.mark.parametrize("entree", ENTREES, ids=lambda e: e["titre"])
def test_le_commit_existe(entree):
    """L'empreinte doit désigner un commit réel : le registre publie le lien.

    Ignoré hors dépôt git — une archive du code reste utilisable, et le test ne
    doit pas transformer son absence en échec.
    """
    r = subprocess.run(
        ["git", "cat-file", "-t", entree["commit"]],
        cwd=RACINE, capture_output=True, text=True)
    if r.returncode != 0 and "not a git repository" in r.stderr.lower():
        pytest.skip("hors dépôt git")
    assert r.returncode == 0, f"commit {entree['commit']} introuvable"
    assert r.stdout.strip() == "commit"
