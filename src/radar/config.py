"""Chemins et sources de données du radar."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Législature courante de l'Assemblée nationale.
LEGISLATURE = int(os.environ.get("RADAR_LEGISLATURE", "17"))

_BASE = "https://data.assemblee-nationale.fr/static/openData/repository"


@dataclass(frozen=True)
class Source:
    """Un jeu de données open data de l'Assemblée nationale."""

    key: str
    url: str
    #: Sous-dossier de `data/raw/` où le zip est décompressé.
    extract_dir: str
    description: str


def sources(legislature: int = LEGISLATURE) -> dict[str, Source]:
    ll = legislature
    return {
        "scrutins": Source(
            key="scrutins",
            url=f"{_BASE}/{ll}/loi/scrutins/Scrutins.json.zip",
            extract_dir="scrutins",
            description="Scrutins publics : un JSON par scrutin, avec le vote nominatif.",
        ),
        "acteurs": Source(
            key="acteurs",
            url=(
                f"{_BASE}/{ll}/amo/tous_acteurs_mandats_organes_xi_legislature"
                "/AMO30_tous_acteurs_tous_mandats_tous_organes_historique.json.zip"
            ),
            extract_dir="acteurs",
            description="Acteurs (députés), mandats et organes (groupes, commissions).",
        ),
        "amendements": Source(
            key="amendements",
            url=f"{_BASE}/{ll}/loi/amendements_div_legis/Amendements.json.zip",
            extract_dir="amendements",
            description="Amendements déposés, leur auteur, leur sort. Volumineux (~300 Mo).",
        ),
    }


def project_root() -> Path:
    """Racine du projet : `RADAR_HOME`, sinon deux niveaux au-dessus du package."""
    env = os.environ.get("RADAR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def tables(self) -> Path:
        return self.root / "data" / "tables"

    @property
    def out(self) -> Path:
        return self.root / "data" / "out"

    def ensure(self) -> "Paths":
        for p in (self.raw, self.tables, self.out):
            p.mkdir(parents=True, exist_ok=True)
        return self


def paths() -> Paths:
    return Paths(project_root())


#: Positions de vote normalisées.
POSITIONS = ("pour", "contre", "abstention", "nonVotant")

#: Positions qui comptent comme un vote exprimé.
EXPRESSED = ("pour", "contre", "abstention")

#: Causes de non-vote qui ne sont pas un choix du député (fonction incompatible).
#: MG = membre du Gouvernement, PDS = président de séance, PSE = président de séance.
STRUCTURAL_NONVOTE_CAUSES = frozenset({"MG", "PDS", "PSE"})
