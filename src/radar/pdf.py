"""Rendu PDF du bulletin hebdomadaire.

Le bulletin existe en Markdown pour être republié tel quel ; le PDF est la
version qu'on envoie à une rédaction ou qu'on imprime. Même contenu, même
hiérarchie visuelle que les graphiques (titres à gauche, encre secondaire pour
le commentaire, accent bleu pour le seul élément qui doit accrocher l'œil).

Pas de dépendance système : reportlab suffit, ce qui évite d'imposer pandoc ou
un moteur LaTeX pour produire un bulletin hebdomadaire.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
)

from .alerts import Alerte

# Mêmes valeurs que le thème clair de `viz`, pour que le PDF et les figures
# qu'il contient parlent la même langue.
ENCRE = colors.HexColor("#0b0b0b")
ENCRE_2 = colors.HexColor("#52514e")
ACCENT = colors.HexColor("#2a78d6")
FILET = colors.HexColor("#e6e5e0")

MARGE = 20 * mm

#: Intitulés lisibles pour les catégories d'alertes, dans l'ordre du bulletin.
SECTIONS = {
    "activité": "Activité de la semaine",
    "vote serré": "Votes serrés",
    "fracture": "Groupes divisés",
    "rupture": "Députés en rupture",
    "sujet": "Sujets qui montent",
}


def _styles() -> dict[str, ParagraphStyle]:
    base = {"fontName": "Helvetica", "textColor": ENCRE, "alignment": TA_LEFT}
    gras = {"fontName": "Helvetica-Bold"}
    doux = {"textColor": ENCRE_2}

    def style(nom: str, **surcharges) -> ParagraphStyle:
        return ParagraphStyle(nom, **(base | surcharges))

    return {
        "titre": style("titre", **gras, fontSize=22, leading=26, spaceAfter=2),
        "sous_titre": style("sous_titre", **doux, fontSize=11, leading=15, spaceAfter=10),
        "chiffres": style("chiffres", **doux, fontSize=9.5, leading=13, spaceAfter=4),
        "section": style("section", **gras, fontSize=13, leading=16,
                         spaceBefore=14, spaceAfter=6),
        "alerte": style("alerte", **gras, fontSize=10.5, leading=14, spaceAfter=1),
        "detail": style("detail", **doux, fontSize=9.5, leading=13, spaceAfter=9),
        "legende": style("legende", **doux, fontSize=9, leading=12,
                         spaceBefore=3, spaceAfter=14),
        "pied": style("pied", **doux, fontSize=8.5, leading=12),
    }


def _pied_de_page(canvas, doc) -> None:
    """Filet et numéro de page, discrets, en bas de chaque page."""
    canvas.saveState()
    y = MARGE - 6 * mm
    canvas.setStrokeColor(FILET)
    canvas.setLineWidth(0.6)
    canvas.line(MARGE, y + 4 * mm, A4[0] - MARGE, y + 4 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(ENCRE_2)
    canvas.drawString(MARGE, y, "Radar parlementaire · open data de l'Assemblée nationale")
    canvas.drawRightString(A4[0] - MARGE, y, str(canvas.getPageNumber()))
    canvas.restoreState()


def _image_ajustee(chemin: Path, largeur_max: float) -> Image:
    """Insère une figure en préservant ses proportions."""
    from reportlab.lib.utils import ImageReader

    largeur_px, hauteur_px = ImageReader(str(chemin)).getSize()
    largeur = min(largeur_max, largeur_px)
    return Image(str(chemin), width=largeur, height=largeur * hauteur_px / largeur_px)


def ecrire_pdf(
    chemin: Path,
    jour: date,
    alertes: list[Alerte],
    *,
    n_deputes: int,
    n_scrutins: int,
    figures: list[tuple[Path, str]] | None = None,
) -> Path:
    """Écrit le bulletin de la semaine en PDF.

    Args:
        chemin: fichier de sortie.
        jour: lundi de la semaine analysée.
        alertes: sortie de `alerts.toutes_les_alertes()`.
        n_deputes, n_scrutins: volumétrie rappelée en tête de bulletin.
        figures: couples (chemin de l'image, légende).
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    s = _styles()
    largeur_utile = A4[0] - 2 * MARGE

    doc = BaseDocTemplate(
        str(chemin),
        pagesize=A4,
        leftMargin=MARGE, rightMargin=MARGE,
        topMargin=MARGE, bottomMargin=MARGE,
        title=f"Radar parlementaire — semaine du {jour}",
        author="Radar parlementaire",
        subject="Votes, amendements et sujets de l'Assemblée nationale",
    )
    cadre = Frame(MARGE, MARGE, largeur_utile, A4[1] - 2 * MARGE, id="corps")
    doc.addPageTemplates([PageTemplate(id="page", frames=[cadre], onPage=_pied_de_page)])

    story: list = [
        Paragraph("Radar parlementaire", s["titre"]),
        Paragraph(f"Semaine du {jour:%d/%m/%Y}", s["sous_titre"]),
        HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=8),
        Paragraph(
            f"{n_deputes} députés en exercice · {n_scrutins} scrutins dépouillés · "
            f"{len(alertes)} signalements",
            s["chiffres"],
        ),
    ]

    if not alertes:
        story.append(Paragraph("Rien de notable cette semaine.", s["detail"]))

    for categorie, intitule in SECTIONS.items():
        lot = [a for a in alertes if a.categorie == categorie]
        if not lot:
            continue
        bloc = [Paragraph(intitule, s["section"])]
        for a in lot:
            # Titre et détail restent solidaires d'une page à l'autre.
            bloc.append(
                KeepTogether([
                    Paragraph(escape(a.titre), s["alerte"]),
                    Paragraph(escape(a.detail), s["detail"]),
                ])
            )
        story.extend(bloc)

    for chemin_figure, legende in figures or []:
        if not Path(chemin_figure).exists():
            continue
        story.append(Spacer(1, 6))
        story.append(
            KeepTogether([
                _image_ajustee(Path(chemin_figure), largeur_utile),
                Paragraph(escape(legende), s["legende"]),
            ])
        )

    story += [
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=0.6, color=FILET, spaceAfter=6),
        Paragraph(
            "Source : open data de l'Assemblée nationale (data.assemblee-nationale.fr), "
            "Licence Ouverte 2.0. Les agrégats sont recalculés depuis le dépouillement "
            "nominatif de chaque scrutin ; la position majoritaire de chaque groupe est "
            "recomptée et non reprise du champ publié. Aucune interprétation politique "
            "n'est produite.",
            s["pied"],
        ),
    ]

    doc.build(story)
    return chemin
