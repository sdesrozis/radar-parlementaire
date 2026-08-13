"""L'abstention : la position que les analyses de vote laissent de côté.

Toutes les analyses du radar qui portent sur « l'accord » traitent l'abstention
comme une position parmi trois, et le modèle de points idéaux (`ideal`) l'exclut
purement et simplement. Ces deux traitements reposent sur une croyance jamais
vérifiée : que l'abstention serait un vote flou, sans contenu, ou au contraire
une position intermédiaire entre le pour et le contre.

Ce module la prend pour objet, et établit trois choses sur la 17ᵉ législature :

1. **L'abstention est une décision collective, pas un flottement individuel.**
   Sur l'ensemble des abstentions — y compris celles que la méthode ne sait pas
   classer — près de trois sur quatre surviennent quand l'abstention est la
   position majoritaire du groupe. Ce n'est pas un député qui hésite, c'est un
   groupe qui choisit de ne pas choisir. Une abstention sur dix reste
   indéterminée, parce que le groupe n'avait ce jour-là aucune ligne : elle est
   comptée comme telle, et non répartie entre les deux autres cas.

2. **Elle est, le plus souvent, bel et bien intermédiaire.** Replacés sur l'axe
   estimé sans elles, les abstentionnistes se situent entre les deux camps dans
   environ sept scrutins sur dix, et à mi-chemin en médiane. Le doute exprimé
   dans le notebook `04` était donc trop catégorique — mais pas infondé, puisque
   dans les trois cas restants ils se collent à un camp, voire le dépassent.

3. **Elle décide, parfois.** Une dizaine de votes sur l'ensemble d'un texte se
   sont joués sur un écart inférieur au nombre d'abstentions : s'abstenir y
   revenait à choisir, sans avoir à l'assumer.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from .analyze import SEUIL_LIGNE, VoteCube
from .parse import load


# --------------------------------------------------------------------------
# Combien, et par qui
# --------------------------------------------------------------------------


def taux(par: str = "groupe", *, portee: str | None = None) -> pl.DataFrame:
    """Taux d'abstention, rapporté aux seuls suffrages exprimés.

    Le dénominateur exclut les absents et les non-votants : on mesure la part
    des députés qui, **s'étant déplacés pour voter**, ont choisi de ne se
    prononcer ni pour ni contre. Rapporter l'abstention à l'effectif total la
    confondrait avec l'absentéisme, qui est un tout autre phénomène.

    Args:
        par: `groupe`, `portee`, `categorie` ou `depute`.
        portee: restreindre à une portée de scrutin.
    """
    votes = load("votes")
    scrutins = load("scrutins").select("scrutin_uid", "portee", "categorie", "date_d")
    deputes = load("deputes").filter(pl.col("en_exercice")).select(
        "acteur_uid", "nom_complet", "groupe"
    )

    d = (
        votes.filter(pl.col("position").is_in(["pour", "contre", "abstention"]))
        .join(scrutins, on="scrutin_uid", how="inner")
        .join(deputes, on="acteur_uid", how="inner")
    )
    if portee:
        d = d.filter(pl.col("portee") == portee)

    cles = {"depute": ["nom_complet", "groupe"]}.get(par, [par])
    return (
        d.group_by(cles)
        .agg(
            (pl.col("position") == "abstention").sum().alias("abstentions"),
            pl.len().alias("suffrages_exprimes"),
        )
        .with_columns(
            (pl.col("abstentions") / pl.col("suffrages_exprimes")).alias("taux")
        )
        .sort("taux", descending=True)
    )


def decomposition(
    portee: str | None = None,
    *,
    min_votants_groupe: int = 5,
    seuil_ligne: float = SEUIL_LIGNE,
) -> pl.DataFrame:
    """L'abstention suit-elle la ligne du groupe, ou s'en écarte-t-elle ?

    On croise chaque abstention avec la position majoritaire de son groupe sur
    ce scrutin, recalculée depuis le dépouillement nominatif. **Trois** cas, et
    le troisième n'est pas un résidu technique :

    - `consigne` — le groupe s'abstient, le député aussi. C'est une décision
      collective : le groupe refuse de trancher ;
    - `retrait` — le groupe vote pour ou contre, le député s'abstient. Là, c'est
      un écart individuel, une façon discrète de ne pas suivre ;
    - `indeterminee` — **le groupe n'avait pas de ligne** : aucune position n'y
      réunissait la majorité absolue des suffrages, ou trop peu de ses membres
      avaient voté. Sans ligne, il n'y a ni consigne à suivre ni consigne à
      quitter, et forcer l'abstention dans l'une des deux cases inventerait une
      information.

    **Le dénominateur est le total des abstentions, pas le sous-ensemble
    classifiable.** Cette fonction s'appuyait auparavant sur `votes_vs_ligne`,
    qui écarte silencieusement les scrutins sans ligne : les parts publiées
    portaient sur 89,9 % des abstentions sans que rien ne le dise, et
    « quatre abstentions sur cinq sont une consigne » était en réalité « quatre
    sur cinq **parmi celles qu'on sait classer** ». Les trois parts somment
    désormais à 1, et la troisième mesure ce que la méthode ne sait pas trancher.
    """
    scrutins = load("scrutins").select("scrutin_uid", "portee")
    lignes = load("positions_groupe")

    a = (
        load("votes")
        .filter(pl.col("position") == "abstention")
        .join(scrutins, on="scrutin_uid", how="inner")
        # `left` et non `inner` : une abstention dont le groupe n'a pas de ligne
        # doit rester dans le total. C'est tout l'objet de la troisième catégorie.
        .join(lignes, on=["scrutin_uid", "groupe_uid"], how="left")
    )
    if portee:
        a = a.filter(pl.col("portee") == portee)
    if not a.height:
        return pl.DataFrame(
            schema={"nature": pl.Utf8, "abstentions": pl.UInt32, "part": pl.Float64}
        )

    sans_ligne = (
        pl.col("majoritaire").is_null()
        | (pl.col("votants_groupe") < min_votants_groupe)
        | (pl.col("part_majoritaire") <= seuil_ligne)
    )
    return (
        a.with_columns(
            pl.when(sans_ligne)
            .then(pl.lit("indeterminee"))
            .when(pl.col("majoritaire") == "abstention")
            .then(pl.lit("consigne"))
            .otherwise(pl.lit("retrait"))
            .alias("nature")
        )
        .group_by("nature")
        .agg(pl.len().alias("abstentions"))
        .with_columns((pl.col("abstentions") / a.height).alias("part"))
        .sort("abstentions", descending=True)
    )


def cohesion_par_ligne() -> pl.DataFrame:
    """Une consigne d'abstention est-elle aussi suivie qu'une consigne de vote ?

    On compare la part du groupe qui suit sa propre position majoritaire, selon
    que cette position est « pour », « contre » ou « abstention ». Une consigne
    d'abstention moins suivie signalerait un ordre plus mou — un compromis
    interne plutôt qu'une position assumée.
    """
    return (
        load("positions_groupe")
        .filter((pl.col("votants_groupe") >= 10) & pl.col("majoritaire").is_not_null())
        .group_by("majoritaire")
        .agg(
            pl.col("part_majoritaire").mean().alias("suivi_moyen"),
            pl.len().alias("occurrences"),
        )
        .sort("suivi_moyen", descending=True)
    )


# --------------------------------------------------------------------------
# L'abstention est-elle une position intermédiaire ?
# --------------------------------------------------------------------------


def situer_abstentionnistes(
    cube: VoteCube, modele, *, min_par_camp: int = 10
) -> pl.DataFrame:
    """Replace les abstentionnistes sur l'axe estimé **sans eux**.

    **L'hypothèse à trancher.** Le modèle de points idéaux exclut les
    abstentions, au motif qu'une abstention ne serait pas un demi-vote. Si
    c'était faux — si les abstentionnistes étaient réellement les députés situés
    entre les deux camps — alors les exclure jetterait une information utile.

    **La méthode.** L'axe est estimé sur les seuls « pour » et « contre » : les
    abstentionnistes n'ont donc pas participé à sa construction, et les y
    replacer constitue un test hors modèle. Pour chaque scrutin, on compare la
    position médiane des trois populations.

    `position_relative` vaut 0 quand les abstentionnistes se confondent avec le
    camp le plus proche, 0,5 quand ils sont à mi-chemin, 1 quand ils rejoignent
    le camp opposé. Au-delà de [0, 1], ils sont hors de l'intervalle des deux
    camps — et l'abstention n'est alors sûrement pas un compromis.
    """
    index = {u: i for i, u in enumerate(modele.deputes["acteur_uid"].to_list())}
    place = np.array([index.get(u, -1) for u in cube.deputes["acteur_uid"].to_list()])
    connu = place >= 0
    x = np.full(cube.n_deputes, np.nan)
    x[connu] = modele.positions[place[connu], 0]

    lignes = []
    for j in range(cube.n_scrutins):
        ab = cube.abstention[:, j] & connu
        po = cube.pour[:, j] & connu
        co = cube.contre[:, j] & connu
        if min(ab.sum(), po.sum(), co.sum()) < min_par_camp:
            continue
        m_pour, m_contre, m_abst = (np.median(x[k]) for k in (po, co, ab))
        bas, haut = sorted((m_pour, m_contre))
        lignes.append(
            {
                "scrutin_uid": cube.scrutins["scrutin_uid"][j],
                "date": cube.scrutins["date"][j],
                "titre": cube.scrutins["titre"][j],
                "n_abstentions": int(ab.sum()),
                "mediane_pour": float(m_pour),
                "mediane_contre": float(m_contre),
                "mediane_abstention": float(m_abst),
                "entre_les_camps": bool(bas < m_abst < haut),
                "position_relative": float((m_abst - bas) / (haut - bas))
                if haut > bas
                else float("nan"),
            }
        )
    return pl.DataFrame(lignes)


def resume_intermediaire(test: pl.DataFrame) -> dict:
    """Résume le test précédent en trois chiffres lisibles."""
    return {
        "scrutins_testes": test.height,
        "part_entre_les_camps": float(test["entre_les_camps"].mean()),
        "position_relative_mediane": float(test["position_relative"].median()),
    }


# --------------------------------------------------------------------------
# Quand l'abstention décide
# --------------------------------------------------------------------------


def scrutins_bascule(*, portee: str | None = None, k: int | None = None) -> pl.DataFrame:
    """Scrutins où les abstentionnistes détenaient réellement l'issue du vote.

    **Le seuil n'est pas le même selon le résultat, et c'est tout l'objet de
    cette fonction.** Un texte est adopté quand les « pour » l'emportent
    strictement sur les « contre » ; les abstentions ne comptent pas dans les
    suffrages exprimés, et l'égalité vaut rejet. Le critère naïf
    « abstentions ≥ écart » traite donc les deux cas de la même façon alors
    qu'ils ne le sont pas :

    - **scrutin adopté**, écart `e = pour − contre` : si les `a` abstentionnistes
      avaient voté contre, le camp du contre atteindrait `contre + a`. Le texte
      tombe dès que `contre + a ≥ pour`, donc dès que **`a ≥ e`**, puisque
      l'égalité suffit à faire échouer ;
    - **scrutin rejeté**, écart `e = contre − pour` : le camp du pour doit
      *dépasser* le contre, pas l'égaler. Il faut donc **`a > e`**. Avec `a = e`
      le vote reste rejeté, et le compter comme « bascule » serait faux.

    Appliquer `a ≥ e` partout faisait entrer 69 scrutins où l'égalité était
    atteinte sans que rien ne bascule. Le critère ci-dessous est celui du
    règlement, appliqué au résultat effectivement obtenu.

    Le cas des motions de censure ne se pose pas : elles exigent 289 voix sur
    l'effectif de l'Assemblée, et la source n'y publie ni « contre » ni
    abstention — aucune n'a d'abstention, donc aucune n'entre ici.

    Ce n'est pas une affirmation sur les intentions : c'est une arithmétique. On
    ne prétend pas que ces députés *voulaient* le résultat obtenu, seulement
    qu'ils avaient les moyens de l'empêcher.

    La colonne `bascule_vers` dit dans quel sens le résultat aurait tourné, et
    `abstentions_requises` combien d'abstentionnistes il aurait fallu déplacer.
    """
    s = load("scrutins").filter(
        (pl.col("n_abstention") > 0) & (pl.col("nb_votants") > 20)
    )
    if portee:
        s = s.filter(pl.col("portee") == portee)

    adopte = pl.col("n_pour") > pl.col("n_contre")
    ecart = (pl.col("n_pour") - pl.col("n_contre")).abs()

    d = (
        s.with_columns(
            ecart.alias("ecart"),
            adopte.alias("adopte"),
            # Le nombre d'abstentionnistes qu'il aurait fallu faire changer de
            # camp : `e` pour renverser une adoption, `e + 1` pour emporter un
            # rejet, parce que l'égalité ne suffit pas à faire adopter.
            pl.when(adopte).then(ecart).otherwise(ecart + 1).alias("abstentions_requises"),
        )
        .filter(pl.col("n_abstention") >= pl.col("abstentions_requises"))
        .with_columns(
            pl.when(pl.col("adopte"))
            .then(pl.lit("rejet"))
            .otherwise(pl.lit("adoption"))
            .alias("bascule_vers"),
            (pl.col("n_abstention") / pl.col("abstentions_requises"))
            .alias("marge_de_bascule"),
        )
        .sort("ecart")
        .select(
            "scrutin_uid", "date", "portee", "categorie", "n_pour", "n_contre",
            "n_abstention", "ecart", "abstentions_requises", "bascule_vers",
            "marge_de_bascule", "sort_code", "titre",
        )
    )
    return d.head(k) if k else d


def qui_s_abstenait(scrutin_uid: str) -> pl.DataFrame:
    """Répartition par groupe des positions sur un scrutin donné.

    Complément indispensable de `scrutins_bascule` : savoir qu'une abstention
    était décisive n'a d'intérêt que si l'on sait de qui elle venait.
    """
    groupes = load("organes").select(
        pl.col("organe_uid").alias("groupe_uid"), pl.col("libelle_abrev").alias("groupe")
    )
    return (
        load("votes")
        .filter(pl.col("scrutin_uid") == scrutin_uid)
        .join(groupes, on="groupe_uid", how="left")
        .group_by("groupe")
        .agg(
            (pl.col("position") == "pour").sum().alias("pour"),
            (pl.col("position") == "contre").sum().alias("contre"),
            (pl.col("position") == "abstention").sum().alias("abstention"),
        )
        .with_columns(
            (pl.col("pour") + pl.col("contre") + pl.col("abstention")).alias("total")
        )
        .sort("abstention", descending=True)
    )
