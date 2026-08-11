"""Génère les notebooks du radar à partir de listes de cellules.

Les notebooks sont générés plutôt qu'édités à la main : le JSON d'un .ipynb se
relit mal en diff, et on veut pouvoir régénérer les trois d'un coup après un
changement d'API.

    uv run python notebooks/_generer.py
    uv run jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb
"""
import json
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent

C = []
def md(s): C.append(("markdown", s.strip("\n")))
def code(s): C.append(("code", s.strip("\n")))


def ecrire(nom: str) -> None:
    """Écrit les cellules accumulées dans `C` puis vide la liste."""
    cells = []
    for kind, src in C:
        lignes = src.split("\n")
        source = [l + "\n" for l in lignes[:-1]] + [lignes[-1]]
        cells.append({
            "cell_type": kind,
            "id": f"c{len(cells):02d}",
            "metadata": {},
            "source": source,
            **({"execution_count": None, "outputs": []} if kind == "code" else {}),
        })
    nb = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python",
                           "name": "python3"},
            "language_info": {"name": "python", "version": "3.12"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    cible = DOSSIER / nom
    cible.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{cible.name} — {len(cells)} cellules")
    C.clear()

md("""
# 1 — Prise en main

Ce notebook fait le tour de ce que le radar sait faire à partir de l'[open data
de l'Assemblée nationale](https://data.assemblee-nationale.fr) : qui vote avec
qui, quels groupes se fissurent, quels sujets montent, qui dépose quels
amendements. C'est la visite guidée, pas une enquête.

Les deux notebooks suivants creusent chacun une question précise, avec
hypothèse, méthode et contre-épreuve :

- **`02_portee_des_scrutins`** — tous les votes ne se valent pas, et en
  l'ignorant on se trompe de conclusion ;
- **`03_reseau_de_cosignatures`** — voter ensemble et travailler ensemble sont
  deux choses différentes.

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

**Attention à un piège que ce notebook ne corrige pas.** Tous les chiffres
ci-dessus agrègent les 8 434 scrutins sans distinction, alors que 86 % d'entre
eux portent sur un amendement. C'est le sujet du notebook `02`, et ça change
plusieurs conclusions — notamment sur la solidité de l'alliance de gauche.

Quelques pistes encore ouvertes :

- **Interventions en séance** — le compte rendu (dataset *Syceron*) permettrait
  d'ajouter le temps de parole aux classements.
- **Dossiers législatifs** — rattacher chaque scrutin à son dossier donnerait des
  séries par texte plutôt que par mot-clé.
- **Automatisation** — `radar update && radar rapport` en tâche planifiée
  hebdomadaire suffit à alimenter un site ou une newsletter.

Source : open data de l'Assemblée nationale, Licence Ouverte 2.0.
""")

ecrire("01_prise_en_main.ipynb")


# ==========================================================================
# 02 — La portée des scrutins
# ==========================================================================

md("""
# 2 — Tous les votes ne se valent pas

**Ce que ce notebook établit :** agréger les scrutins sans distinguer leur
portée politique fausse les conclusions. L'accord LFI↔SOC passe de **79 %** sur
les amendements à **63 %** sur les votes d'ensemble ; RN↔DR fait le chemin
inverse, de **67 %** à **86 %**. Ce n'est pas un effet de taille d'échantillon,
et ça se vérifie.

Le notebook `01` publie des chiffres « tous scrutins confondus ». Après lecture
de celui-ci, on sait pourquoi ils sous-estiment les divisions à gauche et la
convergence à droite.
""")

code("""
import numpy as np
import polars as pl
import matplotlib.pyplot as plt

from radar import analyze, viz
from radar.parse import load

viz.set_theme("clair")
pl.Config.set_tbl_rows(20)
""")

md("""
## 1. L'hypothèse

Un vote sur un sous-amendement et un vote sur l'ensemble d'une loi n'engagent
pas de la même façon. Le premier est souvent **tactique** — on peut voter un
aménagement technique proposé par un adversaire sans rien approuver de son
texte, ou voter contre un amendement allié pour des raisons de calendrier. Le
second est un **positionnement public** sur le texte entier.

Si cette intuition est juste, alors la proximité entre deux groupes devrait
changer selon le type de scrutin considéré. Et comme les votes d'amendement
sont écrasants en nombre, une moyenne non pondérée mesurerait surtout de la
tactique parlementaire.

Première chose à vérifier : cet écrasement est-il réel ?
""")

code("""
s = load("scrutins")
repartition = (
    s.group_by("portee", "categorie").len()
     .sort("len", descending=True)
     .with_columns((pl.col("len") / s.height).alias("part"))
)
repartition
""")

md("""
86 % des scrutins publics portent sur un amendement. L'hypothèse mérite donc
d'être testée : si la portée change quelque chose, tout ce que le radar publie
« toutes portées confondues » décrit avant tout la vie des amendements.

## 2. La méthode de classement

Les titres de scrutins de l'AN suivent une syntaxe très régulière — « l'ensemble
de la proposition de loi… », « l'amendement n° 117 de M. X… », « la motion de
censure… ». On classe donc sur le titre, par une cascade d'expressions
régulières (`parse.CATEGORIES`), puis on regroupe en trois niveaux de portée.

Un classement lexical n'est acceptable que si on mesure son taux d'échec.
""")

code("""
from radar.parse import CATEGORIES, PORTEES

for nom, motif in CATEGORIES:
    print(f"{nom:18s} {motif}")
print()
print("portées :", PORTEES)
""")

code("""
# Taux de reconnaissance : la part de scrutins qui tombe dans « autre ».
non_classes = s.filter(pl.col("categorie") == "autre")
print(f"scrutins non reconnus : {non_classes.height} / {s.height} "
      f"({non_classes.height / s.height:.2%})")
print()
for t in non_classes["titre"].head(5).to_list():
    print(" •", (t or "")[:100])
""")

md("""
99,7 % des scrutins sont reconnus, et les 25 restants sont des cas isolés
(votes sur une résolution, sur une partie de loi de finances) qui ne relèvent
d'aucune des catégories courantes. Le classement tient.

Un point de méthode qui a son importance : **le drapeau officiel « scrutin
solennel » ne suffisait pas.** Il ne couvre que 72 scrutins, alors que 222 votes
portent sur l'ensemble d'un texte. S'y fier aurait fait perdre les deux tiers de
la population qui nous intéresse.
""")

code("""
ensemble = s.filter(pl.col("categorie") == "ensemble")
print(f"votes sur l'ensemble d'un texte : {ensemble.height}")
print(f"  dont marqués « solennel »     : {ensemble.filter(pl.col('type_vote_code') == 'SPS').height}")
""")

md("""
## 3. Le test

On recalcule l'accord entre groupes sur les trois populations de scrutins, qui
sont **disjointes** : chaque scrutin appartient à une portée et une seule.
""")

code("""
paires = [("LFI-NFP", "SOC"), ("LFI-NFP", "ECOS"), ("RN", "DR"),
          ("EPR", "DR"), ("EPR", "SOC"), ("LIOT", "EPR")]

comparaison = analyze.comparer_portees(paires)
comparaison.select("groupe_a", "groupe_b", "portee", "n_scrutins", "accord")
""")

code("""
viz.pente_portees(comparaison, paires)
plt.show()
""")

md("""
## 4. Le résultat

Les pentes ne vont pas dans le même sens, et c'est tout l'intérêt :

- **À gauche, l'accord se défait quand l'enjeu monte.** LFI↔SOC perd 16 points
  entre les amendements et les votes d'ensemble. Les deux groupes déposent et
  soutiennent les mêmes amendements, puis divergent au moment de voter le texte.
- **À droite, il se renforce.** RN↔DR gagne 19 points, EPR↔DR 12 points. La
  convergence ne se voit pas sur les amendements — où chacun défend ses propres
  rédactions — mais sur le vote final.

Autrement dit, les deux blocs fonctionnent à l'inverse l'un de l'autre, et une
moyenne unique masque exactement ce qui les distingue.

Second effet, moins visible mais important : la carte des députés devient bien
plus lisible dès qu'on retire les amendements.
""")

code("""
(comparaison.select("portee", "n_scrutins", "inertie_axe1")
            .unique()
            .sort("inertie_axe1"))
""")

md("""
Le premier axe capte 24 % de la variance sur les amendements contre 39 % sur les
votes d'ensemble. Le clivage politique structure bien mieux les votes qui
engagent — ce qui est rassurant, et confirme qu'on mesure quelque chose de réel.

## 5. La contre-épreuve

L'objection évidente : il y a 7 216 votes d'amendement contre 245 votes
d'ensemble. Un écart entre deux échantillons aussi déséquilibrés pourrait n'être
que du bruit.

Le test : tirer au hasard, quarante fois, 245 votes d'amendement, et regarder si
la valeur observée sur les textes tombe dans l'étendue de ces tirages. Si oui,
on ne conclut rien.
""")

code("""
robustesse = analyze.test_taille_echantillon(
    [("LFI-NFP", "SOC"), ("RN", "DR"), ("EPR", "DR")], n_tirages=40
)
robustesse
""")

md("""
Les trois paires tombent **hors** de l'étendue des tirages. L'écart n'est pas un
artefact d'échantillonnage : la portée du scrutin explique quelque chose que le
hasard n'explique pas.

## 6. Ce que ça change concrètement

Toute publication du radar doit désormais préciser sa population de scrutins.
En pratique, `build_cube` accepte un filtre `portee` :

```python
analyze.build_cube(portee="texte")     # les votes qui engagent
analyze.build_cube(portee="detail")    # la vie des amendements
```

et la comparaison est disponible en une commande :

```bash
uv run radar portees
```

**Limite honnête.** Trois niveaux de portée, c'est une simplification. Un vote
sur un article central d'une loi de finances pèse plus qu'un vote sur un article
de coordination, et rien ici ne les distingue. Rattacher chaque scrutin à son
dossier législatif permettrait de pondérer par l'importance du texte lui-même —
c'est la suite naturelle de ce travail.
""")

ecrire("02_portee_des_scrutins.ipynb")


# ==========================================================================
# 03 — Le réseau de cosignatures
# ==========================================================================

md("""
# 3 — Voter ensemble, ou travailler ensemble

**Ce que ce notebook établit :** la cosignature d'amendements révèle des
alliances que le vote ne montre pas, et en dément d'autres. Trois résultats —
la gauche vote ensemble sans travailler ensemble (LFI↔ECOS : **88 %** d'accord
de vote, **0,1 %** des cosignatures) ; DR et UDDPLR votent ensemble à **74 %**
avec **zéro** amendement cosigné ; HOR est le seul groupe qui cosigne davantage
hors de ses rangs qu'en son sein.

Il établit aussi que deux artefacts méthodologiques suffisent à produire des
résultats entièrement faux, et comment les neutraliser.
""")

code("""
import numpy as np
import polars as pl
import matplotlib.pyplot as plt

from radar import analyze, cosign, viz
from radar.parse import load

viz.set_theme("clair")
pl.Config.set_tbl_rows(20)
pl.Config.set_fmt_str_lengths(40)
""")

md("""
## 1. L'hypothèse

Le vote et la cosignature ne mesurent pas la même chose.

**Voter** est contraint. Le groupe donne une consigne, la discipline est forte —
la cohésion intra-groupe dépasse 90 % partout (cf. notebook `01`). Un vote
conforme n'apprend donc pas grand-chose sur les affinités personnelles.

**Cosigner** est volontaire. Personne n'est tenu d'apposer son nom sur
l'amendement d'un collègue, et surtout personne ne le fait par inadvertance sur
celui d'un adversaire. C'est un acte public, traçable, et coûteux en capital
politique.

L'hypothèse : ces deux réseaux ne se superposent pas, et leurs écarts sont
informatifs.

## 2. La méthode

Un amendement signé par A (auteur) et cosigné par B et C crée les liens A–B,
A–C et B–C. On construit la matrice d'incidence creuse (députés × amendements)
et le produit `D · Dᵀ` donne, pour chaque paire, le nombre d'amendements
cosignés ensemble.

L'affinité est mesurée par l'indice de **Jaccard** — `communs / (total_A +
total_B − communs)` — et non par le compte brut, qui ne remonterait que les
députés les plus prolifiques.
""")

code("""
amd = load("amendements")
print(f"amendements            : {amd.height:,}")
print(f"avec au moins un cosignataire : {amd.filter(pl.col('nb_cosignataires') > 0).height:,}")
print(f"liens de cosignature   : {amd['nb_cosignataires'].sum():,}")
""")

md("""
## 3. Premier piège : les dépôts de groupe entier

Trois millions de liens, c'est beaucoup pour 577 députés. Avant d'interpréter
quoi que ce soit, il faut regarder combien de signatures porte un amendement
typique.
""")

code("""
n_sig = amd["nb_cosignataires"] + 1
for q in (0.5, 0.75, 0.90, 0.95):
    print(f"  quantile {q:.0%} : {int(n_sig.quantile(q)):3d} signataires")
print(f"  maximum     : {int(n_sig.max())}")
print()
print("effectifs des groupes :")
print(load("deputes").filter(pl.col("en_exercice"))
      .group_by("groupe").len().sort("len", descending=True).head(4))
""")

md("""
Le 90ᵉ centile est à 71 signataires — soit exactement l'effectif du groupe
LFI. Ce ne sont pas des affinités, ce sont des **dépôts collectifs** : le groupe
signe en bloc. Or un amendement à 71 signatures crée à lui seul 2 485 liens, et
relie mécaniquement tous les membres du groupe deux à deux.

Sans plafond, le réseau ne mesure donc pas l'affinité entre personnes mais
l'appartenance à un groupe — c'est-à-dire une information qu'on avait déjà.

Voyons à quel point le résultat dépend de ce plafond.
""")

code("""
lignes = []
for seuil in (None, 100, 40, 20, 10, 6):
    r = cosign.build_reseau(max_signataires=seuil, exclure_rapporteurs=False)
    top = cosign.paires_cosignataires(r, k=1, min_signatures=10, min_communs=3)
    lignes.append({
        "plafond": seuil or 9999,
        "amendements_retenus": r.n_amendements,
        "signatures_medianes": int(np.median(r.signatures)),
        "paire_de_tete": f"{top['depute_a'][0]} ({top['groupe_a'][0]}) ↔ "
                         f"{top['depute_b'][0]} ({top['groupe_b'][0]})",
    })
pl.DataFrame(lignes)
""")

md("""
La paire de tête change complètement selon le plafond. Le seuil n'est donc pas
un détail de réglage : c'est une décision de méthode qu'il faut assumer et
documenter. Le radar retient **10 signataires** par défaut — au-delà, on
considère qu'il s'agit d'un dépôt collectif.

## 4. Second piège : les rapporteurs

À plafond bas, une paire spectaculaire apparaît : une députée EPR et une députée
LFI, deux blocs qui ne votent ensemble qu'un tiers du temps, avec une affinité
de cosignature de 0,79. Trop beau pour être vrai — vérifions de quoi sont faits
leurs amendements communs.
""")

code("""
d = load("deputes").filter(pl.col("nom_complet").is_in(["Brigitte Liso", "Élise Leboucher"]))
uids = dict(zip(d["nom_complet"], d["acteur_uid"]))
signataires = pl.concat_list(pl.col("auteur_uid").fill_null(""), pl.col("cosignataires"))

communs = amd.filter(
    (pl.col("nb_cosignataires") + 1 <= 10)
    & signataires.list.contains(uids["Brigitte Liso"])
    & signataires.list.contains(uids["Élise Leboucher"])
)
print(f"amendements communs : {communs.height}")
print(communs["sort"].value_counts().sort("count", descending=True))
print()
for t in communs["expose_sommaire"].drop_nulls().head(4).to_list():
    print(" •", t[:90])
""")

md("""
Des amendements **rédactionnels**, adoptés à plus de 90 %. Ces deux députées
sont co-rapporteures d'un même texte : elles corédigent des ajustements
techniques au nom de la commission. C'est un **rôle institutionnel**, pas une
alliance politique.

L'open data expose le champ qui permet de les repérer —
`auteurRapporteurOrganeRef`, non nul quand l'amendement est déposé au nom d'une
commission. Le radar le stocke sous `auteur_rapporteur` et l'écarte par défaut.
""")

code("""
print(f"amendements de rapporteur : {amd['auteur_rapporteur'].sum():,} "
      f"({amd['auteur_rapporteur'].mean():.1%})")
""")

md("""
## 5. Troisième piège : la taille des groupes

Pour trouver les « courtiers » — ceux qui travaillent hors de leur camp — le
réflexe est de calculer la part des cosignatures adressées à d'autres groupes.
C'est un piège : un membre d'un groupe de 17 a 96 % de l'Assemblée hors de son
groupe, contre 79 % pour un membre d'un groupe de 122. Classer sur la part brute
revient à classer les groupes par petitesse.

La première version de ce code faisait exactement cette erreur, et son palmarès
était intégralement occupé par le plus petit groupe.

**La correction :** rapporter la part observée à la part attendue sous mélange
aléatoire, `(N − effectif) / (N − 1)`. Un ratio de 1 signifie « cosigne hors de
son groupe exactement autant que le hasard le prédirait ».
""")

code("""
reseau = cosign.build_reseau()          # plafond 10, rapporteurs exclus
print(f"amendements retenus : {reseau.n_amendements:,}")

cosign.courtiers(reseau, k=12).select(
    "nom_complet", "groupe", "signatures",
    "part_hors_groupe", "part_attendue", "ratio",
)
""")

md("""
Résultat inattendu : **presque personne ne dépasse 1**. La cosignature est
massivement intra-groupe, bien plus que le hasard ne le voudrait, et le
« courtage » entre groupes est un phénomène marginal à l'Assemblée. Seule
Christine Le Nabour (HOR) dépasse le seuil — et la suite explique pourquoi.

C'est un résultat négatif, mais c'en est un : il n'existe pas, dans cette
législature, de figure individuelle qui fasse le lien entre les blocs par le
travail législatif.

## 6. Les résultats, au niveau des groupes
""")

code("""
parts = cosign.cosignatures_entre_groupes(reseau)
viz.heatmap_cosignatures(parts)
plt.show()
""")

code("""
# Pour chaque groupe, ses trois principales destinations de cosignature.
for grp in sorted(parts["groupe_a"].unique().to_list()):
    top = parts.filter(pl.col("groupe_a") == grp).sort("part", descending=True).head(3)
    dest = "   ".join(f"{r['groupe_b']} {r['part']:.0%}" for r in top.iter_rows(named=True))
    print(f"  {grp:8s} → {dest}")
""")

md("""
**HOR est l'exception de l'Assemblée.** C'est le seul groupe dont la première
destination de cosignature n'est pas lui-même : il adresse plus de cosignatures
à EPR qu'à ses propres membres. Le groupe fonctionne en satellite du bloc
présidentiel — ce que les votes seuls ne révèlent pas, puisque HOR y garde une
cohésion interne de 90 %.

## 7. Là où vote et cosignature divergent
""")

code("""
cube = analyze.build_cube()
comparaison = cosign.comparer_vote_et_cosignature(
    reseau, analyze.accord_entre_groupes(cube)
)
viz.nuage_vote_vs_cosignature(comparaison)
plt.show()
""")

code("""
# Les paires qui votent beaucoup ensemble... sans jamais rien cosigner.
(comparaison.sort("accord_vote", descending=True)
            .filter(pl.col("part_cosignatures") < 0.01)
            .head(8)
            .select("groupe_a", "groupe_b", "accord_vote", "part_cosignatures", "liens"))
""")

md("""
Le contraste le plus net de tout ce travail :

- **LFI↔ECOS et LFI↔GDR votent ensemble à 88 %** et échangent environ **0,1 %**
  de leurs cosignatures. Le Nouveau Front populaire est une coalition de vote,
  pas un atelier législatif commun.
- **DR↔UDDPLR votent ensemble à 74 % avec zéro amendement cosigné.** Pas « peu » :
  aucun. Deux groupes de droite qui votent de concert les trois quarts du temps
  sans jamais avoir corédigé une ligne.

Aucune de ces deux observations n'est accessible depuis les votes. C'est ce que
la cosignature apporte.

## 8. Limites

- **Cosigner n'est pas rédiger.** Un député peut apposer son nom sans avoir
  participé au travail. Le réseau mesure une association publique revendiquée,
  ce qui reste plus engageant qu'un vote, mais moins qu'une corédaction.
- **Le plafond de 10 signataires est un choix**, montré sensible en section 3.
  Toute publication doit le mentionner.
- **Les amendements irrecevables comptent comme les autres.** Un amendement
  retoqué au titre de l'article 40 a été déposé et cosigné : l'intention est
  là, l'effet législatif non.
- **Les non-inscrits sont exclus des courtiers** : n'étant pas un groupe, leur
  « hors groupe » n'a pas de sens.

En ligne de commande :

```bash
uv run radar cosignatures --par-groupe
uv run radar courtiers
```
""")

ecrire("03_reseau_de_cosignatures.ipynb")
