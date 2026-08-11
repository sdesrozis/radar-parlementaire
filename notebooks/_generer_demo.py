"""Génère notebooks/demo.ipynb à partir d'une liste de cellules."""
import json
from pathlib import Path

C = []
def md(s): C.append(("markdown", s.strip("\n")))
def code(s): C.append(("code", s.strip("\n")))

md("""
# Radar parlementaire — démonstration

Ce notebook parcourt tout ce que le radar sait faire à partir de l'[open data de
l'Assemblée nationale](https://data.assemblee-nationale.fr) : qui vote avec qui,
quels groupes se fissurent, quels sujets montent, qui dépose quels amendements.

**Avant de lancer ce notebook**, construisez les tables :

```bash
uv run radar update --amendements
```

Les chiffres ci-dessous sont recalculés depuis le dépouillement nominatif de
chaque scrutin. Aucune interprétation politique n'est produite : le radar
signale des écarts, la lecture reste au lecteur.
""")

code("""
import polars as pl
import matplotlib.pyplot as plt

from radar import analyze, alerts, topics, viz
from radar.parse import load

viz.set_theme("clair")          # "sombre" pour la version fond noir
pl.Config.set_tbl_rows(15)
pl.Config.set_fmt_str_lengths(60)
""")

md("""
## 1. Ce qu'on a sous la main

Six tables, construites une fois pour toutes en Parquet.
""")

code("""
for nom in ("deputes", "scrutins", "votes", "positions_groupe", "amendements"):
    try:
        df = load(nom)
        print(f"{nom:18s} {df.height:>9,} lignes")
    except FileNotFoundError:
        print(f"{nom:18s}   absente — lancez `uv run radar update --amendements`")

scrutins = load("scrutins")
print(f"\\nScrutins du {scrutins['date'].min()} au {scrutins['date'].max()}")
""")

code("""
load("deputes").group_by("groupe").len().sort("len", descending=True)
""")

md("""
## 2. Le cube de votes

Toutes les analyses partent de la même structure : la matrice
(députés × scrutins) des positions, doublée d'une matrice d'éligibilité qui dit
quels députés siégeaient à la date de chaque scrutin. Sans cette seconde
matrice, un député arrivé en cours de législature passerait pour un absentéiste.
""")

code("""
cube = analyze.build_cube()          # députés en exercice, tous scrutins
print(f"{cube.n_deputes} députés × {cube.n_scrutins} scrutins")
print(f"votes exprimés : {cube.exprime.sum():,}")
print(f"couples (député, scrutin) où le député siégeait : {cube.eligible.sum():,}")
""")

md("""
## 3. « Quels députés votent le plus souvent ensemble ? »

L'accord entre deux députés est calculé sur les seuls scrutins où **les deux**
se sont prononcés : un absent n'est ni d'accord ni en désaccord. Les paires qui
partagent moins de trente scrutins sont écartées, sinon on obtiendrait des
« 100 % d'accord » sur trois votes.

### 3.1 Vue d'ensemble : groupe par groupe

Douze groupes, donc douze couleurs ? Non. Le rouge de LFI et celui de GDR sont
séparés par un ΔE de 5,4 — en dessous du plancher de 15, ils sont confondus même
en vision normale. Ici la couleur porte une **grandeur** sur une rampe unique, et
l'identité est portée par les axes.
""")

code("""
accords = analyze.accord_entre_groupes(cube)
viz.heatmap_groupes(accords)
plt.show()
""")

md("""
La lecture se fait par blocs : le carré des groupes de gauche en haut à gauche,
le bloc de droite ailleurs, et la diagonale — toujours la plus foncée — donne la
cohésion interne de chaque groupe.

### 3.2 Cohésion interne des groupes
""")

code("""
cohesion = analyze.cohesion_groupes(cube)
viz.barres_cohesion(cohesion)
plt.show()
cohesion
""")

md("""
Les non-inscrits ferment la marche, ce qui est mécanique : ce n'est pas un
groupe mais un résidu. LIOT, groupe-charnière revendiqué, est nettement moins
homogène que les autres — cette fois c'est une information politique.

### 3.3 Zoom sur un député
""")

code("""
nom = "Charles de Courson"

proches = analyze.plus_proches(cube, nom, k=12)
viz.profil_depute(cube, proches, nom)
plt.show()
""")

code("""
# Et hors de son propre groupe — souvent plus révélateur
analyze.plus_proches(cube, nom, k=10, hors_groupe=True)
""")

code("""
# À l'opposé : ceux dont il ne partage presque jamais le vote
analyze.plus_proches(cube, nom, k=8, inverse=True)
""")

md("""
### 3.4 Alliances qui traversent les groupes

Deux députés du même groupe qui votent pareil, ce n'est pas une information.
Deux députés de groupes différents à 99 % d'accord, si.
""")

code("""
analyze.paires_remarquables(cube, k=15)
""")

md("""
## 4. La carte des votes

On projette les députés en deux dimensions à partir de leurs votes. Deux députés
proches sur la carte votent de la même façon. Les axes n'ont pas de sens
politique intrinsèque : seul compte le positionnement relatif.
""")

code("""
carte = analyze.carte_politique(cube, methode="pca")
viz.carte_apercu(carte)
plt.show()
""")

md("""
Les points sont volontairement neutres : c'est le nom du groupe, posé au
barycentre de ses députés, qui porte l'identité.

Pour voir où se situe chaque groupe sans empiler douze teintes, on répète la
même carte en mettant un seul groupe en avant à chaque fois — la réponse
prescrite dès qu'un nuage de points dépasse trois classes.
""")

code("""
viz.carte_facettes(carte)
plt.show()
""")

md("""
## 5. Discipline de groupe

La « ligne du groupe » est **recalculée** depuis le dépouillement nominatif.
L'Assemblée publie bien un champ `positionMajoritaire`, mais il diverge du vote
réellement majoritaire dans environ 8 % des cas — assez pour fabriquer des
dissidents qui n'existent pas.
""")

code("""
dissidents = analyze.dissidence(cube)
viz.barres_emphase(
    dissidents, "taux_dissidence", "nom_complet", k=15,
    titre="Députés qui s'écartent le plus de leur groupe",
    sous_titre="part des votes exprimés hors de la ligne majoritaire du groupe",
)
plt.show()
dissidents.head(10).select("nom_complet", "groupe", "votes_exprimes", "taux_dissidence")
""")

code("""
# Les scrutins qui ont le plus fracturé les groupes
analyze.scrutins_clivants(cube, k=10).select("date", "taux_fracture", "titre")
""")

code("""
# Et ceux qui se sont joués à quelques voix
analyze.scrutins_serres(cube, k=10, ecart_max=6)
""")

md("""
## 6. Participation

Le dénominateur compte autant que le numérateur : on rapporte les votes exprimés
aux seuls scrutins où le député siégeait, et on retire les non-votes structurels
(membre du Gouvernement, président de séance) qui ne sont pas des absences.
""")

code("""
part = analyze.participation(cube)
viz.barres_emphase(
    part.tail(15).reverse(), "participation", "nom_complet", k=15,
    titre="Participations les plus faibles",
    sous_titre="votes exprimés rapportés aux scrutins où le député siégeait",
)
plt.show()
part.head(10).select("nom_complet", "groupe", "votes_exprimes", "scrutins_eligibles", "participation")
""")

md("""
## 7. « Quels sujets explosent cette semaine ? »

On compte les termes des titres de scrutins et des exposés sommaires
d'amendements, semaine par semaine, puis on compare chaque terme à **sa propre
moyenne récente**. Un terme ne remonte pas parce qu'il est fréquent — « article »
et « gouvernement » le sont toujours — mais parce qu'il l'est plus que d'habitude.
""")

code("""
freqs = topics.frequences_hebdo("scrutins")     # "tout" inclut les amendements (plus lent)
montants = topics.sujets_qui_montent(freqs, k=15)
viz.barres_sujets(montants)
plt.show()
montants
""")

code("""
# Suivre un terme précis dans le temps
terme = "agricole"
viz.courbe_terme(topics.serie_terme(terme, freqs=freqs), terme)
plt.show()
""")

md("""
## 8. « Qui dépose quels amendements ? »
""")

code("""
analyze.amendements_par_groupe()
""")

code("""
analyze.amendements_par_depute(15).drop("auteur_uid")
""")

md("""
Le taux d'adoption se lit avec prudence : un amendement de la majorité et un
amendement d'opposition ne jouent pas le même jeu, et « Tombé » ou « Non
soutenu » ne veut pas dire « Rejeté ».
""")

code("""
load("amendements")["sort"].value_counts().sort("count", descending=True)
""")

md("""
## 9. Les alertes de la semaine

C'est la sortie destinée à une newsletter, un fil social ou un tableau de bord :
uniquement ce qui s'écarte de la normale.
""")

code("""
semaine = alerts.derniere_semaine()
print(f"Semaine du {semaine}\\n")

for a in alerts.toutes_les_alertes(semaine, cube=cube):
    print(a, "\\n")
""")

md("""
## 10. Et ensuite

Le bulletin Markdown complet, graphiques compris :

```bash
uv run radar rapport
```

Quelques pistes pour aller plus loin :

- **Interventions en séance** — le compte rendu (dataset *Syceron*) permettrait
  d'ajouter le temps de parole aux classements.
- **Dossiers législatifs** — rattacher chaque scrutin à son dossier donnerait des
  séries par texte plutôt que par mot-clé.
- **Automatisation** — `radar update && radar rapport` en tâche planifiée
  hebdomadaire suffit à alimenter un site ou une newsletter.

Source : open data de l'Assemblée nationale, Licence Ouverte 2.0.
""")

cells = []
for kind, src in C:
    lines = src.split("\n")
    source = [l + "\n" for l in lines[:-1]] + [lines[-1]]
    if kind == "markdown":
        cells.append({"cell_type": "markdown", "id": f"c{len(cells):02d}",
                      "metadata": {}, "source": source})
    else:
        cells.append({"cell_type": "code", "id": f"c{len(cells):02d}",
                      "execution_count": None,
                      "metadata": {}, "outputs": [], "source": source})

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = Path("/Users/desroziers/Work/radar/notebooks/demo.ipynb")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{out} — {len(cells)} cellules")
