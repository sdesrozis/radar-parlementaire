---
name: site
description: Construire, étendre ou mettre à jour le site public du Radar parlementaire — gabarits HTML, générateur Python, textes pédagogiques. À utiliser dès qu'on touche à une page du site, qu'on ajoute une mesure, qu'on rédige un commentaire de chiffre, ou qu'on régénère le site après un `radar update`.
---

# Le site public du Radar parlementaire

## Régénérer, après une mise à jour des données

```bash
uv run radar update                        # retélécharge et reconstruit les tables
uv run python site/generer.py --servir     # 581 pages en ~20 s, puis les sert
python3 site/serveur.py                    # rouvrir sans recalculer, instantané
```

Le générateur appelle `Donnees.construire()` directement — aucun serveur n'est requis
pour produire les fichiers. `--servir` enchaîne sur `site/serveur.py`, qui sert
`sortie/` exactement comme le fera l'hébergeur : **c'est le seul mode de relecture
valable**, parce que la page carte charge un canvas et que `file://` ment sur le reste.

Options : `--bootstrap 0` (rapide, mais pas d'intervalles de confiance, donc pas de
bloc « position estimée ») et `--limite 5` (cinq fiches, pour une mise au point).

## Statique / dynamique : la frontière

```
src/radar/                ALGORITHMES  aucun HTML/CSS/JS — la CI le vérifie
src/radar/vues.py         FRONTIÈRE    données prêtes à afficher, zéro mise en page
site/statique/style.css   STATIQUE     système visuel — copié tel quel dans sortie/
site/statique/radar.js    STATIQUE     bande des 577 + recherche — copié tel quel
site/statique/carte.js    STATIQUE     la matrice des accords en canvas
site/gabarits/*.html      STATIQUE     structure + textes pédagogiques, jetons {{...}}
site/redaction.py         RÉDACTION    les phrases — aucune importation, aucun calcul
site/generer.py           ORCHESTRE    quel gabarit, quel jeton, quel fichier
site/sortie/              PRODUIT      jetable, régénéré à chaque fois
```

**Un calcul ne s'écrit jamais dans `site/`.** S'il manque un chiffre, il se calcule
dans `radar/` et s'expose par `vues.py`. C'est ce qui garde le paquet publiable seul.

**Un fichier de `statique/` ne doit jamais contenir de jeton `{{...}}`** : il est copié
sans substitution, et un jeton non remplacé casse tout le fichier. C'est arrivé une fois
avec `const DONNEES = {{DONNEES_JSON}};` laissé dans `radar.js` — erreur de syntaxe,
JavaScript mort, bandes et recherche muettes. Les données variables sont déclarées par
la page (`<script>const DONNEES = …</script>` en fin de gabarit), jamais par le statique.

## Les trois règles, qui ne se négocient pas

1. **Aucun chiffre n'est écrit à la main**, ni dans un gabarit, ni dans une phrase.
   Tout nombre visible vient d'un jeton rempli par `generer.py`.
2. **Aucune phrase figée quand elle porte un jugement relatif.** « au-dessus de la
   médiane » se calcule (`situer()`), « seuls N députés » se dose (`combien()` — le
   superlatif ne s'emploie qu'en deçà d'un dixième de l'Assemblée).
3. **Aucun taux sans son dénominateur**, dans le même bloc, en `--mono`.

## Les pièges déjà rencontrés — à ne pas refaire

**Le dénominateur de la phrase doit être celui du calcul.** Neuf députés en exercice
sont entrés en cours de législature : leur taux se calcule sur les scrutins où leur
mandat courait, pas sur 245. Toute phrase qui cite un total doit prendre
`a["engageants_eligibles"]`, jamais `n_texte`.

**Les accords en genre.** 243 des 648 députés sont des femmes. `accords(identite)`
donne il/elle, présent/présente, député/députée depuis la civilité. Toute phrase
nouvelle qui contient un pronom doit passer par là. Dans les gabarits, préférer une
tournure sans pronom personnel — le gabarit est le même pour tous.

**L'absence de donnée n'est pas un zéro.** Un député sans aucun vote nominatif
(`votes_exprimes == 0`) reçoit un texte qui décrit la lacune de la source. Publier
« 0 % de présence » serait l'accusation la plus grave du site, portée sur ce qui n'est
peut-être qu'un trou dans les données.

Ce piège s'était refermé sur le site lui-même : le chiffre de tête affichait
« 0,0 % » en corps 88 **au-dessus** du paragraphe qui explique qu'on préfère une case
vide à un zéro trompeur. Un grand chiffre passe donc par `grand_chiffre(valeur, unite)`,
qui rend un tiret cadratin quand la valeur est `None` — et c'est au générateur de
passer `None`, jamais `0`, quand la mesure n'existe pas.

**Ne jamais citer une étiquette qu'on ne peut pas sourcer.** Le bloc « position »
opposait « un groupe classé au centre » : vrai pour LIOT, faux pour les autres,
et invérifiable. Il oppose maintenant le raccourci « un député de {{GROUPE}} » à la
mesure, avec l'étendue réelle du groupe — factuel et calculé.

**« gauche » et « droite » ne décrivent jamais l'axe estimé.** On situe par rapport
aux médianes de groupes nommées, jamais par une étiquette politique.

## Ajouter une mesure à une fiche

1. Vérifier que le chiffre existe dans `radar/analyze.py` ou `radar/site.py`. Sinon il
   se calcule côté Python — jamais en JavaScript.
2. Ajouter une section `.mesure` au gabarit avec les **cinq éléments obligatoires** :
   le jalon, la valeur, la provenance en mono, la phrase en clair, la bande des 577.
3. Écrire `phrase_<mesure>()` dans `generer.py`. Elle doit brancher sur les données et
   donner le contexte de groupe quand il renverse la lecture.
4. Ajouter un `<details class="piege">` qui dit ce que la mesure écarte. Toujours.
5. `page()` s'arrête si un jeton du gabarit n'a pas de valeur — laisser cette garde.

## La note méthodologique voyage avec le site

`docs/note-methodologique.pdf` est compilé par LaTeX, hors du générateur, et **ignoré
par git** : un clone frais ne l'a pas. `generer.py` le copie dans
`sortie/methode/`, la page Méthode l'y lie et le sitemap l'annonce — s'il est là.

**Une note absente et une note périmée ne se valent pas.** L'absence est honnête :
l'encart disparaît, rien n'est promis. Le retard publie sous l'autorité du document de
référence une définition que le code n'applique plus, alors que la page Méthode affirme
que « si le site et la note divergent, c'est le site qui a tort ». La génération
s'arrête donc quand le PDF est plus ancien que son `.tex`, avant les trente secondes de
calcul, et donne la commande. `--note-perimee` passe outre.

**Toute modification d'une définition se fait dans les deux endroits, le même jour** :
`radar/` et le `.tex`. Puis on recompile.

```bash
cd docs && latexmk -pdf note-methodologique.tex
```

## Contrôler après génération

```bash
cd site/sortie
grep -l "{{" *.html                        # jetons orphelins : doit être vide
grep -l ">None\|nan %" *.html              # valeurs manquantes visibles : vide
```

Vérifier aussi la **répartition des angles de thèse** : `phrase_these()` choisit
l'angle le plus saillant, et une répartition saine tourne autour de 30/30/20/20 entre
dissidence, discipline, présence forte et présence faible. Si un angle dépasse la
moitié, le site produit 577 fiches qui se ressemblent.

## Écrire et dessiner

`references/charte-editoriale.md` avant de rédiger une phrase.
`references/systeme-visuel.md` avant de toucher au CSS. Les jetons font foi en tête de
`site/statique/style.css`. Aucune couleur ne désigne un groupe politique.

## État actuel

Fait : fiche de député (577) — **bilan d'activité en tête** (trois assiettes de
scrutins, leurs dénominateurs et leur présence), **quatre mesures** (présence,
**délégation**, écart à la ligne, position estimée), **le relevé des 245 votes qui
engagent** écrit dans la page et filtrable, et **les amendements déposés** avec la
ventilation de leurs sorts —, accueil pédagogique **ouvert par la recherche**, annuaire
avec recherche par nom, département, numéro, région et groupe **plus l'index HTML des
577 par département**, **carte « Qui vote avec qui ? »** (matrice des 164 451 paires en
canvas, paire épinglable au clic, au tactile, au clavier ou par le nom, **et la
délégation à l'échelle de l'Assemblée et des groupes**), **page Méthode** avec le bloc
« comment citer », **page Mentions** (éditeur, hébergeur, financement, licences,
**maxime**), **registre des corrections**, **barre de navigation mobile**, **verrou
complet en tête et en pied** en deux versions dessinées.

**Le relevé coûte 95 Ko par fiche** — 245 lignes de titres de scrutins, 79 Mo pour le
site contre 22 Mo avant lui. C'est le prix de la pièce justificative, et il se paie en
balisage nu : les lignes du relevé n'ont qu'une classe, tout le reste se style par
sélecteur d'élément. Y ajouter un attribut coûte une centaine de kilo-octets.

À faire, dans cet ordre : la recherche par **commune** (l'open data ne publie pas la
composition des circonscriptions — il faut une table externe), la page « cette semaine »
branchée sur `radar/alerts.py`, les pages scrutin et groupe, le glossaire, l'image
OpenGraph dédiée (le logo y sert de solution d'attente), puis les adresses lisibles
(`/deputes/prenom-nom-departement-n` plutôt que `PA1008.html`, avec la table de
redirections que ça impose).

## Le registre des corrections

`site/corrections.toml` est une **donnée**, pas du code : une entrée s'ajoute sans
toucher au générateur, et son format est documenté en tête du fichier.

**Une correction s'écrit au moment du correctif, dans le même commit.** Le pied de page
annonce le nombre d'entrées et la page Méthode y renvoie : un registre en retard se voit.
`tests/test_corrections.py` vérifie que chaque entrée est complète, que sa portée est
l'une des trois, que sa date est passée, que son avant/après est chiffré ou explicité, et
que son empreinte désigne un commit qui existe.

Ce qui n'y entre pas : les erreurs de la source — quand l'Assemblée corrige un scrutin,
nos chiffres changent au recalcul suivant sans que ce soit notre erreur — et les ajouts
de mesure ou de page, que le journal du dépôt porte déjà.

## Ce que `sortie/` ne garde pas

`generer.py` **efface les HTML de la racine** avant d'écrire les nouveaux. Une page
retirée du site restait sinon dans `sortie/` et repartait chez l'hébergeur : c'est ainsi
qu'une page « Soutenir » abandonnée est restée en ligne, sans lien entrant, absente du
plan du site, avec un appel au don que plus rien ne soutenait.

Une page se déclare donc **à trois endroits** : elle s'écrit dans `main()`, elle entre
dans `ecrire_index_moteurs()`, et elle reçoit un lien depuis le pied de page de
`base.html`. Une page qui manque au deuxième point n'est pas trouvable ; au troisième,
elle est orpheline — ce qui revient au même.

## Métadonnées : ce qui doit être absolu

`page()` exige un `chemin` : il alimente `<link rel="canonical">` et `og:url`. **Une URL
d'aperçu de lien ne peut pas être relative** — les robots qui fabriquent les vignettes
lisent la balise hors de tout document et n'ont aucune base pour la résoudre.
`og:image` était `statique/logo.png` et partait donc sans image sur les 581 pages ; il
passe par `{{BASE_URL}}`.

## La marque

`site/assets/` porte les images de marque ; `generer.py` copie ses fichiers de premier
niveau dans `sortie/statique/` (`assets/sources/` reste sur place : les originaux portent
le bloc-marque de l'État). Chaque image existe en deux versions **dessinées** — `-clair`
pour le papier clair, `-sombre` pour le papier sombre —, jamais un filtre : la version
précédente éclaircissait le bleu d'encre au `brightness` et délavait aussi le drapeau.

`marque-v2` est le verrou complet : emblème, mot-marque, barre tricolore et signature.
Il sert **en tête et en pied**, en un seul et même dessin. L'en-tête a composé sa marque
en HTML pendant plusieurs versions — l'emblème recadré, puis « RADAR » en mono espacée et
« parlementaire » en italique — et le site portait donc deux marques légèrement
différentes selon l'endroit où on la regardait.

`embleme-v2` est l'emblème seul recadré au carré. Il ne sert plus que de **favicon** :
à 32 pixels, le verrou complet est une bouillie. `partage-v2` est la carte OpenGraph
1200×630, à fond plein — un PNG transparent disparaîtrait sur une plateforme qui compose
sa vignette sur fond sombre.

**Le verrou ne descend pas sous 165 px de large.** En dessous, la barre tricolore et
« Ce que fait votre député » se referment en deux filets gris : ce n'est plus un verrou,
c'est un mot-marque mal recadré. D'où 196 px en tête sur écran large (barre de 108 px) et
168 px sous 720 px (barre de 92 px). Le CSS le note à l'endroit où il pourrait être réduit.

**La maxime est sur « Qui édite ce site », et là seulement.** Elle a été essayée sous le
logo du pied, où elle apparaissait sur les 581 pages à côté d'un taux de présence : une
adresse au lecteur répétée à ce point devient un slogan de bas de page. Elle est dite une
fois, sur la seule page qui explique pourquoi le site existe, et adossée à l'article 15 de
la Déclaration de 1789 cité en dessous — le droit de demander des comptes est écrit, il se
rappelle, il ne se revendique pas. C'est le seul endroit du site qui s'adresse au lecteur ;
les autres pages comptent.

**Le bleu et le rouge du logo n'entrent dans aucun graphique.** Ils sont déclarés dans
`style.css` (`--marque`, `--drapeau-bleu`, `--drapeau-rouge`) uniquement pour être nommés
et cantonnés : un lecteur qui verrait ces deux couleurs sur une mesure aurait raison de
croire qu'elles désignent des camps. Le pied de page le dit explicitement.

## Une seule recherche

`recherche()` dans `radar.js` sert l'accueil **et** l'annuaire : deux implémentations
donneraient deux réponses au même mot. Les pages ne diffèrent que par `data-limite` et
`data-vide="masquer"`. Les trois signaux (présence, écart au groupe, position) sont mis
en forme **côté Python** dans `Site.index_annuaire()` — le JavaScript ne fabrique ni
pourcentage ni virgule décimale, et n'a pas à savoir qu'un taux manquant n'est pas zéro.

## La carte des accords

`Donnees.matrice_accords()` renvoie le triangle supérieur strict : accord quantifié sur
un octet, scrutins communs sur deux, en base64. 460 Ko gzippés incrustés dans la page —
la pré-compression n'aide pas, le base64 annule le gain.

Deux pièges déjà rencontrés, à ne pas refaire :

**`const` dans un script classique ne crée pas de propriété sur `window`.** `carte.js`
lisait `window.DONNEES_CARTE`, obtenait `undefined` et sortait en silence : canvas vide,
aucune erreur en console. On lit l'identifiant nu gardé par `typeof`, comme `radar.js`
le fait pour `DONNEES`.

**Le bas de la rampe est un indigo pâle, jamais le gris du papier.** Sinon une case à
0 % d'accord et une case *non mesurable* sont deux gris voisins — le site afficherait
une absence de donnée comme un zéro, ce qu'il reproche à tout le monde. L'absence se
distingue par la teinte (grise, achromatique), pas par la clarté.
