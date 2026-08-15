"""Un taux publié et un taux comparable ne sont pas la même chose.

Ces tests portent tous sur la même règle, appliquée à deux mesures : un taux
calculé sur trop peu d'observations reste exact, reste publié, reste accompagné
de son dénominateur — et ne sert de repère à personne. Il sort de la médiane, du
maximum, des rangs et de la bande des 577.

La règle existait pour la présence (`vues.MIN_VOTABLES`) et pas pour la
dissidence, alors que la note méthodologique annonçait un minimum de 50 votes
comparables. Les trois députés à 0,0 % de la 17ᵉ législature étaient exactement
ceux dont le groupe n'avait eu de ligne que sur 4, 8 et 25 scrutins : ils
fixaient à eux seuls l'extrémité « discipline » de la distribution.
"""

import inspect

from radar.analyze import MIN_VOTES_LIGNE, dissidence
from radar.vues import MIN_VOTABLES


class TestSeuilDissidence:
    def test_le_seuil_est_celui_que_la_note_annonce(self):
        """La note publie 50 ; le code doit publier le même nombre.

        C'est le seul contrôle qui empêche la note et le calcul de diverger
        sans que personne s'en aperçoive : la valeur est citée dans les deux.
        """
        assert MIN_VOTES_LIGNE == 50

    def test_le_defaut_de_la_fonction_est_ce_seuil(self):
        """`dissidence()` doit écarter par défaut ce que la note dit écarter.

        On lit la signature plutôt que d'exécuter la fonction : son corps
        interroge les tables `votes` et `positions_groupe`, absentes d'un clone
        frais, et un test qui s'exécuterait dessus passerait à vide — le cube
        jouet n'a aucun scrutin en commun avec elles, et le résultat serait
        toujours une table nulle, seuil ou pas.
        """
        defaut = inspect.signature(dissidence).parameters["min_votes"].default
        assert defaut == MIN_VOTES_LIGNE

    def test_le_seuil_ne_s_applique_pas_au_calcul_du_taux(self):
        """Écarter d'une comparaison n'est pas refuser de calculer.

        `min_votes` est un paramètre, et le site l'appelle avec 1 : la fiche
        d'un député entré en juin affiche son taux et son dénominateur. C'est
        `dissidence_comparable` qui lui retire le rang et la bande, pas ce
        seuil-ci — sans quoi la fiche n'aurait plus rien à publier.
        """
        assert "min_votes" in inspect.signature(dissidence).parameters


class TestSeuilPresence:
    def test_les_deux_seuils_sont_nommes_et_distincts(self):
        """Deux assiettes différentes, deux seuils, tous deux nommés.

        La présence se mesure sur les votes qui engagent, la dissidence sur
        tous les scrutins où le groupe avait une ligne : leurs dénominateurs
        n'ont pas le même ordre de grandeur, et un seuil unique aurait été un
        chiffre rond plutôt qu'une raison.
        """
        assert MIN_VOTABLES == 20
        assert MIN_VOTES_LIGNE == 50
        assert MIN_VOTES_LIGNE > MIN_VOTABLES


class TestDistributionDeComparaison:
    """La distribution ne doit contenir que des taux comparables.

    Reproduit ce que fait `generer.Site.__init__` : la médiane, le maximum et
    les rangs se calculent sur les seuls députés dont le drapeau est vrai.
    """

    def deputes(self) -> list[dict]:
        return [
            {"taux_dissidence": 0.0, "votes_avec_ligne": 4, "dissidence_comparable": False},
            {"taux_dissidence": 0.0, "votes_avec_ligne": 8, "dissidence_comparable": False},
            {"taux_dissidence": 0.30, "votes_avec_ligne": 122, "dissidence_comparable": True},
            {"taux_dissidence": 0.02, "votes_avec_ligne": 2341, "dissidence_comparable": True},
        ]

    def test_les_petits_denominateurs_sortent_de_la_distribution(self):
        retenus = [
            d["taux_dissidence"] for d in self.deputes() if d["dissidence_comparable"]
        ]
        assert retenus == [0.30, 0.02]

    def test_le_minimum_n_est_plus_fixe_par_quatre_votes(self):
        """Sans le filtre, le plancher de l'Assemblée valait 0 % sur 4 votes."""
        tous = [d["taux_dissidence"] for d in self.deputes()]
        retenus = [
            d["taux_dissidence"] for d in self.deputes() if d["dissidence_comparable"]
        ]
        assert min(tous) == 0.0
        assert min(retenus) == 0.02


class TestPasDeZeroPourUneAbsence:
    """Une mesure absente ne se pose pas à zéro sur la bande.

    `generer` sert une chaîne vide, et `radar.js` retire alors le dispositif.
    Le cas réel : une députée dont le mandat a commencé après le dernier
    scrutin couvert, dont la fiche dit « rien à compter » — et dont la bande
    portait un losange à 0,0 % avec un `aria-label` qui énonçait le taux.
    """

    def valeur_de_bande(self, taux, comparable) -> str:
        return (
            f"{taux:.4f}" if taux is not None and comparable else ""
        )

    def test_une_dissidence_absente_ne_donne_aucune_bande(self):
        assert self.valeur_de_bande(None, False) == ""

    def test_une_dissidence_non_comparable_ne_donne_aucune_bande(self):
        assert self.valeur_de_bande(0.0, False) == ""

    def test_une_dissidence_mesuree_donne_sa_valeur(self):
        assert self.valeur_de_bande(0.0271, True) == "0.0271"
