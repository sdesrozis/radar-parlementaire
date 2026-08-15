"""Téléchargement et décompression des jeux de données de l'Assemblée nationale.

Le téléchargement est *conditionnel* : on garde l'en-tête `Last-Modified` renvoyé
par le serveur et on ne retélécharge que si l'archive a changé. Utile parce que
l'AN republie les fichiers plusieurs fois par jour et que l'archive des
amendements pèse ~300 Mo.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from .config import LEGISLATURE, Source, paths, sources

_STAMP = ".fetch-state.json"
_CHUNK = 1 << 20


@dataclass
class FetchResult:
    key: str
    path: Path
    updated: bool
    size: int
    last_modified: str | None

    def __str__(self) -> str:
        state = "mis à jour" if self.updated else "déjà à jour"
        return f"{self.key:12s} {state:12s} {self.size / 1e6:8.1f} Mo  {self.last_modified or ''}"


def _load_state(raw: Path) -> dict[str, dict]:
    f = raw / _STAMP
    if f.exists():
        return json.loads(f.read_text())
    return {}


def _save_state(raw: Path, state: dict[str, dict]) -> None:
    (raw / _STAMP).write_text(json.dumps(state, indent=2, ensure_ascii=False))


def fetch_one(src: Source, *, force: bool = False, timeout: float = 120.0) -> FetchResult:
    """Télécharge une archive si elle a changé, puis la décompresse."""
    p = paths().ensure()
    zip_path = p.raw / f"{src.key}.zip"
    dest = p.raw / src.extract_dir
    state = _load_state(p.raw)
    known = state.get(src.key, {})

    headers = {}
    if not force and zip_path.exists() and known.get("last_modified"):
        headers["If-Modified-Since"] = known["last_modified"]

    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with client.stream("GET", src.url, headers=headers) as r:
            if r.status_code == 304 and dest.exists():
                return FetchResult(src.key, dest, False, zip_path.stat().st_size,
                                   known.get("last_modified"))
            r.raise_for_status()
            tmp = zip_path.with_suffix(".part")
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(_CHUNK):
                    fh.write(chunk)
            tmp.replace(zip_path)
            last_modified = r.headers.get("last-modified")

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)

    state[src.key] = {"last_modified": last_modified, "url": src.url}
    _save_state(p.raw, state)
    return FetchResult(src.key, dest, True, zip_path.stat().st_size, last_modified)


def fetch_all(keys: list[str] | None = None, *, force: bool = False) -> list[FetchResult]:
    """Télécharge plusieurs jeux de données. Par défaut : scrutins et acteurs.

    Les amendements ne sont pas inclus par défaut : l'archive est lourde et
    n'est pas nécessaire pour l'analyse des votes.
    """
    catalogue = sources()
    keys = keys or ["scrutins", "acteurs"]
    unknown = set(keys) - set(catalogue)
    if unknown:
        raise KeyError(f"jeu(x) de données inconnu(s) : {sorted(unknown)}")
    return [fetch_one(catalogue[k], force=force) for k in keys]


# --------------------------------------------------------------------------
# Les portraits officiels
# --------------------------------------------------------------------------
#
# Ils ne sont dans aucun jeu de données : le JSON d'un acteur porte `uid`,
# `etatCivil`, `profession`, `mandats`, `adresses` et `uri_hatvp`, et rien
# d'autre. La photographie vit sur le site institutionnel, à une adresse qui
# se déduit de l'identifiant — c'est d'ailleurs celle que la HATVP appelle
# depuis ses propres pages nominatives.
#
# C'est donc le seul élément du site bâti sur une convention d'adresse plutôt
# que sur une donnée publiée : elle peut changer sans préavis et sans que rien
# ne l'annonce. Deux conséquences tenues ailleurs dans le code — le générateur
# n'exige pas la photo pour écrire une fiche, et une photo absente ne laisse
# aucune silhouette de remplacement, qui ferait croire à un visage.
_PORTRAIT_URL = "https://www.assemblee-nationale.fr/dyn/static/tribun/{leg}/photos/{n}.jpg"

#: L'état de chaque portrait : son `ETag` et son `Last-Modified`, ou la date à
#: laquelle la source a répondu qu'elle n'en avait pas.
#:
#: Il joue le rôle que `.fetch-state.json` joue pour les archives, et pour la
#: même raison : **un portrait se met à jour comme le reste du site.** Un
#: député qui fait changer sa photographie officielle en cours de mandat verrait
#: sinon la précédente republiée indéfiniment, parce qu'un fichier déjà présent
#: sur le disque n'aurait plus jamais été redemandé. Chaque exécution
#: redemande donc les 648, conditionnellement : le serveur répond 304 pour
#: celles qui n'ont pas bougé, et rien ne transite.
_ETAT = ".portraits-state.json"


@dataclass
class PortraitsResult:
    nouveaux: int
    modifies: int
    inchanges: int
    retires: list[str]
    absents: list[str]
    dossier: Path

    def __str__(self) -> str:
        return (f"portraits    {self.nouveaux} nouveaux, {self.modifies} mis à jour, "
                f"{self.inchanges} inchangés, {len(self.absents)} sans photo à la source")


def fetch_portraits(
    uids: Iterable[str],
    *,
    legislature: int = LEGISLATURE,
    force: bool = False,
    timeout: float = 30.0,
) -> PortraitsResult:
    """Met à jour les portraits officiels des députés donnés, tels qu'ils sont servis.

    Conditionnel, comme `fetch_one` : on renvoie l'`ETag` et le `Last-Modified`
    connus, et on ne rapatrie que ce qui a changé depuis la dernière fois.

    Aucune retouche ici : `data/raw/` garde ce que le serveur a envoyé. Le
    redimensionnement et la recompression sont l'affaire de qui publie.
    """
    dest = paths().ensure().raw / "portraits"
    dest.mkdir(parents=True, exist_ok=True)
    fichier_etat = dest / _ETAT
    etat: dict[str, dict] = (
        {} if force or not fichier_etat.exists() else json.loads(fichier_etat.read_text())
    )

    uids = list(dict.fromkeys(uids))
    aujourd_hui = date.today().isoformat()

    def un(uid: str) -> tuple[str, str, dict | None]:
        """Rend (uid, ce qui s'est passé, nouvel état)."""
        connu = etat.get(uid, {})
        jpg = dest / f"{uid}.jpg"
        headers = {}
        # Un état connu ne vaut conditionnel que si le fichier est encore là :
        # un `data/raw/` partiellement effacé se rechargerait sinon en 304,
        # c'est-à-dire pas du tout.
        if jpg.exists():
            if connu.get("etag"):
                headers["If-None-Match"] = connu["etag"]
            if connu.get("last_modified"):
                headers["If-Modified-Since"] = connu["last_modified"]

        r = client.get(
            _PORTRAIT_URL.format(leg=legislature, n=uid.removeprefix("PA")), headers=headers
        )
        if r.status_code == 304:
            return uid, "inchange", connu
        # Un 404 est une réponse, pas une panne : le site institutionnel n'a
        # pas de photo de ce député. Tout autre code est une anomalie dont on
        # veut être averti plutôt que de publier des fiches silencieusement
        # dépeuplées de leurs portraits.
        if r.status_code == 404:
            # Une photographie retirée de la source est retirée d'ici. Garder
            # le fichier reviendrait à republier un portrait que l'Assemblée
            # ne montre plus, sans que rien ne le signale ; et le manque est
            # sans gravité, puisque la prochaine exécution le retéléchargera
            # si le 404 n'était que passager.
            retire = jpg.exists()
            jpg.unlink(missing_ok=True)
            return uid, "retire" if retire else "absent", {"absent_depuis": aujourd_hui}
        r.raise_for_status()
        neuf = not jpg.exists()
        tmp = dest / f"{uid}.part"
        tmp.write_bytes(r.content)
        tmp.replace(jpg)
        return uid, "nouveau" if neuf else "modifie", {
            "etag": r.headers.get("etag"),
            "last_modified": r.headers.get("last-modified"),
        }

    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            issues = list(pool.map(un, uids))

    for uid, _, nouvel_etat in issues:
        if nouvel_etat is not None:
            etat[uid] = nouvel_etat
    fichier_etat.write_text(json.dumps(etat, indent=2, ensure_ascii=False, sort_keys=True))

    compte = {quoi: sum(1 for _, q, _ in issues if q == quoi)
              for quoi in ("nouveau", "modifie", "inchange")}
    return PortraitsResult(
        nouveaux=compte["nouveau"],
        modifies=compte["modifie"],
        inchanges=compte["inchange"],
        retires=sorted(u for u, q, _ in issues if q == "retire"),
        absents=sorted(u for u, q, _ in issues if q in ("absent", "retire")),
        dossier=dest,
    )


def json_files(extract_dir: str, subdir: str = "") -> list[Path]:
    """Liste les JSON d'un jeu décompressé, quelle que soit la profondeur du zip."""
    root = paths().raw / extract_dir
    if subdir:
        # L'archive contient un dossier `json/`, parfois précédé d'un niveau.
        matches = [d for d in root.rglob(subdir) if d.is_dir()]
        roots = matches or [root]
    else:
        roots = [root]
    out: list[Path] = []
    for r in roots:
        out.extend(sorted(r.rglob("*.json")))
    return out
