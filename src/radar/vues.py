"""La frontière entre les algorithmes et ce qui les affiche.

Ce module ne calcule rien que les autres ne calculent déjà : il assemble leurs
sorties en vues prêtes à afficher, et s'arrête là. Il ne sait pas ce qu'est une
page, un gabarit ni un serveur — c'est la condition pour que `radar` reste un
paquet d'analyse, installable et utilisable sans le site.

C'est aussi là que le piège se déplace : **une page web donne à un chiffre une
autorité que sa définition ne lui donne pas**. Affiché seul, « 78 % de
proximité » ressemble à une mesure ; il n'est qu'une moyenne sur un ensemble de
scrutins qu'on a choisi.

Trois précautions, en conséquence, sont câblées dans les données servies plutôt
que laissées à la mise en page :

1. **La proximité entre deux députés est toujours servie en double** — sur tous
   les scrutins, et sur les seuls votes qui engagent (`portee="texte"`). Ce sont
   deux nombres différents pour la même paire, et leur écart est l'information.
   Voir `analyze.comparer_portees`.
2. **La dissidence n'est comptée que là où une ligne existe.** Un groupe partagé
   n'a pas de ligne à enfreindre : ces scrutins sortent du dénominateur, comme
   dans `analyze.votes_vs_ligne`.
3. **Le point idéal est servi avec son intervalle**, jamais seul. Sans lui, le
   classement laisse croire à un ordre là où les zones se recouvrent.

Tout est calculé une fois par `Donnees.construire()` — une douzaine de secondes
— puis lu depuis la mémoire. Le sérialiseur (`lignes`, `_propre`) est ici parce
qu'il porte une garantie de correction, pas de mise en page : un NaN traversant
la frontière casse la page qui le reçoit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

import numpy as np
import polars as pl

from . import analyze, cosign, ideal
from .analyze import VoteCube
from .config import EXPRESSED
from .parse import load

#: Scrutins en commun exigés pour qu'un taux d'accord soit publié. En deçà, le
#: taux n'est pas bas : il n'existe pas. Une seule constante, parce que la page
#: Méthode publie ce nombre et qu'il doit être celui du calcul.
MIN_COMMUNS = 30

#: Le même seuil sur les seuls votes qui engagent. Il est plus bas parce que
#: l'assiette l'est aussi : exiger 30 scrutins communs sur les quelques centaines
#: qui engagent écarterait des paires parfaitement mesurables.
MIN_COMMUNS_TEXTE = 20

#: Dénominateur minimal pour qu'un taux de présence entre dans la
#: **distribution de comparaison** — la médiane, le maximum, les rangs et la
#: bande des 577.
#:
#: Le taux d'un député dont le dénominateur est plus petit reste exact, reste
#: publié, et garde son effectif à côté de lui. Ce qu'il ne peut pas faire, c'est
#: servir de repère aux autres : la présidente de l'Assemblée, qui ne vote pas
#: tant qu'elle préside, a exprimé 19 suffrages sur les 19 scrutins où elle
#: n'était pas empêchée. Son taux de 100 % est juste, et il devenait « le député
#: le plus assidu de la législature » — au-dessus de qui a voté 190 fois sur 245.
#: Le maximum de la distribution était ainsi fixé par le plus petit dénominateur
#: de l'Assemblée.
#:
#: La valeur est celle de `MIN_COMMUNS_TEXTE`, et ce n'est pas une coïncidence :
#: c'est le nombre de scrutins qui engagent en deçà duquel ce projet considère
#: déjà qu'un taux n'est pas mesuré. Une seule idée, un seul seuil.
MIN_VOTABLES = MIN_COMMUNS_TEXTE

#: Nombre de rééchantillonnages pour l'intervalle de confiance des positions.
#:
#: Il valait 40, avec pour justification que « au-delà, les bornes ne bougent
#: plus ». C'était une affirmation, et elle est fausse. Mesurée contre une
#: référence à B=2000, sur dix répliques indépendantes par palier — l'écart-type
#: inter-répliques donne le bruit de tirage, à données constantes, et le biais
#: se lit sur chaque borne :
#:
#:     B      bruit d'une borne     largeur manquante     un bootstrap complet
#:     40     0,041  (10,0 %)       −0,042  (−10,2 %)       9 s
#:     200    0,019  ( 4,7 %)       −0,009  ( −2,3 %)      46 s
#:     500    0,012  ( 2,9 %)       −0,006  ( −1,5 %)     114 s
#:     2000   référence             référence             518 s
#:
#: (pourcentages rapportés à la largeur médiane publiée, 0,409)
#:
#: Deux choses en sortent. Le bruit d'abord : à 40, une borne bougeait de 10 %
#: de la largeur qu'elle borne selon la graine — deux exécutions du même calcul
#: sur les mêmes données publiaient des intervalles visiblement différents. Le
#: biais ensuite, plus grave : trop peu de tirages resserrent les quantiles
#: extrêmes vers le centre, et les intervalles sortaient 10 % trop courts. C'est
#: exactement le défaut que le bootstrap par blocs venait corriger, réintroduit
#: par la porte d'à côté.
#:
#: 500 est le point où le coût cesse d'acheter de la précision : passer à 2000
#: quadruple le temps pour récupérer un dernier centième et demi de largeur. Le
#: coût est de toute façon celui d'une génération, pas d'un affichage.
BOOTSTRAP = 500

#: Fonctions dans un organe qui méritent d'être affichées. « Membre » ne dit
#: rien : tout le monde est membre de quelque chose.
FONCTIONS_NOTABLES = frozenset(
    {"Président", "Co-Président", "Vice-Président", "Secrétaire", "Rapporteur",
     "Co-rapporteur", "Questeur", "Rapporteur général"}
)

#: Types d'organes retenus pour la fiche, du plus au moins parlant.
TYPES_ORGANES = {
    "COMPER": "Commission permanente",
    "COMNL": "Commission spéciale",
    "CE": "Commission d'enquête",
    "MISINFO": "Mission d'information",
    "CMP": "Commission mixte paritaire",
    "DELEG": "Délégation",
    "GE": "Groupe d'études",
    "GA": "Groupe d'amitié",
    "PARPOL": "Parti politique",
}


# --------------------------------------------------------------------------
# Sérialisation
# --------------------------------------------------------------------------


def _propre(valeur: Any) -> Any:
    """Rend une valeur sérialisable, en transformant NaN et ±∞ en `null`.

    Un NaN sérialisé en `NaN` casse `JSON.parse` côté navigateur. On le convertit
    ici plutôt que de laisser chaque appelant y penser : un accord non calculé
    (trop peu de scrutins communs) est une absence de mesure, et `null` le dit.
    """
    if isinstance(valeur, float):
        return None if (math.isnan(valeur) or math.isinf(valeur)) else valeur
    if isinstance(valeur, (np.floating, np.integer)):
        return _propre(valeur.item())
    if isinstance(valeur, np.bool_):
        return bool(valeur)
    if isinstance(valeur, date):
        return valeur.isoformat()
    if isinstance(valeur, dict):
        return {k: _propre(v) for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_propre(v) for v in valeur]
    return valeur


def lignes(df: pl.DataFrame) -> list[dict]:
    """DataFrame → liste de dictionnaires sérialisables."""
    return [_propre(r) for r in df.to_dicts()]


# --------------------------------------------------------------------------
# Les données du site, calculées une fois
# --------------------------------------------------------------------------


@dataclass
class Donnees:
    """Tout ce que le site sert, en mémoire.

    Deux cubes, et c'est volontaire : `cube` porte les 8 434 scrutins, dont 86 %
    d'amendements ; `cube_texte` les 245 votes sur l'ensemble d'un texte et les
    motions de censure. Les mêmes deux députés n'ont pas la même proximité selon
    le cube, et le site montre les deux.
    """

    cube: VoteCube
    cube_texte: VoteCube
    deputes: pl.DataFrame
    scrutins: pl.DataFrame
    groupes: pl.DataFrame
    votes: pl.DataFrame
    positions_groupe: pl.DataFrame
    organes: pl.DataFrame
    responsabilites: pl.DataFrame
    accord: np.ndarray
    communs: np.ndarray
    accord_texte: np.ndarray
    communs_texte: np.ndarray
    #: acteur_uid → ligne dans les matrices d'accord (celles de `cube`).
    index: dict[str, int] = field(default_factory=dict)
    index_texte: dict[str, int] = field(default_factory=dict)
    avec_amendements: bool = False
    #: Affinité de cosignature (Jaccard) et son index, si la table est là.
    jaccard: np.ndarray | None = None
    cosign_communs: np.ndarray | None = None
    index_cosign: dict[str, int] = field(default_factory=dict)
    #: Rééchantillonnages réellement effectués. La page Méthode publie ce
    #: nombre : servir la constante plutôt que la valeur employée ferait
    #: annoncer `BOOTSTRAP` intervalles à un site généré avec `--bootstrap 0`.
    bootstrap: int = BOOTSTRAP
    #: Cube restreint aux députés en exercice, construit à la demande par
    #: `cube_en_exercice()`. Il sert à toute mesure publiée à côté d'un effectif
    #: actuel — cf. `_table_groupes` et `matrice_groupes`.
    _cube_actuel: VoteCube | None = None

    # -- construction ------------------------------------------------------

    @classmethod
    def construire(cls, *, bootstrap: int = BOOTSTRAP, journal: Callable[[str], None] = lambda _: None) -> "Donnees":
        journal("cube de votes")
        cube = analyze.build_cube(en_exercice_seulement=False)
        cube_texte = analyze.build_cube(portee="texte", en_exercice_seulement=False)

        journal("accords deux à deux")
        accord, communs = analyze.agreement(cube, min_communs=MIN_COMMUNS)
        accord_texte, communs_texte = analyze.agreement(
            cube_texte, min_communs=MIN_COMMUNS_TEXTE)

        journal("participation et dissidence")
        stats = _statistiques_deputes(cube)

        journal(f"points idéaux ({bootstrap} rééchantillonnages)")
        positions = _positions(cube_texte, bootstrap=bootstrap)

        journal("amendements")
        amendements = _amendements()
        reseau = _reseau_cosignatures() if amendements is not None else None

        deputes = (
            cube.deputes.drop("i")
            .join(stats, on="acteur_uid", how="left")
            .join(positions, on="acteur_uid", how="left")
        )
        if amendements is not None:
            deputes = deputes.join(amendements, on="acteur_uid", how="left")
        if reseau is not None:
            deputes = deputes.join(_ouverture(reseau), on="acteur_uid", how="left")
        deputes = deputes.with_columns(age=_age(pl.col("date_naissance")))

        organes = load("organes")
        journal("organes et responsabilités")
        responsabilites = _responsabilites(organes)

        d = cls(
            cube=cube,
            cube_texte=cube_texte,
            deputes=deputes,
            scrutins=cube.scrutins.drop("j"),
            groupes=pl.DataFrame(),
            votes=load("votes"),
            positions_groupe=load("positions_groupe"),
            organes=organes,
            responsabilites=responsabilites,
            accord=accord,
            communs=communs,
            accord_texte=accord_texte,
            communs_texte=communs_texte,
            index={u: i for i, u in enumerate(cube.deputes["acteur_uid"].to_list())},
            index_texte={u: i for i, u in enumerate(cube_texte.deputes["acteur_uid"].to_list())},
            avec_amendements=amendements is not None,
            bootstrap=bootstrap,
            jaccard=None if reseau is None else reseau.jaccard(),
            cosign_communs=None if reseau is None else reseau.communs,
            index_cosign=(
                {}
                if reseau is None
                else {u: i for i, u in enumerate(reseau.deputes["acteur_uid"].to_list())}
            ),
        )
        journal("groupes")
        d.groupes = d._table_groupes()
        return d

    # -- vues --------------------------------------------------------------

    def apercu(self) -> dict:
        """Chiffres de tête : périmètre des données, et ce qu'il écarte."""
        s = self.scrutins
        par_portee = {
            r["portee"]: r["len"]
            for r in s.group_by("portee").len().to_dicts()
        }
        return _propre(
            {
                "legislature": s["legislature"][0],
                "deputes": self.deputes.height,
                "en_exercice": int(self.deputes["en_exercice"].sum()),
                "scrutins": s.height,
                "scrutins_par_portee": par_portee,
                "votes": self.votes.height,
                "debut": s["date"].min(),
                "fin": s["date"].max(),
                "groupes": lignes(self.groupes),
                "avec_amendements": self.avec_amendements,
                "bootstrap": self.bootstrap,
                # Le nombre de blocs du bootstrap : c'est lui, et non le nombre
                # de scrutins, qui dit la taille réelle de l'échantillon derrière
                # les intervalles. La page Méthode le publie.
                "blocs_bootstrap": len(ideal.blocs_de_scrutins(self.cube_texte)),
                "entres_en_cours": self._entres_en_cours(),
            }
        )

    def _entres_en_cours(self) -> int:
        """Députés en exercice dont le mandat n'a pas couru toute la législature.

        Leur dénominateur de présence est plus petit que celui des autres : la
        page Méthode le dit, et ce nombre est ce qui rend la phrase vérifiable.
        """
        cube = self.cube_texte
        eligibles = cube.eligible.sum(axis=1)
        if not eligibles.size:
            return 0
        en_exercice = set(
            self.deputes.filter(pl.col("en_exercice"))["acteur_uid"].to_list()
        )
        uids = cube.deputes["acteur_uid"].to_list()
        plein = int(eligibles.max())
        return sum(
            1 for u, e in zip(uids, eligibles) if u in en_exercice and int(e) < plein
        )

    def liste_deputes(self) -> list[dict]:
        """La table de tous les députés, telle que la liste l'affiche et la trie."""
        colonnes = [
            "acteur_uid", "nom_complet", "groupe", "groupe_libelle", "departement",
            "num_departement", "num_circo", "en_exercice", "participation",
            "taux_dissidence", "dissidence_basse", "dissidence_haute",
            "votes_avec_ligne", "part_abstention", "votes_exprimes", "axe1",
            "borne_basse", "borne_haute", "age", "cat_socio_pro",
            # Servies ici pour que le site n'ait jamais à refaire la division :
            # le taux, son numérateur et son dénominateur voyagent ensemble.
            "participation_engageants", "votes_engageants",
            "engageants_eligibles", "engageants_votables",
            "part_delegation_engageants", "participation_comparable",
        ]
        if self.avec_amendements:
            colonnes += ["amendements", "taux_adoption"]
        return lignes(self.deputes.select(colonnes).sort("nom_complet"))

    def fiche(self, uid: str) -> dict:
        """La fiche complète d'un député."""
        d = self.deputes.filter(pl.col("acteur_uid") == uid)
        if d.is_empty():
            raise KeyError(uid)
        r = d.to_dicts()[0]

        groupe = r["groupe"]
        reperes = self.groupes.filter(pl.col("groupe") == groupe).to_dicts()
        resp = self.responsabilites.filter(pl.col("acteur_uid") == uid)

        return _propre(
            {
                "identite": {
                    k: r[k] for k in (
                        "acteur_uid", "civilite", "prenom", "nom", "nom_complet",
                        "date_naissance", "age", "profession", "cat_socio_pro",
                        "uri_hatvp", "departement", "num_departement", "num_circo",
                        "region", "mandat_debut", "mandat_fin", "en_exercice",
                        "groupe", "groupe_libelle", "groupe_qualite",
                        "nb_groupes_legislature",
                    )
                },
                "activite": {
                    k: r[k] for k in (
                        "scrutins_eligibles", "votes_exprimes", "non_votants_structurels",
                        "scrutins_votables", "participation",
                        "part_pour", "part_contre", "part_abstention",
                        "votes_avec_ligne", "votes_dissidents", "taux_dissidence",
                        "dissidence_basse", "dissidence_haute",
                        # Les quatre colonnes de la présence aux votes qui
                        # engagent : le numérateur, les deux retraits et le
                        # dénominateur réellement divisé. Le site n'en recalcule
                        # aucune — c'est ce qui garantit un seul chiffre.
                        "votes_engageants", "engageants_eligibles",
                        "engageants_structurels", "engageants_votables",
                        "participation_engageants", "participation_comparable",
                        # Ce que « présence » recouvre : les suffrages émis au
                        # nom du député par un collègue mandaté. Comptés dans
                        # les numérateurs ci-dessus, servis à part pour être dits.
                        "votes_delegues", "part_delegation",
                        "engageants_delegues", "part_delegation_engageants",
                    )
                },
                "position": {
                    "axe1": r["axe1"],
                    "borne_basse": r["borne_basse"],
                    "borne_haute": r["borne_haute"],
                    "rang": r["rang_axe1"],
                    "classes": int(self.deputes["axe1"].is_not_null().sum()),
                },
                "amendements": (
                    {
                        "deposes": r.get("amendements"),
                        "adoptes": r.get("adoptes"),
                        "taux_adoption": r.get("taux_adoption"),
                        "cosignes": r.get("cosignes"),
                        "cosignataires_moyen": r.get("cosignataires_moyen"),
                        "signatures_retenues": r.get("signatures"),
                        "part_hors_groupe": r.get("part_hors_groupe"),
                        "ouverture": r.get("ouverture"),
                    }
                    if self.avec_amendements
                    else None
                ),
                "cosignataires": self._cosignataires(uid),
                "responsabilites": lignes(resp.drop("acteur_uid")),
                "proches": {
                    "tous": self._voisins(uid, self.accord, self.communs, self.index),
                    "texte": self._voisins(uid, self.accord_texte, self.communs_texte, self.index_texte),
                    "hors_groupe": self._voisins(uid, self.accord, self.communs, self.index, hors_groupe=True),
                    "opposes": self._voisins(uid, self.accord, self.communs, self.index, inverse=True),
                },
                "repere_groupe": reperes[0] if reperes else None,
            }
        )

    def votes_du_depute(
        self, uid: str, *, portee: str | None = None, seulement_dissidents: bool = False,
        limite: int = 200,
    ) -> dict:
        """Les votes nominatifs d'un député, du plus récent au plus ancien."""
        scrutins = self.scrutins
        if portee:
            scrutins = scrutins.filter(pl.col("portee") == portee)

        v = (
            self.votes.filter(pl.col("acteur_uid") == uid)
            .join(
                scrutins.select(
                    "scrutin_uid", "date", "titre", "portee", "categorie",
                    "sort_libelle", "n_pour", "n_contre", "n_abstention",
                ),
                on="scrutin_uid",
                how="inner",
            )
            .join(
                self.positions_groupe.select(
                    "scrutin_uid", "groupe_uid", "majoritaire", "part_majoritaire", "partage"
                ),
                on=["scrutin_uid", "groupe_uid"],
                how="left",
            )
            .with_columns(
                # Pas de ligne, pas de dissidence : `null` et non `false`. Un
                # groupe partagé n'a pas de consigne à enfreindre.
                dissident=pl.when(
                    pl.col("majoritaire").is_not_null()
                    & (pl.col("part_majoritaire") > analyze.SEUIL_LIGNE)
                    & pl.col("position").is_in(list(EXPRESSED))
                )
                .then(pl.col("position") != pl.col("majoritaire"))
                .otherwise(None)
            )
            .sort("date", descending=True)
        )
        total = v.height
        if seulement_dissidents:
            v = v.filter(pl.col("dissident"))
        return _propre({"total": total, "retenus": v.height, "votes": lignes(v.head(limite))})

    def liste_scrutins(
        self, *, portee: str | None = None, q: str | None = None,
        categorie: str | None = None, limite: int = 200, decalage: int = 0,
    ) -> dict:
        s = self.scrutins
        if portee:
            s = s.filter(pl.col("portee") == portee)
        if categorie:
            s = s.filter(pl.col("categorie") == categorie)
        if q:
            s = s.filter(pl.col("titre").str.contains("(?i)" + re.escape(q)))
        s = s.sort("date_d", "numero", descending=[True, True])
        return _propre(
            {
                "total": s.height,
                "scrutins": lignes(
                    s.slice(decalage, limite).select(
                        "scrutin_uid", "numero", "date", "titre", "portee", "categorie",
                        "sort_libelle", "n_pour", "n_contre", "n_abstention",
                        "nb_votants", "contestation", "demandeur",
                    )
                ),
            }
        )

    def scrutin(self, uid: str) -> dict:
        """Le détail d'un scrutin : résultat, position de chaque groupe, dissidents."""
        s = self.scrutins.filter(pl.col("scrutin_uid") == uid)
        if s.is_empty():
            raise KeyError(uid)

        groupes = (
            self.positions_groupe.filter(pl.col("scrutin_uid") == uid)
            .join(
                self.organes.select(
                    pl.col("organe_uid").alias("groupe_uid"),
                    pl.col("libelle_abrev").alias("groupe"),
                    pl.col("libelle").alias("groupe_libelle"),
                ),
                on="groupe_uid",
                how="left",
            )
            .sort("votants_groupe", descending=True)
        )

        votes = (
            self.votes.filter(pl.col("scrutin_uid") == uid)
            .join(
                self.deputes.select("acteur_uid", "nom_complet", "groupe"),
                on="acteur_uid",
                how="inner",
            )
            .join(
                groupes.select("groupe_uid", "majoritaire", "part_majoritaire"),
                on="groupe_uid",
                how="left",
            )
            .with_columns(
                dissident=pl.when(
                    pl.col("majoritaire").is_not_null()
                    & (pl.col("part_majoritaire") > analyze.SEUIL_LIGNE)
                    & pl.col("position").is_in(list(EXPRESSED))
                )
                .then(pl.col("position") != pl.col("majoritaire"))
                .otherwise(None)
            )
            .select("acteur_uid", "nom_complet", "groupe", "position", "dissident",
                    "par_delegation", "cause")
            .sort("nom_complet")
        )

        return _propre(
            {
                "scrutin": s.to_dicts()[0],
                "groupes": lignes(groupes.drop("scrutin_uid")),
                "votes": lignes(votes),
            }
        )

    def liste_groupes(self) -> list[dict]:
        return lignes(self.groupes)

    def matrice_accords(self, *, min_communs: int = MIN_COMMUNS) -> dict:
        """La matrice des accords deux à deux, prête à être dessinée.

        C'est la réponse littérale à « qui vote avec qui » : pour chaque paire de
        députés, la part des scrutins où ils ont voté pareil. Aucune projection,
        aucune dimension inventée — la mesure elle-même, et son dénominateur.

        **L'ordre porte l'argument.** Les députés sont rangés le long de l'axe
        estimé par le modèle de points idéaux (`ideal.py`), et par rien d'autre :
        ni par groupe, ni par ordre alphabétique. Les blocs qui apparaissent
        alors n'ont pas été mis là par le classement, ils sortent des votes. Une
        seconde permutation (`ordre_groupe`) est fournie pour comparer, mais elle
        est la vue de contrôle, pas la vue par défaut.

        Le transport est compact parce que la matrice ne l'est pas : les 574
        députés retenus font 164 451 paires. On envoie le triangle supérieur strict, l'accord
        quantifié sur un octet et les scrutins communs sur deux, en base64 ; la
        page reconstruit. Pour la paire `(i, j)` avec `i < j`, l'indice est
        `i * n - i * (i + 1) // 2 + (j - i - 1)`.

        Une paire sous `min_communs` scrutins en commun n'est pas un accord bas :
        c'est une absence de mesure. Elle vaut `ABSENT` (255) et se dessine comme
        un trou, jamais comme un zéro.
        """
        import base64

        # L'ordre étant celui de l'axe estimé, un député sans position estimée
        # n'a pas de place dans la matrice : trois députés en exercice sont dans
        # ce cas, faute d'assez de votes qui engagent. `absents_de_la_carte` les
        # compte pour que la page puisse le dire au lieu de les faire disparaître.
        candidats = self.deputes.filter(
            pl.col("en_exercice") & pl.col("axe1").is_not_null()
        ).select("acteur_uid", "nom_complet", "groupe", "axe1").sort("axe1")

        uids = candidats["acteur_uid"].to_list()
        pris = [(u, self.index[u]) for u in uids if u in self.index]
        uids = [u for u, _ in pris]
        idx = np.array([i for _, i in pris], dtype=int)
        n = len(idx)

        table = candidats.filter(pl.col("acteur_uid").is_in(uids))
        accord = self.accord[np.ix_(idx, idx)]
        communs = self.communs[np.ix_(idx, idx)]

        haut = np.triu_indices(n, k=1)
        a = accord[haut]
        c = communs[haut]

        # 255 est réservé à « pas mesurable » : l'accord se quantifie sur 0..254.
        octets = np.full(a.shape, 255, dtype=np.uint8)
        mesurable = ~np.isnan(a) & (c >= min_communs)
        octets[mesurable] = np.rint(np.clip(a[mesurable], 0.0, 1.0) * 254).astype(np.uint8)

        groupes = table["groupe"].to_list()
        medianes = {
            g: float(np.median([x for x, gg in zip(table["axe1"].to_list(), groupes) if gg == g]))
            for g in dict.fromkeys(groupes)
        }
        # Les groupes sont contigus dans cette permutation, et rangés entre eux
        # par leur médiane sur l'axe : c'est la vue de contrôle.
        ordre_groupe = sorted(
            range(n), key=lambda p: (medianes[groupes[p]], table["axe1"][p])
        )

        # Les bornes de l'échelle de couleur sont celles des valeurs réellement
        # observées, arrondies au vingtième, et elles sont renvoyées pour être
        # affichées dans la légende. Une échelle dont on ne publie pas les bornes
        # est un mensonge par cadrage, qu'elle soit tronquée ou non.
        mesures = a[mesurable]
        echelle_bas = float(np.floor(mesures.min() * 20) / 20) if mesures.size else 0.0
        echelle_haut = float(np.ceil(mesures.max() * 20) / 20) if mesures.size else 1.0

        # Les paires remarquables : de quoi écrire des phrases qui portent sur ce
        # que la matrice contient réellement, plutôt que sur ce qu'on en attend.
        li, lj = haut
        memes = np.array([groupes[i] == groupes[j] for i, j in zip(li, lj)])

        def paire_dite(k: int) -> dict:
            i, j = int(li[k]), int(lj[k])
            return {
                "a": table["nom_complet"][i], "groupe_a": groupes[i],
                "b": table["nom_complet"][j], "groupe_b": groupes[j],
                "accord": float(a[k]), "communs": int(c[k]),
            }

        def extreme(masque: np.ndarray, plus_haut: bool) -> dict | None:
            ou = np.flatnonzero(masque & mesurable)
            if not ou.size:
                return None
            return paire_dite(int(ou[np.argmax(a[ou]) if plus_haut else np.argmin(a[ou])]))

        tout = np.ones_like(mesurable)
        # « NI » rassemble les non-inscrits : ce n'est pas un groupe, et le taux
        # d'accord de deux non-inscrits ne mesure aucune ligne commune. Il est
        # donc hors des superlatifs qui parlent de groupes, comme ailleurs.
        vrai_groupe = np.array([groupes[i] != "NI" for i in li])

        return _propre(
            {
                "n": n,
                "min_communs": min_communs,
                "absents_de_la_carte": int(
                    self.deputes.filter(
                        pl.col("en_exercice") & pl.col("axe1").is_null()
                    ).height
                ),
                "absent": 255,
                "paires": int(a.size),
                "mesurables": int(mesurable.sum()),
                "echelle_bas": echelle_bas,
                "echelle_haut": echelle_haut,
                "paire_haute": extreme(tout, True),
                "paire_basse": extreme(tout, False),
                "hors_groupe_haute": extreme(~memes, True),
                "meme_groupe_basse": extreme(memes & vrai_groupe, False),
                "deputes": [
                    {"u": u, "n": nom, "g": g, "x": x}
                    for u, nom, g, x in zip(
                        uids, table["nom_complet"].to_list(), groupes, table["axe1"].to_list()
                    )
                ],
                "accord_b64": base64.b64encode(octets.tobytes()).decode("ascii"),
                "communs_b64": base64.b64encode(
                    np.clip(np.nan_to_num(c, nan=0), 0, 65535).astype("<u2").tobytes()
                ).decode("ascii"),
                "ordre_groupe": ordre_groupe,
                "groupes": [
                    {"g": g, "n": groupes.count(g), "mediane": m}
                    for g, m in sorted(medianes.items(), key=lambda kv: kv[1])
                ],
            }
        )

    def matrice_groupes(self) -> dict:
        """La même mesure que `matrice_accords`, agrégée par groupe.

        164 451 cases sont une forme ; 12 sur 12 sont un tableau qu'on lit. Les deux
        disent la même chose, et la seconde est là pour que la première ne soit
        pas seulement jolie.

        L'ordre des lignes et des colonnes est celui des médianes sur l'axe
        estimé — le même que la grande matrice, pour que les deux se comparent.
        Les effectifs accompagnent chaque groupe : une moyenne sur un groupe de
        quatre députés et une moyenne sur un groupe de quatre-vingt-dix ne se
        lisent pas de la même façon.

        Deux valeurs par case, et non une : `cases` porte la moyenne non
        pondérée des taux de paires, `cases_agregees` le quotient des sommes sur
        tous les votes communs. Ce sont deux conventions différentes, elles
        diffèrent de plusieurs points, et la page les publie toutes les deux
        avec le nombre de paires — cf. `analyze.accord_entre_groupes`.

        Le calcul porte sur `cube_en_exercice()`, pour que la mesure et
        l'effectif affiché décrivent la même Assemblée.
        """
        accords = analyze.accord_entre_groupes(self.cube_en_exercice())
        table = self.groupes.filter(pl.col("effectif_actuel") > 0)

        ordre = [
            r["groupe"] for r in
            table.filter(pl.col("position_mediane").is_not_null())
            .sort("position_mediane").to_dicts()
        ]
        # Un groupe sans médiane calculable n'a pas de place sur l'axe : il va
        # en fin de tableau plutôt que d'être écarté sans le dire.
        ordre += [g for g in table["groupe"].to_list() if g not in ordre]

        effectifs = dict(table.select("groupe", "effectif_actuel").iter_rows())
        par_case = {(r["groupe_a"], r["groupe_b"]): r for r in accords.to_dicts()}

        def champ(nom: str) -> list[list]:
            return [[(par_case.get((a, b)) or {}).get(nom) for b in ordre] for a in ordre]

        return _propre(
            {
                "ordre": ordre,
                "effectifs": {g: effectifs.get(g) for g in ordre},
                "cases": champ("accord"),
                "cases_agregees": champ("accord_agrege"),
                "paires": champ("n_paires"),
                "scrutins_communs": champ("scrutins_communs"),
            }
        )

    # -- interne -----------------------------------------------------------

    def _voisins(
        self, uid: str, accord: np.ndarray, communs: np.ndarray, index: dict[str, int],
        *, k: int = 8, inverse: bool = False, hors_groupe: bool = False,
    ) -> list[dict]:
        """Les k députés les plus proches (ou les plus éloignés) de celui-ci."""
        i = index.get(uid)
        if i is None:
            return []
        ligne = accord[i].copy()
        uids = list(index)
        groupes = self.deputes.select("acteur_uid", "groupe", "nom_complet").to_dicts()
        par_uid = {r["acteur_uid"]: r for r in groupes}

        if hors_groupe:
            mien = par_uid.get(uid, {}).get("groupe")
            for j, u in enumerate(uids):
                if par_uid.get(u, {}).get("groupe") == mien:
                    ligne[j] = np.nan

        ordre = np.argsort(ligne if inverse else -ligne)
        retenus = [int(j) for j in ordre if not np.isnan(ligne[j])][:k]
        return [
            {
                "acteur_uid": uids[j],
                "nom_complet": par_uid[uids[j]]["nom_complet"],
                "groupe": par_uid[uids[j]]["groupe"],
                "accord": float(ligne[j]),
                "scrutins_communs": int(communs[i, j]),
            }
            for j in retenus
            if uids[j] in par_uid
        ]

    def _cosignataires(self, uid: str, *, k: int = 8) -> list[dict]:
        """Les collègues dont il cosigne le plus les amendements, indice de Jaccard.

        Jaccard et non compte brut : le compte remonterait les plus prolifiques,
        pas les plus affines. Voir `cosign.ReseauCosignatures.jaccard`.
        """
        if self.jaccard is None:
            return []
        i = self.index_cosign.get(uid)
        if i is None:
            return []
        ligne = self.jaccard[i]
        uids = list(self.index_cosign)
        par_uid = {
            r["acteur_uid"]: r
            for r in self.deputes.select("acteur_uid", "nom_complet", "groupe").to_dicts()
        }
        ordre = [int(j) for j in np.argsort(-ligne) if not np.isnan(ligne[j])][:k]
        return [
            {
                "acteur_uid": uids[j],
                "nom_complet": par_uid[uids[j]]["nom_complet"],
                "groupe": par_uid[uids[j]]["groupe"],
                "affinite": float(ligne[j]),
                "amendements_communs": int(self.cosign_communs[i, j]),
            }
            for j in ordre
            if uids[j] in par_uid
        ]

    def cube_en_exercice(self) -> VoteCube:
        """Le cube restreint aux députés siégeant aujourd'hui.

        Le cube principal porte tous les députés ayant siégé depuis 2024, parce
        que les fiches des anciens doivent rester calculables. Mais toute mesure
        publiée **à côté d'un effectif actuel** doit porter sur ce même
        périmètre : afficher « 24 députés, cohésion 87 % » quand les 87 % sont
        calculés sur 31 personnes dont sept sont parties fait deux chiffres qui
        ne parlent pas de la même Assemblée. L'écart atteignait 0,82 point.
        """
        if self._cube_actuel is None:
            actifs = np.flatnonzero(self.cube.deputes["en_exercice"].to_numpy())
            self._cube_actuel = analyze.sous_cube_deputes(self.cube, actifs)
        return self._cube_actuel

    def _table_groupes(self) -> pl.DataFrame:
        """Effectif, cohésion et moyennes d'activité par groupe.

        La cohésion se calcule sur `cube_en_exercice()` : c'est l'effectif
        affiché dans la colonne d'à côté.
        """
        cohesion = analyze.cohesion_groupes(self.cube_en_exercice())
        moyennes = (
            self.deputes.filter(pl.col("en_exercice"))
            .group_by("groupe")
            .agg(
                pl.col("groupe_libelle").first(),
                pl.col("participation").mean().alias("participation_moyenne"),
                pl.col("taux_dissidence").mean().alias("dissidence_moyenne"),
                pl.col("part_abstention").mean().alias("abstention_moyenne"),
                pl.col("axe1").median().alias("position_mediane"),
                pl.col("axe1").min().alias("position_min"),
                pl.col("axe1").max().alias("position_max"),
                pl.len().alias("effectif_actuel"),
            )
        )
        return (
            moyennes.join(cohesion.select("groupe", "cohesion"), on="groupe", how="left")
            .sort("effectif_actuel", descending=True)
        )


# --------------------------------------------------------------------------
# Statistiques par député
# --------------------------------------------------------------------------


def _age(colonne: pl.Expr) -> pl.Expr:
    aujourdhui = date.today()
    naissance = colonne.str.to_date(strict=False)
    return (
        (pl.lit(aujourdhui) - naissance).dt.total_days() // 365
    ).cast(pl.Int32, strict=False)


#: Niveau des intervalles publiés — celui du bootstrap des positions. Un seul
#: niveau sur tout le site : deux mesures accompagnées d'intervalles à des
#: niveaux différents ne se comparent pas, et rien ne le signalerait au lecteur.
NIVEAU = 0.90


def _intervalle_proportion(k: np.ndarray, n: np.ndarray,
                           niveau: float = NIVEAU) -> tuple[np.ndarray, np.ndarray]:
    """Intervalle de Jeffreys sur une proportion `k / n`.

    **Pourquoi il en faut un.** Le site refuse d'ordonner deux positions
    estimées dont les intervalles se recouvrent, et publiait dans le même temps
    un rang de dissidence sans réserve. C'était une asymétrie de rigueur sans
    justification : un taux de dissidence est une proportion binomiale, son
    incertitude se calcule, et elle est loin d'être négligeable — l'intervalle
    fait environ un point pour un député médian, jusqu'à quatre pour les taux
    élevés, de sorte qu'un rang recouvre en réalité des dizaines de positions
    indiscernables.

    **Pourquoi Jeffreys** plutôt que l'intervalle de Wald appris à l'école.
    Wald — `p ± z√(p(1−p)/n)` — se dégrade exactement là où on en a besoin :
    proche de 0, il descend sous zéro et affiche des bornes négatives, et son
    taux de couverture réel s'effondre. Or la dissidence *est* proche de zéro
    presque partout : la moitié des députés sont sous 3 %. Jeffreys est
    l'intervalle bayésien de loi a priori Beta(½, ½) ; il reste dans [0, 1] par
    construction, se comporte bien aux extrêmes, et vaut zéro exactement quand
    aucun écart n'a été observé.
    """
    from scipy.stats import beta

    k = np.asarray(k, dtype=float)
    n = np.asarray(n, dtype=float)
    q = (1 - niveau) / 2
    with np.errstate(invalid="ignore"):
        bas = beta.ppf(q, k + 0.5, n - k + 0.5)
        haut = beta.ppf(1 - q, k + 0.5, n - k + 0.5)
    # Un député sans aucun vote comparable n'a pas d'intervalle : il n'a pas
    # non plus de taux, et une borne à 0 laisserait croire à une mesure.
    vide = n <= 0
    bas = np.where(vide, np.nan, np.clip(np.nan_to_num(bas, nan=0.0), 0.0, 1.0))
    haut = np.where(vide, np.nan, np.clip(np.nan_to_num(haut, nan=1.0), 0.0, 1.0))
    return bas, haut


def _delegation(scrutins: pl.DataFrame, prefixe: str) -> pl.DataFrame:
    """Suffrages émis **par délégation**, sur l'assiette de scrutins donnée.

    Un député empêché donne délégation à un collègue, qui vote en son nom. Le
    vote lui est imputé — c'est le droit — et il entre donc au numérateur de la
    présence exactement comme un vote émis en personne.

    D'où l'intérêt de publier ce compte à part : sans lui, « présence aux votes »
    se lit comme une présence physique, alors que la mesure porte sur les
    suffrages émis **au nom** du député. L'écart n'est pas anecdotique — la
    délégation pèse près d'un quart des suffrages sur les votes qui engagent, et
    certains députés dépassent 90 %.

    On ne compte que le délégant : la source ne nomme jamais le porteur de la
    délégation, et aucune statistique sur les récipiendaires n'est donc possible.
    """
    return (
        load("votes")
        .join(scrutins.select("scrutin_uid"), on="scrutin_uid", how="inner")
        .filter(pl.col("position").is_in(list(EXPRESSED)))
        .group_by("acteur_uid")
        .agg(pl.col("par_delegation").sum().alias(f"{prefixe}_delegues"))
    )


def _statistiques_deputes(cube: VoteCube) -> pl.DataFrame:
    """Participation, répartition des positions, dissidence — une ligne par député.

    La participation se calcule sur les seuls scrutins où le député siégeait, et
    hors non-votants structurels (ministre, président de séance) : voir
    `analyze.participation`. La dissidence n'entre au dénominateur que quand le
    groupe a une ligne : voir `analyze.votes_vs_ligne`.
    """
    part = analyze.participation(cube).select(
        "acteur_uid", "scrutins_eligibles", "votes_exprimes",
        "non_votants_structurels", pl.col("denominateur").alias("scrutins_votables"),
        "participation",
    )
    diss = analyze.dissidence(cube, min_votes=1).select(
        "acteur_uid",
        pl.col("votes_exprimes").alias("votes_avec_ligne"),
        "votes_dissidents",
        "taux_dissidence",
    )
    # Le taux de dissidence est une proportion : il est servi avec son
    # intervalle, comme la position l'est avec le sien. Sans lui, la fiche
    # publie un rang là où des dizaines de députés sont indiscernables.
    bas, haut = _intervalle_proportion(
        diss["votes_dissidents"].to_numpy(), diss["votes_avec_ligne"].to_numpy()
    )
    diss = diss.with_columns(
        dissidence_basse=pl.Series(bas), dissidence_haute=pl.Series(haut)
    )

    exprime = (cube.exprime & cube.eligible).sum(axis=1).astype(np.float64)
    repartition = pl.DataFrame(
        {
            "acteur_uid": cube.deputes["acteur_uid"],
            "part_pour": np.divide(
                (cube.pour & cube.eligible).sum(axis=1), exprime,
                out=np.full(cube.n_deputes, np.nan), where=exprime > 0,
            ),
            "part_contre": np.divide(
                (cube.contre & cube.eligible).sum(axis=1), exprime,
                out=np.full(cube.n_deputes, np.nan), where=exprime > 0,
            ),
            "part_abstention": np.divide(
                (cube.abstention & cube.eligible).sum(axis=1), exprime,
                out=np.full(cube.n_deputes, np.nan), where=exprime > 0,
            ),
        }
    )

    # Même fonction que ci-dessus, sur l'autre assiette de scrutins : le
    # dénominateur de présence ne se réécrit nulle part ailleurs. Cf.
    # `analyze.participation`, qui retire aussi les non-votants structurels.
    cube_texte = analyze.build_cube(portee="texte", en_exercice_seulement=False)
    engagement = analyze.participation(cube_texte).select(
        "acteur_uid",
        pl.col("votes_exprimes").alias("votes_engageants"),
        pl.col("scrutins_eligibles").alias("engageants_eligibles"),
        pl.col("non_votants_structurels").alias("engageants_structurels"),
        pl.col("denominateur").alias("engageants_votables"),
        pl.col("participation").alias("participation_engageants"),
    )

    # Les suffrages émis par délégation, sur les deux mêmes assiettes. Ils sont
    # déjà comptés dans les numérateurs de présence — c'est le droit — et sont
    # servis à part pour que la fiche puisse dire ce que « présence » recouvre.
    delegation = _delegation(cube.scrutins, "votes")
    delegation_texte = _delegation(cube_texte.scrutins, "engageants")

    return (
        part.join(diss, on="acteur_uid", how="left")
        .join(repartition, on="acteur_uid", how="left")
        .join(engagement, on="acteur_uid", how="left")
        .join(delegation, on="acteur_uid", how="left")
        .join(delegation_texte, on="acteur_uid", how="left")
        .with_columns(
            pl.col("votes_delegues").fill_null(0),
            pl.col("engageants_delegues").fill_null(0),
            # Le taux est-il assez assis pour servir de repère aux autres ?
            # Cf. `MIN_VOTABLES` : en deçà, il reste publié mais sort de la
            # médiane, du maximum, des rangs et de la bande.
            (pl.col("engageants_votables") >= MIN_VOTABLES)
            .fill_null(False)
            .alias("participation_comparable"),
        )
        .with_columns(
            # La part est servie calculée : le site ne refait aucune division,
            # c'est ce qui garantit qu'il n'existe qu'un seul chiffre par mesure.
            pl.when(pl.col("votes_exprimes") > 0)
            .then(pl.col("votes_delegues") / pl.col("votes_exprimes"))
            .otherwise(None)
            .alias("part_delegation"),
            pl.when(pl.col("votes_engageants") > 0)
            .then(pl.col("engageants_delegues") / pl.col("votes_engageants"))
            .otherwise(None)
            .alias("part_delegation_engageants"),
        )
        # Un député sans aucun suffrage exprimé produit un NaN, que les moyennes
        # de groupe propageraient : c'est une absence de mesure, donc un `null`.
        .with_columns(pl.col(pl.Float64).fill_nan(None))
    )


def _positions(cube: VoteCube, *, bootstrap: int) -> pl.DataFrame:
    """Points idéaux avec intervalle de confiance, et rang sur l'axe.

    `bootstrap = 0` sert le point sans intervalle — c'est un mode de démarrage
    rapide, pas un mode par défaut : sans bornes, deux positions séparées d'un
    centième se lisent comme un écart.
    """
    if bootstrap:
        iv = ideal.intervalles(cube, n_bootstrap=bootstrap)
    else:
        modele = ideal.estimer(cube)
        iv = modele.table_deputes().select(
            "acteur_uid", "axe1",
            pl.lit(None, dtype=pl.Float64).alias("borne_basse"),
            pl.lit(None, dtype=pl.Float64).alias("borne_haute"),
        )
    return (
        iv.select("acteur_uid", "axe1", "borne_basse", "borne_haute")
        .sort("axe1")
        .with_row_index("rang_axe1", offset=1)
        .with_columns(pl.col("rang_axe1").cast(pl.Int64))
    )


def _amendements() -> pl.DataFrame | None:
    """Dépôts, adoptions et cosignatures. `None` si la table n'a pas été construite."""
    try:
        amd = load("amendements")
    except FileNotFoundError:
        return None

    deposes = (
        amd.filter(pl.col("auteur_uid").is_not_null())
        .group_by(pl.col("auteur_uid").alias("acteur_uid"))
        .agg(
            pl.len().alias("amendements"),
            (pl.col("sort") == "Adopté").sum().alias("adoptes"),
            pl.col("nb_cosignataires").mean().alias("cosignataires_moyen"),
        )
        .with_columns((pl.col("adoptes") / pl.col("amendements")).alias("taux_adoption"))
    )
    cosignes = (
        amd.select("cosignataires")
        .explode("cosignataires")
        .drop_nulls()
        .group_by(pl.col("cosignataires").alias("acteur_uid"))
        .len()
        .rename({"len": "cosignes"})
    )
    return deposes.join(cosignes, on="acteur_uid", how="full", coalesce=True)


def _reseau_cosignatures() -> "cosign.ReseauCosignatures | None":
    """Réseau de cosignatures, avec les garde-fous de `cosign.build_reseau`.

    Le plafond de dix signataires n'est pas un détail de réglage : sans lui, les
    dépôts de groupe entier relient mécaniquement tous les membres d'un groupe et
    le réseau ne mesure plus qu'une appartenance déjà connue.
    """
    try:
        return cosign.build_reseau(en_exercice_seulement=False)
    except FileNotFoundError:
        return None


def _ouverture(reseau: "cosign.ReseauCosignatures") -> pl.DataFrame:
    """Part des cosignatures hors groupe, rapportée à ce que donnerait le hasard.

    Même correction que `cosign.courtiers` : la part brute classe les groupes par
    petite taille, puisqu'un membre d'un groupe de 17 a presque toute l'Assemblée
    « hors de son groupe ». Le ratio vaut 1 quand le député sort de son camp
    exactement autant que le hasard le prédit.
    """
    groupes = np.array(reseau.groupes(), dtype=object)
    communs = reseau.communs.copy()
    np.fill_diagonal(communs, 0)
    total = communs.sum(axis=1)
    dehors = (communs * (groupes[:, None] != groupes[None, :])).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        part = np.divide(dehors, total, out=np.full(len(total), np.nan), where=total > 0)

    n = len(groupes)
    effectifs = {g: int((groupes == g).sum()) for g in set(groupes)}
    attendue = np.array([(n - effectifs[g]) / (n - 1) for g in groupes], dtype=float)

    return pl.DataFrame(
        {
            "acteur_uid": reseau.deputes["acteur_uid"],
            "signatures": reseau.signatures.astype(np.int64),
            "part_hors_groupe": part,
            "ouverture": part / attendue,
        }
    ).with_columns(pl.col(pl.Float64).fill_nan(None))


def _responsabilites(organes: pl.DataFrame) -> pl.DataFrame:
    """Organes en cours de chaque député, fonctions notables d'abord."""
    mandats = load("mandats")
    return (
        mandats.filter(
            pl.col("date_fin").is_null() & pl.col("type_organe").is_in(list(TYPES_ORGANES))
        )
        .join(
            organes.select("organe_uid", "libelle", "libelle_abrege"),
            on="organe_uid",
            how="left",
        )
        .select(
            "acteur_uid",
            pl.col("type_organe").replace_strict(TYPES_ORGANES, default="Organe").alias("type"),
            "libelle",
            "qualite",
            "date_debut",
            pl.col("qualite").is_in(list(FONCTIONS_NOTABLES)).alias("notable"),
            # Un député siège dans une commission et dans quinze groupes
            # d'amitié : sans cet ordre, la fiche n'affiche que les seconds.
            pl.col("type_organe")
            .replace_strict({t: i for i, t in enumerate(TYPES_ORGANES)}, default=99)
            .alias("rang_type"),
        )
        .unique(subset=["acteur_uid", "libelle", "qualite"])
        .sort(["rang_type", "notable", "libelle"], descending=[False, True, False])
        .drop("rang_type")
    )
