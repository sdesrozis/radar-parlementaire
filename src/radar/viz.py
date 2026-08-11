"""Graphiques du radar (matplotlib).

Un mot sur les couleurs, parce que c'est le piège de ce jeu de données.

L'Assemblée compte douze groupes politiques. La tentation est d'attribuer à
chacun sa couleur conventionnelle — et c'est une mauvaise idée mesurable : le
rouge de LFI (`#cc2443`) et celui de GDR (`#dd0000`) sont séparés par un ΔE de
5,4, en dessous du plancher de 15. Deux électeurs sur deux, daltoniens ou non,
les confondent. Idem pour les trois bleus de droite.

Donc **la couleur ne porte jamais l'identité d'un groupe ici**. Elle porte des
grandeurs (rampe séquentielle bleue, clair → foncé) ou une emphase (un accent,
le reste en gris). L'identité passe par le texte : étiquettes directes, axes
nommés, petits multiples titrés. Palette d'accents validée pour la vision
normale et les trois principaux daltonismes, en clair comme en sombre.
"""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from matplotlib.colors import LinearSegmentedColormap

from .analyze import VoteCube


@dataclass(frozen=True)
class Theme:
    surface: str
    texte: str
    texte_secondaire: str
    grille: str
    accent: str
    accent_2: str
    accent_3: str
    muet: str
    rampe: tuple[str, ...]

    @property
    def cmap(self) -> LinearSegmentedColormap:
        return LinearSegmentedColormap.from_list("radar", list(self.rampe))


CLAIR = Theme(
    surface="#fcfcfb",
    texte="#0b0b0b",
    texte_secondaire="#52514e",
    grille="#e6e5e0",
    accent="#2a78d6",
    accent_2="#eb6834",
    accent_3="#1baf7a",
    muet="#bfbeb8",
    rampe=("#eef4fc", "#c8dcf3", "#93bfe9", "#5c9edd", "#2a78d6", "#1b57a1", "#123a6c"),
)

SOMBRE = Theme(
    surface="#1a1a19",
    texte="#ffffff",
    texte_secondaire="#c3c2b7",
    grille="#33322f",
    accent="#3987e5",
    accent_2="#d95926",
    accent_3="#199e70",
    muet="#55544f",
    rampe=("#11243c", "#173a63", "#1d5090", "#2668bb", "#3987e5", "#77b0ee", "#b9d6f7"),
)

_theme = CLAIR


def set_theme(mode: str = "clair") -> Theme:
    """Bascule clair/sombre. Le mode sombre a ses propres pas, ce n'est pas une inversion."""
    global _theme
    _theme = CLAIR if mode == "clair" else SOMBRE
    mpl.rcParams.update(
        {
            "figure.facecolor": _theme.surface,
            "axes.facecolor": _theme.surface,
            "savefig.facecolor": _theme.surface,
            "text.color": _theme.texte,
            "axes.labelcolor": _theme.texte_secondaire,
            "xtick.color": _theme.texte_secondaire,
            "ytick.color": _theme.texte_secondaire,
            "axes.edgecolor": _theme.grille,
            "grid.color": _theme.grille,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.titleweight": "semibold",
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 120,
        }
    )
    return _theme


set_theme("clair")


def _habiller(ax, titre: str, sous_titre: str = "") -> None:
    """Titre à gauche, sous-titre en ink secondaire, grille discrète."""
    ax.set_title(titre, pad=18 if sous_titre else 8)
    if sous_titre:
        ax.text(
            0.0, 1.02, sous_titre, transform=ax.transAxes,
            fontsize=9, color=_theme.texte_secondaire, va="bottom",
        )


# --------------------------------------------------------------------------
# Grandeurs : rampe séquentielle
# --------------------------------------------------------------------------


def barres_cohesion(cohesion: pl.DataFrame, ax=None):
    """Cohésion interne des groupes. Magnitude ordonnée → barres + rampe séquentielle."""
    d = cohesion.sort("cohesion")
    ax = ax or plt.subplots(figsize=(8, 5))[1]
    valeurs = d["cohesion"].to_numpy()
    norm = (valeurs - valeurs.min()) / max(np.ptp(valeurs), 1e-9)
    y = np.arange(len(d))

    ax.barh(y, valeurs, color=_theme.cmap(0.25 + 0.6 * norm), height=0.62)
    ax.set_yticks(y, [f"{g}  ({n})" for g, n in zip(d["groupe"], d["effectif"])])
    ax.set_xlim(0, 1.0)
    # Les étiquettes sont en pourcentages : l'axe doit parler la même langue.
    ax.xaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0, decimals=0))
    ax.set_xlabel("accord moyen entre membres du groupe")
    for i, v in enumerate(valeurs):
        # Étiquette directe : la valeur est lisible sans revenir à l'axe.
        ax.text(v + 0.008, i, f"{v:.0%}", va="center", fontsize=9,
                color=_theme.texte_secondaire)
    ax.grid(axis="x", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, "Cohésion des groupes",
              "part des votes où deux membres du même groupe votent pareil · effectif entre parenthèses")
    return ax


def _serier(accords: pl.DataFrame, groupes: list[str]) -> list[str]:
    """Ordonne les groupes par proximité de vote, pour faire apparaître les blocs.

    Un ordre alphabétique éparpille les groupes proches aux quatre coins de la
    grille. On projette donc les groupes sur le premier axe de la matrice
    d'accord et on trie dessus : les alliés se retrouvent côte à côte et la
    structure du paysage politique saute aux yeux.
    """
    m = accords.pivot(values="accord", index="groupe_a", on="groupe_b").sort("groupe_a")
    idx = {g: i for i, g in enumerate(m["groupe_a"].to_list())}
    M = np.array([[m[b][idx[a]] for b in groupes] for a in groupes], dtype=float)
    M = np.nan_to_num(M, nan=float(np.nanmean(M)))
    X = M - M.mean(axis=0, keepdims=True)
    axe1 = np.linalg.svd(X, full_matrices=False)[0][:, 0]
    return [g for _, g in sorted(zip(axe1, groupes))]


def heatmap_groupes(accords: pl.DataFrame, ax=None, ordre: list[str] | None = None):
    """Accord moyen entre chaque paire de groupes.

    C'est la bonne forme pour « qui vote avec qui » à douze groupes : une grille
    de magnitudes sur une seule rampe, où l'identité est portée par les axes.
    Les groupes sont sériés par proximité de vote, pas triés par ordre
    alphabétique — sinon la structure en blocs reste invisible.
    """
    tous = sorted(accords["groupe_a"].unique().to_list())
    groupes = ordre or _serier(accords, tous)
    m = (
        accords.pivot(values="accord", index="groupe_a", on="groupe_b")
        .sort("groupe_a")
    )
    idx = {g: i for i, g in enumerate(m["groupe_a"].to_list())}
    M = np.array([[m[g_b][idx[g_a]] for g_b in groupes] for g_a in groupes], dtype=float)

    ax = ax or plt.subplots(figsize=(7.5, 6.5))[1]
    im = ax.imshow(M, cmap=_theme.cmap, vmin=np.nanmin(M), vmax=1.0)
    ax.set_xticks(range(len(groupes)), groupes, rotation=45, ha="right")
    ax.set_yticks(range(len(groupes)), groupes)
    ax.set_xticks(np.arange(len(groupes) + 1) - 0.5, minor=True)
    ax.set_yticks(np.arange(len(groupes) + 1) - 0.5, minor=True)
    # 2 px de surface entre cellules : les blocs ne se touchent pas.
    ax.grid(which="minor", color=_theme.surface, lw=2)
    ax.tick_params(which="minor", length=0)

    for i in range(len(groupes)):
        for j in range(len(groupes)):
            v = M[i, j]
            if np.isnan(v):
                continue
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=7.5,
                    color=_theme.surface if v > 0.72 else _theme.texte_secondaire)

    cb = plt.colorbar(im, ax=ax, fraction=0.043, pad=0.03)
    cb.outline.set_visible(False)
    cb.set_label("taux d'accord", color=_theme.texte_secondaire)
    _habiller(ax, "Qui vote avec qui",
              "accord moyen entre députés de deux groupes, sur les scrutins où les deux se prononcent")
    return ax


def barres_sujets(montants: pl.DataFrame, ax=None, k: int = 15):
    """Termes en poussée cette semaine. Magnitude → rampe séquentielle."""
    d = montants.head(k).sort("score")
    ax = ax or plt.subplots(figsize=(8, 0.34 * len(d) + 1.8))[1]
    v = d["score"].to_numpy()
    norm = (v - v.min()) / max(np.ptp(v), 1e-9)
    y = np.arange(len(d))

    ax.barh(y, v, color=_theme.cmap(0.3 + 0.6 * norm), height=0.62)
    ax.set_yticks(y, d["terme"].to_list())
    ax.set_xlabel("score de poussée   (observé − habituel) / √habituel")
    for i, (s, n, moy) in enumerate(zip(v, d["n"], d["moyenne_precedente"])):
        ax.text(s + max(v) * 0.015, i, f"{n} vs {moy:.0f}", va="center", fontsize=8.5,
                color=_theme.texte_secondaire)
    ax.grid(axis="x", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    semaine = montants["semaine"][0] if montants.height else ""
    _habiller(ax, "Sujets qui montent",
              f"semaine du {semaine} · occurrences cette semaine vs moyenne des semaines précédentes")
    return ax


def courbe_terme(serie: pl.DataFrame, terme: str, ax=None):
    """Suivi d'un terme dans le temps. Série unique → pas de légende, le titre nomme."""
    ax = ax or plt.subplots(figsize=(9, 3.4))[1]
    x = serie["semaine"].to_list()
    y = serie["n"].to_numpy()
    ax.plot(x, y, lw=2, color=_theme.accent)
    ax.fill_between(x, y, alpha=0.12, color=_theme.accent, lw=0)

    if len(y):
        i = int(np.argmax(y))
        ax.plot([x[i]], [y[i]], "o", ms=8, color=_theme.accent,
                mec=_theme.surface, mew=2)
        ax.annotate(f"  {y[i]} le {x[i]}", (x[i], y[i]), fontsize=9,
                    color=_theme.texte_secondaire, va="center")
    ax.set_ylabel("occurrences")
    ax.grid(axis="y", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, f"« {terme} » semaine par semaine")
    return ax


# --------------------------------------------------------------------------
# Identité : emphase et petits multiples, jamais douze couleurs
# --------------------------------------------------------------------------


def carte_apercu(carte: pl.DataFrame, ax=None, annoter: bool = True):
    """Carte des députés, tous groupes confondus.

    Les points sont neutres : c'est le nom du groupe, posé au barycentre de ses
    députés, qui porte l'identité. Un halo de surface évite que les points
    superposés fusionnent en une tache.
    """
    ax = ax or plt.subplots(figsize=(8, 7))[1]
    ax.scatter(carte["x"], carte["y"], s=26, c=_theme.muet,
               edgecolors=_theme.surface, linewidths=1.2, alpha=0.85)

    if annoter:
        centres = (
            carte.filter(pl.col("groupe").is_not_null())
            .group_by("groupe")
            .agg(pl.col("x").median().alias("x"), pl.col("y").median().alias("y"))
            .sort("y")
        )
        etendue_y = float(carte["y"].max() - carte["y"].min())
        for g, x, y in _decoller(
            centres["groupe"].to_list(),
            centres["x"].to_numpy(),
            centres["y"].to_numpy(),
            ecart_min=etendue_y * 0.045,
        ):
            ax.text(x, y, g, fontsize=11, fontweight="bold",
                    color=_theme.texte, ha="center", va="center",
                    path_effects=_contour())

    ix = carte["inertie_x"][0] if carte.height else 0
    iy = carte["inertie_y"][0] if carte.height else 0
    ax.set_xlabel(f"axe 1 — {ix:.0%} de la variance")
    ax.set_ylabel(f"axe 2 — {iy:.0%}")
    ax.grid(lw=0.6, alpha=0.5)
    ax.set_axisbelow(True)
    _habiller(ax, "Carte des députés",
              "deux députés proches votent de la même façon · les axes n'ont pas de sens politique en soi")
    return ax


def _contour():
    import matplotlib.patheffects as pe

    return [pe.withStroke(linewidth=3.5, foreground=_theme.surface)]


def _decoller(labels: list[str], xs: np.ndarray, ys: np.ndarray, *,
              ecart_min: float, iterations: int = 60):
    """Écarte verticalement des étiquettes trop proches pour qu'aucune n'en masque une autre.

    Les groupes du centre (DEM, HOR, LIOT, NI…) ont des barycentres presque
    confondus : sans ce décalage, deux noms se superposent et deviennent
    illisibles. On ne bouge que l'axe vertical, pour ne pas suggérer un
    déplacement le long de l'axe principal — celui qui, lui, a du sens.
    """
    ys = ys.astype(float).copy()
    ordre = np.argsort(ys)
    for _ in range(iterations):
        bouge = False
        for a, b in zip(ordre, ordre[1:]):
            manque = ecart_min - (ys[b] - ys[a])
            if manque > 0:
                ys[a] -= manque / 2
                ys[b] += manque / 2
                bouge = True
        if not bouge:
            break
    return list(zip(labels, xs, ys))


def carte_facettes(carte: pl.DataFrame, groupes: list[str] | None = None,
                   n_cols: int = 4, figsize: tuple[float, float] | None = None):
    """Un petit multiple par groupe : le groupe en accent, les autres en gris.

    C'est la réponse prescrite quand il y a plus de trois classes dans un nuage
    de points : au lieu d'empiler des teintes indistinguables, on répète la même
    carte et on met un seul groupe en avant à chaque fois.
    """
    groupes = groupes or [
        g for g in carte.group_by("groupe").len().sort("len", descending=True)["groupe"]
        if g is not None
    ]
    n_rows = int(np.ceil(len(groupes) / n_cols))
    fig, axes = plt.subplots(
        n_rows, n_cols, figsize=figsize or (2.7 * n_cols, 2.7 * n_rows),
        sharex=True, sharey=True,
    )
    axes = np.atleast_1d(axes).ravel()

    for ax, groupe in zip(axes, groupes):
        sel = carte["groupe"] == groupe
        ax.scatter(carte.filter(~sel)["x"], carte.filter(~sel)["y"],
                   s=9, c=_theme.muet, alpha=0.5, linewidths=0)
        d = carte.filter(sel)
        ax.scatter(d["x"], d["y"], s=22, c=_theme.accent,
                   edgecolors=_theme.surface, linewidths=0.9)
        ax.set_title(f"{groupe}  ·  {d.height}", fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)

    for ax in axes[len(groupes):]:
        ax.set_visible(False)
    fig.suptitle("Où siège chaque groupe sur la carte des votes",
                 x=0.02, ha="left", fontsize=12, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return fig


def barres_emphase(d: pl.DataFrame, valeur: str, etiquette: str, *,
                   k: int = 15, titre: str = "", sous_titre: str = "",
                   format_valeur: str = "{:.0%}", ax=None):
    """Classement où seul le haut du tableau est le sujet : accent + reste en gris."""
    d = d.head(k).sort(valeur)
    ax = ax or plt.subplots(figsize=(8, 0.34 * len(d) + 1.8))[1]
    v = d[valeur].to_numpy()
    y = np.arange(len(d))
    seuil = len(d) - 3
    couleurs = [_theme.accent if i >= seuil else _theme.muet for i in range(len(d))]

    ax.barh(y, v, color=couleurs, height=0.62)
    ax.set_yticks(y, d[etiquette].to_list())
    for i, val in enumerate(v):
        ax.text(val + max(v) * 0.012, i, format_valeur.format(val), va="center",
                fontsize=9, color=_theme.texte_secondaire)
    ax.grid(axis="x", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, titre, sous_titre)
    return ax


def pente_portees(comparaison: pl.DataFrame, paires: list[tuple[str, str]],
                  ax=None, ordre: tuple[str, ...] = ("detail", "intermediaire", "texte")):
    """Évolution de l'accord entre groupes selon l'enjeu du scrutin.

    Forme choisie : un graphique de pentes. La question n'est pas « quelle est
    la valeur ? » mais « dans quel sens ça bouge quand l'enjeu monte ? », et la
    pente répond directement. Une seule teinte par sens de variation : bleu si
    l'accord monte avec l'enjeu, orange s'il baisse.
    """
    ax = ax or plt.subplots(figsize=(8.5, 6))[1]
    x = np.arange(len(ordre))
    etiquettes = {"detail": "amendements", "intermediaire": "articles\net motions",
                  "texte": "vote sur\nl'ensemble"}

    fins, debuts = [], []
    for a, b in paires:
        d = comparaison.filter((pl.col("groupe_a") == a) & (pl.col("groupe_b") == b))
        y = [d.filter(pl.col("portee") == p)["accord"][0] for p in ordre]
        monte = y[-1] >= y[0]
        couleur = _theme.accent if monte else _theme.accent_2
        ax.plot(x, y, "-o", lw=2, ms=7, color=couleur,
                mec=_theme.surface, mew=1.5, zorder=3)
        # Séparateur « / » et non « – » : les noms de groupes contiennent déjà
        # des tirets (« LFI-NFP »), et « LFI-NFP-SOC » ne se découpe pas à l'œil.
        fins.append((f"{a} / {b}", y[-1]))
        debuts.append((f"{y[0]:.0%}", y[0]))

    # Les paires arrivent souvent à des valeurs très proches : sans écartement,
    # deux noms se superposent et aucun des deux n'est lisible.
    etendue = max(v for _, v in fins) - min(v for _, v in fins)
    for etiquette, _, yy in _decoller([e for e, _ in fins], np.zeros(len(fins)),
                                      np.array([v for _, v in fins]),
                                      ecart_min=max(etendue, 0.1) * 0.075):
        ax.annotate(f"  {etiquette}", (x[-1], yy), fontsize=9.5, va="center",
                    color=_theme.texte, fontweight="bold", annotation_clip=False)
    for etiquette, _, yy in _decoller([e for e, _ in debuts], np.zeros(len(debuts)),
                                      np.array([v for _, v in debuts]),
                                      ecart_min=max(etendue, 0.1) * 0.06):
        ax.annotate(f"{etiquette}  ", (x[0], yy), fontsize=9, va="center",
                    ha="right", color=_theme.texte_secondaire)

    ax.set_xticks(x, [etiquettes.get(p, p) for p in ordre])
    ax.set_xlim(-0.55, len(ordre) - 0.32)
    ax.yaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0, decimals=0))
    ax.set_ylabel("accord de vote")
    ax.grid(axis="y", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, "L'accord entre groupes dépend de l'enjeu du vote",
              "bleu : l'accord se renforce quand l'enjeu monte · orange : il se défait")
    return ax


def nuage_vote_vs_cosignature(comparaison: pl.DataFrame, ax=None, k_etiquettes: int = 7):
    """Confronte proximité de vote et travail commun, paire de groupes par paire.

    Chaque point est une paire de groupes. La diagonale implicite est
    l'attendu : voter ensemble et cosigner ensemble devraient aller de pair.
    Les points qui s'en écartent sont l'information — d'où l'étiquetage
    sélectif des seuls extrêmes, plutôt qu'un nom sur chaque point.
    """
    ax = ax or plt.subplots(figsize=(8.5, 6))[1]
    d = comparaison.filter(pl.col("part_cosignatures").is_not_nan())
    x = d["accord_vote"].to_numpy()
    y = d["part_cosignatures"].to_numpy()

    ax.scatter(x, y, s=42, c=_theme.accent, alpha=0.75,
               edgecolors=_theme.surface, linewidths=1.2, zorder=3)

    # On n'annote pas les 132 paires, seulement les deux histoires du graphique :
    # les paires qui cosignent le plus, et celles qui votent ensemble sans
    # jamais rien cosigner. Un nom partout serait illisible et sans propos.
    cosignent = d.sort("part_cosignatures", descending=True).head(k_etiquettes)
    votent_sans_cosigner = (
        d.filter(pl.col("part_cosignatures") < 0.01)
        .sort("accord_vote", descending=True)
        .head(k_etiquettes)
    )
    choisies = pl.concat([cosignent, votent_sans_cosigner]).unique(
        subset=["groupe_a", "groupe_b"]
    )

    etendue = float(y.max() - y.min())
    noms = [f"{r['groupe_a']} / {r['groupe_b']}" for r in choisies.iter_rows(named=True)]
    for etiquette, xx, yy in _decoller(
        noms,
        choisies["accord_vote"].to_numpy(),
        choisies["part_cosignatures"].to_numpy(),
        ecart_min=etendue * 0.045,
    ):
        ax.annotate(etiquette, (xx, yy), fontsize=8.5, color=_theme.texte,
                    xytext=(7, 0), textcoords="offset points", va="center",
                    path_effects=_contour())

    ax.xaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0, decimals=0))
    ax.yaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0, decimals=0))
    ax.set_xlabel("accord de vote")
    ax.set_ylabel("part des cosignatures adressées à l'autre groupe")
    ax.grid(lw=0.6, alpha=0.5)
    ax.set_axisbelow(True)
    _habiller(ax, "Voter ensemble n'est pas travailler ensemble",
              "chaque point est une paire de groupes · seuls les cas extrêmes sont nommés")
    return ax


def heatmap_cosignatures(parts: pl.DataFrame, ax=None):
    """Part des cosignatures de chaque groupe adressée à chaque autre groupe.

    Matrice volontairement asymétrique : elle se lit **en ligne**. « 22 % des
    cosignatures de HOR vont vers EPR » n'est pas la même affirmation que
    l'inverse, parce que les groupes n'ont ni la même taille ni la même
    activité.
    """
    groupes = sorted(parts["groupe_a"].unique().to_list())
    m = parts.pivot(values="part", index="groupe_a", on="groupe_b").sort("groupe_a")
    idx = {g: i for i, g in enumerate(m["groupe_a"].to_list())}
    M = np.array([[m[b][idx[a]] for b in groupes] for a in groupes], dtype=float)

    ax = ax or plt.subplots(figsize=(7.5, 6.5))[1]
    im = ax.imshow(M, cmap=_theme.cmap, vmin=0, vmax=np.nanmax(M))
    ax.set_xticks(range(len(groupes)), groupes, rotation=45, ha="right")
    ax.set_yticks(range(len(groupes)), groupes)
    ax.set_xticks(np.arange(len(groupes) + 1) - 0.5, minor=True)
    ax.set_yticks(np.arange(len(groupes) + 1) - 0.5, minor=True)
    ax.grid(which="minor", color=_theme.surface, lw=2)
    ax.tick_params(which="minor", length=0)

    seuil = np.nanmax(M) * 0.55
    for i in range(len(groupes)):
        for j in range(len(groupes)):
            v = M[i, j]
            if np.isnan(v) or v < 0.005:
                continue
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=7.5,
                    color=_theme.surface if v > seuil else _theme.texte_secondaire)

    cb = plt.colorbar(im, ax=ax, fraction=0.043, pad=0.03)
    cb.outline.set_visible(False)
    cb.set_label("part des cosignatures", color=_theme.texte_secondaire)
    ax.set_ylabel("groupe qui cosigne")
    _habiller(ax, "Avec qui chaque groupe cosigne",
              "à lire en ligne : part des cosignatures d'un groupe adressée à chaque autre")
    return ax


def courbe_scrutin(modele, index: int, ax=None, montrer_votes: bool = True):
    """Courbe caractéristique d'un scrutin : le graphique qui explique le modèle.

    En abscisse, la position estimée des députés. En ordonnée, la probabilité
    que le modèle leur attribue de voter « pour ». La courbe logistique est le
    modèle ; les points sont les votes réellement exprimés, posés à 0 ou 1 et
    légèrement dispersés pour qu'on voie leur densité.

    Tout le modèle se lit ici : **où** la courbe bascule, c'est `α/β` ; **à quel
    point elle est raide**, c'est `β`. Un scrutin bien discriminant montre une
    marche d'escalier avec les votes nettement séparés de part et d'autre ; un
    scrutin transversal montre une courbe molle et des points mélangés.
    """
    ax = ax or plt.subplots(figsize=(8, 4.2))[1]
    x = modele.positions[:, 0]
    beta = float(modele.discrimination[index, 0])
    alpha = float(modele.difficulte[index])

    grille = np.linspace(x.min() - 0.3, x.max() + 0.3, 400)
    ax.plot(grille, 1 / (1 + np.exp(-np.clip(beta * grille - alpha, -35, 35))),
            lw=2.5, color=_theme.accent, zorder=3)

    if montrer_votes and modele.votes_pour is not None:
        observe = modele.votes_observes[:, index]
        y = modele.votes_pour[observe, index].astype(float)
        # Léger bruit vertical : sans lui, tous les votes se superposent sur
        # deux traits et on ne voit plus où ils se concentrent.
        bruit = np.random.default_rng(0).normal(0, 0.022, len(y))
        ax.scatter(x[observe], y + bruit, s=22, alpha=0.5,
                   c=_theme.muet, linewidths=0, zorder=2)

    if abs(beta) > 1e-6:
        bascule = alpha / beta
        if x.min() - 0.5 < bascule < x.max() + 0.5:
            ax.axvline(bascule, ls="--", lw=1.2, color=_theme.accent_2, zorder=1)
            ax.annotate(f"bascule\n{bascule:+.2f}", (bascule, 0.5), fontsize=9,
                        color=_theme.accent_2, ha="left", va="center",
                        xytext=(6, 0), textcoords="offset points",
                        path_effects=_contour())

    ax.set_ylim(-0.12, 1.12)
    ax.set_yticks([0, 0.5, 1], ["contre", "50 %", "pour"])
    ax.set_xlabel("position estimée du député (axe 1)")
    ax.grid(lw=0.6, alpha=0.5)
    ax.set_axisbelow(True)
    titre = (modele.scrutins["titre"][index] or "")[:95]
    _habiller(ax, f"β = {beta:.1f}  ·  plus β est grand, plus la coupure est nette",
              titre)
    return ax


def positions_par_groupe(table: pl.DataFrame, ax=None, colonne: str = "axe1"):
    """Distribution des positions estimées, un groupe par ligne.

    Forme choisie plutôt qu'un nuage coloré : avec douze groupes, l'identité est
    portée par l'axe vertical et ses libellés, jamais par la teinte. On voit
    d'un coup l'ordre des groupes, leur dispersion interne et leurs
    recouvrements — trois choses qu'un nuage de points masque.
    """
    ordre = (
        table.group_by("groupe").agg(pl.col(colonne).median().alias("m"))
        .sort("m")["groupe"].to_list()
    )
    ax = ax or plt.subplots(figsize=(8.5, 0.42 * len(ordre) + 1.8))[1]
    rng = np.random.default_rng(0)

    for i, g in enumerate(ordre):
        v = table.filter(pl.col("groupe") == g)[colonne].to_numpy()
        ax.scatter(v, np.full(len(v), i) + rng.normal(0, 0.07, len(v)),
                   s=16, alpha=0.55, c=_theme.accent, linewidths=0)
        ax.plot([np.median(v)], [i], "|", ms=22, mew=2.5, color=_theme.texte)

    ax.set_yticks(range(len(ordre)), [f"{g}  ({table.filter(pl.col('groupe') == g).height})"
                                      for g in ordre])
    ax.set_xlabel(f"position estimée ({colonne})")
    ax.grid(axis="x", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, "Où siège chaque groupe",
              "un point par député · le trait vertical marque la médiane du groupe")
    return ax


def intervalles_deputes(table: pl.DataFrame, ax=None, k: int = 25,
                        extrait: str = "etendue"):
    """Positions avec leur intervalle de confiance, pour un extrait de députés.

    Le graphique qui justifie le modèle : sans les barres, on lirait un
    classement ; avec elles, on voit que des députés sont indiscernables les
    uns des autres.

    Args:
        extrait: `"etendue"` prélève des députés régulièrement espacés sur tout
            l'axe — c'est le défaut, parce que prendre simplement les `k`
            premiers ne montre que l'extrémité d'un seul groupe, où la question
            du chevauchement ne se pose pas. `"tete"` garde les `k` premiers.
    """
    table = table.sort("axe1")
    if extrait == "etendue" and table.height > k:
        pas = np.linspace(0, table.height - 1, k).round().astype(int)
        d = table[np.unique(pas)]
    else:
        d = table.head(k)
    ax = ax or plt.subplots(figsize=(8, 0.32 * len(d) + 1.8))[1]
    y = np.arange(len(d))
    basse = d["borne_basse"].to_numpy()
    haute = d["borne_haute"].to_numpy()
    centre = d["axe1"].to_numpy()

    ax.hlines(y, basse, haute, lw=2.5, color=_theme.muet, zorder=2)
    ax.plot(centre, y, "o", ms=7, color=_theme.accent,
            mec=_theme.surface, mew=1.5, zorder=3)
    ax.set_yticks(y, [f"{n}  ({g})" for n, g in zip(d["nom_complet"], d["groupe"])])
    ax.invert_yaxis()
    ax.set_xlabel("position estimée (axe 1) et intervalle à 90 %")
    ax.grid(axis="x", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, "Une position n'est pas un point, c'est une zone",
              "les intervalles qui se chevauchent désignent des députés indiscernables")
    return ax


def dimensionnalite(table: pl.DataFrame, ax=None):
    """Ajustement en apprentissage et hors échantillon, dimension par dimension.

    Deux séries, donc deux teintes validées et une légende — plus des étiquettes
    directes, l'identité n'étant jamais portée par la seule couleur. L'écart
    entre les deux courbes *est* le surajustement.
    """
    ax = ax or plt.subplots(figsize=(7.5, 4.6))[1]
    x = table["dimensions"].to_numpy()
    for colonne, couleur, etiquette in (
        ("apre_apprentissage", _theme.muet, "en apprentissage"),
        ("apre_test", _theme.accent, "sur des votes non vus"),
    ):
        y = table[colonne].to_numpy()
        ax.plot(x, y, "-o", lw=2.2, ms=8, color=couleur, mec=_theme.surface,
                mew=1.5, label=etiquette, zorder=3)
        ax.annotate(f"  {etiquette}", (x[-1], y[-1]), fontsize=9.5, va="center",
                    color=_theme.texte, fontweight="bold")

    ax.fill_between(x, table["apre_test"].to_numpy(),
                    table["apre_apprentissage"].to_numpy(),
                    color=_theme.accent_2, alpha=0.12, lw=0)
    ax.set_xticks(x, [str(int(v)) for v in x])
    ax.set_xlim(x.min() - 0.1, x.max() + 0.75)
    ax.set_xlabel("nombre de dimensions")
    ax.set_ylabel("APRE")
    ax.grid(axis="y", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    # Légende à gauche : les étiquettes directes occupent déjà la droite.
    ax.legend(frameon=False, loc="upper left")
    _habiller(ax, "Combien de dimensions ?",
              "la zone orangée est le surajustement · seul le gain hors échantillon compte")
    return ax


def distribution_abstention(test: pl.DataFrame, ax=None, n_classes: int = 24):
    """Où se situent les abstentionnistes entre les deux camps, scrutin par scrutin.

    Forme choisie : un histogramme, parce que la question porte sur une
    **distribution** et non sur un classement. Trois repères la rendent lisible
    sans légende — 0 et 1 marquent les deux camps, 0,5 le milieu exact.

    Si l'abstention était un compromis, la masse se concentrerait autour de 0,5.
    Si elle n'était qu'une façon de suivre son camp sans le dire, elle
    s'accumulerait aux bornes.
    """
    ax = ax or plt.subplots(figsize=(8, 4.4))[1]
    v = test["position_relative"].drop_nulls().drop_nans().to_numpy()
    v = v[np.isfinite(v)]

    ax.hist(np.clip(v, -0.5, 1.5), bins=n_classes, color=_theme.accent,
            alpha=0.85, edgecolor=_theme.surface, linewidth=1.2, zorder=3)
    for x, etiquette in ((0.0, "camp le\nplus proche"), (0.5, "milieu exact"),
                         (1.0, "camp\nopposé")):
        ax.axvline(x, ls="--", lw=1.2,
                   color=_theme.accent_2 if x == 0.5 else _theme.muet, zorder=2)
        ax.annotate(etiquette, (x, ax.get_ylim()[1]), fontsize=8.5, ha="center",
                    va="top", color=_theme.texte_secondaire,
                    xytext=(0, -4), textcoords="offset points",
                    path_effects=_contour())

    mediane = float(np.median(v))
    # Les valeurs extrêmes sont ramenées aux bornes pour rester lisibles : il
    # faut le dire, sinon les deux barres d'extrémité se lisent comme des
    # concentrations réelles.
    hors = int((v < -0.5).sum() + (v > 1.5).sum())
    mention = f" · {hors} scrutins hors cadre, ramenés aux barres extrêmes" if hors else ""
    ax.set_xlabel("position des abstentionnistes entre les deux camps")
    ax.set_ylabel("nombre de scrutins")
    ax.grid(axis="y", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, "L'abstention est-elle une position intermédiaire ?",
              f"médiane à {mediane:.2f} · axe estimé sans les abstentions,"
              f" elles y sont replacées après coup{mention}")
    return ax


def barres_abstention(taux: pl.DataFrame, ax=None, colonne: str = "groupe"):
    """Taux d'abstention par groupe. Magnitude ordonnée → rampe séquentielle."""
    d = taux.sort("taux")
    ax = ax or plt.subplots(figsize=(8, 0.36 * d.height + 1.8))[1]
    v = d["taux"].to_numpy()
    norm = (v - v.min()) / max(np.ptp(v), 1e-9)
    y = np.arange(d.height)

    ax.barh(y, v, color=_theme.cmap(0.25 + 0.6 * norm), height=0.62)
    ax.set_yticks(y, d[colonne].to_list())
    ax.xaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0, decimals=0))
    for i, (val, n) in enumerate(zip(v, d["suffrages_exprimes"])):
        ax.text(val + max(v) * 0.015, i, f"{val:.1%}   ({n:,} suffrages)".replace(",", " "),
                va="center", fontsize=8.5, color=_theme.texte_secondaire)
    ax.set_xlim(0, max(v) * 1.35)
    ax.grid(axis="x", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, "Qui s'abstient",
              "part des suffrages exprimés — les absents ne sont pas comptés")
    return ax


def profil_depute(cube: VoteCube, proches: pl.DataFrame, nom: str, ax=None):
    """Les députés les plus alignés avec un député donné, groupe indiqué en texte."""
    d = proches.sort("accord")
    ax = ax or plt.subplots(figsize=(8, 0.34 * len(d) + 1.8))[1]
    v = d["accord"].to_numpy()
    y = np.arange(len(d))
    norm = (v - v.min()) / max(np.ptp(v), 1e-9)

    ax.barh(y, v, color=_theme.cmap(0.3 + 0.6 * norm), height=0.62)
    ax.set_yticks(y, [f"{n}  ({g})" for n, g in zip(d["depute"], d["groupe"])])
    ax.set_xlim(min(0.5, v.min() - 0.05), 1.0)
    ax.xaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0, decimals=0))
    for i, (val, n) in enumerate(zip(v, d["scrutins_communs"])):
        ax.text(val + 0.004, i, f"{val:.1%}  ·  {n} scrutins", va="center",
                fontsize=8.5, color=_theme.texte_secondaire)
    ax.grid(axis="x", lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    _habiller(ax, f"Qui vote comme {nom}",
              "taux d'accord sur les scrutins où les deux se prononcent")
    return ax
