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

# Selon l'enjeu politique du scrutin
uv run radar portees

# Points idéaux : position estimée de chaque député, avec son incertitude
uv run radar positions --incertitude
uv run radar positions --pivots
uv run radar dimensions

# L'abstention : consigne de groupe, position intermédiaire, arbitrage
uv run radar abstentions
uv run radar abstentions --bascule

# Alliances de travail : qui cosigne les amendements de qui
uv run radar cosignatures --par-groupe
uv run radar courtiers

# Classements
uv run radar participation --pire
uv run radar dissidence

# Le bulletin hebdomadaire, graphiques compris
uv run radar rapport                         # Markdown + figures PNG
uv run radar rapport --pdf                   # et la version PDF
uv run radar rapport --semaine 2026-06-15 --pdf

# Le site : une fiche par député, la matrice des accords, la méthode
uv run python site/generer.py --servir       # http://127.0.0.1:8000
```

Le PDF est produit par reportlab, sans dépendance système : pas besoin
d'installer pandoc ni un moteur LaTeX pour sortir le bulletin de la semaine.

## La note méthodologique

[`docs/note-methodologique.tex`](docs/note-methodologique.tex) est la
**spécification de référence** des calculs : chaque mesure affichée par le site
y est définie formellement, avec son numérateur, son dénominateur, sa
population de référence, ses exclusions et son incertitude. En cas de
divergence entre ce document et le code, c'est le code qui a tort.

Elle s'adresse autant au lecteur curieux qu'au contradicteur : on ne peut
discuter un chiffre que si la règle qui l'a produit est écrite quelque part.

Sa compilation nécessite une distribution LaTeX incluant les paquets usuels
(`babel`, `amsmath`, `booktabs`, `fancyhdr`, `hyperref`) :

```bash
cd docs && latexmk -pdf note-methodologique.tex
```

Sans `latexmk`, lancer deux fois `pdflatex` génère aussi la table des matières
et les renvois internes :

```bash
cd docs && pdflatex note-methodologique.tex && pdflatex note-methodologique.tex
```

Le fichier produit est `docs/note-methodologique.pdf`. `site/generer.py` le
recopie dans `sortie/methode/` s'il existe, et la page Méthode s'y adapte
seule. Cette compilation est indépendante du PDF hebdomadaire, qui reste
généré par ReportLab.

## Le site

Le site est **statique** : `site/generer.py` construit les données une fois puis
écrit 581 fichiers HTML dans `site/sortie/`. Aucun serveur en production, aucune
API, aucune base de données — ce qu'on regarde en local est exactement ce qui
sera en ligne.

```bash
uv run python site/generer.py --servir   # recalcule tout, puis sert (~20 s)
python3 site/serveur.py                  # rouvre ce qui est déjà généré, instantané
```

La seconde forme n'a aucune dépendance — ni `uv`, ni `radar`, ni polars. Relire
le site ne doit pas coûter les vingt secondes que coûte le reconstruire, sans
quoi on prend l'habitude d'ouvrir `file://`, qui ment sur les chemins.

Options : `--bootstrap 0` (démarrage plus rapide, positions sans intervalle) et
`--limite 5` (cinq fiches, pour une mise au point).

Quatre pages : **l'accueil** pédagogique, **l'annuaire** des députés avec
recherche par nom, département, région et groupe, **la fiche** d'un député, et
**« Qui vote avec qui ? »** — les 164 451 paires de députés dessinées d'un coup,
rangées non par groupe mais le long de l'axe estimé à partir des seuls votes.
Une cinquième, *Méthode*, donne la définition exacte et le dénominateur de
chaque mesure, puis ce qu'aucune d'elles ne peut dire.

### La frontière algorithmes / site

Le paquet `src/radar/` ne contient **aucune ligne de HTML, CSS ou JavaScript**,
et la CI échoue si l'on en réintroduit. Il s'installe et s'utilise seul, sans
que le site existe. La frontière est `radar/vues.py` : il assemble les sorties
des algorithmes en vues prêtes à afficher, et ne sait pas ce qu'est une page.

```
src/radar/        les algorithmes           →  publiable seul
  vues.py         la frontière
site/
  generer.py      l'orchestration           →  consomme radar, ne calcule rien
  redaction.py    les phrases
  gabarits/       la structure
  statique/       le système visuel
  serveur.py      le mode développement
```

`redaction.py` est séparé pour une raison qui n'est pas cosmétique : un chiffre
est vrai ou faux, une phrase qui le commente peut être exacte et malhonnête à la
fois. Ce sont deux métiers, ils se relisent différemment. Le module n'a aucune
importation — il reçoit des dictionnaires et rend des chaînes.

La fiche affiche systématiquement ce que la ligne de commande fait afficher à la
demande : le taux avec son dénominateur, la position avec son intervalle, et
surtout **la proximité de vote en double** — sur les 8 434 scrutins d'un côté,
sur les 245 votes qui engagent de l'autre. C'est là que le radar sert à quelque
chose : un binôme peut être à 96 % d'accord dans la première colonne et
nettement moins dans la seconde, et cet écart est plus informatif que chacun des
deux nombres.

Une page web donne à un chiffre une autorité que sa définition ne lui donne pas.
Le site est construit contre ce biais : rien n'y est affiché sans son
dénominateur, et les votes d'un groupe partagé — aucune position au-dessus de la
moitié des suffrages — sont marqués comme tels au lieu d'être comptés comme de
la dissidence.

---

# Comment lire les chiffres

Cette partie est le cœur du projet. Chaque mesure repose sur une définition, et
chaque définition écarte quelque chose. Le but n'est pas de supprimer ces
choix — c'est impossible — mais de les rendre visibles, chiffrés et
discutables. On procède partout de la même façon : **la question, le piège, ce
qu'on fait, ce que ça change.**

## 1. La portée du scrutin

**La question.** « Ces deux groupes votent-ils souvent ensemble ? »

**Le piège.** 86 % des scrutins publics portent sur un **amendement**. Seuls
245 scrutins engagent vraiment — vote sur l'ensemble d'un texte, motion de
censure. Un vote sur un sous-amendement est souvent tactique : on approuve un
aménagement technique sans approuver le texte. Les agréger tous revient donc à
mesurer surtout de la tactique parlementaire, et une seule loi très amendée
peut peser plus lourd que deux ans de votes solennels.

**Ce qu'on fait.** Les scrutins sont classés en trois portées par la syntaxe
très régulière de leurs titres — 99,7 % sont reconnus — et l'accord est
recalculé séparément sur chacune.

| Portée | Ce que c'est | Nombre |
|---|---|---|
| `detail` | amendements, sous-amendements | 7 216 |
| `intermediaire` | articles, motions de procédure | 973 |
| `texte` | ensemble d'un texte, motion de censure | 245 |

**Ce que ça change.** Beaucoup, et de façon ordonnée avec l'enjeu :

| Paire | Amendements | Vote sur l'ensemble |
|---|---|---|
| LFI-NFP ↔ SOC | 79 % | **63 %** |
| RN ↔ DR | 67 % | **86 %** |
| EPR ↔ DR | 71 % | **83 %** |

Les deux blocs fonctionnent à l'inverse l'un de l'autre : la gauche se défait
quand l'enjeu monte, la droite se resserre. La part de variance captée par le
premier axe passe de 24 % à 38 % — autrement dit le clivage politique devient
bien plus lisible dès qu'on retire les amendements.

**La contre-épreuve.** On pourrait objecter que 245 scrutins contre 7 216, ce
n'est qu'un effet de taille d'échantillon. On tire donc quarante fois 245
votes d'amendement au hasard et on recalcule : LFI↔SOC donne 72–87 % sur ces
tirages, contre 63 % sur les textes. L'écart tombe nettement hors de
l'étendue ; ce n'est pas du bruit (`analyze.verifier_taille_echantillon`).

```python
analyze.build_cube(portee="texte")     # les votes qui engagent
analyze.build_cube(portee="detail")    # la vie des amendements
```

Détail dans [`02_portee_des_scrutins`](notebooks/02_portee_des_scrutins.ipynb).

## 2. La ligne du groupe : trois situations à ne pas confondre

**La question.** « Ce député s'est-il écarté de son groupe ? »

**Le premier piège : lire la ligne au lieu de la compter.** L'Assemblée publie
un champ `positionMajoritaire` par groupe et par scrutin. Mais c'est une
position de *consigne*, pas la position *constatée* : elle diverge du
dépouillement nominatif dans **4,9 %** des couples (groupe × scrutin). S'y
fier fabrique des dissidents qui n'existent pas — cela doublerait le nombre de
votes classés « hors ligne » (6,7 % au lieu de 3,1 %) et changerait le verdict
sur **5,6 %** des votes individuels. Le radar recompte donc la majorité depuis
les bulletins.

**Le second piège, plus subtil : il n'y a pas toujours de ligne.** Il y a trois
positions possibles et non deux, donc « la position la plus fréquente » ne veut
pas dire « la position majoritaire ». Un groupe qui vote **5 pour, 7 contre et
5 abstentions** a une position dominante à 41 % : les dix autres votes ne sont
pas des écarts à une consigne, ils sont la preuve qu'aucune consigne ne s'est
dégagée. Compter dix « dissidents » ici, c'est confondre deux situations
politiquement opposées :

| Situation | Ce que ça veut dire |
|---|---|
| **Ligne nette** — une position > 50 % des suffrages du groupe | le groupe a tranché ; on peut parler d'écart |
| **Groupe partagé** — position dominante ≤ 50 % | le groupe n'a pas tranché ; personne ne « dissident » |
| **Dissidence individuelle** — un député s'écarte d'une ligne nette | le seul cas où le mot a un sens |

**Ce qu'on fait.** La table `positions_groupe` publie les **trois effectifs**
(`n_pour`, `n_contre`, `n_abstention`) et la part de la position dominante, pas
un simple compte de dissidents. C'est ensuite à la lecture de trancher :
`analyze.votes_vs_ligne` n'accepte que les scrutins dépassant `SEUIL_LIGNE`, la
majorité absolue — le seuil le moins arbitraire disponible. Les alertes
hebdomadaires distinguent en conséquence deux catégories, `fracture` et
`groupe partagé`, qui ne disent pas du tout la même chose :

```
[fracture]       SOC : 28/58 votes s'écartent de la ligne « pour »
                 30 pour / 0 contre / 28 abstentions — protection des mineurs…

[groupe partagé] GDR sans ligne majoritaire (position dominante à 41 %)
                 5 pour / 7 contre / 5 abstentions sur 17 votants — …
```

**Ce que ça change.** 1,31 % des couples (groupe × scrutin) sont des groupes
partagés : peu en volume, mais ils étaient surreprésentés là où ça compte. Un
détecteur qui cherche les scrutins les plus éclatés sélectionne mécaniquement
ceux où la position dominante ne domine rien — sur la semaine du 20 juillet
2026, quatre des alertes de « fracture » les plus fortes étaient en réalité des
groupes sans ligne. Elles sont désormais nommées pour ce qu'elles sont.

```python
analyze.votes_vs_ligne()                    # ligne nette seulement (défaut)
analyze.votes_vs_ligne(seuil_ligne=0.0)     # ancien comportement, pour comparer
```

## 3. Le lien de cosignature : compter la même chose des deux côtés

**La question.** « Avec qui ce groupe travaille-t-il ? »

**Pourquoi un second réseau, alors qu'on a déjà les votes.** Parce que les deux
mesurent des choses différentes. Le vote mesure la *discipline* : l'accord
intra-groupe dépasse 90 % partout, parce que c'est la consigne. La cosignature
mesure l'*initiative* : personne n'est tenu de cosigner l'amendement d'un
collègue, et on ne cosigne pas celui d'un adversaire par inadvertance.

**L'unité comptée est le lien** : une paire de députés ayant cosigné un même
amendement. Un amendement signé par A, B et C crée trois liens — A–B, A–C, B–C.

**Le piège.** La matrice des cosignatures est symétrique : à l'intérieur d'un
groupe, chaque paire y figure **deux fois**, en (i, j) et en (j, i). Et sa
diagonale ne contient pas des liens, mais le nombre d'amendements signés par
chaque député — un nombre énorme, sans rapport. Une version précédente en
tenait correctement compte au numérateur et pas au dénominateur. Résultat : les
parts d'un groupe totalisaient **0,40 à 0,80 au lieu de 1**, et le déficit
variait d'un groupe à l'autre. Les groupes n'étaient donc même pas comparables
entre eux — ce qui est le seul usage d'une part.

**Ce qu'on fait.** La même convention partout : chaque paire une fois, la
diagonale jamais. Les parts d'une ligne totalisent 1, et un test le vérifie.
Chaque part est publiée avec son nombre de liens absolu, parce qu'une part de
0,3 % sur 59 liens et sur 59 000 ne se lisent pas pareil.

**Ce que ça change.** Les ordres de grandeur restent, les niveaux bougent :
LFI↔ECOS passe de 0,1 % à **0,3 %** des cosignatures. La conclusion tient
toujours — deux groupes qui votent à 88 % ensemble n'échangent presque aucun
travail législatif, et DR↔UDDPLR votent à 74 % avec **zéro** amendement
commun — mais elle est maintenant chiffrée sur une base cohérente.

**Deux garde-fous supplémentaires**, sans lesquels le réseau ne mesure pas ce
qu'on croit :

- **les dépôts de groupe entier.** La moitié des amendements portent au moins
  dix signatures, et le 90ᵉ centile en compte 71 — l'effectif exact du groupe
  LFI. Ces dépôts collectifs relient mécaniquement tous les membres d'un groupe
  deux à deux : le réseau finirait par mesurer l'appartenance au groupe. D'où le
  plafond de signataires, réglé bas par défaut ;
- **les amendements de rapporteur.** Deux co-rapporteurs d'un même texte
  cosignent des dizaines d'amendements rédactionnels, quels que soient leurs
  groupes. Sans filtre, la paire la plus « affine » de l'Assemblée est une
  députée EPR et une députée LFI qui ont corédigé un texte — 94 amendements
  communs, pour l'essentiel intitulés « Rédactionnel ». C'est un rôle
  institutionnel, pas une alliance.

Enfin, l'affinité entre deux députés est mesurée par l'**indice de Jaccard** —
`communs / (total_A + total_B − communs)` — et non par le compte brut, qui ne
remonterait que les plus prolifiques.

Détail dans [`03_reseau_de_cosignatures`](notebooks/03_reseau_de_cosignatures.ipynb).

## 4. Un sujet qui monte : à volume de documents égal

**La question.** « De quoi débat-on cette semaine ? »

**Le piège.** Le corpus hebdomadaire ne fait pas du tout la même taille d'une
semaine à l'autre : un seul texte peut produire des centaines d'amendements, et
une semaine de vacances parlementaires en produit zéro. Comparer les
occurrences **brutes** d'une semaine chargée à celles d'une semaine creuse
revient à mesurer le calendrier plutôt que le débat — n'importe quel terme
banal remonte comme « sujet qui monte » simplement parce qu'il y avait plus de
texte à lire.

**Ce qu'on fait.** Le score compare des **taux**, en deux temps :

1. **le taux de référence** — combien de fois le terme apparaît *par document*,
   sur les semaines précédentes prises ensemble ;
2. **l'attendu** — ce taux multiplié par le nombre de documents de la semaine
   analysée. C'est ce qu'on devrait observer si rien n'avait changé, *à volume
   de la semaine*.

Le score est ensuite de type Poisson : `(observé − attendu) / √(attendu + 1)`.
Le dénominateur est là parce qu'un comptage fluctue d'autant plus qu'il est
grand — passer de 100 à 130 est banal, passer de 2 à 32 ne l'est pas. Le `+ 1`
empêche un terme quasi inédit d'obtenir un score infini. La sortie affiche
`n`, `attendu` et `n_documents` côte à côte, pour qu'on puisse toujours voir
sur quel volume la comparaison est faite.

**Trois familles de mots sont écartées, pour trois raisons différentes** — et
les confondre rendait la liste impossible à maintenir :

| Famille | Pourquoi | Exemples |
|---|---|---|
| `MOTS_VIDES` | aucun sens propre | de, les, avec, plus |
| `JARGON_LEGISTIQUE` | syntaxe obligée de tout amendement | alinéa, substituer, rédiger, visant |
| `TERMES_PROCEDURAUX` | désignent des étapes, pas des sujets | commission, séance, mixte paritaire |

La troisième est la plus intéressante. Ces termes désignent de vraies choses,
mais des étapes du parcours d'un texte : une commission mixte paritaire qui se
réunit fait bondir « mixte paritaire » sans que rien ne se soit passé dans le
débat public. Elle est volontairement **étroite** : « public » n'y figure pas,
parce que « service public » est un sujet ; « vote » non plus, à cause du
« droit de vote ».

**Deux nettoyages complètent le dispositif :**

- **les noms de députés sont filtrés** (depuis la table `deputes`) : les titres
  de scrutins nomment l'auteur, et sans ce filtre un député qui dépose trente
  amendements dans la semaine devient un « sujet » ;
- **les n-grammes redondants sont fusionnés** : un texte discuté sur vingt-quatre
  scrutins fait remonter tous les fragments de son titre avec exactement le même
  effectif — « corse », « corse autonome », « constitutionnelle corse »… C'est
  un sujet, pas huit.

**Ce que ça change.** Sur la semaine du 20 juillet 2026, le classement était
mené par « mixte paritaire » — une procédure. Il l'est maintenant par
« protection », qui renvoie aux deux textes réellement débattus cette
semaine-là (protection des mineurs en ligne, protection de l'enfance).

`topics.TERMES_PROCEDURAUX` et ses deux voisines restent le levier de réglage
principal : si un terme parasite revient, c'est là qu'il se retire.

## 5. Trois dénominateurs qui ne vont pas de soi

**Un absent n'est ni d'accord ni en désaccord.** L'accord entre deux députés est
calculé sur les seuls scrutins où *les deux* se sont prononcés, et les paires
partageant moins de trente scrutins sont écartées — sinon on remonte des
« 100 % d'accord » établis sur trois votes.

**Le dénominateur de la participation n'est pas le nombre de scrutins.** Il faut
retirer les scrutins antérieurs à l'arrivée du député, ainsi que les non-votes
structurels (membre du Gouvernement, président de séance) qui ne sont pas des
absences. D'où la matrice d'éligibilité, construite depuis les dates de mandat.

**Un « courtier » n'est pas celui qui cosigne le plus hors de son groupe.** La
part brute ne se compare pas d'un député à l'autre : un membre d'un groupe de
17 a 96 % de l'Assemblée « hors de son groupe », contre 79 % pour un membre
d'un groupe de 122. Classer sur la part brute revient à classer les groupes par
petite taille — c'est ce que faisait une première version, dont le palmarès
était intégralement occupé par le plus petit groupe. On rapporte donc la part
observée à la part **attendue** sous mélange aléatoire.

## 6. Décrire ou modéliser : les deux, pour des usages différents

L'ACP (`analyze.carte_politique`) reste l'outil par défaut pour une carte : elle
est instantanée, sans réglage, et rien ne peut s'y casser. Le modèle de points
idéaux (`ideal.estimer`) coûte plus cher et demande un réglage, mais donne trois
choses que l'ACP ne peut pas donner, parce qu'elle ne modélise rien :

1. **une incertitude par député** — l'intervalle médian fait un dixième de
   l'étendue de l'axe, de quoi rendre indiscernables des dizaines de députés
   qu'un classement brut aurait séparés ;
2. **une lecture de chaque scrutin** — ce vote a-t-il activé le clivage
   principal, ou l'a-t-il traversé ? ;
3. **un test du nombre de dimensions**, hors échantillon. Réponse pour la 17ᵉ
   législature : deux dimensions se justifient, trois non. L'axe 1 oppose LFI au
   reste, l'axe 2 le RN au reste — deux oppositions systématiques distinctes,
   qui ne portent pas sur les mêmes textes et ne peuvent donc pas tenir sur une
   seule ligne.

Les deux méthodes classent les députés de façon quasi identique — corrélation
de Pearson 0,95, de Spearman 0,93. Elles ne se contredisent pas ; elles
n'autorisent simplement pas les mêmes affirmations. Détail dans
[`04_points_ideaux`](notebooks/04_points_ideaux.ipynb).

## 7. L'abstention n'est pas un vote flou

5,7 % des suffrages exprimés, mais 1,6 % pour EPR contre 8,9 % pour UDDPLR :
s'abstenir est un luxe d'opposition et de groupe charnière, pas une hésitation
répartie au hasard.

- **72 % des abstentions suivent une consigne de groupe** — c'est un instrument
  collectif, pas un flottement individuel. 18 % sont des retraits individuels, et
  les 10 % restants sont indéterminés : le groupe n'avait ce jour-là aucune ligne,
  et il n'y a donc ni consigne à suivre ni consigne à quitter. Les trois parts
  somment à 1. Et une consigne d'abstention est moins suivie qu'une consigne de
  vote : 88 % contre 96 %.
- **Elle est intermédiaire dans 71 % des scrutins.** Replacés sur l'axe des
  points idéaux estimé *sans eux*, les abstentionnistes tombent entre les deux
  camps, à mi-chemin en médiane.
- **Elle a tenu l'issue de dix votes sur l'ensemble d'un texte** — ceux où le
  nombre d'abstentions dépasse l'écart entre les camps. Le plus serré : un
  budget adopté par 217 voix contre 213, avec 84 abstentions, dont 59 du groupe
  socialiste et 20 des écologistes.

Détail dans [`05_abstention`](notebooks/05_abstention.ipynb).

---

# Ce que le radar ne mesure pas

Les limites connues, écrites ici pour qu'on n'ait pas à les redécouvrir.

**Le périmètre exact est « scrutins publics et dépôts d'amendements ».** La
participation aux scrutins ne mesure ni le temps de présence, ni le travail en
commission, ni le travail en circonscription. Les interventions en séance,
questions au Gouvernement et rapports ne sont pas couverts.

**15,4 % des suffrages exprimés le sont par délégation.** Le champ
`par_delegation` est conservé dans la table `votes`, mais aucune analyse ne le
distingue encore : un vote émis par un collègue mandaté compte comme un vote
personnel, y compris dans les classements de dissidence individuelle.

**Le groupe affiché est le groupe actuel, pas celui du jour du vote.** La table
`votes` porte bien `groupe_uid` au moment du scrutin — les lignes de groupe et
la dissidence sont donc correctes historiquement. Mais les agrégats qui passent
par `deputes.groupe` (accord entre groupes, cohésion, cosignatures) rattachent
un vote passé au groupe actuel de son auteur, ce qui concerne 1,5 % des votes.
Par ailleurs `en_exercice_seulement=True` est le défaut : **71 députés et 4,7 %
des votes** sont écartés des analyses, y compris de mesures de cohésion qui
portent justement sur des groupes ayant perdu des membres.

**Les scrutins ne sont pas rattachés à un dossier législatif.** Le champ
`objet.dossierLegislatif` est nul dans les 8 434 fichiers source : ce n'est pas
un défaut de parsing, la donnée n'y est pas. Y remédier demande d'ingérer le
jeu *Dossiers législatifs* de l'Assemblée, ou de rattacher par titre. Les
amendements, eux, portent déjà `texte_legislatif_uid` à 100 % : une unité
« texte » est disponible de ce côté-là.

**Compter les amendements ne mesure pas l'influence législative.** Le volume
favorise la quantité, les dépôts collectifs et parfois l'obstruction. Distinguer
dépôt, retrait, rejet, adoption et reprise par le Gouvernement reste à faire.

**Les alertes ne tiennent pas compte du calendrier de séance.** Une semaine sans
séance apparaît « calme » alors qu'il ne s'est simplement rien tenu. Les seuils
sont fixes et ne corrigent ni la multiplicité des tests, ni la corrélation entre
les votes d'un même texte.

---

# Les données

| Table | Contenu | Volume |
|---|---|---|
| `deputes` | état civil, groupe, circonscription, dates de mandat | 648 |
| `scrutins` | un scrutin public par ligne, avec son sort et sa portée | 8 434 |
| `votes` | (scrutin × député) → position, groupe du jour, délégation | 1 270 476 |
| `positions_groupe` | ligne de chaque groupe, **recalculée**, et ses trois effectifs | 91 996 |
| `amendements` | amendements déposés, auteur, **cosignataires**, sort | 123 224 |
| `organes`, `mandats` | groupes, commissions, historique des mandats | — |

Volumes constatés sur la 17ᵉ législature au 11 août 2026. Tout est stocké en
Parquet dans `data/tables/`, reconstruit par `radar build`.

Le téléchargement est conditionnel (`If-Modified-Since`) : l'Assemblée republie
ses archives plusieurs fois par jour, on ne retélécharge que ce qui a changé.

# Les notebooks

| Notebook | Ce qu'il établit |
|---|---|
| [`01_prise_en_main`](notebooks/01_prise_en_main.ipynb) | La visite guidée : proximités, cohésion, carte, sujets, amendements, alertes. |
| [`02_portee_des_scrutins`](notebooks/02_portee_des_scrutins.ipynb) | Tous les votes ne se valent pas. L'accord LFI↔SOC passe de 79 % à 63 % selon l'enjeu du scrutin, RN↔DR de 67 % à 86 %. Avec contre-épreuve sur la taille d'échantillon. |
| [`03_reseau_de_cosignatures`](notebooks/03_reseau_de_cosignatures.ipynb) | Voter ensemble n'est pas travailler ensemble. LFI et ECOS votent à 88 % et échangent 0,3 % de leurs cosignatures ; DR et UDDPLR votent à 74 % avec zéro amendement commun. |
| [`04_points_ideaux`](notebooks/04_points_ideaux.ipynb) | Décrire ou modéliser. Ce qu'un modèle permet d'affirmer que l'ACP ne permet pas : incertitude, lecture des scrutins, test de dimensionnalité. Deux dimensions se justifient, trois non. |
| [`05_abstention`](notebooks/05_abstention.ipynb) | Ce qu'on avait écarté. L'abstention est une consigne de groupe dans 72 % des cas, une position intermédiaire dans 71 % des scrutins — et elle a tenu l'issue de dix votes sur l'ensemble d'un texte. |

Les notebooks 02 à 05 forment une suite : chacun montre qu'une réponse
apparemment solide reposait sur un **choix invisible** — la population de
scrutins (`02`), la relation mesurée (`03`), la méthode elle-même (`04`), les
données écartées (`05`). Ils sont écrits en hypothèse → méthode → résultat →
contre-épreuve, et documentent les biais rencontrés plutôt que les seules
conclusions. Le `05` corrige d'ailleurs une affirmation du `04` : la suite est
faillible, et le dit.

Ils sont **générés**, pas édités à la main :

```bash
uv run python notebooks/_generer.py
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/0*.ipynb
```

# Les couleurs des graphiques

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

# Structure

```
src/radar/
    config.py     sources open data, chemins, vocabulaire des positions
    fetch.py      téléchargement conditionnel + décompression
    parse.py      JSON de l'AN → tables Parquet
    analyze.py    accord, cohésion, ligne de groupe, participation, carte
    topics.py     corpus hebdomadaire et détection des poussées
    alerts.py     détecteurs d'anomalies de la semaine
    cosign.py     réseau de cosignatures d'amendements
    ideal.py      modèle de points idéaux (IRT à deux paramètres)
    abstention.py l'abstention comme objet d'analyse
    viz.py        graphiques matplotlib
    pdf.py        rendu PDF du bulletin (reportlab)
    vues.py       la frontière : données prêtes à afficher, aucune mise en page
    cli.py        interface en ligne de commande
site/
    generer.py    orchestration : quel gabarit, quel jeton, quel fichier
    redaction.py  les phrases (aucune importation, aucun calcul)
    serveur.py    le mode développement : sert sortie/ comme le fera l'hébergeur
    gabarits/     la structure des pages, jetons {{...}}
    statique/     système visuel et comportement (CSS, JS)
    sortie/       produit, jetable, régénéré à chaque fois
notebooks/
    _generer.py                  source des cinq notebooks
    01_prise_en_main.ipynb
    02_portee_des_scrutins.ipynb
    03_reseau_de_cosignatures.ipynb
    04_points_ideaux.ipynb
    05_abstention.ipynb
tests/
```

```bash
uv run pytest
```

Les tests ne vérifient pas seulement que le code tourne : chacun **verrouille un
biais déjà rencontré**, et son docstring dit lequel. Les parts de cosignature
d'un groupe totalisent 1 ; une pluralité à 41 % ne fait pas ligne ; un député de
petit groupe ne devient pas « courtier » par la seule taille de son groupe.

# Automatiser

`radar update && radar rapport` en tâche hebdomadaire suffit à alimenter un
site, une newsletter ou un fil social. Le rapport sort en Markdown avec ses
figures dans `data/out/`.

# Pistes non couvertes

- **Interventions en séance.** Le compte rendu (dataset *Syceron*) permettrait
  d'ajouter le temps de parole aux classements. L'URL testée pour la 17ᵉ
  législature renvoie 404 ; le chemin exact reste à trouver.
- **Dossiers législatifs.** Rattacher chaque scrutin à son dossier donnerait des
  séries par texte plutôt que par mot-clé — plus robuste que la détection
  lexicale, et permettrait de pondérer par l'importance du texte au lieu des
  trois niveaux de portée actuels. Demande un jeu de données supplémentaire
  (voir « Ce que le radar ne mesure pas »).
- **Dynamique temporelle.** Tout est statique sur deux ans ; une fenêtre
  glissante dirait *quand* les alliances se sont nouées ou défaites, et
  lèverait l'hypothèse de positions fixes du modèle de points idéaux.
- **Un modèle ordonné** (pour / abstention / contre) pour réintégrer les
  abstentions dans le modèle de points idéaux. Le notebook `05` a établi
  qu'elles sont bien intermédiaires dans 71 % des scrutins : les exclure jette
  de l'information, et le modèle logistique binaire actuel ne sait pas les
  représenter.
- **API CIVIX.** Elle expose les mêmes données sous forme d'API REST
  (`https://www.civix.fr/api`). Le radar attaque directement les archives de
  l'Assemblée, ce qui évite toute dépendance à un tiers, mais CIVIX peut être
  utile pour des requêtes ponctuelles.

# Citer

Si vous utilisez le Radar parlementaire dans un travail académique, de recherche
ou professionnel, citez **la note méthodologique et le logiciel**. La première
spécifie les mesures et ne bouge qu'à la révision ; le second les calcule et
bouge à chaque version. Un article qui discute une définition cite la note ; un
travail qui rejoue les calculs cite les deux.

```bibtex
@techreport{desroziers2026radarnote,
  author      = {Desroziers, Sylvain},
  title       = {Mesurer l'activité de l'Assemblée nationale à partir de ses
                 données ouvertes : définitions, dénominateurs et incertitudes},
  institution = {Radar parlementaire},
  type        = {Note méthodologique},
  year        = {2026},
  url         = {https://radar-parlementaire.fr/methode/note-methodologique.pdf}
}

@software{desroziers2026radarlogiciel,
  author  = {Desroziers, Sylvain},
  title   = {Radar parlementaire : votes, amendements et sujets de
             l'Assemblée nationale},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/sdesrozis/radar},
  license = {AGPL-3.0-or-later},
  note    = {Données : open data de l'Assemblée nationale, Licence Ouverte 2.0}
}
```

Dans un article de presse, la forme courte suffit — mais **la date de calcul en
fait partie** : un taux se recalcule à chaque mise à jour de la source, et sans
sa date il n'est pas vérifiable.

> Radar parlementaire, Sylvain Desroziers, 2026. Calculs de l'auteur d'après
> l'open data de l'Assemblée nationale, données au JJ mois AAAA.
> https://radar-parlementaire.fr

Le fichier [`CITATION.cff`](CITATION.cff) porte les mêmes références au format
Citation File Format ; c'est lui qui alimente le bouton « Cite this repository »
de GitHub. Sa version doit rester égale à celle de `pyproject.toml`, dont le
site et les deux références tirent la leur.

# Sources et licence

Données : [open data de l'Assemblée nationale](https://data.assemblee-nationale.fr),
Licence Ouverte 2.0. Le radar ne modifie pas les données sources ; tous les
agrégats sont recalculés depuis le dépouillement nominatif et reproductibles
avec `radar build`.

Code sous **AGPL-3.0-or-later** ([`LICENSE`](LICENSE)). Note méthodologique sous
**CC BY 4.0**. Les chiffres publiés par le site sont libres de reprise avec leur
source et leur date de calcul.

Le site est édité par Sylvain Desroziers, sans financement, sans publicité et
sans lien avec un parti, un élu ou un groupe parlementaire — cf. la page
[« Qui édite ce site »](https://radar-parlementaire.fr/mentions.html). Les
erreurs trouvées dans les chiffres publiés sont inscrites au
[registre des corrections](https://radar-parlementaire.fr/corrections.html),
alimenté par [`site/corrections.toml`](site/corrections.toml).
