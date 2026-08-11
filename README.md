# Radar parlementaire

Un radar automatisé sur l'activité de l'Assemblée nationale, construit à partir
de son [open data](https://data.assemblee-nationale.fr) : qui vote avec qui,
quels groupes se fissurent, quels sujets montent, qui dépose quels amendements.

Le radar signale des écarts à la normale. Il ne produit aucune interprétation
politique : la lecture reste au lecteur.

```bash
uv sync --extra notebook
uv run radar update              # télécharge et construit les tables (~1 min)
uv run radar alertes             # ce qu'il s'est passé cette semaine
```

## Ce qu'on peut demander

```bash
# « Quels députés votent le plus souvent ensemble ? »
uv run radar proches "Charles de Courson" --hors-groupe
uv run radar paires -k 20                    # alliances qui traversent les groupes
uv run radar cohesion                        # cohésion interne des groupes

# « Quels sujets explosent cette semaine ? »
uv run radar sujets
uv run radar alertes --semaine 2026-06-15

# « Qui dépose quels amendements ? »
uv run radar update --amendements            # archive lourde, ~300 Mo
uv run radar amendements -k 20
uv run radar amendements --par-groupe

# Classements
uv run radar participation --pire
uv run radar dissidence

# Le bulletin hebdomadaire, graphiques compris
uv run radar rapport                         # Markdown + figures PNG
uv run radar rapport --pdf                   # et la version PDF
uv run radar rapport --semaine 2026-06-15 --pdf
```

Le PDF est produit par reportlab, sans dépendance système : pas besoin
d'installer pandoc ni un moteur LaTeX pour sortir le bulletin de la semaine.

Le notebook [`notebooks/demo.ipynb`](notebooks/demo.ipynb) parcourt l'ensemble
avec les graphiques.

## Les données

| Table | Contenu | Volume |
|---|---|---|
| `deputes` | état civil, groupe, circonscription, dates de mandat | 648 |
| `scrutins` | un scrutin public par ligne, avec son sort | 8 434 |
| `votes` | (scrutin × député) → position de vote | 1 270 476 |
| `positions_groupe` | ligne majoritaire de chaque groupe, **recalculée** | 91 996 |
| `amendements` | amendements déposés, auteur, sort | 123 224 |
| `organes`, `mandats` | groupes, commissions, historique des mandats | — |

Volumes constatés sur la 17ᵉ législature au 11 août 2026. Tout est stocké en
Parquet dans `data/tables/`, reconstruit par `radar build`.

Le téléchargement est conditionnel (`If-Modified-Since`) : l'Assemblée republie
ses archives plusieurs fois par jour, on ne retélécharge que ce qui a changé.

## Trois décisions qui changent les chiffres

**La ligne du groupe est recalculée, pas lue.** L'Assemblée publie un champ
`positionMajoritaire` par groupe et par scrutin. Il diverge du dépouillement
nominatif dans **7,6 %** des cas — c'est une position de consigne, pas la
position constatée. S'y fier fabrique des dissidents qui n'existent pas : sur
une semaine test, un groupe apparaissait « divisé à 48 contre 54 » alors que le
vote réel était quasi unanime. Le radar recompte donc la majorité depuis les
votes, et marque `partage` les scrutins où le groupe se divise à égalité — sans
ligne majoritaire, parler de dissidence n'a pas de sens.

**Un absent n'est ni d'accord ni en désaccord.** L'accord entre deux députés est
calculé sur les seuls scrutins où *les deux* se sont prononcés, et les paires
partageant moins de trente scrutins sont écartées — sinon on remonte des
« 100 % d'accord » établis sur trois votes.

**Le dénominateur de la participation n'est pas le nombre de scrutins.** Il faut
retirer les scrutins antérieurs à l'arrivée du député, ainsi que les non-votes
structurels (membre du Gouvernement, président de séance) qui ne sont pas des
absences. D'où la matrice d'éligibilité, construite depuis les dates de mandat.

## Les couleurs des graphiques

Douze groupes politiques, donc douze couleurs conventionnelles ? Non — et ce
n'est pas une question de goût, c'est mesurable. Le rouge de LFI (`#cc2443`) et
celui de GDR (`#dd0000`) sont séparés par un ΔE de 5,4, largement sous le
plancher de 15 : on les confond **même en vision normale**, sans parler des
daltonismes. Idem pour les trois bleus de droite.

Ici la couleur ne porte donc jamais l'identité d'un groupe. Elle porte des
grandeurs (rampe séquentielle, clair → foncé) ou une emphase (un accent, le
reste en gris). L'identité passe par le texte et par la forme : heatmap sériée
par proximité de vote, petits multiples titrés, étiquettes directes. La palette
d'accents est validée pour la vision normale et les trois principaux
daltonismes, en mode clair comme en mode sombre (`viz.set_theme("sombre")`).

## Structure

```
src/radar/
    config.py     sources open data, chemins, vocabulaire des positions
    fetch.py      téléchargement conditionnel + décompression
    parse.py      JSON de l'AN → tables Parquet
    analyze.py    accord, cohésion, dissidence, participation, carte
    topics.py     corpus hebdomadaire et détection des poussées
    alerts.py     détecteurs d'anomalies de la semaine
    viz.py        graphiques matplotlib
    pdf.py        rendu PDF du bulletin (reportlab)
    cli.py        interface en ligne de commande
notebooks/demo.ipynb
tests/
```

```bash
uv run pytest
```

## Automatiser

`radar update && radar rapport` en tâche hebdomadaire suffit à alimenter un
site, une newsletter ou un fil social. Le rapport sort en Markdown avec ses
figures dans `data/out/`.

## La détection de sujets est lexicale, donc perfectible

Les sujets sont détectés sur les mots des titres de scrutins et des exposés
sommaires — approche simple, sans modèle, mais qui demande trois garde-fous,
tous implémentés dans `topics.py` :

- **les noms de députés sont filtrés** (depuis la table `deputes`) : sans cela,
  un député qui dépose trente amendements dans la semaine remonte comme un
  « sujet », ce qui dit quelque chose de son activité mais rien du débat ;
- **les n-grammes redondants sont fusionnés** : un texte discuté sur vingt-quatre
  scrutins fait remonter tous les fragments de son titre avec le même effectif —
  « corse », « corse autonome », « constitutionnelle corse »… C'est un sujet, pas
  huit ;
- **le jargon parlementaire est en mots vides** : « article », « amendement »,
  « première lecture », « identiques », « rect. » saturent sinon tout classement.

La liste `topics.STOPWORDS` reste le levier de réglage principal : si un terme
parasite revient, c'est là qu'il se retire. Rattacher les scrutins à leur dossier
législatif (voir ci-dessous) serait plus robuste que l'approche lexicale.

## Pistes non couvertes

- **Interventions en séance.** Le compte rendu (dataset *Syceron*) permettrait
  d'ajouter le temps de parole aux classements. L'URL testée pour la 17ᵉ
  législature renvoie 404 ; le chemin exact reste à trouver.
- **Dossiers législatifs.** Rattacher chaque scrutin à son dossier donnerait des
  séries par texte plutôt que par mot-clé — plus robuste que la détection
  lexicale actuelle.
- **API CIVIX.** Elle expose les mêmes données sous forme d'API REST
  (`https://www.civix.fr/api`). Le radar attaque directement les archives de
  l'Assemblée, ce qui évite toute dépendance à un tiers, mais CIVIX peut être
  utile pour des requêtes ponctuelles.

## Sources et licence

Données : [open data de l'Assemblée nationale](https://data.assemblee-nationale.fr),
Licence Ouverte 2.0. Le radar ne modifie pas les données sources ; tous les
agrégats sont recalculés depuis le dépouillement nominatif et reproductibles
avec `radar build`.
