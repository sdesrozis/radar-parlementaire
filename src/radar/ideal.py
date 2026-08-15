"""Points idéaux : où siège chaque député sur l'axe que les votes révèlent.

**Ce module ne remplace pas l'ACP de `analyze.carte_politique`, il la
complète.** Les deux répondent à des questions différentes, et la bonne
pratique est de les lire ensemble.

L'ACP *décrit* : elle résume la matrice des votes en quelques axes, sans rien
supposer. C'est rapide, robuste, sans réglage, et parfait pour une première
carte — « qui est près de qui ». C'est ce que fait le notebook `01`, et c'est
suffisant pour la plupart des usages.

Le modèle de points idéaux *explique* : il pose une hypothèse sur la façon dont
un vote est produit, puis estime les quantités qu'implique cette hypothèse.
Cela coûte plus cher — un modèle, des réglages, un risque de surajustement —
mais donne trois choses que l'ACP ne peut pas donner, parce qu'elle ne modélise
rien :

1. **Une incertitude par député.** L'ACP renvoie un point ; le modèle permet un
   intervalle. Sans lui, on classerait deux députés séparés par un centième
   d'écart-type comme si la différence était réelle.
2. **Une lecture de chaque scrutin.** Le modèle attribue à chaque vote une
   direction et une force de séparation : ce scrutin a-t-il activé le clivage
   principal, ou l'a-t-il traversé ? L'ACP ne dit rien des scrutins.
3. **Un test du nombre de dimensions.** L'ACP donne des pourcentages de
   variance, qui ne se testent pas. Le modèle permet de demander : une seconde
   dimension prédit-elle mieux **des votes qu'elle n'a pas vus** ?

**Le modèle.** Celui de Clinton, Jackman et Rivers (2004), équivalent à un
modèle de réponse à l'item à deux paramètres :

    P(le député i vote « pour » au scrutin j) = logistique(βⱼ · xᵢ − αⱼ)

- `xᵢ` est le **point idéal** du député i : sa position dans un espace de
  dimension `d`. C'est ce qu'on cherche.
- `βⱼ` est la **discrimination** du scrutin j : la direction et la force avec
  lesquelles ce vote sépare l'Assemblée. Un scrutin consensuel a un β proche de
  zéro, un scrutin clivant un β élevé.
- `αⱼ` est la **difficulté** : elle situe le point de bascule du vote.

Une image pour fixer les idées : imaginez les députés rangés sur une ligne. Un
scrutin trace une frontière sur cette ligne — à gauche on vote « contre », à
droite « pour ». `αⱼ/βⱼ` dit **où** passe la frontière, `βⱼ` dit **à quel point
elle est nette** : un β élevé donne une coupure franche, un β faible un dégradé
où la position ne prédit presque rien. Estimer le modèle revient à chercher le
rangement des députés qui rend le plus grand nombre de scrutins explicables par
une frontière.

**L'estimation.** Moindres carrés repondérés itérés (IRLS) en alternance : à
positions fixées, on ajuste les paramètres de chaque scrutin ; à paramètres de
scrutins fixés, on ajuste les positions. Les deux étapes sont vectorisées sur
l'ensemble des scrutins et des députés, ce qui rend le bootstrap abordable.

**Les abstentions sont exclues** — c'est la limite la plus coûteuse de ce
module, et la raison n'est pas celle qu'on croit.

Une première version justifiait cette exclusion en avançant qu'une abstention
n'est pas une position intermédiaire. Le module `abstention` a testé cette
affirmation en replaçant les abstentionnistes sur l'axe estimé sans eux : ils
se situent **entre les deux camps dans 71 % des scrutins**, à mi-chemin en
médiane. L'affirmation était donc fausse dans la majorité des cas.

La vraie raison est structurelle : le modèle logistique à deux paramètres est
binaire par construction, il n'a pas de troisième issue à prédire. Représenter
l'abstention demanderait un modèle ordonné à seuils. En attendant, il faut
savoir qu'on écarte une position qui porte de l'information — voir le notebook
`05`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .analyze import VoteCube, sous_cube


@dataclass
class ModeleIdeal:
    """Résultat d'une estimation : positions, paramètres de scrutins, ajustement."""

    deputes: pl.DataFrame
    scrutins: pl.DataFrame
    #: (n_députés × d) — les points idéaux.
    positions: np.ndarray
    #: (n_scrutins × d) — direction et force de séparation de chaque scrutin.
    discrimination: np.ndarray
    #: (n_scrutins,) — point de bascule de chaque scrutin.
    difficulte: np.ndarray
    dimensions: int
    log_vraisemblance: float
    #: Part des votes individuels correctement prédits.
    classification: float
    #: Réduction proportionnelle d'erreur agrégée, la mesure usuelle en la matière.
    apre: float
    n_votes: int
    #: Classification sur les votes mis de côté, si une validation a été demandée.
    classification_test: float | None = None
    apre_test: float | None = None
    n_votes_test: int = 0
    #: Votes bruts conservés (n × m), pour pouvoir superposer les votes réels
    #: aux courbes du modèle — c'est ce qui rend le modèle vérifiable à l'œil.
    votes_pour: np.ndarray | None = None
    votes_observes: np.ndarray | None = None

    def probabilites(self) -> np.ndarray:
        """P(vote « pour ») prédite pour chaque couple (député, scrutin)."""
        plan = np.column_stack([np.ones(len(self.positions)), self.positions])
        coefs = np.vstack([-self.difficulte, self.discrimination.T])
        return _sigmoide(plan @ coefs)

    def table_deputes(self) -> pl.DataFrame:
        """Députés avec leurs coordonnées, triés sur le premier axe."""
        cols = {
            f"axe{k + 1}": self.positions[:, k] for k in range(self.dimensions)
        }
        return self.deputes.select("acteur_uid", "nom_complet", "groupe").with_columns(
            **{k: pl.Series(v) for k, v in cols.items()}
        ).sort("axe1")

    def table_scrutins(self) -> pl.DataFrame:
        """Scrutins avec leur pouvoir de séparation et leur point de bascule."""
        norme = np.linalg.norm(self.discrimination, axis=1)
        cols = {
            f"beta{k + 1}": self.discrimination[:, k] for k in range(self.dimensions)
        }
        return self.scrutins.select(
            "scrutin_uid", "date", "categorie", "titre"
        ).with_columns(
            **{k: pl.Series(v) for k, v in cols.items()},
            pouvoir_separateur=pl.Series(norme),
            # Point de bascule sur le premier axe : la position à laquelle un
            # député a une chance sur deux de voter « pour ».
            bascule=pl.Series(
                np.divide(
                    self.difficulte,
                    self.discrimination[:, 0],
                    out=np.full_like(self.difficulte, np.nan),
                    where=np.abs(self.discrimination[:, 0]) > 1e-6,
                )
            ),
        )


# --------------------------------------------------------------------------
# Estimation
# --------------------------------------------------------------------------


def _sigmoide(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def _irls_par_colonne(
    X: np.ndarray, Y: np.ndarray, M: np.ndarray, B: np.ndarray,
    ridge: np.ndarray,
    decalage: np.ndarray | None = None,
) -> np.ndarray:
    """Un pas de Newton sur des régressions logistiques partageant le plan `X`.

    On ajuste `m` régressions indépendantes d'un coup : `X` est la matrice de
    plan commune (n × k), `Y` les réponses (n × m), `M` le masque des
    observations disponibles, `B` les coefficients courants (k × m).

    `ridge` est un vecteur d'une pénalisation par coefficient, et non un
    scalaire : la difficulté `αⱼ` ne doit pas être contrainte comme la
    discrimination `βⱼ`, sans quoi on empêcherait un scrutin d'être largement
    acquis. Voir `estimer` pour le rôle de ces pénalisations.

    `decalage` est un terme additif connu, non estimé. Il est indispensable
    pour l'étape qui ajuste les positions : la difficulté `−αⱼ` du scrutin y
    est déjà connue et doit entrer dans le prédicteur linéaire sans être
    réestimée. L'oublier revient à optimiser une autre fonction que l'étape
    précédente, et l'alternance cesse de converger.

    Les faire une par une coûterait des milliers d'appels Python ; les traiter
    en lot rend le bootstrap possible.
    """
    eta = X @ B
    if decalage is not None:
        eta = eta + decalage
    p = _sigmoide(eta)
    w = p * (1.0 - p) * M
    r = (Y - p) * M

    # Gradient (k × m) et hessienne empilée (m × k × k).
    g = X.T @ r - ridge[:, None] * B
    H = np.einsum("nk,nm,nl->mkl", X, w, X)
    H += np.diag(ridge + 1e-6)[None, :, :]

    pas = np.linalg.solve(H, g.T[:, :, None])[:, :, 0].T
    return B + pas


def _identifier(X: np.ndarray, ancrage: np.ndarray | None) -> np.ndarray:
    """Fixe l'échelle, la rotation et le sens, qui ne sont pas identifiés.

    Le modèle est invariant par translation, rotation et changement d'échelle :
    `βⱼ · xᵢ` reste inchangé si l'on tourne l'espace et que l'on tourne les β en
    sens inverse. Trois conventions lèvent l'ambiguïté :

    1. centrer et réduire les positions ;
    2. tourner sur les axes principaux, pour que l'axe 1 porte la plus grande
       dispersion ;
    3. orienter l'axe 1 pour que le groupe d'ancrage soit du côté négatif.

    Sans la troisième, deux exécutions successives peuvent renvoyer des cartes
    en miroir — ce qui n'est pas faux, mais illisible d'une fois sur l'autre.
    """
    X = X - X.mean(axis=0, keepdims=True)
    # Rotation sur les composantes principales.
    _, _, Vt = np.linalg.svd(X, full_matrices=False)
    X = X @ Vt.T
    ecart = X.std(axis=0, keepdims=True)
    X = X / np.where(ecart > 1e-9, ecart, 1.0)
    if ancrage is not None and ancrage.any() and X[ancrage, 0].mean() > 0:
        X[:, 0] *= -1
    return X


#: Groupe placé du côté négatif du premier axe, pour que deux estimations
#: successives se lisent dans le même sens. Un axe latent est défini au miroir
#: près : le retourner ne change ni les positions relatives, ni les intervalles,
#: ni ce que le modèle prédit.
#:
#: C'est **le seul endroit** où un nom de groupe touche ce calcul — aucun groupe
#: n'entre dans l'ajustement. La constante est nommée parce que la page Méthode
#: écrivait « le modèle ne sait pas qui siège dans quel groupe » sans réserve :
#: c'est vrai de l'estimation, faux de l'orientation, et le site doit pouvoir
#: citer le groupe d'ancrage plutôt que de laisser croire qu'il n'y en a pas.
GROUPE_ANCRAGE = "LFI-NFP"


def estimer(
    cube: VoteCube,
    *,
    dimensions: int = 1,
    iterations: int = 60,
    ridge_positions: float = 1.0,
    ridge_discrimination: float = 0.5,
    min_votes_depute: int = 10,
    min_votes_scrutin: int = 20,
    contestation_min: float = 0.025,
    groupe_ancrage: str = GROUPE_ANCRAGE,
    tolerance: float = 1e-5,
    part_test: float = 0.0,
    graine_test: int = 0,
) -> ModeleIdeal:
    """Estime les points idéaux sur les votes d'un cube.

    Args:
        dimensions: nombre d'axes. Commencer par 1, puis vérifier avec
            `evaluer_dimensionnalite()` si un second apporte quelque chose.
        ridge_positions: pénalisation des positions, équivalente à une loi a
            priori normale centrée réduite sur `xᵢ`. La valeur 1 correspond à
            l'échelle sur laquelle les positions sont normalisées.
        ridge_discrimination: pénalisation des `βⱼ`. **C'est le réglage
            critique.** Sur des votes sur l'ensemble d'un texte, beaucoup de
            scrutins sont parfaitement séparables — un groupe vote pour, un
            autre contre, sans exception. Le maximum de vraisemblance part alors
            à l'infini : `β` explose, la courbe devient une marche verticale, et
            les positions se tassent les unes sur les autres puisque leur écart
            n'a plus besoin d'être grand pour tout expliquer. Pénaliser `β`
            rend les courbes moins raides et redonne aux positions une échelle
            interprétable. La valeur retenue a été choisie par validation
            croisée, cf. `choisir_penalisation()`.
        contestation_min: écarte les scrutins quasi unanimes. Ils n'apportent
            aucune information de séparation et déstabilisent l'estimation.
        groupe_ancrage: groupe placé du côté négatif de l'axe 1, pour que deux
            exécutions donnent la même orientation.
        part_test: fraction des votes mise de côté pour valider le modèle sur
            des données qu'il n'a pas vues. Sans cela, l'ajustement se mesure
            là où le modèle a appris, et augmente mécaniquement à chaque
            paramètre ajouté — ce qui interdit de comparer les dimensions.
    """
    # Les abstentions ne sont pas des demi-votes : on ne garde que pour/contre.
    pour = cube.pour
    contre = cube.contre
    M = (pour | contre).astype(np.float64)
    Y = pour.astype(np.float64)

    if contestation_min > 0:
        n_pour = pour.sum(axis=0)
        n_contre = contre.sum(axis=0)
        total = np.maximum(n_pour + n_contre, 1)
        minorite = np.minimum(n_pour, n_contre) / total
        garde_s = minorite >= contestation_min
    else:
        garde_s = np.ones(cube.n_scrutins, dtype=bool)

    garde_s &= M.sum(axis=0) >= min_votes_scrutin
    garde_d = M[:, garde_s].sum(axis=1) >= min_votes_depute
    if garde_s.sum() < 2 or garde_d.sum() < 2:
        raise ValueError("trop peu de votes exploitables pour estimer un modèle")

    M = M[np.ix_(garde_d, garde_s)]
    Y = Y[np.ix_(garde_d, garde_s)]
    deputes = cube.deputes.filter(pl.Series(garde_d))
    scrutins = cube.scrutins.filter(pl.Series(garde_s))
    n, m = M.shape
    d = dimensions

    # Mise de côté d'une partie des votes, cellule par cellule. On retire des
    # votes individuels et non des scrutins entiers : retirer un scrutin
    # entier priverait le modèle de ses paramètres αⱼ et βⱼ, et il n'y aurait
    # plus rien à prédire dessus.
    M_test = np.zeros_like(M)
    if part_test > 0:
        rng = np.random.default_rng(graine_test)
        tirage = rng.random(M.shape) < part_test
        M_test = M * tirage
        M = M * (1 - tirage)

    # Départ : les composantes principales de la matrice signée, qui placent
    # déjà les députés à peu près correctement et accélèrent la convergence.
    S = np.where(M > 0, 2 * Y - 1, 0.0)
    S = S - S.mean(axis=0, keepdims=True)
    U, sv, _ = np.linalg.svd(S, full_matrices=False)
    X = U[:, :d] * sv[:d]
    X = X / np.maximum(X.std(axis=0, keepdims=True), 1e-9)

    ancrage = np.array(
        [g == groupe_ancrage for g in deputes["groupe"].to_list()], dtype=bool
    )

    B = np.zeros((d + 1, m))          # scrutins : [−α ; β]
    Xt = X.T.copy()                   # députés  : x, en (d × n)
    ll_precedent = -np.inf

    # La difficulté α n'est pas pénalisée : un scrutin peut légitimement être
    # acquis à 95 %, et le contraindre reviendrait à l'interdire.
    penalite_scrutins = np.array([0.0] + [ridge_discrimination] * d)
    penalite_positions = np.full(d, ridge_positions)

    for _ in range(iterations):
        # Étape 1 — paramètres des scrutins, positions fixées.
        # Plan : [1, x] ; coefficients estimés : [−α, β].
        Xd = np.column_stack([np.ones(n), X])
        B = _irls_par_colonne(Xd, Y, M, B, penalite_scrutins)

        # Étape 2 — positions, paramètres des scrutins fixés. Le rôle des deux
        # matrices s'échange : les β deviennent les variables explicatives, et
        # la difficulté −α entre comme décalage connu — pas comme paramètre.
        Xt = _irls_par_colonne(
            B[1:].T, Y.T, M.T, Xt, penalite_positions, decalage=B[0][:, None]
        )
        X = Xt.T

        # Échelle fixée à chaque tour. L'échelle des positions n'est pas
        # identifiée : doubler tous les x et diviser tous les β par deux donne
        # exactement la même vraisemblance. Laissée libre, l'estimation dérive
        # vers des positions très étalées et des β énormes — un modèle
        # quasi déterministe, numériquement fragile, et dont les positions
        # centrales finissent écrasées les unes sur les autres. On normalise
        # donc à chaque itération plutôt qu'une seule fois à la fin.
        X = X - X.mean(axis=0, keepdims=True)
        X = X / np.maximum(X.std(axis=0, keepdims=True), 1e-9)
        Xt = X.T.copy()

        eta = np.column_stack([np.ones(n), X]) @ B
        p = _sigmoide(eta)
        ll = float(np.sum(M * (Y * np.log(p + 1e-12) + (1 - Y) * np.log(1 - p + 1e-12))))
        if abs(ll - ll_precedent) < tolerance * max(abs(ll_precedent), 1.0):
            break
        ll_precedent = ll

    X = _identifier(X, ancrage)

    # Réajustement final des scrutins dans l'espace identifié, pour que les β
    # renvoyés correspondent bien aux positions renvoyées.
    Xd = np.column_stack([np.ones(n), X])
    B = np.zeros((d + 1, m))
    for _ in range(25):
        B = _irls_par_colonne(Xd, Y, M, B, penalite_scrutins)

    p = _sigmoide(Xd @ B)
    ll = float(np.sum(M * (Y * np.log(p + 1e-12) + (1 - Y) * np.log(1 - p + 1e-12))))
    correct, apre = _ajustement(Y, M, p)
    if part_test > 0:
        correct_test, apre_test = _ajustement(Y, M_test, p)
    else:
        correct_test = apre_test = None

    return ModeleIdeal(
        deputes=deputes,
        scrutins=scrutins,
        positions=X,
        discrimination=B[1:].T,
        difficulte=-B[0],
        dimensions=d,
        log_vraisemblance=ll,
        classification=correct,
        apre=apre,
        n_votes=int(M.sum()),
        classification_test=correct_test,
        apre_test=apre_test,
        n_votes_test=int(M_test.sum()),
        votes_pour=Y.astype(bool),
        votes_observes=(M + M_test).astype(bool),
    )


def _ajustement(Y: np.ndarray, M: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """Taux de classification correcte et APRE.

    L'APRE (*aggregate proportional reduction in error*) est la mesure usuelle
    pour ce type de modèle. Elle compare les erreurs du modèle à celles du
    prédicteur trivial « tout le monde vote comme la majorité » : un scrutin
    acquis à 95 % est facile à prédire sans rien comprendre, et ne doit pas
    valoir crédit au modèle. `APRE = 1` signifie aucune erreur, `0` signifie
    qu'on ne fait pas mieux que la majorité.
    """
    predit = (p >= 0.5).astype(np.float64)
    erreurs = ((predit != Y) * M).sum(axis=0)
    n_pour = (Y * M).sum(axis=0)
    n_contre = ((1 - Y) * M).sum(axis=0)
    minorite = np.minimum(n_pour, n_contre)

    correct = 1.0 - erreurs.sum() / max(M.sum(), 1)
    apre = (minorite.sum() - erreurs.sum()) / max(minorite.sum(), 1)
    return float(correct), float(apre)


# --------------------------------------------------------------------------
# Incertitude, dimensionnalité, lectures
# --------------------------------------------------------------------------


#: Colonnes de la table des scrutins pouvant servir de bloc de rééchantillonnage,
#: de la plus fine à la plus grossière. La première **entièrement** remplie gagne.
#:
#: `dossier_uid` est le bon niveau — les scrutins d'un même texte sont fortement
#: corrélés. L'Assemblée ne le renseigne que depuis mars 2026 : il couvre 2 606
#: des 2 608 scrutins postérieurs et 2 des 5 824 antérieurs. Une colonne
#: partiellement remplie est écartée, et non complétée au petit bonheur —
#: mélanger des blocs « dossier » et des blocs « un scrutin isolé » donnerait
#: des blocs de tailles incomparables, donc des intervalles faussés dans un
#: sens qu'on ne saurait pas nommer. `seance_uid` reste donc le regroupement
#: employé tant que la lacune n'est pas résorbée.
#:
#: Ce commentaire a dit pendant toute la vie du dépôt que la source ne
#: renseignait *jamais* ce champ. C'était faux : le parseur le perdait. Cf. la
#: note de `parse.SCRUTIN_SCHEMA` et le registre des corrections.
BLOCS_CANDIDATS = ("dossier_uid", "seance_uid", "date")


def blocs_de_scrutins(cube: VoteCube, cle: str | None = None) -> list[np.ndarray]:
    """Découpe les colonnes du cube en blocs de scrutins non indépendants.

    Renvoie une liste de tableaux d'indices de colonnes. Sans `cle`, on prend la
    première colonne de `BLOCS_CANDIDATS` réellement remplie ; `cle="aucun"`
    rend un bloc par scrutin, c'est-à-dire le bootstrap indépendant.
    """
    if cle == "aucun":
        return [np.array([j]) for j in range(cube.n_scrutins)]

    colonnes = [cle] if cle else list(BLOCS_CANDIDATS)
    for nom in colonnes:
        if nom not in cube.scrutins.columns:
            continue
        valeurs = cube.scrutins[nom].to_list()
        if any(v is None for v in valeurs):
            continue
        groupes: dict = {}
        for j, v in enumerate(valeurs):
            groupes.setdefault(v, []).append(j)
        return [np.array(v) for v in groupes.values()]
    return [np.array([j]) for j in range(cube.n_scrutins)]


def intervalles(
    cube: VoteCube,
    *,
    n_bootstrap: int = 60,
    dimensions: int = 1,
    graine: int = 0,
    niveau: float = 0.90,
    bloc: str | None = None,
    **kwargs,
) -> pl.DataFrame:
    """Intervalle de confiance sur la position de chaque député, par bootstrap.

    **Ce que ça règle.** Une position estimée sur 245 votes n'est pas un point,
    c'est une zone. Sans intervalle, on classerait deux députés séparés par un
    centième d'écart-type comme s'il s'agissait d'une différence réelle.

    **La méthode : un bootstrap par blocs.** On rééchantillonne les *scrutins*
    avec remise — et non les députés, car ce sont les scrutins qui constituent
    l'échantillon d'observations. Mais **les scrutins ne sont pas indépendants
    entre eux** : ceux d'une même séance portent sur le même texte, dans le même
    rapport de force, et se ressemblent bien plus que deux scrutins tirés au
    hasard dans la législature. Les tirer un par un revient à faire comme si
    l'échantillon comptait 245 observations indépendantes alors qu'il en compte
    173 paquets — et à publier des intervalles trop serrés, donc des
    classements trop assurés.

    On tire donc des **blocs entiers** avec remise, autant de blocs qu'il en
    existe. Le niveau de bloc est choisi par `blocs_de_scrutins` : le dossier
    législatif si la source le renseignait, la séance sinon.

    L'intervalle est le quantile empirique des positions obtenues. Le signe de
    l'axe étant fixé par ancrage à chaque réestimation, les tirages sont
    directement comparables.

    Args:
        bloc: colonne de regroupement. `None` choisit automatiquement,
            `"aucun"` restitue le bootstrap indépendant d'avant — utile pour
            mesurer de combien les intervalles s'élargissent.
    """
    rng = np.random.default_rng(graine)
    base = estimer(cube, dimensions=dimensions, **kwargs)
    ordre = {u: i for i, u in enumerate(base.deputes["acteur_uid"].to_list())}
    tirages = np.full((n_bootstrap, len(ordre)), np.nan)

    blocs = blocs_de_scrutins(cube, bloc)

    for b in range(n_bootstrap):
        choisis = rng.integers(0, len(blocs), len(blocs))
        colonnes = np.concatenate([blocs[i] for i in choisis])
        try:
            m = estimer(sous_cube(cube, colonnes), dimensions=dimensions, **kwargs)
        except (ValueError, np.linalg.LinAlgError):
            continue
        for u, x in zip(m.deputes["acteur_uid"].to_list(), m.positions[:, 0]):
            if u in ordre:
                tirages[b, ordre[u]] = x

    q = (1 - niveau) / 2
    # On repart de `base.deputes`, pas de `table_deputes()` : cette dernière est
    # triée par position, alors que `base.positions` et `tirages` suivent
    # l'ordre d'origine. Les mélanger décale silencieusement les valeurs.
    return (
        base.deputes.select("acteur_uid", "nom_complet", "groupe")
        .with_columns(
            axe1=pl.Series(base.positions[:, 0]),
            borne_basse=pl.Series(np.nanquantile(tirages, q, axis=0)),
            borne_haute=pl.Series(np.nanquantile(tirages, 1 - q, axis=0)),
            ecart_type=pl.Series(np.nanstd(tirages, axis=0)),
            # Le nombre de votes qui ont **réellement** servi à situer ce
            # député : ses « pour » et « contre » sur les seuls scrutins que les
            # filtres ont gardés. Ce n'est pas son nombre de votes sur les
            # scrutins qui engagent, et l'écart n'est pas anecdotique — les
            # abstentions et les motions de censure en sortent. Une fiche qui
            # annonce le second à côté d'une position estimée surestime la
            # matière dont le modèle a disposé ; cette colonne existe pour
            # qu'elle puisse annoncer le premier.
            votes_modele=pl.Series(
                base.votes_observes.sum(axis=1).astype(np.int64)
                if base.votes_observes is not None
                else np.zeros(base.deputes.height, dtype=np.int64)
            ),
        )
        .with_columns(
            (pl.col("borne_haute") - pl.col("borne_basse")).alias("largeur"),
            # L'assiette du modèle, portée en colonne constante comme
            # `carte_politique` porte son inertie : le site publie ces deux
            # nombres, et les recalculer ailleurs rouvrirait l'écart entre le
            # périmètre annoncé et le périmètre estimé.
            pl.lit(base.scrutins.height).alias("modele_scrutins"),
            pl.lit(cube.n_scrutins).alias("modele_scrutins_offerts"),
        )
        .sort("axe1")
    )


def evaluer_dimensionnalite(
    cube: VoteCube,
    *,
    max_dimensions: int = 3,
    part_test: float = 0.2,
    n_repetitions: int = 3,
    iterations: int = 150,
    **kwargs,
) -> pl.DataFrame:
    """Combien d'axes faut-il pour décrire cette Assemblée ?

    **Le piège.** On serait tenté de comparer les dimensions sur leur ajustement
    en échantillon. C'est sans valeur : ajouter une dimension ajoute 577
    paramètres, et l'ajustement en échantillon ne peut que s'améliorer. Mesuré
    ainsi, le modèle à deux dimensions classe 99 % des votes — un chiffre qui
    ne dit rien d'autre que « il y a assez de paramètres pour mémoriser ».

    **La méthode.** On met de côté une partie des votes, on estime le modèle
    sur le reste, et on mesure l'ajustement **sur les votes non vus**. Une
    dimension qui ne fait que mémoriser n'améliore rien hors échantillon ; une
    dimension qui capte un vrai second clivage améliore les deux.

    L'écart entre les deux colonnes est lui-même informatif : plus il se creuse,
    plus le modèle décrit du bruit.
    """
    lignes = []
    for d in range(1, max_dimensions + 1):
        for r in range(n_repetitions):
            m = estimer(
                cube, dimensions=d, part_test=part_test, graine_test=r,
                iterations=iterations, **kwargs,
            )
            lignes.append(
                {
                    "dimensions": d,
                    "tirage": r,
                    "apre_apprentissage": m.apre,
                    "apre_test": m.apre_test,
                    "classification_test": m.classification_test,
                    "n_parametres": m.positions.size
                    + m.discrimination.size
                    + len(m.difficulte),
                }
            )
    return (
        pl.DataFrame(lignes)
        .group_by("dimensions")
        .agg(
            pl.col("n_parametres").first(),
            pl.col("apre_apprentissage").mean(),
            pl.col("apre_test").mean(),
            pl.col("apre_test").std().alias("apre_test_ecart_type"),
            pl.col("classification_test").mean(),
        )
        .sort("dimensions")
        .with_columns(
            (pl.col("apre_apprentissage") - pl.col("apre_test")).alias("surajustement"),
            (pl.col("apre_test") - pl.col("apre_test").shift(1)).alias("gain_hors_echantillon"),
        )
    )


def choisir_penalisation(
    cube: VoteCube,
    valeurs: tuple[float, ...] = (0.02, 0.1, 0.5, 2.0, 8.0),
    *,
    dimensions: int = 1,
    part_test: float = 0.2,
    n_repetitions: int = 3,
    **kwargs,
) -> pl.DataFrame:
    """Choisit la pénalisation des βⱼ par validation croisée.

    **Pourquoi ce réglage ne peut pas être choisi à l'œil.** Sans pénalisation,
    l'ajustement en échantillon est toujours meilleur — donc « le modèle qui
    colle le mieux » est systématiquement le plus dégénéré. Le seul critère
    honnête est la capacité à prédire des votes non vus.

    La colonne `beta_max` montre l'enjeu concret : à pénalisation trop faible,
    les discriminations partent à plusieurs dizaines, ce qui signifie des
    courbes verticales, des positions tassées et une estimation fragile.
    """
    lignes = []
    for r in valeurs:
        for graine in range(n_repetitions):
            m = estimer(
                cube, dimensions=dimensions, ridge_discrimination=r,
                part_test=part_test, graine_test=graine, **kwargs,
            )
            lignes.append(
                {
                    "ridge_discrimination": r,
                    "apre_apprentissage": m.apre,
                    "apre_test": m.apre_test,
                    "beta_max": float(np.abs(m.discrimination).max()),
                }
            )
    return (
        pl.DataFrame(lignes)
        .group_by("ridge_discrimination")
        .agg(
            pl.col("apre_apprentissage").mean(),
            pl.col("apre_test").mean(),
            pl.col("beta_max").mean(),
        )
        .sort("ridge_discrimination")
    )


def pivots(modele: ModeleIdeal, k: int = 20) -> pl.DataFrame:
    """Les députés dont la position est la plus proche de la médiane de l'Assemblée.

    Sur un vote serré, ce sont eux qui font basculer le résultat : le théorème
    de l'électeur médian s'applique ici très concrètement, puisqu'un scrutin
    départage l'hémicycle en un point de l'axe et que ces députés se trouvent
    des deux côtés selon le texte.
    """
    x = modele.positions[:, 0]
    mediane = float(np.median(x))
    return (
        modele.table_deputes()
        .with_columns((pl.col("axe1") - mediane).abs().alias("distance_mediane"))
        .sort("distance_mediane")
        .head(k)
    )


def scrutins_discriminants(modele: ModeleIdeal, k: int = 15) -> pl.DataFrame:
    """Les scrutins qui séparent le plus nettement l'Assemblée selon l'axe estimé.

    Un β élevé signifie que connaître la position d'un député suffit presque à
    prédire son vote — le scrutin a activé le clivage principal. Un β faible
    signale au contraire un vote transversal, que l'axe n'explique pas.
    """
    return (
        modele.table_scrutins()
        .sort("pouvoir_separateur", descending=True)
        .head(k)
        .select("date", "categorie", "pouvoir_separateur", "bascule", "titre")
    )


def scrutins_transversaux(modele: ModeleIdeal, k: int = 15) -> pl.DataFrame:
    """Les scrutins que l'axe principal n'explique pas — les votes qui brouillent les camps."""
    return (
        modele.table_scrutins()
        .sort("pouvoir_separateur")
        .head(k)
        .select("date", "categorie", "pouvoir_separateur", "titre")
    )
