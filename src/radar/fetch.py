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
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Source, paths, sources

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
