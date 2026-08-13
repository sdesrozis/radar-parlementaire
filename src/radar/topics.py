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

# Trois natures de mots à écarter, séparées parce qu'elles s'écartent pour
# trois raisons différentes — et que confondre les trois rend la liste
# impossible à maintenir.

#: 1. Les mots vides ordinaires du français. Ils ne portent aucun sens propre.
MOTS_VIDES = frozenset(
    """
    a au aux avec ce ces dans de des du elle en et eux il ils je la le les leur
    lui ma mais me meme mes moi mon ne nos notre nous on ou par pas pour qu que
    qui sa se ses son sur ta te tes toi ton tu un une vos votre vous y d l n s c
    j m t est sont etre ete avoir eu fait faire plus moins tres aussi ainsi donc
    or ni car si sans sous entre vers chez apres avant pendant depuis jusqu
    lorsque quand comme tout tous toute toutes autre autres meme memes tel telle
    cet cette ceux celle celles dont lequel laquelle lesquels auquel
    apres avant premier premiere second seconde suivant suivante suivants
    ii iii iv vi vii viii ix xi xii bis ter quater
    ont sont avaient etaient soit puisse doivent doit peut peuvent
    mme mmes mlle collegues annee annees leurs nos numero
    euros millions milliards montant montants
    etat etats francais france national nationale nationaux
    """.split()
)

#: 2. La langue de la rédaction législative. « Substituer à l'alinéa 3 les mots
#: … » est la syntaxe obligée de tout amendement : ces mots sont présents
#: partout, quel que soit le sujet, et ne discriminent donc rien.
JARGON_LEGISTIQUE = frozenset(
    """
    amendement amendements article articles alinea alineas rediger redige
    substituer inserer supprimer completer remplacer mots mot phrase phrases
    present presente code loi lois texte textes projet proposition
    visant visan relatif relative relatifs portant
    instituant modifiant creant tendant permettant favorisant renforcant
    sein egard matiere oeuvre lieu titre unique nouvelle nouveau
    disposition dispositions mesure mesures cas chapitre section
    paragraphe conditions condition application applicable prevu prevue
    vise visee mentionne mentionnee defini definie fixe fixee
    identique identiques supprime suppression rectifie rectifiee rect
    """.split()
)

#: 3. Les termes de **procédure**. Ceux-là sont trompeurs d'une autre façon :
#: ils désignent de vraies choses, mais des étapes du parcours d'un texte, pas
#: des sujets de politique publique. Une commission mixte paritaire qui se
#: réunit fait bondir « mixte paritaire » sans que rien ne se soit passé dans
#: le débat public. La question à laquelle ce module doit répondre est « de
#: quoi débat-on ? », pas « quelle formule administrative revient le plus ? ».
#:
#: Volontairement étroite : on n'y met que ce qui est procédural **sans
#: ambiguïté**. « Public » n'y figure pas, parce que « service public » et
#: « fonction publique » sont des sujets ; « vote » non plus, parce que « droit
#: de vote » en est un.
TERMES_PROCEDURAUX = frozenset(
    """
    assemblee nationale gouvernement ministre commission seance
    lecture lectures ensemble definitive
    mixte paritaire rapporteur rapporteurs saisine navette
    """.split()
) | {"mixte paritaire", "commission mixte", "nouvelle lecture",
     "lecture definitive", "premiere lecture", "seconde lecture"}

#: Union des trois : ce que `tokeniser` écarte effectivement.
STOPWORDS = MOTS_VIDES | JARGON_LEGISTIQUE | TERMES_PROCEDURAUX


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

    Les paires sont filtrées elles aussi, ce qui permet d'écarter une
    expression sans écarter ses mots — utile quand seule la locution est
    parasite.

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
    paires = [
        p for a, b in zip(mots, mots[1:])
        if (p := f"{a} {b}") not in STOPWORDS
    ]
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
    """Compte chaque terme par semaine ISO.

    Colonnes : `semaine`, `terme`, `n_docs` (documents de la semaine contenant
    le terme **au moins une fois**), `n` (occurrences brutes) et `n_documents`
    (documents de la semaine, tous termes confondus).

    **Deux comptages, et un seul sert de taux.** `n` est le nombre
    d'occurrences : il compte dix fois un terme martelé dix fois dans un même
    exposé sommaire. Rapporté au nombre de *documents*, il ne forme pas un taux,
    parce que numérateur et dénominateur ne comptent pas la même chose — et les
    documents sont hétérogènes, un titre de scrutin d'une ligne pesant autant
    qu'un exposé tronqué à 600 caractères. `n_docs` est une **part de
    documents** : borné par `n_documents`, comparable d'une semaine à l'autre,
    insensible à la longueur des textes. C'est lui que `sujets_qui_montent`
    divise. `n` reste servi parce qu'il est parlant à l'affichage, jamais comme
    dénominateur.
    """
    exclus = noms_de_deputes() if filtrer_noms else frozenset()
    docs = corpus(source, depuis=depuis).with_columns(
        pl.col("date").dt.truncate("1w").alias("semaine")
    ).with_row_index("doc")
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
        .agg(
            pl.len().alias("n"),
            pl.col("doc").n_unique().alias("n_docs"),
        )
    )
    return termes.join(par_semaine, on="semaine", how="left").sort("semaine", "n_docs")


# --------------------------------------------------------------------------
# Détection des poussées
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Poussee:
    """Une ligne de `sujets_qui_montent`, sous une forme affichable.

    `n_docs` et non `n` : le comptage qui porte le taux est un nombre de
    documents mentionnant le terme, pas un nombre d'occurrences.
    """

    terme: str
    semaine: str
    n_docs: int
    n_documents: int
    attendu: float
    score: float
    q_valeur: float

    def __str__(self) -> str:
        return (
            f"{self.terme:35s} {self.n_docs:5d} documents sur {self.n_documents} "
            f"(attendu : {self.attendu:.1f}) — score {self.score:.1f}, "
            f"q = {self.q_valeur:.3f}"
        )


def sujets_qui_montent(
    freqs: pl.DataFrame | None = None,
    *,
    semaine: str | None = None,
    semaines_reference: int = 8,
    min_documents: int = 5,
    k: int = 25,
    source: str = "tout",
    seulement_hausses: bool = True,
    fusionner_ngrammes: bool = True,
    fdr: float | None = 0.05,
) -> pl.DataFrame:
    """Les termes dont l'usage explose par rapport aux semaines précédentes.

    **Le score compare des taux, pas des volumes.** C'est la précaution
    essentielle, et elle n'est pas cosmétique : le corpus hebdomadaire ne fait
    pas du tout la même taille d'une semaine à l'autre. Un seul texte peut
    produire des centaines d'amendements, et une semaine sans séance en produit
    zéro. Comparer les comptages bruts d'une semaine chargée à ceux d'une
    semaine creuse revient donc à mesurer le calendrier parlementaire plutôt
    que le débat.

    **Le taux est une part de documents, pas un nombre d'occurrences.** C'est la
    correction la plus importante de cette fonction, et elle change les
    classements. Diviser des occurrences par un nombre de documents ne produit
    pas un taux : un terme martelé dix fois dans un long exposé sommaire pèse
    dix fois plus qu'un terme cité une fois dans un titre de scrutin, alors que
    les deux documents comptent pour un au dénominateur. On mesure donc, pour
    chaque terme, **la part des documents de la semaine qui le mentionnent au
    moins une fois** — grandeur bornée par le nombre de documents, insensible à
    leur longueur, et directement comparable d'une semaine à l'autre.

    On procède en trois temps :

    1. **le taux de référence** `p` — part des documents des semaines
       précédentes qui mentionnent le terme ;
    2. **l'attendu** — `p` multiplié par le nombre de documents de la semaine
       analysée. C'est ce qu'on devrait observer si rien n'avait changé, *à
       volume de la semaine* ;
    3. **le test** — sous l'hypothèse « rien n'a changé », le nombre de
       documents mentionnant le terme suit une binomiale `B(N, p)`. La
       `p_valeur` est la probabilité d'observer au moins autant de mentions par
       le seul hasard.

    **Le contrôle des tests multiples n'est pas une coquetterie.** On teste
    plusieurs milliers de termes chaque semaine : au seuil usuel de 5 %,
    quelques centaines ressortiraient « significatifs » alors même que rien ne
    bouge. La procédure de Benjamini-Hochberg convertit les `p_valeur` en
    `q_valeur`, qui se lit comme la part de fausses découvertes acceptée parmi
    les termes retenus. Avec `fdr=0.05`, au plus un terme signalé sur vingt est
    un accident.

    Le `score` reste `(observé − attendu) / √(attendu + 1)` et sert au
    classement : il ordonne par ampleur là où la `q_valeur` ne fait que trier
    l'admissible de l'inadmissible.

    Args:
        freqs: sortie de `frequences_hebdo()`, recalculée si absente.
        semaine: semaine analysée (`AAAA-MM-JJ`, un lundi). Par défaut la
            dernière semaine présente dans les données.
        semaines_reference: nombre de semaines servant de référence.
        min_documents: plancher de documents mentionnant le terme dans la
            semaine analysée. Sous ce seuil, il n'y a rien à tester.
        seulement_hausses: écarter les termes en recul. Les garder n'a de sens
            que pour une analyse symétrique « ce dont on ne parle plus ».
        fusionner_ngrammes: masquer les mots seuls déjà couverts par une paire
            mieux classée, pour ne pas occuper trois lignes avec « mixte »,
            « paritaire » et « mixte paritaire ».
        fdr: seuil de fausses découvertes. `None` renvoie tous les termes avec
            leur `q_valeur`, sans filtrer — utile pour inspecter la frontière.
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

    # Volume documentaire : constant sur toutes les lignes d'une même semaine.
    documents = (
        freqs.group_by("semaine")
        .agg(pl.col("n_documents").first())
        .to_dict(as_series=False)
    )
    par_semaine = dict(zip(documents["semaine"], documents["n_documents"]))
    docs_reference = sum(par_semaine.get(s, 0) for s in reference)
    docs_courant = par_semaine.get(cible, 0)
    if not docs_reference or not docs_courant:
        raise ValueError("volume documentaire nul : impossible de normaliser")

    courant = freqs.filter(pl.col("semaine") == cible).select("terme", "n_docs", "n")
    base = (
        freqs.filter(pl.col("semaine").is_in(reference))
        .group_by("terme")
        .agg(pl.col("n_docs").sum().alias("docs_reference"))
        # Part des documents de référence mentionnant le terme, ramenée au
        # volume de la semaine analysée : voilà ce qu'on devrait voir si rien
        # n'avait bougé. La part est bornée à 1 — la somme des `n_docs` d'un
        # terme présent dans tous les documents vaut exactement `docs_reference`.
        .with_columns(
            (pl.col("docs_reference") / docs_reference).clip(0.0, 1.0).alias("part_reference")
        )
        .select("terme", "part_reference")
    )

    classe = (
        courant.join(base, on="terme", how="left")
        .with_columns(pl.col("part_reference").fill_null(0.0))
        .with_columns(
            (pl.col("part_reference") * docs_courant).alias("attendu"),
            (pl.col("n_docs") / docs_courant).alias("part"),
        )
        .filter(pl.col("n_docs") >= min_documents)
        .with_columns(
            ((pl.col("n_docs") - pl.col("attendu")) / (pl.col("attendu") + 1.0).sqrt())
            .alias("score")
        )
        .filter(pl.col("score") > 0 if seulement_hausses else pl.lit(True))
    )

    # Deux affectations et non une chaîne : `classe["p_valeur"]` doit être lu
    # sur la table *après* ajout de la colonne, or Python évalue l'expression
    # entière avant de réaffecter le nom.
    p = _p_binomiale(
        classe["n_docs"].to_list(), docs_courant, classe["part_reference"].to_list()
    )
    classe = classe.with_columns(pl.Series("p_valeur", p, dtype=pl.Float64))
    classe = classe.with_columns(
        pl.Series("q_valeur", _benjamini_hochberg(p), dtype=pl.Float64)
    )

    if fdr is not None:
        classe = classe.filter(pl.col("q_valeur") <= fdr)

    classe = (
        classe.with_columns(
            pl.lit(cible).alias("semaine"),
            pl.lit(docs_courant).alias("n_documents"),
        )
        .sort("score", descending=True)
        .select("semaine", "terme", "n_docs", "n", "attendu", "part",
                "score", "p_valeur", "q_valeur", "n_documents")
    )
    if fusionner_ngrammes and classe.height:
        classe = classe.filter(
            pl.Series(
                _masque_redondances(
                    classe["terme"].to_list(),
                    classe["n_docs"].to_list(),
                    classe["attendu"].to_list(),
                ),
                dtype=pl.Boolean,
            )
        )
    return classe.head(k)


def _p_binomiale(observes: list[int], n: int, parts: list[float]) -> list[float]:
    """`P(X ≥ observé)` sous `B(n, part)`, terme par terme.

    Un terme jamais vu dans la période de référence a une part nulle : la
    binomiale lui donnerait une probabilité nulle, donc une significativité
    infinie sur une seule mention. On lui substitue la plus petite part non
    nulle observable, `1 / n_documents_de_référence` approché ici par `1 / n` —
    c'est la règle de continuité usuelle, et elle évite qu'un mot inédit et
    isolé écrase le classement.
    """
    from scipy.stats import binom

    plancher = 1.0 / max(n, 1)
    return [
        float(binom.sf(int(k) - 1, n, min(max(p, plancher), 1.0)))
        for k, p in zip(observes, parts)
    ]


def _benjamini_hochberg(p: list[float]) -> list[float]:
    """`q`-valeurs de Benjamini-Hochberg, dans l'ordre d'entrée.

    Plusieurs milliers de termes sont testés chaque semaine. Sans correction, le
    seuil de 5 % laisse passer 5 % de bruit — soit, à cette échelle, plus de
    « sujets qui montent » faux que de vrais. La `q`-valeur se lit comme la
    proportion de fausses découvertes tolérée parmi les termes retenus.
    """
    m = len(p)
    if not m:
        return []
    ordre = sorted(range(m), key=lambda i: p[i])
    q = [0.0] * m
    courant = 1.0
    # Remontée depuis la plus grande `p`-valeur : la `q`-valeur est monotone,
    # chaque rang hérite du minimum de ce qui le suit.
    for rang in range(m - 1, -1, -1):
        i = ordre[rang]
        courant = min(courant, p[i] * m / (rang + 1))
        q[i] = min(courant, 1.0)
    return q


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
    """Suivi hebdomadaire d'un terme donné, pour tracer sa courbe.

    La colonne à tracer est `part` — la fraction des documents de la semaine qui
    mentionnent le terme. Tracer `n` donnerait la courbe du calendrier
    parlementaire bien plus que celle du terme.
    """
    if freqs is None:
        freqs = frequences_hebdo(source)
    cible = normaliser(terme)
    semaines = freqs.group_by("semaine").agg(pl.col("n_documents").first())
    return (
        semaines.join(
            freqs.filter(pl.col("terme") == cible).select("semaine", "n", "n_docs"),
            on="semaine",
            how="left",
        )
        .with_columns(pl.col("n").fill_null(0), pl.col("n_docs").fill_null(0))
        .with_columns((pl.col("n_docs") / pl.col("n_documents")).alias("part"))
        .sort("semaine")
    )
