# Système visuel

Les jetons CSS en tête de `site/gabarits/fiche.html` font foi. Ce document dit
**pourquoi** ils sont ce qu'ils sont, pour qu'on ne les défasse pas par inadvertance.

## Le registre

Revue de données, pas portail institutionnel. Le site ne doit **jamais** ressembler à
assemblee-nationale.fr ni à un site de l'État : un site citoyen indépendant qui a l'air
officiel est un problème de confiance. La police *Marianne* est réservée à l'État — elle
est exclue.

Références de niveau visé : *Our World in Data*, *FT Visual & Data Journalism*,
*Les Décodeurs*.

## Couleur

| Jeton | Clair | Sombre | Emploi |
|---|---|---|---|
| `--papier` | `#F5F4EF` | `#131416` | fond |
| `--encre` | `#191A1C` | `#EDEBE4` | texte |
| `--encre-2` / `--encre-3` | `#5E5F5C` / `#8C8D88` | `#9E9F99` / `#74756F` | texte secondaire, étiquettes |
| `--trait` / `--trait-fort` | `#DBD9D0` / `#C4C2B7` | `#2C2E32` / `#3E4045` | filets, bordures |
| `--accent` | `#33327A` | `#9C99F0` | le sujet de la page, un seul par écran |
| `--signal` | `#8A6B12` | `#D9B348` | **les pièges uniquement** |

**La règle qui prime sur tout : aucune couleur ne désigne un groupe politique.** Les
rouges de deux groupes de gauche, comme les trois bleus de droite, sont indistinguables
de façon fiable, daltonisme ou non. La couleur porte une grandeur ou une emphase ;
l'identité passe par le texte. L'indigo a été choisi parce qu'aucun parti français ne le
revendique.

Le thème sombre est redéfini trois fois — `@media (prefers-color-scheme: dark)`, puis
`:root[data-theme="dark"]` et `:root[data-theme="light"]` — pour que le bouton du
lecteur l'emporte dans les deux sens.

## Typographie

Trois familles, et chacune veut dire quelque chose :

- `--serif` (New York / Iowan Old Style / Charter) — **la voix**. Titres, thèse, phrases
  en clair, grands chiffres.
- `--sans` (SF Pro / système) — **l'interface**. Navigation, corps de texte courant,
  formulaires.
- `--mono` (SF Mono / Menlo) — **la provenance**. Dénominateurs, effectifs, jalons de
  section, étiquettes de bande.

Cette dernière association est la signature du site : *mono = vérifiable, dénombrable,
opposable*. Un dénominateur n'est jamais composé dans une autre famille.

> En production, auto-héberger les fontes en woff2 dans `web/` : la pile système donne
> New York sur Apple mais Georgia ailleurs. Candidats testés : Spectral, Source Serif 4,
> Newsreader.

## Mise en page

Deux pistes. Une colonne de lecture à `--lecture` (63ch) pour la prose ; une piste de
données qui occupe tout le `--cadre` (1140px) pour les bandes et les tableaux. C'est le
décalage entre les deux qui donne l'impression d'une page composée plutôt que rendue.

Les jalons de section (`.jalon`) portent toujours une information — « Mesure 2 — écarts à
la ligne du groupe ». Jamais un numéro décoratif.

**Piège de cascade à connaître** : `.socle`, `.mesure`, `.ecart`, `.pied` partagent
l'élément avec `.cadre`. Elles doivent utiliser `padding-block` et jamais `padding` ni
`margin` en raccourci, sous peine d'écraser silencieusement le centrage et les gouttières
de `.cadre`. Cette erreur a déjà été commise une fois.

## La bande des 577

Le dispositif central, et l'identité visuelle du site. Une barre par député en exercice,
la médiane en pointillé, le sujet en losange indigo au-dessus, et l'intervalle de
confiance en aplat quand la mesure en a un.

Mécanique : un `<path>` unique pour les 577 barres (léger, thémable, `vector-effect:
non-scaling-stroke`), un SVG en `preserveAspectRatio="none"` pour que l'échelle
horizontale suive la largeur — et **les étiquettes en HTML positionné par-dessus**,
jamais en `<text>` SVG, qui s'étirerait avec le tracé.

Elle se révèle en deux temps à l'entrée dans le champ : la population d'abord, l'individu
ensuite. C'est la thèse du site jouée en animation, et c'est la seule animation de la
page. `prefers-reduced-motion` la désactive entièrement.
