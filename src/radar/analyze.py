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
from .parse import ORDRE_PORTEES, load

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
    portee: str | list[str] | None = None,
    categories: list[str] | None = None,
    contestation_min: float | None = None,
    min_votants: int = 0,
    en_exercice_seulement: bool = True,
) -> VoteCube:
    """Assemble le cube de votes à partir des tables Parquet.

    Args:
        depuis, jusqua: bornes de date (`AAAA-MM-JJ`) sur les scrutins.
        types_vote: codes officiels, ex. `["SPS"]` pour les scrutins solennels.
        portee: enjeu politique — `"texte"`, `"intermediaire"` ou `"detail"`.
            **C'est le filtre le plus important.** Sans lui, les 7 216 votes
            d'amendement écrasent les 245 votes qui engagent politiquement, et
            les conclusions changent : cf. `comparer_portees()`.
        categories: catégories fines, ex. `["ensemble"]` (vote sur l'ensemble
            d'un texte) ou `["motion_censure"]`.
        contestation_min: ne garder que les scrutins dont la position
            minoritaire pèse au moins cette fraction. Écarte les votes joués
            d'avance, qui gonflent l'accord entre tous les députés.
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
    if portee:
        scrutins = scrutins.filter(
            pl.col("portee").is_in([portee] if isinstance(portee, str) else portee)
        )
    if categories:
        scrutins = scrutins.filter(pl.col("categorie").is_in(categories))
    if contestation_min is not None:
        scrutins = scrutins.filter(pl.col("contestation") >= contestation_min)
    if min_votants:
        scrutins = scrutins.filter(pl.col("nb_votants") >= min_votants)

    if scrutins.is_empty():
        raise ValueError("aucun scrutin ne correspond à ces filtres")

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
    # Ne trier que les paires retenues : sinon, quand il y a moins de k
    # candidates, `argsort` classe des NaN et des paires exclues par le masque
    # ressortent avec un score relu dans la matrice non masquée.
    plats = valeurs.ravel()
    valides = np.flatnonzero(~np.isnan(plats))
    ordre = valides[np.argsort(-plats[valides])][:k]
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
    )


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


def comparer_portees(
    paires_groupes: list[tuple[str, str]] | None = None,
    *,
    portees: tuple[str, ...] = ORDRE_PORTEES,
    min_communs: int = 10,
) -> pl.DataFrame:
    """Mesure l'accord entre groupes selon l'enjeu politique du scrutin.

    **Hypothèse.** Un vote sur un sous-amendement et un vote sur l'ensemble
    d'une loi ne disent pas la même chose. Le premier est souvent tactique — on
    vote un aménagement technique sans approuver le texte ; le second engage.
    Or 86 % des scrutins publics sont des votes d'amendement : toute moyenne
    non pondérée mesure surtout de la tactique.

    **Méthode.** On recalcule l'accord entre groupes sur trois populations
    disjointes de scrutins (`detail`, `intermediaire`, `texte`) et on regarde
    si le classement des proximités bouge.

    **Résultat** (17ᵉ législature, août 2026) : il bouge beaucoup, et de façon
    monotone avec l'enjeu. LFI↔SOC tombe de 79 % à 60 %, RN↔DR monte de 67 % à
    86 %. La part de variance captée par le premier axe passe de 24 % à 39 %,
    c'est-à-dire que le clivage politique devient bien plus lisible dès qu'on
    retire les amendements. Publier une proximité « tous scrutins confondus »
    revient donc à sous-estimer les divisions à gauche et la convergence à
    droite.

    Args:
        paires_groupes: paires à suivre. Par défaut, toutes les paires.
        portees: portées à comparer, dans l'ordre croissant d'enjeu.
    """
    lignes = []
    for p in portees:
        cube = build_cube(portee=p)
        accords = accord_entre_groupes(cube, min_communs=min_communs)
        carte = carte_politique(cube)
        accords = accords.with_columns(
            pl.lit(p).alias("portee"),
            pl.lit(cube.n_scrutins).alias("n_scrutins"),
            pl.lit(float(carte["inertie_x"][0])).alias("inertie_axe1"),
        )
        if paires_groupes:
            garde = pl.any_horizontal(
                *[
                    (pl.col("groupe_a") == a) & (pl.col("groupe_b") == b)
                    for a, b in paires_groupes
                ]
            )
            accords = accords.filter(garde)
        lignes.append(accords)

    ordre = {p: i for i, p in enumerate(portees)}
    return (
        pl.concat(lignes)
        .with_columns(pl.col("portee").replace_strict(ordre).alias("_o"))
        .sort("groupe_a", "groupe_b", "_o")
        .drop("_o")
    )


def sous_cube(cube: VoteCube, colonnes: np.ndarray) -> VoteCube:
    """Restreint un cube à un sous-ensemble de scrutins, par index de colonnes."""
    return VoteCube(
        deputes=cube.deputes,
        scrutins=cube.scrutins[colonnes],
        pour=cube.pour[:, colonnes],
        contre=cube.contre[:, colonnes],
        abstention=cube.abstention[:, colonnes],
        non_votant=cube.non_votant[:, colonnes],
        eligible=cube.eligible[:, colonnes],
    )


def verifier_taille_echantillon(
    paires_groupes: list[tuple[str, str]],
    *,
    n_tirages: int = 40,
    graine: int = 0,
    min_communs: int = 10,
) -> pl.DataFrame:
    """Vérifie que l'effet de portée n'est pas un artefact de taille d'échantillon.

    **L'objection à écarter.** Les votes sur l'ensemble ne sont que 245, contre
    7 216 votes d'amendement. Un écart entre deux échantillons de tailles si
    différentes pourrait n'être que du bruit d'échantillonnage.

    **Le test.** On tire au hasard, de nombreuses fois, 245 votes d'amendement,
    et on recalcule l'accord sur chaque tirage. Si l'accord observé sur les
    votes sur l'ensemble tombe *dans* l'étendue de ces tirages, l'écart n'est
    pas concluant. S'il tombe en dehors, la portée du scrutin explique bien
    quelque chose que le hasard n'explique pas.

    **Résultat** (17ᵉ législature) : les trois paires testées tombent nettement
    hors de l'étendue. LFI↔SOC est à 63 % sur les textes quand 40 tirages
    d'amendements donnent 72–87 %. L'effet est réel.
    """
    detail = build_cube(portee="detail")
    texte = build_cube(portee="texte")
    accords_texte = accord_entre_groupes(texte, min_communs=min_communs)
    n_cible = texte.n_scrutins
    rng = np.random.default_rng(graine)

    tirages: dict[tuple[str, str], list[float]] = {p: [] for p in paires_groupes}
    for _ in range(n_tirages):
        colonnes = rng.choice(detail.n_scrutins, n_cible, replace=False)
        accords = accord_entre_groupes(sous_cube(detail, colonnes), min_communs=min_communs)
        for a, b in paires_groupes:
            v = accords.filter((pl.col("groupe_a") == a) & (pl.col("groupe_b") == b))
            tirages[(a, b)].append(float(v["accord"][0]))

    lignes = []
    for (a, b), valeurs in tirages.items():
        v = np.array(valeurs)
        observe = float(
            accords_texte.filter(
                (pl.col("groupe_a") == a) & (pl.col("groupe_b") == b)
            )["accord"][0]
        )
        lignes.append(
            {
                "groupe_a": a,
                "groupe_b": b,
                "accord_sur_textes": observe,
                "amendements_moyenne": float(v.mean()),
                "amendements_min": float(v.min()),
                "amendements_max": float(v.max()),
                "hors_intervalle": not (v.min() <= observe <= v.max()),
            }
        )
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


#: Fraction des suffrages d'un groupe qu'une position doit réunir pour qu'on
#: parle de « ligne ». La majorité absolue est le seuil le moins arbitraire :
#: en deçà, la position dominante n'est qu'une pluralité et le groupe est
#: partagé. Voir `votes_vs_ligne`.
SEUIL_LIGNE = 0.5


def votes_vs_ligne(
    scrutins: pl.DataFrame | None = None,
    *,
    min_votants_groupe: int = 5,
    seuil_ligne: float = SEUIL_LIGNE,
) -> pl.DataFrame:
    """Votes exprimés, enrichis d'un booléen `dissident`.

    La « ligne du groupe » est la position majoritaire **recalculée** depuis le
    dépouillement nominatif (voir `parse.build_positions_groupe`).

    **Encore faut-il qu'il y ait une ligne.** C'est le piège de cette mesure, et
    il vient du fait qu'il y a trois positions possibles et non deux : la
    position la plus fréquente d'un groupe peut ne réunir qu'une minorité de
    ses suffrages. Un groupe qui vote 7 pour, 5 contre et 5 abstentions a une
    position dominante à 41 % ; compter les dix autres votes comme
    « dissidents » revient à traiter un groupe qui n'a pas su se mettre
    d'accord comme un groupe dont dix membres auraient enfreint une consigne.
    Ce sont deux situations politiquement opposées.

    D'où `seuil_ligne` : seuls les scrutins où une position réunit plus que
    cette fraction des suffrages du groupe entrent dans le calcul. Par défaut,
    la majorité absolue. Sous le seuil, le groupe est **partagé** — ce n'est
    pas un cas de dissidence, c'est un autre phénomène, qui mérite d'être
    décrit par ses trois effectifs plutôt que par un taux.

    Args:
        scrutins: restreint à ces scrutins. Tous par défaut.
        min_votants_groupe: ignore les groupes ayant trop peu voté sur le
            scrutin — sur quatre votants, une « ligne » ne veut rien dire.
        seuil_ligne: fraction minimale des suffrages du groupe pour qu'une
            position compte comme ligne. `0.0` restitue l'ancien comportement,
            où toute pluralité faisait ligne.
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
            & (pl.col("part_majoritaire") > seuil_ligne)
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
