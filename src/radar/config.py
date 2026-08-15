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


#: Nombre de sièges à l'Assemblée nationale. Ce n'est pas un paramètre de
#: mesure : l'article 24 de la Constitution plafonne l'effectif à 577 députés,
#: et l'article LO 119 du code électoral le fixe à ce plafond. Un siège peut
#: être vacant — décès, démission, annulation d'une élection en attente d'une
#: partielle — donc l'effectif d'un jour donné est **au plus** 577, jamais
#: plus. `controles.effectifs` refuse de publier si le compte le dépasse.
SIEGES = 577

#: Positions de vote normalisées.
POSITIONS = ("pour", "contre", "abstention", "nonVotant")

#: Positions qui comptent comme un vote exprimé.
EXPRESSED = ("pour", "contre", "abstention")

#: Causes de non-vote qui ne sont pas un choix du député, mais la conséquence
#: d'une fonction incompatible avec le vote. La source les nomme explicitement ;
#: les compter comme des absences reviendrait à reprocher à un élu d'avoir
#: occupé le perchoir ou un poste ministériel.
#:
#: - `MG`  — membre du Gouvernement ;
#: - `PSE` — président de séance, celui qui préside au moment du scrutin ;
#: - `PAN` — président de l'Assemblée nationale.
#:
#: `PAN` manquait à cette liste, et le coût était considérable : ses 7 508
#: non-votes tombaient au dénominateur de présence de la seule personne
#: concernée. Sur les votes qui engagent, la présidente de l'Assemblée était
#: publiée à 8,6 % (19 sur 221) au lieu de 100 % (19 sur 19) — le site donnait
#: la titulaire du perchoir pour la députée la moins assidue de la législature.
#: C'est très exactement l'erreur que `analyze.participation` décrit et prétend
#: écarter.
#:
#: `PDS` figurait ici et ne correspond à aucune ligne de la source : le code
#: glosait deux fois « président de séance » pour deux codes différents, signe
#: que la liste avait été écrite d'après une supposition plutôt que d'après les
#: données. Les causes réellement présentes sont vérifiées par un test.
STRUCTURAL_NONVOTE_CAUSES = frozenset({"MG", "PSE", "PAN"})
