"""Réseau de cosignatures d'amendements.

**Pourquoi un second réseau, alors qu'on a déjà les votes ?**

Parce que les deux mesurent des choses différentes. Le vote mesure la
*discipline* : un député vote avec son groupe parce que c'est la consigne, et
l'accord intra-groupe dépasse 90 % partout. La cosignature mesure l'*initiative*
: personne n'est tenu de cosigner l'amendement d'un collègue, et on ne cosigne
pas celui d'un adversaire par inadvertance. C'est un acte volontaire, public et
coûteux — donc bien plus informatif sur les affinités réelles.

Concrètement : deux députés qui votent pareil à 95 % peuvent n'avoir jamais
cosigné ensemble ; deux députés de groupes opposés qui cosignent régulièrement
travaillent ensemble, ce qu'aucune analyse de vote ne révèle.

**Méthode.** Un amendement signé par A (auteur) et cosigné par B et C crée les
liens A–B, A–C et B–C. On construit la matrice d'incidence creuse
(députés × amendements) et son produit `D · Dᵀ` donne, pour chaque paire, le
nombre d'amendements cosignés ensemble.

L'affinité est mesurée par l'indice de Jaccard — `communs / (total_A + total_B −
communs)` — et non par le compte brut : sans cette normalisation, le classement
ne remonterait que les députés les plus prolifiques.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl
from scipy import sparse

from .parse import load


@dataclass
class ReseauCosignatures:
    """Matrice de cosignature entre députés, plus ce qu'il faut pour la lire."""

    deputes: pl.DataFrame
    #: (n × n) — nombre d'amendements cosignés par chaque paire.
    communs: np.ndarray
    #: (n,) — nombre d'amendements signés par chaque député (auteur ou cosignataire).
    signatures: np.ndarray
    n_amendements: int

    @property
    def n_deputes(self) -> int:
        return len(self.signatures)

    def jaccard(self, min_signatures: int = 20) -> np.ndarray:
        """Affinité de cosignature, normalisée par le volume d'activité.

        Le compte brut favorise mécaniquement les gros déposants. Jaccard
        rapporte les cosignatures communes à l'union des deux répertoires : un
        député discret qui cosigne presque toujours avec le même collègue
        remonte, un hyperactif qui cosigne avec tout le monde ne remonte pas.
        """
        union = (
            self.signatures[:, None] + self.signatures[None, :] - self.communs
        ).astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            j = self.communs / union
        # Sous ce seuil, l'indice devient instable : deux députés à trois
        # amendements chacun peuvent afficher une affinité de 1.
        rare = self.signatures < min_signatures
        j[rare, :] = np.nan
        j[:, rare] = np.nan
        np.fill_diagonal(j, np.nan)
        return j

    def groupes(self) -> list[str]:
        return self.deputes["groupe"].to_list()

    def noms(self) -> list[str]:
        return self.deputes["nom_complet"].to_list()

    def index_depute(self, nom: str) -> int:
        noms = self.noms()
        cible = nom.casefold()
        exact = [i for i, n in enumerate(noms) if n.casefold() == cible]
        if exact:
            return exact[0]
        partiels = [i for i, n in enumerate(noms) if cible in n.casefold()]
        if len(partiels) == 1:
            return partiels[0]
        if not partiels:
            raise KeyError(f"aucun député ne correspond à « {nom} »")
        raise KeyError(f"« {nom} » est ambigu : {', '.join(noms[i] for i in partiels[:8])}")


def build_reseau(
    *,
    depuis: str | None = None,
    max_signataires: int | None = 10,
    exclure_rapporteurs: bool = True,
    en_exercice_seulement: bool = True,
) -> ReseauCosignatures:
    """Construit le réseau de cosignatures depuis la table des amendements.

    Deux garde-fous, sans lesquels le réseau ne mesure pas ce qu'on croit :

    **Les dépôts de groupe entier.** La moitié des amendements portent au moins
    dix signatures, et le 90ᵉ centile en compte 71 — l'effectif exact du groupe
    LFI. Ce sont des dépôts collectifs : ils relient mécaniquement tous les
    membres d'un groupe deux à deux, et le réseau finit par mesurer
    l'appartenance au groupe plutôt qu'une affinité entre personnes. D'où le
    plafond, réglé bas par défaut.

    **Les amendements de rapporteur.** Deux co-rapporteurs d'un même texte
    cosignent des dizaines d'amendements rédactionnels, quels que soient leurs
    groupes. Sans filtre, la paire la plus « affine » de toute l'Assemblée est
    une députée EPR et une députée LFI qui ont corédigé un texte — leurs 94
    amendements communs sont pour l'essentiel intitulés « Rédactionnel » et
    adoptés à 91 %. C'est un rôle institutionnel, pas une alliance.

    Args:
        depuis: ne compter que les amendements déposés à partir de cette date.
        max_signataires: plafond de signataires par amendement. `None` le retire,
            ce qui n'est justifié que pour étudier les dépôts collectifs eux-mêmes.
        exclure_rapporteurs: écarter les amendements déposés au nom d'une commission.
        en_exercice_seulement: ne garder que les députés siégeant aujourd'hui.
    """
    deputes = load("deputes")
    if en_exercice_seulement:
        deputes = deputes.filter(pl.col("en_exercice"))
    deputes = deputes.sort("nom_complet").with_row_index("i")
    index = dict(zip(deputes["acteur_uid"].to_list(), deputes["i"].to_list()))

    amd = load("amendements")
    if depuis:
        amd = amd.filter(pl.col("date_depot_d") >= pl.lit(depuis).str.to_date())
    if exclure_rapporteurs:
        amd = amd.filter(~pl.col("auteur_rapporteur").fill_null(False))

    # Signataires = auteur + cosignataires, en une seule liste par amendement.
    signataires = amd.select(
        pl.concat_list(
            pl.col("auteur_uid").fill_null(""), pl.col("cosignataires")
        ).alias("signataires")
    )["signataires"].to_list()

    lignes: list[int] = []
    colonnes: list[int] = []
    for j, sig in enumerate(signataires):
        idx = {index[s] for s in sig if s in index}
        if len(idx) < 2:
            continue  # un amendement solitaire ne crée aucun lien
        if max_signataires and len(idx) > max_signataires:
            continue
        lignes.extend(idx)
        colonnes.extend([j] * len(idx))

    n = deputes.height
    D = sparse.csr_matrix(
        (np.ones(len(lignes), dtype=np.int32), (lignes, colonnes)),
        shape=(n, len(signataires)),
    )
    communs = np.asarray((D @ D.T).todense())
    signatures = np.asarray(D.sum(axis=1)).ravel()

    return ReseauCosignatures(
        deputes=deputes,
        communs=communs,
        signatures=signatures,
        n_amendements=len(set(colonnes)),
    )


# --------------------------------------------------------------------------
# Lectures du réseau
# --------------------------------------------------------------------------


def paires_cosignataires(
    reseau: ReseauCosignatures,
    k: int = 20,
    *,
    inter_groupes: bool = True,
    min_signatures: int = 20,
    min_communs: int = 5,
) -> pl.DataFrame:
    """Les binômes qui cosignent le plus souvent ensemble.

    `inter_groupes` est vrai par défaut : deux députés du même groupe qui
    cosignent, c'est le fonctionnement normal d'un groupe. L'information est
    dans les binômes qui traversent les frontières.
    """
    j = reseau.jaccard(min_signatures)
    groupes = np.array(reseau.groupes(), dtype=object)
    masque = np.triu(np.ones_like(j, dtype=bool), k=1)
    masque &= reseau.communs >= min_communs
    if inter_groupes:
        masque &= groupes[:, None] != groupes[None, :]

    valeurs = np.where(masque, j, np.nan)
    # On ne trie que les paires réellement retenues. Trier tout le tableau puis
    # couper à k laisse passer des paires masquées dès qu'il y a moins de k
    # candidates : `argsort` classe alors des NaN, et la relecture des valeurs
    # dans la matrice non masquée leur redonne un score valide.
    plats = valeurs.ravel()
    valides = np.flatnonzero(~np.isnan(plats))
    ordre = valides[np.argsort(-plats[valides])][:k]
    i_s, j_s = np.unravel_index(ordre, valeurs.shape)
    noms = reseau.noms()
    return pl.DataFrame(
        {
            "depute_a": [noms[int(i)] for i in i_s],
            "groupe_a": [groupes[int(i)] for i in i_s],
            "depute_b": [noms[int(x)] for x in j_s],
            "groupe_b": [groupes[int(x)] for x in j_s],
            "affinite": [float(j[i, x]) for i, x in zip(map(int, i_s), map(int, j_s))],
            "amendements_communs": [
                int(reseau.communs[i, x]) for i, x in zip(map(int, i_s), map(int, j_s))
            ],
        }
    )


def cosignatures_entre_groupes(reseau: ReseauCosignatures) -> pl.DataFrame:
    """Part des cosignatures de chaque groupe qui vont vers chaque autre groupe.

    On raisonne en parts de lignes et non en affinité moyenne : un groupe de 122
    députés et un groupe de 10 n'ont pas le même potentiel de cosignature, et la
    part sortante répond directement à « avec qui ce groupe travaille-t-il ? ».
    """
    groupes = reseau.groupes()
    uniques = sorted({g for g in groupes if g})
    idx = {g: np.array([i for i, x in enumerate(groupes) if x == g]) for g in uniques}

    lignes = []
    for a in uniques:
        bloc_total = reseau.communs[idx[a], :].sum()
        for b in uniques:
            bloc = reseau.communs[np.ix_(idx[a], idx[b])]
            if a == b:
                # Ne pas compter deux fois les liens internes, ni la diagonale.
                total = (bloc.sum() - np.trace(bloc)) / 2
            else:
                total = bloc.sum()
            lignes.append(
                {
                    "groupe_a": a,
                    "groupe_b": b,
                    "liens": float(total),
                    "part": float(total / bloc_total) if bloc_total else float("nan"),
                }
            )
    return pl.DataFrame(lignes)


def courtiers(
    reseau: ReseauCosignatures, k: int = 20, *, min_signatures: int = 30
) -> pl.DataFrame:
    """Députés qui cosignent hors de leur groupe bien plus que ne le voudrait le hasard.

    **Le piège à éviter.** La part brute de cosignatures hors groupe ne se
    compare pas d'un député à l'autre : un membre d'un groupe de 17 a 96 % de
    l'Assemblée « hors de son groupe », contre 79 % pour un membre d'un groupe
    de 122. Classer sur la part brute revient à classer les groupes par petite
    taille — c'est ce que faisait une première version, dont le palmarès était
    intégralement occupé par le plus petit groupe.

    **La correction.** On rapporte la part observée à la part attendue sous
    mélange aléatoire, `(N − effectif_du_groupe) / (N − 1)`. Le ratio vaut 1
    quand le député cosigne hors de son groupe exactement autant que le hasard
    le prédit, et dépasse 1 quand il sort réellement de son camp.

    Les non-inscrits sont exclus : n'étant pas un groupe, leur « hors groupe »
    n'a pas de sens.
    """
    groupes = np.array(reseau.groupes(), dtype=object)
    hors_groupe = groupes[:, None] != groupes[None, :]
    communs = reseau.communs.copy()
    np.fill_diagonal(communs, 0)

    total = communs.sum(axis=1)
    dehors = (communs * hors_groupe).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        part = dehors / total

    # Part attendue si un député cosignait au hasard parmi tous les autres.
    n = len(groupes)
    effectifs = {g: int((groupes == g).sum()) for g in set(groupes)}
    attendue = np.array(
        [(n - effectifs[g]) / (n - 1) for g in groupes], dtype=float
    )

    return (
        pl.DataFrame(
            {
                "nom_complet": reseau.noms(),
                "groupe": list(groupes),
                "signatures": reseau.signatures.astype(np.int64),
                "liens_hors_groupe": dehors.astype(np.int64),
                "part_hors_groupe": part,
                "part_attendue": attendue,
                "ratio": part / attendue,
            }
        )
        .filter(
            (pl.col("signatures") >= min_signatures)
            & pl.col("groupe").is_not_null()
            & (pl.col("groupe") != "NI")
            & pl.col("ratio").is_not_nan()
        )
        .sort("ratio", descending=True)
        .head(k)
    )


def comparer_vote_et_cosignature(
    reseau: ReseauCosignatures, accords_vote: pl.DataFrame
) -> pl.DataFrame:
    """Confronte la proximité de vote et l'affinité de cosignature, par paire de groupes.

    **Hypothèse.** Voter ensemble et travailler ensemble sont deux choses
    distinctes. Deux groupes peuvent voter pareil par convergence d'intérêts
    ponctuelle sans jamais rien construire en commun ; à l'inverse, deux groupes
    opposés dans l'hémicycle peuvent cosigner des textes de niche.

    **Méthode.** Pour chaque paire de groupes, on met côte à côte l'accord de
    vote et la part des cosignatures adressées à l'autre groupe. Le rang de
    chaque mesure est comparé : `ecart_rang` est positif quand une paire
    cosigne davantage que ce que sa proximité de vote laisserait attendre.
    """
    cos = cosignatures_entre_groupes(reseau).filter(
        pl.col("groupe_a") != pl.col("groupe_b")
    )
    vote = accords_vote.filter(pl.col("groupe_a") != pl.col("groupe_b"))

    return (
        vote.join(cos, on=["groupe_a", "groupe_b"], how="inner")
        .rename({"accord": "accord_vote", "part": "part_cosignatures"})
        .with_columns(
            pl.col("accord_vote").rank(descending=True).alias("rang_vote"),
            pl.col("part_cosignatures").rank(descending=True).alias("rang_cosignature"),
        )
        .with_columns(
            (pl.col("rang_vote") - pl.col("rang_cosignature")).alias("ecart_rang")
        )
        .sort("ecart_rang", descending=True)
        .select(
            "groupe_a", "groupe_b", "accord_vote", "part_cosignatures",
            "liens", "ecart_rang",
        )
    )
