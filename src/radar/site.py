"""Site local : une fiche par député, ses votes, ses statistiques.

Le site ne calcule rien que les autres modules ne calculent déjà. Il ouvre une
fenêtre sur eux, et c'est là que le piège se déplace : **une page web donne à
un chiffre une autorité que sa définition ne lui donne pas**. Affiché seul,
« 78 % de proximité » ressemble à une mesure ; il n'est qu'une moyenne sur un
ensemble de scrutins qu'on a choisi.

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

Le serveur est celui de la bibliothèque standard : le site est local, mono-
utilisateur, et il n'a aucune raison d'ajouter une dépendance. Tout est calculé
une fois au démarrage (une douzaine de secondes) puis servi depuis la mémoire.
"""

from __future__ import annotations

import json
import math
import re
import traceback
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np
import polars as pl

from . import analyze, cosign, ideal
from .analyze import VoteCube
from .config import EXPRESSED
from .parse import load

#: Dossier des fichiers statiques (HTML, CSS, JS) servis à la racine.
WEB = Path(__file__).parent / "web"

#: Nombre de rééchantillonnages pour l'intervalle de confiance des positions.
#: 40 suffit à séparer les zones qui se séparent ; au-delà, on paye du temps de
#: démarrage pour des bornes qui ne bougent plus.
BOOTSTRAP = 40

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

    # -- construction ------------------------------------------------------

    @classmethod
    def construire(cls, *, bootstrap: int = BOOTSTRAP, journal: Callable[[str], None] = lambda _: None) -> "Donnees":
        journal("cube de votes")
        cube = analyze.build_cube(en_exercice_seulement=False)
        cube_texte = analyze.build_cube(portee="texte", en_exercice_seulement=False)

        journal("accords deux à deux")
        accord, communs = analyze.agreement(cube)
        accord_texte, communs_texte = analyze.agreement(cube_texte, min_communs=20)

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
                "bootstrap": BOOTSTRAP,
            }
        )

    def liste_deputes(self) -> list[dict]:
        """La table de tous les députés, telle que la liste l'affiche et la trie."""
        colonnes = [
            "acteur_uid", "nom_complet", "groupe", "groupe_libelle", "departement",
            "num_departement", "num_circo", "en_exercice", "participation",
            "taux_dissidence", "part_abstention", "votes_exprimes", "axe1",
            "borne_basse", "borne_haute", "age", "cat_socio_pro",
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
                        "participation", "part_pour", "part_contre", "part_abstention",
                        "votes_avec_ligne", "votes_dissidents", "taux_dissidence",
                        "votes_engageants", "participation_engageants",
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

    def _table_groupes(self) -> pl.DataFrame:
        """Effectif, cohésion et moyennes d'activité par groupe."""
        cohesion = analyze.cohesion_groupes(self.cube)
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


def _statistiques_deputes(cube: VoteCube) -> pl.DataFrame:
    """Participation, répartition des positions, dissidence — une ligne par député.

    La participation se calcule sur les seuls scrutins où le député siégeait, et
    hors non-votants structurels (ministre, président de séance) : voir
    `analyze.participation`. La dissidence n'entre au dénominateur que quand le
    groupe a une ligne : voir `analyze.votes_vs_ligne`.
    """
    part = analyze.participation(cube).select(
        "acteur_uid", "scrutins_eligibles", "votes_exprimes",
        "non_votants_structurels", "participation",
    )
    diss = analyze.dissidence(cube, min_votes=1).select(
        "acteur_uid",
        pl.col("votes_exprimes").alias("votes_avec_ligne"),
        "votes_dissidents",
        "taux_dissidence",
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

    engageants = analyze.build_cube(portee="texte", en_exercice_seulement=False)
    e_exprime = (engageants.exprime & engageants.eligible).sum(axis=1)
    e_eligible = engageants.eligible.sum(axis=1).astype(np.float64)
    engagement = pl.DataFrame(
        {
            "acteur_uid": engageants.deputes["acteur_uid"],
            "votes_engageants": e_exprime.astype(np.int64),
            "participation_engageants": np.divide(
                e_exprime, e_eligible,
                out=np.full(engageants.n_deputes, np.nan), where=e_eligible > 0,
            ),
        }
    )

    return (
        part.join(diss, on="acteur_uid", how="left")
        .join(repartition, on="acteur_uid", how="left")
        .join(engagement, on="acteur_uid", how="left")
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


# --------------------------------------------------------------------------
# Serveur
# --------------------------------------------------------------------------

TYPES_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    """Routeur minimal : `/api/...` en JSON, le reste depuis `web/`."""

    protocol_version = "HTTP/1.1"
    server_version = "radar"

    def __init__(self, *args, donnees: Donnees, silencieux: bool = True, **kwargs):
        self.donnees = donnees
        self.silencieux = silencieux
        super().__init__(*args, **kwargs)

    # Le journal par défaut écrit une ligne par requête sur stderr, y compris
    # pour chaque fichier statique : illisible pour un site local.
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if not self.silencieux:
            super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        chemin = unquote(url.path)
        params = parse_qs(url.query)
        try:
            if chemin.startswith("/api/"):
                self._api(chemin, params)
            else:
                self._statique(chemin)
        except KeyError as e:
            self._envoyer_json({"erreur": f"introuvable : {e}"}, code=404)
        except ValueError as e:
            self._envoyer_json({"erreur": str(e)}, code=400)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            # Sans ce filet, une erreur ferme la connexion sans rien dire et le
            # navigateur n'affiche qu'un échec réseau : l'erreur va dans la page.
            traceback.print_exc()
            self._envoyer_json({"erreur": f"{type(e).__name__}: {e}"}, code=500)

    # -- routes ------------------------------------------------------------

    def _api(self, chemin: str, params: dict[str, list[str]]) -> None:
        d = self.donnees
        morceaux = [p for p in chemin.split("/") if p][1:]  # sans "api"

        def entier(nom: str, defaut: int) -> int:
            return int(params.get(nom, [defaut])[0])

        def texte(nom: str) -> str | None:
            v = params.get(nom, [None])[0]
            return v or None

        match morceaux:
            case ["apercu"]:
                return self._envoyer_json(d.apercu())
            case ["deputes"]:
                return self._envoyer_json(d.liste_deputes())
            case ["deputes", uid]:
                return self._envoyer_json(d.fiche(uid))
            case ["deputes", uid, "votes"]:
                return self._envoyer_json(
                    d.votes_du_depute(
                        uid,
                        portee=texte("portee"),
                        seulement_dissidents=texte("dissidents") == "1",
                        limite=entier("limite", 200),
                    )
                )
            case ["scrutins"]:
                return self._envoyer_json(
                    d.liste_scrutins(
                        portee=texte("portee"),
                        categorie=texte("categorie"),
                        q=texte("q"),
                        limite=entier("limite", 100),
                        decalage=entier("decalage", 0),
                    )
                )
            case ["scrutins", uid]:
                return self._envoyer_json(d.scrutin(uid))
            case ["groupes"]:
                return self._envoyer_json(d.liste_groupes())
        raise KeyError(chemin)

    def _statique(self, chemin: str) -> None:
        nom = "index.html" if chemin in ("/", "") else chemin.lstrip("/")
        fichier = (WEB / nom).resolve()
        # Un chemin qui remonte hors de `web/` n'a rien à faire ici, même en local.
        if not fichier.is_file() or WEB.resolve() not in fichier.parents:
            fichier = WEB / "index.html"  # routes côté client : /depute/PA123
        corps = fichier.read_bytes()
        self._envoyer(corps, TYPES_MIME.get(fichier.suffix, "application/octet-stream"))

    # -- réponses ----------------------------------------------------------

    def _envoyer_json(self, charge: Any, code: int = 200) -> None:
        corps = json.dumps(charge, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._envoyer(corps, "application/json; charset=utf-8", code)

    def _envoyer(self, corps: bytes, mime: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corps)


def servir(
    donnees: Donnees, *, host: str = "127.0.0.1", port: int = 8000, silencieux: bool = True
) -> ThreadingHTTPServer:
    """Crée le serveur. À l'appelant d'appeler `serve_forever()`."""
    handler = partial(Handler, donnees=donnees, silencieux=silencieux)
    return ThreadingHTTPServer((host, port), handler)
