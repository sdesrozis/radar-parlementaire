"""« Quels sujets explosent cette semaine ? »

On construit un corpus hebdomadaire à partir des titres de scrutins et des
amendements déposés, puis on compare la fréquence de chaque terme à sa propre
moyenne des semaines précédentes. Un terme n'est pas signalé parce qu'il est
fréquent — « article », « amendement » et « gouvernement » le sont toujours —
mais parce qu'il est *plus fréquent que d'habitude*.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import polars as pl

from .parse import load

# Mots vides français, complétés du jargon parlementaire qui sature sinon
# tous les classements.
STOPWORDS = frozenset(
    """
    a au aux avec ce ces dans de des du elle en et eux il ils je la le les leur
    lui ma mais me meme mes moi mon ne nos notre nous on ou par pas pour qu que
    qui sa se ses son sur ta te tes toi ton tu un une vos votre vous y d l n s c
    j m t est sont etre ete avoir eu fait faire plus moins tres aussi ainsi donc
    or ni car si sans sous entre vers chez apres avant pendant depuis jusqu
    lorsque quand comme tout tous toute toutes autre autres meme memes tel telle
    cet cette ceux celle celles dont lequel laquelle lesquels auquel
    amendement amendements article articles alinea alineas rediger redige
    substituer inserer supprimer completer remplacer mots mot phrase phrases
    apres avant premier premiere second seconde suivant suivante suivants
    present presente code loi lois texte textes projet proposition
    assemblee nationale gouvernement ministre commission seance
    lecture lectures ensemble visant visan relatif relative relatifs portant
    instituant modifiant creant tendant permettant favorisant renforcant
    sein egard matiere oeuvre lieu titre unique nouvelle nouveau
    disposition dispositions mesure mesures cas chapitre section
    paragraphe conditions condition application applicable prevu prevue
    vise visee mentionne mentionnee defini definie fixe fixee
    euros millions milliards montant montants numero nos
    etat etats francais france national nationale nationaux
    ii iii iv vi vii viii ix xi xii bis ter quater
    ont sont avaient etaient soit puisse doivent doit peut peuvent
    mme mmes mlle collegues identique identiques supprime suppression
    rectifie rectifiee rect annee annees leurs
    """.split()
)


@lru_cache(maxsize=1)
def noms_de_deputes() -> frozenset[str]:
    """Noms et prénoms des députés, à écarter du comptage des termes.

    Les titres de scrutins nomment l'auteur : « l'amendement n° 117 de Mme
    Balage El Mariky à l'article premier… ». Sans ce filtre, un député qui
    dépose trente amendements dans la semaine devient un « sujet qui monte » —
    ce qui dit quelque chose de son activité, mais rien du sujet débattu.
    """
    try:
        deputes = load("deputes")
    except FileNotFoundError:
        return frozenset()
    mots: set[str] = set()
    for colonne in ("nom", "prenom"):
        for valeur in deputes[colonne].drop_nulls().to_list():
            mots.update(_WORD.findall(normaliser(valeur)))
    return frozenset(mots)

_WORD = re.compile(r"[a-zà-öø-ÿ]{3,}", re.IGNORECASE)


def normaliser(texte: str) -> str:
    """Minuscules sans accents : « Écologie » et « ecologie » comptent ensemble."""
    nfkd = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def tokeniser(texte: str | None, *, bigrammes: bool = True,
              exclus: frozenset[str] = frozenset()) -> list[str]:
    """Découpe un texte en termes utiles : mots seuls et paires de mots.

    Args:
        exclus: mots à écarter en plus des mots vides — typiquement les noms de
            députés, cf. `noms_de_deputes()`.
    """
    if not texte:
        return []
    mots = [
        m for m in _WORD.findall(normaliser(texte))
        if m not in STOPWORDS and m not in exclus
    ]
    if not bigrammes:
        return mots
    paires = [f"{a} {b}" for a, b in zip(mots, mots[1:])]
    return mots + paires


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


def corpus(
    source: str = "tout", *, depuis: str | None = None
) -> pl.DataFrame:
    """Corpus daté : une ligne = un document (`date`, `texte`, `source`).

    Args:
        source: `scrutins`, `amendements` ou `tout`.
        depuis: ne garder que les documents à partir de cette date.
    """
    morceaux = []

    if source in ("scrutins", "tout"):
        s = load("scrutins").filter(pl.col("date_d").is_not_null())
        morceaux.append(
            s.select(
                pl.col("date_d").alias("date"),
                pl.col("titre").alias("texte"),
                pl.lit("scrutins").alias("source"),
            )
        )

    if source in ("amendements", "tout"):
        try:
            a = load("amendements").filter(pl.col("date_depot_d").is_not_null())
        except FileNotFoundError:
            a = None
        if a is not None:
            morceaux.append(
                a.select(
                    pl.col("date_depot_d").alias("date"),
                    pl.concat_str(
                        [
                            pl.col("division_titre").fill_null(""),
                            pl.col("expose_sommaire").fill_null("").str.slice(0, 600),
                        ],
                        separator=" ",
                    ).alias("texte"),
                    pl.lit("amendements").alias("source"),
                )
            )

    if not morceaux:
        raise ValueError(f"source inconnue : {source}")

    df = pl.concat(morceaux, how="vertical").filter(pl.col("texte").is_not_null())
    if depuis:
        df = df.filter(pl.col("date") >= pl.lit(depuis).str.to_date())
    return df


def frequences_hebdo(
    source: str = "tout", *, depuis: str | None = None, bigrammes: bool = True,
    filtrer_noms: bool = True,
) -> pl.DataFrame:
    """Compte chaque terme par semaine ISO. Colonnes : `semaine`, `terme`, `n`.

    Ajoute `n_documents`, le nombre de documents de la semaine, pour pouvoir
    raisonner en fréquence relative et non en volume brut.
    """
    exclus = noms_de_deputes() if filtrer_noms else frozenset()
    docs = corpus(source, depuis=depuis).with_columns(
        pl.col("date").dt.truncate("1w").alias("semaine")
    )
    par_semaine = docs.group_by("semaine").agg(pl.len().alias("n_documents"))

    termes = (
        docs.with_columns(
            pl.col("texte")
            .map_elements(lambda t: tokeniser(t, bigrammes=bigrammes, exclus=exclus),
                          return_dtype=pl.List(pl.Utf8))
            .alias("termes")
        )
        .explode("termes")
        .rename({"termes": "terme"})
        .filter(pl.col("terme").is_not_null())
        .group_by("semaine", "terme")
        .agg(pl.len().alias("n"))
    )
    return termes.join(par_semaine, on="semaine", how="left").sort("semaine", "n")


# --------------------------------------------------------------------------
# Détection des poussées
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Poussee:
    terme: str
    semaine: str
    n: int
    moyenne_precedente: float
    score: float

    def __str__(self) -> str:
        return (
            f"{self.terme:35s} {self.n:5d} occurrences "
            f"(habituel : {self.moyenne_precedente:.1f}) — score {self.score:.1f}"
        )


def sujets_qui_montent(
    freqs: pl.DataFrame | None = None,
    *,
    semaine: str | None = None,
    semaines_reference: int = 8,
    min_occurrences: int = 8,
    k: int = 25,
    source: str = "tout",
    seulement_hausses: bool = True,
    fusionner_ngrammes: bool = True,
) -> pl.DataFrame:
    """Les termes dont l'usage explose par rapport aux semaines précédentes.

    Le score est de type Poisson : `(observé − attendu) / √(attendu + 1)`.
    Il monte quand un terme dépasse sa moyenne récente, sans faire remonter un
    terme rare qui passerait de 1 à 3 occurrences.

    Args:
        freqs: sortie de `frequences_hebdo()`, recalculée si absente.
        semaine: semaine analysée (`AAAA-MM-JJ`, un lundi). Par défaut la
            dernière semaine présente dans les données.
        semaines_reference: nombre de semaines servant de référence.
        min_occurrences: plancher d'occurrences dans la semaine analysée.
        seulement_hausses: écarter les termes en recul. Les garder n'a de sens
            que pour une analyse symétrique « ce dont on ne parle plus ».
        fusionner_ngrammes: masquer les mots seuls déjà couverts par une paire
            mieux classée, pour ne pas occuper trois lignes avec « mixte »,
            « paritaire » et « mixte paritaire ».
    """
    if freqs is None:
        freqs = frequences_hebdo(source)

    semaines = sorted(freqs["semaine"].unique().to_list())
    if not semaines:
        return pl.DataFrame()
    cible = date.fromisoformat(semaine) if semaine else semaines[-1]
    if cible not in semaines:
        raise ValueError(f"aucune donnée pour la semaine du {cible}")

    i = semaines.index(cible)
    reference = semaines[max(0, i - semaines_reference) : i]
    if not reference:
        raise ValueError("pas assez d'historique pour établir une référence")

    courant = freqs.filter(pl.col("semaine") == cible).select("terme", "n", "n_documents")
    base = (
        freqs.filter(pl.col("semaine").is_in(reference))
        .group_by("terme")
        .agg(pl.col("n").sum().alias("total_reference"))
        .with_columns(
            (pl.col("total_reference") / len(reference)).alias("moyenne_precedente")
        )
        .select("terme", "moyenne_precedente")
    )

    classe = (
        courant.join(base, on="terme", how="left")
        .with_columns(pl.col("moyenne_precedente").fill_null(0.0))
        .filter(pl.col("n") >= min_occurrences)
        .with_columns(
            (
                (pl.col("n") - pl.col("moyenne_precedente"))
                / (pl.col("moyenne_precedente") + 1.0).sqrt()
            ).alias("score")
        )
        .filter(pl.col("score") > 0 if seulement_hausses else pl.lit(True))
        .with_columns(pl.lit(cible).alias("semaine"))
        .sort("score", descending=True)
        .select("semaine", "terme", "n", "moyenne_precedente", "score")
    )
    if fusionner_ngrammes:
        classe = classe.filter(
            pl.Series(
                _masque_redondances(
                    classe["terme"].to_list(),
                    classe["n"].to_list(),
                    classe["moyenne_precedente"].to_list(),
                )
            )
        )
    return classe.head(k)


def _masque_redondances(termes: list[str], effectifs: list[int],
                        baselines: list[float]) -> list[bool]:
    """Ne garde qu'un représentant par groupe de termes parfaitement redondants.

    Un même texte discuté sur vingt-quatre scrutins fait remonter *tous* les
    n-grammes de son titre avec exactement le même effectif : « corse »,
    « corse autonome », « autonome sein », « sein republique »… C'est un seul
    sujet, pas huit. On regroupe donc les termes qui ont le même effectif et la
    même moyenne de référence — signature d'une co-occurrence parfaite — et on
    n'en garde qu'un.

    Le choix du représentant dépend de la taille du groupe :

    - **petit groupe** (une paire et ses deux mots, comme « mixte paritaire »,
      « mixte », « paritaire ») : la paire est l'unité de sens, on la garde ;
    - **grand groupe** : c'est un titre entier qui se répète, et aucun fragment
      n'est l'unité de sens. On garde alors le mot seul le plus court, qui est
      le plus lisible — « corse » plutôt que « constitutionnelle corse ».
    """
    garde = [True] * len(termes)

    # Passe 1 — subsomption : un mot seul dont une paire rend presque toujours
    # compte disparaît au profit de la paire, plus informative. Elle passe en
    # premier, sinon la passe 2 peut écarter la paire dont on a besoin ici.
    paires = [(i, termes[i].split()) for i in range(len(termes)) if " " in termes[i]]
    for j, terme in enumerate(termes):
        if " " in terme:
            continue
        if any(terme in mots and effectifs[i] >= 0.8 * effectifs[j] for i, mots in paires):
            garde[j] = False

    # Passe 2 — classes d'équivalence entre les termes restants.
    groupes: dict[tuple[int, float], list[int]] = {}
    for i, (n, base) in enumerate(zip(effectifs, baselines)):
        if garde[i]:
            groupes.setdefault((n, round(base, 3)), []).append(i)

    for indices in groupes.values():
        for i in indices:
            garde[i] = False
        if len(indices) == 1:
            garde[indices[0]] = True
            continue
        mots_seuls = {termes[i] for i in indices if " " not in termes[i]}
        paires = [
            i for i in indices
            if " " in termes[i] and all(m in mots_seuls for m in termes[i].split())
        ]
        if paires and len(indices) <= 3:
            choix = max(paires, key=lambda i: len(termes[i]))
        else:
            choix = min(indices, key=lambda i: (termes[i].count(" "), len(termes[i])))
        garde[choix] = True
    return garde


def serie_terme(terme: str, *, source: str = "tout",
                freqs: pl.DataFrame | None = None) -> pl.DataFrame:
    """Suivi hebdomadaire d'un terme donné, pour tracer sa courbe."""
    if freqs is None:
        freqs = frequences_hebdo(source)
    cible = normaliser(terme)
    semaines = freqs.select("semaine").unique()
    return (
        semaines.join(
            freqs.filter(pl.col("terme") == cible).select("semaine", "n"),
            on="semaine",
            how="left",
        )
        .with_columns(pl.col("n").fill_null(0))
        .sort("semaine")
    )
