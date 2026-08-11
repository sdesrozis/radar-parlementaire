"""Normalisation des JSON de l'Assemblée nationale en tables Parquet.

Les JSON de l'AN sont une transposition automatique du XML : listes qui
deviennent des dicts quand elles n'ont qu'un élément, valeurs nulles encodées
`{"@xsi:nil": "true"}`, textes rangés sous `#text`. Tout le sale boulot est ici,
pour que le reste du code travaille sur des tables plates.

Tables produites dans `data/tables/` :

- `organes`   : groupes politiques, commissions, assemblée…
- `deputes`   : état civil + groupe + circonscription
- `mandats`   : chaque mandat (député, groupe, commission) avec ses dates
- `scrutins`  : un scrutin public par ligne
- `votes`     : (scrutin × député) → position de vote
- `amendements` : amendements déposés, auteur, sort
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import polars as pl

from .config import paths
from .fetch import json_files

# --------------------------------------------------------------------------
# Aplatissement des bizarreries XML→JSON
# --------------------------------------------------------------------------


def as_list(x: Any) -> list:
    """`None` → [], dict → [dict], list → list. Le piège n°1 de ces fichiers."""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def text(x: Any) -> str | None:
    """Extrait la valeur d'un champ, en gérant `#text` et `@xsi:nil`."""
    if x is None:
        return None
    if isinstance(x, dict):
        if x.get("@xsi:nil") == "true":
            return None
        v = x.get("#text")
        return v if v not in ("", None) else None
    if isinstance(x, str):
        return x if x != "" else None
    return str(x)


def dig(d: Any, *keys: str) -> Any:
    """Accès en profondeur tolérant aux `None` : dig(d, "a", "b", "c")."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _int(x: Any) -> int | None:
    v = text(x)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _iter_json(files: Iterable[Path]) -> Iterator[dict]:
    for f in files:
        try:
            yield json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue


# --------------------------------------------------------------------------
# Organes (groupes politiques, commissions…)
# --------------------------------------------------------------------------

ORGANE_SCHEMA = {
    "organe_uid": pl.Utf8,
    "code_type": pl.Utf8,
    "libelle": pl.Utf8,
    "libelle_abrege": pl.Utf8,
    "libelle_abrev": pl.Utf8,
    "date_debut": pl.Utf8,
    "date_fin": pl.Utf8,
    "legislature": pl.Utf8,
    "position_politique": pl.Utf8,
}


def build_organes() -> pl.DataFrame:
    rows = []
    for doc in _iter_json(json_files("acteurs", "organe")):
        o = doc.get("organe", doc)
        rows.append(
            {
                "organe_uid": text(o.get("uid")),
                "code_type": text(o.get("codeType")),
                "libelle": text(o.get("libelle")),
                "libelle_abrege": text(o.get("libelleAbrege")),
                "libelle_abrev": text(o.get("libelleAbrev")),
                "date_debut": text(dig(o, "viMoDe", "dateDebut")),
                "date_fin": text(dig(o, "viMoDe", "dateFin")),
                "legislature": text(o.get("legislature")),
                "position_politique": text(o.get("positionPolitique")),
            }
        )
    return pl.DataFrame(rows, schema=ORGANE_SCHEMA).unique(subset="organe_uid")


# --------------------------------------------------------------------------
# Acteurs → députés + mandats
# --------------------------------------------------------------------------

MANDAT_SCHEMA = {
    "mandat_uid": pl.Utf8,
    "acteur_uid": pl.Utf8,
    "type_organe": pl.Utf8,
    "organe_uid": pl.Utf8,
    "legislature": pl.Utf8,
    "date_debut": pl.Utf8,
    "date_fin": pl.Utf8,
    "qualite": pl.Utf8,
    "departement": pl.Utf8,
    "num_departement": pl.Utf8,
    "num_circo": pl.Utf8,
    "region": pl.Utf8,
}


def _mandat_rows(acteur: dict) -> Iterator[dict]:
    acteur_uid = text(acteur.get("uid"))
    for m in as_list(dig(acteur, "mandats", "mandat")):
        for organe_uid in [text(r) for r in as_list(dig(m, "organes", "organeRef"))]:
            yield {
                "mandat_uid": text(m.get("uid")),
                "acteur_uid": acteur_uid,
                "type_organe": text(m.get("typeOrgane")),
                "organe_uid": organe_uid,
                "legislature": text(m.get("legislature")),
                "date_debut": text(m.get("dateDebut")),
                "date_fin": text(m.get("dateFin")),
                "qualite": text(dig(m, "infosQualite", "codeQualite")),
                "departement": text(dig(m, "election", "lieu", "departement")),
                "num_departement": text(dig(m, "election", "lieu", "numDepartement")),
                "num_circo": text(dig(m, "election", "lieu", "numCirco")),
                "region": text(dig(m, "election", "lieu", "region")),
            }


DEPUTE_SCHEMA = {
    "acteur_uid": pl.Utf8,
    "civilite": pl.Utf8,
    "prenom": pl.Utf8,
    "nom": pl.Utf8,
    "date_naissance": pl.Utf8,
    "date_deces": pl.Utf8,
    "profession": pl.Utf8,
    "cat_socio_pro": pl.Utf8,
    "uri_hatvp": pl.Utf8,
}


def build_acteurs() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Retourne (acteurs, mandats) bruts, toutes législatures confondues."""
    acteurs, mandats = [], []
    for doc in _iter_json(json_files("acteurs", "acteur")):
        a = doc.get("acteur", doc)
        acteurs.append(
            {
                "acteur_uid": text(a.get("uid")),
                "civilite": text(dig(a, "etatCivil", "ident", "civ")),
                "prenom": text(dig(a, "etatCivil", "ident", "prenom")),
                "nom": text(dig(a, "etatCivil", "ident", "nom")),
                "date_naissance": text(dig(a, "etatCivil", "infoNaissance", "dateNais")),
                "date_deces": text(dig(a, "etatCivil", "dateDeces")),
                "profession": text(dig(a, "profession", "libelleCourant")),
                "cat_socio_pro": text(dig(a, "profession", "socProcINSEE", "catSocPro")),
                "uri_hatvp": text(a.get("uri_hatvp")),
            }
        )
        mandats.extend(_mandat_rows(a))
    return (
        pl.DataFrame(acteurs, schema=DEPUTE_SCHEMA).unique(subset="acteur_uid"),
        pl.DataFrame(mandats, schema=MANDAT_SCHEMA),
    )


def build_deputes(
    acteurs: pl.DataFrame, mandats: pl.DataFrame, organes: pl.DataFrame, legislature: int
) -> pl.DataFrame:
    """Députés de la législature demandée, avec groupe politique et circonscription.

    Le groupe retenu est celui du mandat GP le plus récent : un député qui change
    de groupe en cours de législature est rattaché à son groupe actuel.
    """
    leg = str(legislature)
    seat = (
        mandats.filter(
            (pl.col("type_organe") == "ASSEMBLEE") & (pl.col("legislature") == leg)
        )
        .sort("date_debut")
        .group_by("acteur_uid")
        .agg(
            pl.col("date_debut").min().alias("mandat_debut"),
            # Un `date_fin` nul l'emporte : le mandat court toujours.
            pl.when(pl.col("date_fin").is_null().any())
            .then(None)
            .otherwise(pl.col("date_fin").max())
            .alias("mandat_fin"),
            pl.col("departement").drop_nulls().last().alias("departement"),
            pl.col("num_departement").drop_nulls().last().alias("num_departement"),
            pl.col("num_circo").drop_nulls().last().alias("num_circo"),
            pl.col("region").drop_nulls().last().alias("region"),
        )
    )

    gp = (
        mandats.filter((pl.col("type_organe") == "GP") & (pl.col("legislature") == leg))
        # Un mandat encore ouvert l'emporte sur un mandat clos, même plus récent.
        .sort(["acteur_uid", pl.col("date_fin").is_null(), "date_debut"])
        .group_by("acteur_uid")
        .agg(
            pl.col("organe_uid").last().alias("groupe_uid"),
            pl.col("qualite").last().alias("groupe_qualite"),
            pl.col("organe_uid").n_unique().alias("nb_groupes_legislature"),
        )
    )

    groupes = organes.select(
        pl.col("organe_uid").alias("groupe_uid"),
        pl.col("libelle").alias("groupe_libelle"),
        pl.col("libelle_abrev").alias("groupe"),
        pl.col("position_politique").alias("groupe_position"),
    )

    return (
        seat.join(acteurs, on="acteur_uid", how="left")
        .join(gp, on="acteur_uid", how="left")
        .join(groupes, on="groupe_uid", how="left")
        .with_columns(
            (pl.col("prenom") + " " + pl.col("nom")).alias("nom_complet"),
            pl.col("mandat_fin").is_null().alias("en_exercice"),
            pl.col("mandat_debut").str.to_date(strict=False).alias("mandat_debut_d"),
            pl.col("mandat_fin").str.to_date(strict=False).alias("mandat_fin_d"),
        )
        .sort("nom")
    )


# --------------------------------------------------------------------------
# Scrutins → scrutins + votes
# --------------------------------------------------------------------------

SCRUTIN_SCHEMA = {
    "scrutin_uid": pl.Utf8,
    "numero": pl.Int64,
    "date": pl.Utf8,
    "legislature": pl.Utf8,
    "type_vote_code": pl.Utf8,
    "type_vote_libelle": pl.Utf8,
    "sort_code": pl.Utf8,
    "sort_libelle": pl.Utf8,
    "titre": pl.Utf8,
    "demandeur": pl.Utf8,
    "dossier_uid": pl.Utf8,
    "mode_publication": pl.Utf8,
    "nb_votants": pl.Int64,
    "suffrages_exprimes": pl.Int64,
    "suffrages_requis": pl.Int64,
    "n_pour": pl.Int64,
    "n_contre": pl.Int64,
    "n_abstention": pl.Int64,
    "n_non_votant": pl.Int64,
}

VOTE_SCHEMA = {
    "scrutin_uid": pl.Utf8,
    "acteur_uid": pl.Utf8,
    "groupe_uid": pl.Utf8,
    "position": pl.Utf8,
    "position_majoritaire_groupe": pl.Utf8,
    "par_delegation": pl.Boolean,
    "cause": pl.Utf8,
}

#: Clé du bloc nominatif → position normalisée.
_BUCKETS = {
    "pours": "pour",
    "contres": "contre",
    "abstentions": "abstention",
    "nonVotants": "nonVotant",
}


def _scrutin_row(s: dict) -> dict:
    dec = dig(s, "syntheseVote", "decompte") or {}
    return {
        "scrutin_uid": text(s.get("uid")),
        "numero": _int(s.get("numero")),
        "date": text(s.get("dateScrutin")),
        "legislature": text(s.get("legislature")),
        "type_vote_code": text(dig(s, "typeVote", "codeTypeVote")),
        "type_vote_libelle": text(dig(s, "typeVote", "libelleTypeVote")),
        "sort_code": text(dig(s, "sort", "code")),
        "sort_libelle": text(dig(s, "sort", "libelle")),
        "titre": text(s.get("titre")) or text(dig(s, "objet", "libelle")),
        "demandeur": text(dig(s, "demandeur", "texte")),
        "dossier_uid": text(dig(s, "objet", "dossierLegislatif")),
        "mode_publication": text(s.get("modePublicationDesVotes")),
        "nb_votants": _int(dig(s, "syntheseVote", "nombreVotants")),
        "suffrages_exprimes": _int(dig(s, "syntheseVote", "suffragesExprimes")),
        "suffrages_requis": _int(dig(s, "syntheseVote", "nbrSuffragesRequis")),
        "n_pour": _int(dec.get("pour")),
        "n_contre": _int(dec.get("contre")),
        "n_abstention": _int(dec.get("abstentions")),
        "n_non_votant": _int(dec.get("nonVotants")),
    }


def _vote_rows(s: dict) -> Iterator[dict]:
    scrutin_uid = text(s.get("uid"))
    for organe in as_list(dig(s, "ventilationVotes", "organe")):
        for g in as_list(dig(organe, "groupes", "groupe")):
            groupe_uid = text(g.get("organeRef"))
            majoritaire = text(dig(g, "vote", "positionMajoritaire"))
            nominatif = dig(g, "vote", "decompteNominatif") or {}
            for bucket, position in _BUCKETS.items():
                for v in as_list(dig(nominatif, bucket, "votant")):
                    yield {
                        "scrutin_uid": scrutin_uid,
                        "acteur_uid": text(v.get("acteurRef")),
                        "groupe_uid": groupe_uid,
                        "position": position,
                        "position_majoritaire_groupe": majoritaire,
                        "par_delegation": text(v.get("parDelegation")) == "true",
                        "cause": text(v.get("causePositionVote")),
                    }


def build_scrutins() -> tuple[pl.DataFrame, pl.DataFrame]:
    scrutins, votes = [], []
    for doc in _iter_json(json_files("scrutins", "json")):
        s = doc.get("scrutin", doc)
        scrutins.append(_scrutin_row(s))
        votes.extend(_vote_rows(s))
    df_s = (
        pl.DataFrame(scrutins, schema=SCRUTIN_SCHEMA)
        .unique(subset="scrutin_uid")
        .with_columns(pl.col("date").str.to_date(strict=False).alias("date_d"))
        .sort("numero")
    )
    df_v = pl.DataFrame(votes, schema=VOTE_SCHEMA).unique(
        subset=["scrutin_uid", "acteur_uid"]
    )
    return df_s, df_v


# --------------------------------------------------------------------------
# Amendements
# --------------------------------------------------------------------------

AMENDEMENT_SCHEMA = {
    "amendement_uid": pl.Utf8,
    "numero": pl.Utf8,
    "texte_legislatif_uid": pl.Utf8,
    "legislature": pl.Utf8,
    "date_depot": pl.Utf8,
    "date_sort": pl.Utf8,
    "etat": pl.Utf8,
    "sort": pl.Utf8,
    "auteur_uid": pl.Utf8,
    "auteur_groupe_uid": pl.Utf8,
    "auteur_type": pl.Utf8,
    "nb_cosignataires": pl.Int64,
    "division_titre": pl.Utf8,
    "division_article": pl.Utf8,
    "dispositif": pl.Utf8,
    "expose_sommaire": pl.Utf8,
}


def _strip_html(x: Any) -> str | None:
    v = text(x)
    if v is None:
        return None
    import re

    return re.sub(r"<[^>]+>", " ", v).replace("&nbsp;", " ").strip() or None


def _amendement_row(a: dict) -> dict:
    ident = a.get("identification", a)
    auteurs = as_list(dig(a, "signataires", "auteur")) or as_list(a.get("auteur"))
    auteur = auteurs[0] if auteurs else {}
    cosign = as_list(dig(a, "signataires", "cosignataires", "acteurRef"))
    return {
        "amendement_uid": text(a.get("uid")),
        "numero": text(dig(ident, "numeroLong")) or text(dig(ident, "numeroOrdreDepot")),
        "texte_legislatif_uid": text(dig(a, "texteLegislatifRef"))
        or text(dig(ident, "texteLegislatifRef")),
        "legislature": text(dig(ident, "legislature")) or text(a.get("legislature")),
        "date_depot": text(dig(a, "cycleDeVie", "dateDepot")),
        "date_sort": text(dig(a, "cycleDeVie", "dateSort")),
        "etat": text(dig(a, "cycleDeVie", "etatDesTraitements", "etat", "libelle")),
        "sort": text(dig(a, "cycleDeVie", "sort")),
        "auteur_uid": text(auteur.get("acteurRef")),
        "auteur_groupe_uid": text(auteur.get("groupePolitiqueRef")),
        "auteur_type": text(auteur.get("typeAuteur")),
        "nb_cosignataires": len(cosign),
        "division_titre": text(dig(a, "pointeurFragmentTexte", "division", "titre")),
        "division_article": text(dig(a, "pointeurFragmentTexte", "division", "articleDesignationCourte")),
        "dispositif": _strip_html(dig(a, "corps", "contenuAuteur", "dispositif")),
        "expose_sommaire": _strip_html(dig(a, "corps", "contenuAuteur", "exposeSommaire")),
    }


def build_positions_groupe(votes: pl.DataFrame) -> pl.DataFrame:
    """Position majoritaire de chaque groupe sur chaque scrutin, recalculée.

    L'AN publie un champ `positionMajoritaire`, mais il diverge du dépouillement
    nominatif dans environ 8 % des cas (position de consigne plutôt que position
    constatée). Pour mesurer une dissidence, on veut la position réellement
    majoritaire dans le groupe : on la recompte donc à partir des votes.

    En cas d'égalité parfaite entre deux positions, aucune n'est majoritaire et
    la ligne est marquée `partage` : le groupe n'avait pas de ligne.
    """
    compte = (
        votes.filter(pl.col("position").is_in(["pour", "contre", "abstention"]))
        .group_by("scrutin_uid", "groupe_uid", "position")
        .agg(pl.len().alias("n"))
    )
    return (
        compte.sort("n", descending=True)
        .group_by("scrutin_uid", "groupe_uid")
        .agg(
            pl.col("position").first().alias("majoritaire"),
            pl.col("n").first().alias("n_majoritaire"),
            pl.col("n").sum().alias("votants_groupe"),
            # Égalité au sommet : le groupe est partagé, pas de ligne majoritaire.
            (pl.col("n").sort(descending=True).slice(0, 2).n_unique() == 1)
            .and_(pl.col("n").len() > 1)
            .alias("partage"),
        )
        .with_columns(
            pl.when(pl.col("partage")).then(None).otherwise(pl.col("majoritaire"))
            .alias("majoritaire"),
            (pl.col("n_majoritaire") / pl.col("votants_groupe")).alias("part_majoritaire"),
        )
    )


def build_amendements() -> pl.DataFrame:
    files = json_files("amendements", "json")
    rows = []
    for doc in _iter_json(files):
        a = doc.get("amendement", doc)
        rows.append(_amendement_row(a))
    return (
        pl.DataFrame(rows, schema=AMENDEMENT_SCHEMA)
        .unique(subset="amendement_uid")
        .with_columns(
            pl.col("date_depot").str.slice(0, 10).str.to_date(strict=False).alias("date_depot_d")
        )
    )


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def build_all(legislature: int, *, with_amendements: bool = False) -> dict[str, pl.DataFrame]:
    """Construit toutes les tables et les écrit en Parquet."""
    p = paths().ensure()
    organes = build_organes()
    acteurs, mandats = build_acteurs()
    deputes = build_deputes(acteurs, mandats, organes, legislature)
    scrutins, votes = build_scrutins()

    tables = {
        "organes": organes,
        "mandats": mandats,
        "deputes": deputes,
        "scrutins": scrutins,
        "votes": votes,
        "positions_groupe": build_positions_groupe(votes),
    }
    if with_amendements:
        tables["amendements"] = build_amendements()

    for name, df in tables.items():
        df.write_parquet(p.tables / f"{name}.parquet")
    return tables


def load(name: str) -> pl.DataFrame:
    """Charge une table déjà construite."""
    f = paths().tables / f"{name}.parquet"
    if not f.exists():
        raise FileNotFoundError(
            f"table '{name}' absente — lancez d'abord `radar build` ({f})"
        )
    return pl.read_parquet(f)
