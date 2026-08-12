"""Interface en ligne de commande du radar.

    uv run radar update          # télécharge et reconstruit tout
    uv run radar alertes         # ce qu'il s'est passé cette semaine
    uv run radar proches "Charles de Courson" --hors-groupe
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import typer
from rich.console import Console
from rich.table import Table

from . import alerts, analyze, topics
from .config import LEGISLATURE, paths
from .fetch import fetch_all
from .parse import build_all, load

app = typer.Typer(
    help="Radar politique — votes, amendements et sujets de l'Assemblée nationale.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _table(df: pl.DataFrame, titre: str, formats: dict[str, str] | None = None) -> None:
    """Affiche un DataFrame en table rich, avec formatage par colonne."""
    formats = formats or {}
    t = Table(title=titre, title_justify="left", header_style="bold",
              show_lines=False, pad_edge=False)
    for c in df.columns:
        justify = "right" if df[c].dtype.is_numeric() else "left"
        t.add_column(c, justify=justify, overflow="fold")
    for row in df.iter_rows(named=True):
        cells = []
        for c in df.columns:
            v = row[c]
            if v is None:
                cells.append("—")
            elif c in formats:
                cells.append(formats[c].format(v))
            else:
                cells.append(str(v))
        t.add_row(*cells)
    console.print(t)


# --------------------------------------------------------------------------
# Données
# --------------------------------------------------------------------------


@app.command()
def fetch(
    amendements: bool = typer.Option(False, help="Inclure l'archive des amendements (~300 Mo)."),
    force: bool = typer.Option(False, help="Retélécharger même si rien n'a changé."),
) -> None:
    """Télécharge les jeux de données de l'Assemblée nationale."""
    cles = ["scrutins", "acteurs"] + (["amendements"] if amendements else [])
    for r in fetch_all(cles, force=force):
        console.print(f"  {r}")


@app.command()
def build(
    amendements: bool = typer.Option(False, help="Construire aussi la table des amendements."),
    legislature: int = typer.Option(LEGISLATURE, help="Numéro de législature."),
) -> None:
    """Reconstruit les tables Parquet à partir des JSON téléchargés."""
    tables = build_all(legislature, with_amendements=amendements)
    for nom, df in tables.items():
        console.print(f"  {nom:18s} {df.height:>9,} lignes")
    console.print(f"\n  → {paths().tables}")


@app.command()
def update(
    amendements: bool = typer.Option(False, help="Inclure les amendements."),
) -> None:
    """Télécharge puis reconstruit tout, en une commande."""
    fetch(amendements=amendements, force=False)
    build(amendements=amendements, legislature=LEGISLATURE)


# --------------------------------------------------------------------------
# Qui vote avec qui
# --------------------------------------------------------------------------


@app.command()
def proches(
    nom: str = typer.Argument(..., help="Nom du député, complet ou partiel."),
    k: int = typer.Option(10, "-k", help="Nombre de résultats."),
    hors_groupe: bool = typer.Option(False, help="Ignorer les membres de son propre groupe."),
    inverse: bool = typer.Option(False, help="Les plus éloignés plutôt que les plus proches."),
    depuis: str | None = typer.Option(None, help="Restreindre aux scrutins depuis cette date."),
) -> None:
    """Les députés qui votent le plus (ou le moins) comme celui-ci."""
    cube = analyze.build_cube(depuis=depuis)
    d = analyze.plus_proches(cube, nom, k=k, hors_groupe=hors_groupe, inverse=inverse)
    sens = "s'opposent le plus à" if inverse else "votent le plus comme"
    _table(d, f"Députés qui {sens} {nom}", {"accord": "{:.1%}"})


@app.command()
def paires(
    k: int = typer.Option(20, "-k"),
    meme_groupe: bool = typer.Option(False, help="Inclure les paires d'un même groupe."),
) -> None:
    """Les binômes de députés les plus alignés, de préférence entre groupes."""
    cube = analyze.build_cube()
    d = analyze.paires_remarquables(cube, k=k, inter_groupes=not meme_groupe)
    _table(d, "Alliances de vote", {"accord": "{:.1%}"})


@app.command()
def cohesion() -> None:
    """Cohésion interne de chaque groupe politique."""
    cube = analyze.build_cube()
    _table(analyze.cohesion_groupes(cube), "Cohésion des groupes", {"cohesion": "{:.1%}"})


@app.command()
def participation(
    k: int = typer.Option(15, "-k"),
    pire: bool = typer.Option(False, help="Afficher les plus faibles participations."),
) -> None:
    """Classement des députés par participation aux scrutins."""
    cube = analyze.build_cube()
    d = analyze.participation(cube)
    d = d.tail(k).reverse() if pire else d.head(k)
    _table(
        d.select("nom_complet", "groupe", "votes_exprimes", "scrutins_eligibles", "participation"),
        "Participation aux scrutins" + (" — dernières places" if pire else ""),
        {"participation": "{:.1%}"},
    )


@app.command()
def dissidence(k: int = typer.Option(15, "-k")) -> None:
    """Députés qui s'écartent le plus de la ligne majoritaire de leur groupe."""
    cube = analyze.build_cube()
    d = analyze.dissidence(cube).head(k)
    _table(
        d.select("nom_complet", "groupe", "votes_exprimes", "votes_dissidents", "taux_dissidence"),
        "Écarts à la ligne du groupe",
        {"taux_dissidence": "{:.1%}"},
    )


@app.command()
def portees(
    paires: str = typer.Option(
        "LFI-NFP:SOC,RN:DR,EPR:DR,EPR:SOC",
        help="Paires de groupes à suivre, séparées par des virgules.",
    ),
) -> None:
    """Compare l'accord entre groupes selon l'enjeu politique du scrutin.

    86 % des scrutins publics portent sur un amendement. Agréger sans distinguer
    revient à mesurer surtout de la tactique parlementaire.
    """
    lot = [tuple(p.split(":")) for p in paires.split(",") if ":" in p]
    d = analyze.comparer_portees(lot)  # type: ignore[arg-type]
    _table(
        d.select("groupe_a", "groupe_b", "portee", "n_scrutins", "accord", "inertie_axe1"),
        "Accord entre groupes selon l'enjeu du vote",
        {"accord": "{:.1%}", "inertie_axe1": "{:.1%}"},
    )


@app.command()
def positions(
    k: int = typer.Option(15, "-k"),
    dimensions: int = typer.Option(1, help="Nombre d'axes du modèle."),
    portee: str = typer.Option("texte", help="texte, intermediaire, detail ou toutes."),
    pivots_seulement: bool = typer.Option(False, "--pivots", help="Les députés médians."),
    incertitude: bool = typer.Option(False, "--incertitude", help="Intervalles par bootstrap."),
) -> None:
    """Estime les points idéaux : la position de chaque député selon ses votes.

    Complète la carte par ACP en fournissant ce qu'elle ne peut pas donner :
    une incertitude par député et une lecture scrutin par scrutin.
    """
    from . import ideal

    cube = analyze.build_cube(portee=None if portee == "toutes" else portee)
    if incertitude:
        d = ideal.intervalles(cube, dimensions=dimensions).head(k)
        _table(
            d.select("nom_complet", "groupe", "axe1", "borne_basse", "borne_haute", "largeur"),
            "Positions estimées et intervalle à 90 %",
            {c: "{:+.2f}" for c in ("axe1", "borne_basse", "borne_haute", "largeur")},
        )
        return

    modele = ideal.estimer(cube, dimensions=dimensions)
    console.print(
        f"  {modele.n_votes:,} votes · classification {modele.classification:.1%} "
        f"· APRE {modele.apre:.3f}\n"
    )
    d = ideal.pivots(modele, k) if pivots_seulement else modele.table_deputes().head(k)
    titre = "Députés pivots" if pivots_seulement else "Positions estimées"
    _table(d.drop("acteur_uid"), titre, {"axe1": "{:+.2f}", "axe2": "{:+.2f}",
                                         "distance_mediane": "{:.3f}"})


@app.command()
def dimensions(
    max_dimensions: int = typer.Option(3, help="Nombre maximal d'axes à tester."),
    portee: str = typer.Option("texte"),
) -> None:
    """Combien d'axes faut-il pour décrire l'Assemblée ? Testé hors échantillon."""
    from . import ideal

    cube = analyze.build_cube(portee=None if portee == "toutes" else portee)
    d = ideal.evaluer_dimensionnalite(cube, max_dimensions=max_dimensions)
    _table(
        d.select("dimensions", "n_parametres", "apre_apprentissage", "apre_test",
                 "surajustement", "gain_hors_echantillon"),
        "Un axe de plus apporte-t-il quelque chose ?",
        {c: "{:.3f}" for c in ("apre_apprentissage", "apre_test", "surajustement",
                               "gain_hors_echantillon")},
    )


@app.command()
def abstentions(
    k: int = typer.Option(15, "-k"),
    portee: str | None = typer.Option(None, help="texte, intermediaire ou detail."),
    bascule: bool = typer.Option(False, "--bascule",
                                help="Scrutins où l'abstention détenait l'issue."),
) -> None:
    """L'abstention : qui s'abstient, sur consigne ou non, et quand elle décide."""
    from . import abstention as ab

    if bascule:
        d = ab.scrutins_bascule(portee=portee or "texte", k=k)
        _table(
            # Colonnes réduites au strict nécessaire : `rich` répartit la
            # largeur du terminal entre toutes les colonnes, et une de trop
            # suffit à hacher les titres en tranches illisibles.
            d.select(
                "date", "n_pour", "n_contre", "n_abstention", "ecart",
                pl.col("titre").str.slice(0, 58) + "…",
            ),
            "Scrutins où les abstentionnistes détenaient l'issue",
        )
        return

    _table(ab.taux("groupe", portee=portee), "Taux d'abstention par groupe",
           {"taux": "{:.2%}"})
    _table(ab.decomposition(portee), "Consigne de groupe ou écart individuel ?",
           {"part": "{:.1%}"})


@app.command()
def cosignatures(
    k: int = typer.Option(15, "-k"),
    par_groupe: bool = typer.Option(False, help="Vue par groupe plutôt que par binôme."),
    meme_groupe: bool = typer.Option(False, help="Inclure les binômes d'un même groupe."),
    max_signataires: int = typer.Option(
        10, help="Plafond de signataires : au-delà, c'est un dépôt de groupe."
    ),
) -> None:
    """Qui cosigne les amendements de qui — la carte des alliances de travail."""
    from . import cosign

    reseau = cosign.build_reseau(max_signataires=max_signataires)
    if par_groupe:
        d = (
            cosign.cosignatures_entre_groupes(reseau)
            .filter(pl.col("part") > 0.005)
            .sort("part", descending=True)
            .head(k)
        )
        _table(d, "Cosignatures entre groupes", {"part": "{:.1%}", "liens": "{:.0f}"})
    else:
        d = cosign.paires_cosignataires(
            reseau, k=k, inter_groupes=not meme_groupe, min_signatures=10, min_communs=5
        )
        _table(d, "Binômes de cosignataires", {"affinite": "{:.1%}"})


@app.command()
def courtiers(
    k: int = typer.Option(15, "-k"),
    max_signataires: int = typer.Option(10),
) -> None:
    """Députés qui cosignent hors de leur groupe plus que le hasard ne le prédit."""
    from . import cosign

    reseau = cosign.build_reseau(max_signataires=max_signataires)
    _table(
        cosign.courtiers(reseau, k=k),
        "Courtiers entre groupes",
        {"part_hors_groupe": "{:.1%}", "part_attendue": "{:.1%}", "ratio": "{:.2f}"},
    )


# --------------------------------------------------------------------------
# Sujets, amendements, alertes
# --------------------------------------------------------------------------


@app.command()
def sujets(
    semaine: str | None = typer.Option(None, help="Lundi de la semaine (AAAA-MM-JJ)."),
    k: int = typer.Option(20, "-k"),
    source: str = typer.Option("tout", help="scrutins, amendements ou tout."),
) -> None:
    """Les sujets dont la fréquence explose cette semaine."""
    d = topics.sujets_qui_montent(semaine=semaine, k=k, source=source)
    _table(d, "Sujets qui montent", {"attendu": "{:.1f}", "score": "{:.1f}"})


@app.command()
def amendements(
    k: int = typer.Option(20, "-k"),
    par_groupe: bool = typer.Option(False, help="Agréger par groupe plutôt que par député."),
    depuis: str | None = typer.Option(None, help="Amendements déposés depuis cette date."),
) -> None:
    """Qui dépose quels amendements, et lesquels sont adoptés."""
    if par_groupe:
        d = analyze.amendements_par_groupe(depuis=depuis)
        _table(d, "Amendements par groupe", {"taux_adoption": "{:.1%}"})
    else:
        d = analyze.amendements_par_depute(k, depuis=depuis).drop("auteur_uid")
        _table(d, "Plus gros déposants d'amendements",
               {"taux_adoption": "{:.1%}", "cosignataires_moyen": "{:.1f}"})


@app.command()
def alertes(
    semaine: str | None = typer.Option(None, help="Lundi de la semaine (AAAA-MM-JJ)."),
    sujets_inclus: bool = typer.Option(True, "--sujets/--sans-sujets"),
    max_par_categorie: int = typer.Option(5),
) -> None:
    """Ce qu'il s'est passé d'inhabituel cette semaine."""
    lot = alerts.toutes_les_alertes(
        semaine, avec_sujets=sujets_inclus, max_par_categorie=max_par_categorie
    )
    if not lot:
        console.print("[dim]Rien de notable cette semaine.[/dim]")
        return
    for a in lot:
        console.print(f"[bold]{a.titre}[/bold]  [dim]({a.categorie})[/dim]")
        console.print(f"  {a.detail}\n")


@app.command()
def rapport(
    semaine: str | None = typer.Option(None, help="Lundi de la semaine (AAAA-MM-JJ)."),
    sortie: Path | None = typer.Option(None, help="Fichier Markdown de sortie."),
    graphiques: bool = typer.Option(True, "--graphiques/--sans-graphiques"),
    pdf: bool = typer.Option(False, "--pdf", help="Écrire aussi une version PDF."),
) -> None:
    """Produit le bulletin hebdomadaire en Markdown, prêt à publier."""
    from . import viz
    import matplotlib.pyplot as plt

    p = paths().ensure()
    cube = analyze.build_cube()
    jour = alerts.derniere_semaine() if semaine is None else date.fromisoformat(semaine)
    lot = alerts.toutes_les_alertes(jour, cube=cube)

    lignes = [
        f"# Radar parlementaire — semaine du {jour}",
        "",
        f"*{cube.n_deputes} députés en exercice · {cube.n_scrutins} scrutins dépouillés*",
        "",
    ]
    for categorie in ("activité", "vote serré", "fracture", "rupture", "sujet"):
        lot_cat = [a for a in lot if a.categorie == categorie]
        if not lot_cat:
            continue
        lignes += [f"## {categorie.capitalize()}", ""]
        lignes += [f"- **{a.titre}** — {a.detail}" for a in lot_cat] + [""]

    figures: list[tuple[Path, str]] = []
    if graphiques:
        viz.set_theme("clair")
        fig_dir = p.out / "figures"
        fig_dir.mkdir(parents=True, exist_ok=True)

        # Fond blanc : posées sur une page blanche, les figures ne doivent pas
        # former un pavé grisé.
        enregistrer = dict(bbox_inches="tight", dpi=200, facecolor="white")

        ax = viz.heatmap_groupes(analyze.accord_entre_groupes(cube))
        ax.figure.savefig(fig_dir / "accord_groupes.png", **enregistrer)
        plt.close(ax.figure)

        ax = viz.barres_cohesion(analyze.cohesion_groupes(cube))
        ax.figure.savefig(fig_dir / "cohesion.png", **enregistrer)
        plt.close(ax.figure)

        figures = [
            (fig_dir / "accord_groupes.png",
             "Accord moyen entre députés de deux groupes, sur les scrutins où les "
             "deux se prononcent. Groupes ordonnés par proximité de vote."),
            (fig_dir / "cohesion.png",
             "Part des votes où deux membres d'un même groupe votent pareil."),
        ]
        lignes += [
            "## Cartes",
            "",
            "![Accord entre groupes](figures/accord_groupes.png)",
            "",
            "![Cohésion des groupes](figures/cohesion.png)",
            "",
        ]

    lignes += [
        "---",
        "",
        "Source : [open data de l'Assemblée nationale]"
        "(https://data.assemblee-nationale.fr), Licence Ouverte 2.0. "
        "Chiffres recalculés depuis le dépouillement nominatif.",
    ]

    texte = "\n".join(lignes)
    cible = sortie or (p.out / f"rapport-{jour}.md")
    cible.write_text(texte, encoding="utf-8")
    console.print(f"  → {cible}")

    if pdf:
        from .pdf import ecrire_pdf

        chemin_pdf = ecrire_pdf(
            cible.with_suffix(".pdf"),
            jour,
            lot,
            n_deputes=cube.n_deputes,
            n_scrutins=cube.n_scrutins,
            figures=figures,
        )
        console.print(f"  → {chemin_pdf}")


@app.command()
def info() -> None:
    """État des tables construites."""
    t = Table(title="Tables du radar", title_justify="left", header_style="bold")
    t.add_column("table")
    t.add_column("lignes", justify="right")
    t.add_column("colonnes", justify="right")
    for nom in ("organes", "deputes", "mandats", "scrutins", "votes",
                "positions_groupe", "amendements"):
        try:
            df = load(nom)
            t.add_row(nom, f"{df.height:,}", str(len(df.columns)))
        except FileNotFoundError:
            t.add_row(nom, "[dim]absente[/dim]", "—")
    console.print(t)
    s = load("scrutins")
    console.print(f"\n  scrutins du {s['date'].min()} au {s['date'].max()}")


if __name__ == "__main__":
    app()
