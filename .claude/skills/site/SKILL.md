---
name: site
description: Construire, étendre ou mettre à jour le site public du Radar parlementaire — gabarits HTML, générateur Python, textes pédagogiques. À utiliser dès qu'on touche à une page du site, qu'on ajoute une mesure, qu'on rédige un commentaire de chiffre, ou qu'on régénère le site après un `radar update`.
---

# Le site public du Radar parlementaire

## Régénérer, après une mise à jour des données

```bash
uv run radar update                        # retélécharge et reconstruit les tables
uv run python site/generer.py              # 579 pages en ~20 s
open site/sortie/index.html
```

Une seule commande, **aucun serveur** : le générateur appelle `Donnees.construire()`
directement. Pour servir le dossier et tester le JavaScript en conditions réelles :

```bash
python3 -m http.server 8899 --directory site/sortie
```

Options : `--bootstrap 0` (rapide, mais pas d'intervalles de confiance, donc pas de
bloc « position estimée ») et `--limite 5` (cinq fiches, pour une mise au point).

## Statique / dynamique : la frontière

```
site/statique/style.css   STATIQUE  système visuel — copié tel quel dans sortie/
site/statique/radar.js    STATIQUE  bande des 577 + recherche — copié tel quel
site/gabarits/*.html      STATIQUE  structure + textes pédagogiques, jetons {{...}}
site/generer.py           DYNAMIQUE chiffres, distributions, phrases choisies
site/sortie/              PRODUIT   jetable, régénéré à chaque fois
```

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

Fait : fiche de député (577), accueil pédagogique, annuaire avec recherche par nom,
département, numéro, région et groupe.

À faire, dans cet ordre : la recherche par **commune** (l'open data ne publie pas la
composition des circonscriptions — il faut une table externe), la page « cette semaine »
branchée sur `radar/alerts.py`, les pages scrutin et groupe, le glossaire, la page
Méthode, puis la publication (nom de domaine, hébergement, polices auto-hébergées,
images OpenGraph).
