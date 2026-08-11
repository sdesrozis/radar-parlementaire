"""Analyses du radar : proximité de vote, cohésion, dissidence, classements.

Tout part d'un `VoteCube` : la matrice (députés × scrutins) des positions de
vote, plus la matrice d'éligibilité qui dit, pour chaque scrutin, quels députés
siégeaient ce jour-là. Cette seconde matrice est ce qui distingue une vraie
absence d'un simple « ce député n'était pas encore élu ».
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .config import STRUCTURAL_NONVOTE_CAUSES
from .parse import load

# --------------------------------------------------------------------------
# Construction du cube de votes
# --------------------------------------------------------------------------


@dataclass
class VoteCube:
    """Positions de vote sous forme matricielle, prêtes pour numpy.

    Les lignes suivent l'ordre de `deputes`, les colonnes celui de `scrutins`.
    """

    deputes: pl.DataFrame
    scrutins: pl.DataFrame
    pour: np.ndarray
    contre: np.ndarray
    abstention: np.ndarray
    non_votant: np.ndarray
    eligible: np.ndarray

    @property
    def n_deputes(self) -> int:
        return self.pour.shape[0]

    @property
    def n_scrutins(self) -> int:
        return self.pour.shape[1]

    @property
    def exprime(self) -> np.ndarray:
        """Le député a exprimé un suffrage (pour / contre / abstention)."""
        return self.pour | self.contre | self.abstention

    @property
    def signe(self) -> np.ndarray:
        """+1 pour, -1 contre, 0 sinon. Base des analyses factorielles."""
        return self.pour.astype(np.float32) - self.contre.astype(np.float32)

    def noms(self) -> list[str]:
        return self.deputes["nom_complet"].to_list()

    def groupes(self) -> list[str]:
        return self.deputes["groupe"].to_list()

    def index_depute(self, nom: str) -> int:
        """Index d'un député par nom, insensible à la casse et partiel."""
        noms = self.deputes["nom_complet"].to_list()
        cible = nom.casefold()
        exact = [i for i, n in enumerate(noms) if n.casefold() == cible]
        if exact:
            return exact[0]
        partiels = [i for i, n in enumerate(noms) if cible in n.casefold()]
        if len(partiels) == 1:
            return partiels[0]
        if not partiels:
            raise KeyError(f"aucun député ne correspond à « {nom} »")
        raise KeyError(
            f"« {nom} » est ambigu : {', '.join(noms[i] for i in partiels[:8])}"
        )


def build_cube(
    *,
    depuis: str | None = None,
    jusqua: str | None = None,
    types_vote: list[str] | None = None,
    min_votants: int = 0,
    en_exercice_seulement: bool = True,
) -> VoteCube:
    """Assemble le cube de votes à partir des tables Parquet.

    Args:
        depuis, jusqua: bornes de date (`AAAA-MM-JJ`) sur les scrutins.
        types_vote: codes à conserver, ex. `["SOL"]` pour les scrutins solennels.
        min_votants: ignore les scrutins avec trop peu de votants.
        en_exercice_seulement: ne garder que les députés siégeant aujourd'hui.
    """
    deputes = load("deputes")
    scrutins = load("scrutins")
    votes = load("votes")

    if en_exercice_seulement:
        deputes = deputes.filter(pl.col("en_exercice"))
    if depuis:
        scrutins = scrutins.filter(pl.col("date_d") >= pl.lit(depuis).str.to_date())
    if jusqua:
        scrutins = scrutins.filter(pl.col("date_d") <= pl.lit(jusqua).str.to_date())
    if types_vote:
        scrutins = scrutins.filter(pl.col("type_vote_code").is_in(types_vote))
    if min_votants:
        scrutins = scrutins.filter(pl.col("nb_votants") >= min_votants)

    deputes = deputes.sort("nom_complet").with_row_index("i")
    scrutins = scrutins.sort("date_d", "numero").with_row_index("j")

    v = (
        votes.join(deputes.select("acteur_uid", "i"), on="acteur_uid", how="inner")
        .join(scrutins.select("scrutin_uid", "j"), on="scrutin_uid", how="inner")
        .select("i", "j", "position")
    )

    n, m = deputes.height, scrutins.height
    mats = {p: np.zeros((n, m), dtype=bool) for p in
            ("pour", "contre", "abstention", "nonVotant")}
    ii = v["i"].to_numpy()
    jj = v["j"].to_numpy()
    pos = v["position"].to_numpy()
    for p, mat in mats.items():
        sel = pos == p
        mat[ii[sel], jj[sel]] = True

    # Éligibilité : le mandat du député couvre-t-il la date du scrutin ?
    dates = scrutins["date_d"].to_numpy().astype("datetime64[D]")
    debut = deputes["mandat_debut_d"].to_numpy().astype("datetime64[D]")
    fin_raw = deputes["mandat_fin_d"].to_list()
    fin = np.array(
        [np.datetime64("2999-12-31") if f is None else np.datetime64(f) for f in fin_raw],
        dtype="datetime64[D]",
    )
    eligible = (dates[None, :] >= debut[:, None]) & (dates[None, :] <= fin[:, None])

    return VoteCube(
        deputes=deputes,
        scrutins=scrutins,
        pour=mats["pour"],
        contre=mats["contre"],
        abstention=mats["abstention"],
        non_votant=mats["nonVotant"],
        eligible=eligible,
    )


# --------------------------------------------------------------------------
# « Quels députés votent le plus souvent ensemble ? »
# --------------------------------------------------------------------------


def agreement(cube: VoteCube, min_communs: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """Taux d'accord entre chaque paire de députés.

    L'accord est calculé sur les seuls scrutins où **les deux** ont exprimé un
    suffrage : un député absent n'est ni d'accord ni en désaccord. Les paires
    ayant moins de `min_communs` scrutins en commun sont mises à `NaN` plutôt
    que de produire un « 100 % d'accord » sur trois votes.

    Returns:
        (taux, communs) — deux matrices carrées (n_députés × n_députés).
    """
    P = cube.pour.astype(np.float32)
    C = cube.contre.astype(np.float32)
    A = cube.abstention.astype(np.float32)
    V = cube.exprime.astype(np.float32)

    accords = P @ P.T + C @ C.T + A @ A.T
    communs = V @ V.T
    with np.errstate(invalid="ignore", divide="ignore"):
        taux = accords / communs
    taux[communs < min_communs] = np.nan
    np.fill_diagonal(taux, np.nan)
    return taux, communs


def plus_proches(
    cube: VoteCube, nom: str, k: int = 10, *, inverse: bool = False,
    hors_groupe: bool = False, min_communs: int = 30,
) -> pl.DataFrame:
    """Les `k` députés qui votent le plus (ou le moins) comme `nom`."""
    taux, communs = agreement(cube, min_communs)
    i = cube.index_depute(nom)
    ligne = taux[i].copy()

    if hors_groupe:
        mon_groupe = cube.deputes["groupe"][i]
        meme = np.array([g == mon_groupe for g in cube.groupes()])
        ligne[meme] = np.nan

    ordre = np.argsort(ligne if inverse else -ligne)
    ordre = [j for j in ordre if not np.isnan(ligne[j])][:k]
    return pl.DataFrame(
        {
            "depute": [cube.deputes["nom_complet"][j] for j in map(int, ordre)],
            "groupe": [cube.deputes["groupe"][j] for j in map(int, ordre)],
            "accord": [float(ligne[j]) for j in map(int, ordre)],
            "scrutins_communs": [int(communs[i, j]) for j in map(int, ordre)],
        }
    )


def paires_remarquables(
    cube: VoteCube, k: int = 20, *, min_communs: int = 100, inter_groupes: bool = True
) -> pl.DataFrame:
    """Les paires de députés les plus alignées, de préférence entre groupes.

    Deux députés d'un même groupe qui votent pareil, ce n'est pas une
    information. Deux députés de groupes opposés à 95 % d'accord, si.
    """
    taux, communs = agreement(cube, min_communs)
    groupes = np.array(cube.groupes(), dtype=object)
    masque = np.triu(np.ones_like(taux, dtype=bool), k=1)
    if inter_groupes:
        masque &= groupes[:, None] != groupes[None, :]

    valeurs = np.where(masque, taux, np.nan)
    plats = valeurs.ravel()
    ordre = np.argsort(-np.nan_to_num(plats, nan=-1.0))[:k]
    i_s, j_s = np.unravel_index(ordre, valeurs.shape)
    noms = cube.noms()
    return pl.DataFrame(
        {
            "depute_a": [noms[int(i)] for i in i_s],
            "groupe_a": [groupes[int(i)] for i in i_s],
            "depute_b": [noms[int(j)] for j in j_s],
            "groupe_b": [groupes[int(j)] for j in j_s],
            "accord": [float(taux[i, j]) for i, j in zip(map(int, i_s), map(int, j_s))],
            "scrutins_communs": [int(communs[i, j]) for i, j in zip(map(int, i_s), map(int, j_s))],
        }
    ).filter(pl.col("accord").is_not_nan())


def accord_entre_groupes(cube: VoteCube, min_communs: int = 30) -> pl.DataFrame:
    """Taux d'accord moyen entre chaque paire de groupes politiques."""
    taux, _ = agreement(cube, min_communs)
    groupes = cube.groupes()
    uniques = sorted({g for g in groupes if g})
    idx = {g: np.array([k for k, x in enumerate(groupes) if x == g]) for g in uniques}

    lignes = []
    for a in uniques:
        for b in uniques:
            bloc = taux[np.ix_(idx[a], idx[b])]
            if a == b:
                bloc = bloc[~np.eye(len(idx[a]), dtype=bool)]
            valeur = float(np.nanmean(bloc)) if bloc.size else float("nan")
            lignes.append({"groupe_a": a, "groupe_b": b, "accord": valeur})
    return pl.DataFrame(lignes)


# --------------------------------------------------------------------------
# Cohésion, dissidence, participation
# --------------------------------------------------------------------------


def cohesion_groupes(cube: VoteCube, min_communs: int = 30) -> pl.DataFrame:
    """Cohésion interne de chaque groupe : accord moyen entre ses membres."""
    inter = accord_entre_groupes(cube, min_communs)
    interne = inter.filter(pl.col("groupe_a") == pl.col("groupe_b")).select(
        pl.col("groupe_a").alias("groupe"), pl.col("accord").alias("cohesion")
    )
    tailles = (
        cube.deputes.group_by("groupe").len().rename({"len": "effectif"})
    )
    return interne.join(tailles, on="groupe", how="left").sort("cohesion", descending=True)


def votes_vs_ligne(
    scrutins: pl.DataFrame | None = None, *, min_votants_groupe: int = 5
) -> pl.DataFrame:
    """Votes exprimés, enrichis d'un booléen `dissident`.

    La « ligne du groupe » est la position majoritaire **recalculée** depuis le
    dépouillement nominatif (voir `parse.build_positions_groupe`). Les scrutins
    où le groupe s'est partagé à égalité sont exclus : sans ligne majoritaire,
    parler de dissidence n'a pas de sens.
    """
    votes = load("votes")
    lignes = load("positions_groupe")
    if scrutins is not None:
        votes = votes.join(scrutins.select("scrutin_uid"), on="scrutin_uid", how="inner")
    return (
        votes.filter(pl.col("position").is_in(["pour", "contre", "abstention"]))
        .join(lignes, on=["scrutin_uid", "groupe_uid"], how="inner")
        .filter(
            pl.col("majoritaire").is_not_null()
            & (pl.col("votants_groupe") >= min_votants_groupe)
        )
        .with_columns((pl.col("position") != pl.col("majoritaire")).alias("dissident"))
    )


def dissidence(cube: VoteCube, min_votes: int = 50) -> pl.DataFrame:
    """Part des votes où le député s'écarte de la ligne majoritaire de son groupe."""
    deputes = cube.deputes.select("acteur_uid", "nom_complet", "groupe")

    d = (
        votes_vs_ligne(cube.scrutins)
        .join(deputes, on="acteur_uid", how="inner")
        .group_by("acteur_uid", "nom_complet", "groupe")
        .agg(
            pl.len().alias("votes_exprimes"),
            pl.col("dissident").sum().alias("votes_dissidents"),
        )
        .filter(pl.col("votes_exprimes") >= min_votes)
        .with_columns(
            (pl.col("votes_dissidents") / pl.col("votes_exprimes")).alias("taux_dissidence")
        )
    )
    return d.sort("taux_dissidence", descending=True)


def participation(cube: VoteCube) -> pl.DataFrame:
    """Taux de participation, rapporté aux seuls scrutins où le député siégeait.

    Les non-votants « structurels » (membre du Gouvernement, président de
    séance) sont retirés du dénominateur : ce ne sont pas des absences.
    """
    votes = load("votes")
    structurels = (
        votes.join(cube.scrutins.select("scrutin_uid"), on="scrutin_uid", how="inner")
        .filter(pl.col("cause").is_in(list(STRUCTURAL_NONVOTE_CAUSES)))
        .group_by("acteur_uid")
        .agg(pl.len().alias("non_votants_structurels"))
    )

    eligibles = cube.eligible.sum(axis=1)
    exprimes = (cube.exprime & cube.eligible).sum(axis=1)

    df = pl.DataFrame(
        {
            "acteur_uid": cube.deputes["acteur_uid"],
            "nom_complet": cube.deputes["nom_complet"],
            "groupe": cube.deputes["groupe"],
            "scrutins_eligibles": eligibles.astype(np.int64),
            "votes_exprimes": exprimes.astype(np.int64),
        }
    ).join(structurels, on="acteur_uid", how="left")

    return (
        df.with_columns(pl.col("non_votants_structurels").fill_null(0))
        .with_columns(
            (pl.col("scrutins_eligibles") - pl.col("non_votants_structurels"))
            .clip(lower_bound=1)
            .alias("denominateur")
        )
        .with_columns((pl.col("votes_exprimes") / pl.col("denominateur")).alias("participation"))
        .drop("denominateur")
        .sort("participation", descending=True)
    )


# --------------------------------------------------------------------------
# Carte politique
# --------------------------------------------------------------------------


def carte_politique(cube: VoteCube, *, methode: str = "pca") -> pl.DataFrame:
    """Projette les députés en deux dimensions à partir de leurs votes.

    - `pca` : analyse en composantes principales de la matrice signée (+1/−1).
      Rapide, et le premier axe reproduit en général le clivage majorité /
      opposition.
    - `mds` : positionnement multidimensionnel sur la distance `1 − accord`.
      Plus fidèle aux proximités deux à deux, mais plus lent.

    Les axes n'ont pas de sens politique intrinsèque : seul compte le
    positionnement relatif des députés.
    """
    if methode == "pca":
        X = cube.signe.copy()
        X -= X.mean(axis=0, keepdims=True)
        U, S, _ = np.linalg.svd(X, full_matrices=False)
        coords = U[:, :2] * S[:2]
        inertie = (S**2 / (S**2).sum())[:2]
    elif methode == "mds":
        from scipy.linalg import eigh

        taux, _ = agreement(cube, min_communs=10)
        D = 1.0 - np.nan_to_num(taux, nan=float(np.nanmean(taux)))
        np.fill_diagonal(D, 0.0)
        n = D.shape[0]
        J = np.eye(n) - np.ones((n, n)) / n
        B = -0.5 * J @ (D**2) @ J
        vals, vecs = eigh(B, subset_by_index=[n - 2, n - 1])
        ordre = np.argsort(-vals)
        coords = vecs[:, ordre] * np.sqrt(np.clip(vals[ordre], 0, None))
        inertie = np.clip(vals[ordre], 0, None) / np.abs(vals).sum()
    else:
        raise ValueError("methode doit être 'pca' ou 'mds'")

    return pl.DataFrame(
        {
            "nom_complet": cube.noms(),
            "groupe": cube.groupes(),
            "x": coords[:, 0].astype(float),
            "y": coords[:, 1].astype(float),
        }
    ).with_columns(
        pl.lit(float(inertie[0])).alias("inertie_x"),
        pl.lit(float(inertie[1])).alias("inertie_y"),
    )


# --------------------------------------------------------------------------
# Scrutins remarquables
# --------------------------------------------------------------------------


def scrutins_serres(cube: VoteCube, k: int = 20, ecart_max: int = 10) -> pl.DataFrame:
    """Les scrutins qui se sont joués à quelques voix."""
    return (
        cube.scrutins.with_columns(
            (pl.col("n_pour") - pl.col("n_contre")).abs().alias("ecart")
        )
        .filter((pl.col("ecart") <= ecart_max) & (pl.col("nb_votants") > 0))
        .sort("ecart")
        .head(k)
        .select("date", "numero", "ecart", "n_pour", "n_contre", "sort_code", "titre")
    )


def scrutins_clivants(cube: VoteCube, k: int = 20) -> pl.DataFrame:
    """Les scrutins qui ont le plus fracturé les groupes politiques."""
    return (
        votes_vs_ligne(cube.scrutins)
        .join(cube.scrutins.select("scrutin_uid", "date", "titre"),
              on="scrutin_uid", how="inner")
        .group_by("scrutin_uid", "date", "titre")
        .agg(
            pl.len().alias("votes_exprimes"),
            pl.col("dissident").sum().alias("votes_dissidents"),
        )
        .filter(pl.col("votes_exprimes") >= 100)
        .with_columns(
            (pl.col("votes_dissidents") / pl.col("votes_exprimes")).alias("taux_fracture")
        )
        .sort("taux_fracture", descending=True)
        .head(k)
    )


# --------------------------------------------------------------------------
# « Qui dépose quels amendements ? »
# --------------------------------------------------------------------------


def amendements_par_depute(k: int | None = None, *, depuis: str | None = None) -> pl.DataFrame:
    """Classement des députés par nombre d'amendements déposés et taux d'adoption."""
    amd = load("amendements")
    deputes = load("deputes").select("acteur_uid", "nom_complet", "groupe")
    if depuis:
        amd = amd.filter(pl.col("date_depot_d") >= pl.lit(depuis).str.to_date())

    out = (
        amd.filter(pl.col("auteur_uid").is_not_null())
        .join(deputes, left_on="auteur_uid", right_on="acteur_uid", how="inner")
        .group_by("auteur_uid", "nom_complet", "groupe")
        .agg(
            pl.len().alias("amendements"),
            (pl.col("sort") == "Adopté").sum().alias("adoptes"),
            (pl.col("sort") == "Rejeté").sum().alias("rejetes"),
            pl.col("nb_cosignataires").mean().alias("cosignataires_moyen"),
        )
        .with_columns(
            (pl.col("adoptes") / pl.col("amendements")).alias("taux_adoption")
        )
        .sort("amendements", descending=True)
    )
    return out.head(k) if k else out


def amendements_par_groupe(*, depuis: str | None = None) -> pl.DataFrame:
    """Volume et taux d'adoption des amendements, par groupe politique."""
    amd = load("amendements")
    groupes = load("organes").select(
        pl.col("organe_uid").alias("auteur_groupe_uid"), pl.col("libelle_abrev").alias("groupe")
    )
    if depuis:
        amd = amd.filter(pl.col("date_depot_d") >= pl.lit(depuis).str.to_date())
    return (
        amd.join(groupes, on="auteur_groupe_uid", how="inner")
        .group_by("groupe")
        .agg(
            pl.len().alias("amendements"),
            (pl.col("sort") == "Adopté").sum().alias("adoptes"),
            pl.col("auteur_uid").n_unique().alias("deputes_deposants"),
        )
        .with_columns((pl.col("adoptes") / pl.col("amendements")).alias("taux_adoption"))
        .sort("amendements", descending=True)
    )
