"""La couche rédactionnelle du site : les phrases, et rien d'autre.

Elle est séparée du générateur pour une raison qui n'est pas cosmétique. Un
chiffre est vrai ou faux ; une phrase qui le commente peut être exacte et
malhonnête à la fois — « seuls 184 députés », « il est absent », « au centre ».
Ce sont deux métiers, ils se relisent différemment, et ils ne doivent pas se
mélanger dans un même fichier de 700 lignes.

Ce module ne connaît ni `polars`, ni `numpy`, ni le paquet `radar` : **il n'a
aucune importation**. Il reçoit des dictionnaires déjà calculés et rend des
chaînes. C'est ce qui le rend relisable par quelqu'un qui n'écrit pas de code,
et testable sans construire une seule table.

Les trois règles qui gouvernent tout ce fichier :

1. Aucun chiffre écrit en dur — tout vient des dictionnaires reçus.
2. Aucun jugement relatif figé : « au-dessus de la médiane » se calcule
   (`situer`), le superlatif se dose (`combien`).
3. Aucun taux sans son dénominateur dans la même phrase.
"""

from __future__ import annotations

#: Espace insécable. Le français l'exige devant %, :, ; et dans les milliers.
NBSP = "\u00a0"


# ── mise en forme française ────────────────────────────────────────────────

def pct(x: float, dec_: int = 1) -> str:
    return f"{100 * x:.{dec_}f}".replace(".", ",")


def dec(x: float, n: int = 2) -> str:
    return f"{x:.{n}f}".replace(".", ",")


def num(n: float) -> str:
    return f"{int(n):,}".replace(",", NBSP)


MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def jour(iso: str) -> str:
    a, m, j = str(iso).split("-")
    return f"{int(j)}{NBSP}{MOIS[int(m) - 1]}{NBSP}{a}"


def ordinal(n: str | int) -> str:
    return "re" if str(n) == "1" else "e"


def echapper(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── couche éditoriale : les phrases se choisissent ─────────────────────────

def accords(identite: dict) -> dict[str, str]:
    """Les accords en genre, pris de la civilité publiée par l'Assemblée.

    243 des 648 députés de la législature sont des femmes. Écrire « il » partout
    serait faux sur près de quatre fiches sur dix — un site qui prétend à
    l'exactitude des chiffres ne peut pas se permettre l'à-peu-près sur les mots.
    """
    f = identite.get("civilite") == "Mme"
    return {
        "il": "elle" if f else "il",
        "Il": "Elle" if f else "Il",
        "lui": "elle" if f else "lui",
        "depute": "députée" if f else "député",
        "present": "présente" if f else "présent",
        "assidu": "assidue" if f else "assidu",
        "elu": "élue" if f else "élu",
        "e": "e" if f else "",
    }


def combien(n: int, total: int, singulier: str, pluriel: str) -> str:
    """« Seuls 12 députés » se dit, « seuls 184 députés » non.

    Le superlatif doit rester proportionnel : au-delà d'un dixième de
    l'Assemblée, le fait n'est plus rare et la phrase ne doit plus le prétendre.
    """
    if n == 0:
        return f"aucun autre député ne {singulier}"
    if n == 1:
        return f"un seul autre député {singulier}"
    if n <= 0.1 * total:
        return f"seuls {num(n)} députés sur {num(total)} {pluriel}"
    if n <= 0.35 * total:
        return f"{num(n)} députés sur {num(total)} {pluriel}"
    return f"{num(n)} députés sur {num(total)} {pluriel} également"


def situer(x: float, mediane: float, au_dessus: str, au_dessous: str, autour: str,
           seuil: float = 0.1) -> str:
    """Un chiffre ne se juge pas, il se situe. Trois formulations, jamais un verdict."""
    ecart = (x - mediane) / (abs(mediane) or 1)
    if ecart > seuil:
        return au_dessus
    if ecart < -seuil:
        return au_dessous
    return autour


def phrase_participation(a: dict, i: dict, dist: dict, rang_sup: int,
                         n_texte: int) -> tuple[str, str]:
    g = accords(i)
    if not a["votes_exprimes"]:
        return ("Aucun vote nominatif de ce député ne figure dans les données publiées "
                "par l'Assemblée nationale&nbsp;: il n'y a pas de taux à calculer.",
                "Nous préférons une case vide à un zéro trompeur. Un taux de 0&nbsp;% "
                "affirmerait une absence constatée&nbsp;; or nous ne constatons ici "
                "qu'un silence de la source.")
    situation = situer(
        a["participation_engageants"], dist["mediane"],
        f"C'est <b>au-dessus</b> de la médiane de l'Assemblée, qui est de {pct(dist['mediane'])}{NBSP}%.",
        f"C'est <b>en dessous</b> de la médiane de l'Assemblée, qui est de {pct(dist['mediane'])}{NBSP}%{NBSP}: "
        f"{num(rang_sup)} députés votent plus souvent qu'{g['il']}.",
        f"C'est <b>dans la moyenne</b> de l'Assemblée, dont la médiane est à {pct(dist['mediane'])}{NBSP}%.",
    )
    # Le nombre cité est celui qui a été divisé — le dénominateur après retrait
    # des scrutins hors mandat *et* des non-votants structurels. Citer les
    # scrutins éligibles ici donnerait une phrase dont le taux ne se refait pas.
    votables = a["engageants_votables"]
    restrictions = []
    if a["engageants_eligibles"] < n_texte:
        restrictions.append("tenus depuis son entrée en fonction")
    if a["engageants_eligibles"] > votables:
        restrictions.append(f"auxquels {g['il']} pouvait prendre part")
    assiette = (f"Sur les {num(votables)} votes qui engagent réellement — l'ensemble "
                f"d'un texte, une motion de censure —")
    if restrictions:
        assiette += " " + " et ".join(restrictions)
    p1 = (f"{assiette}, {g['il']} en a exprimé <b>{num(a['votes_engageants'])}</b>. {situation}")
    p2 = (f"L'étalon n'est pas 100{NBSP}%. Le député le plus assidu de la législature "
          f"atteint {pct(dist['max'])}{NBSP}%, et le député médian manque "
          f"{dec(10 * (1 - dist['mediane']), 0)} votes qui engagent sur dix. "
          f"Un taux se lit contre cette réalité, pas contre un idéal.")
    return p1, p2


def phrase_dissidence(a: dict, i: dict, dist: dict, au_dessus: int,
                      groupe: dict | None) -> tuple[str, str]:
    g = accords(i)
    if not a["votes_avec_ligne"] or a["taux_dissidence"] is None:
        return (f"Son groupe n'a eu de ligne identifiable sur aucun scrutin où {g['il']} a voté&nbsp;: "
                "il n'y a donc rien à compter ici.", "")
    facteur = a["taux_dissidence"] / dist["mediane"] if dist["mediane"] else 0
    p1 = (f"<b>{num(a['votes_dissidents'])} fois sur {num(a['votes_avec_ligne'])}</b>, "
          f"{g['il']} a voté autrement que la majorité de son groupe. La médiane de l'Assemblée est à "
          f"{pct(dist['mediane'])}{NBSP}%{NBSP}: {g['il']} s'en écarte donc environ "
          f"<b>{dec(facteur, 1)} fois plus souvent</b> que le député médian, et "
          f"{combien(au_dessus, dist['n'], 'le fait davantage', 'le font davantage')}.")

    # Le contexte du groupe renverse souvent la lecture : on le donne toujours.
    if groupe and groupe["dissidence_moyenne"] > a["taux_dissidence"]:
        p2 = (f"Ce chiffre demande aussitôt une correction. Dans son groupe, {i['groupe']}, "
              f"la dissidence atteint {pct(groupe['dissidence_moyenne'])}{NBSP}% en moyenne "
              f"et la cohésion interne tombe à {pct(groupe['cohesion'])}{NBSP}%{NBSP}: "
              f"<b>chez {g['lui']}, {echapper(i['nom'])} est en dessous de la moyenne.</b>")
    elif groupe:
        p2 = (f"Son groupe, {i['groupe']}, affiche {pct(groupe['dissidence_moyenne'])}{NBSP}% de "
              f"dissidence moyenne et {pct(groupe['cohesion'])}{NBSP}% de cohésion interne{NBSP}: "
              f"l'écart se joue donc bien à l'échelle de la personne, pas du groupe.")
    else:
        p2 = ""
    return p1, p2


def phrase_position(p: dict, a: dict, i: dict, dist: dict, recouvre: int,
                    encadrants: list[dict]) -> tuple[str, str]:
    g = accords(i)
    reperes = ", ".join(f"{g['groupe']} ({dec(g['position_mediane'])})" for g in encadrants)
    situation = (f"{g['Il']} se situe entre les médianes de {reperes}." if len(encadrants) > 1
                 else f"{g['Il']} se situe au-delà de la médiane de {reperes}." if encadrants
                 else f"{g['Il']} se situe à l'extrémité de l'axe.")
    p1 = (f"Ses votes sur les textes qui engagent le placent à <b>{dec(p['axe1'])}</b> sur l'axe "
          f"principal, quand la médiane de l'Assemblée est à {dec(dist['mediane'])}. {situation}")

    largeur = p["borne_haute"] - p["borne_basse"]
    etendue = dist["max"] - dist["min"]
    qualificatif = ("large" if largeur > 0.22 * etendue
                    else "étroit" if largeur < 0.08 * etendue else "moyen")
    p2 = (f"Son intervalle est {qualificatif} — de {dec(p['borne_basse'])} à "
          f"{dec(p['borne_haute'])} — parce qu'{g['il']} a exprimé {num(a['votes_engageants'])} votes "
          f"qui engagent. <b>{g['Il']} est indiscernable de {num(recouvre)} autres députés</b>, dont "
          f"l'intervalle recouvre le sien. Le rang n'a donc de sens qu'à cette réserve près.")
    return p1, p2


def phrase_ecart_groupe(p: dict, i: dict, groupe: dict | None) -> str:
    """Situe le député dans son propre groupe : le raccourci « il pense comme son groupe »."""
    g = accords(i)
    if not groupe or groupe.get("position_mediane") is None:
        return ""
    d = p["axe1"] - groupe["position_mediane"]
    if abs(d) < 0.05:
        return f"{g['Il']} est <b>au centre de son groupe</b>, à {dec(abs(d))} de sa médiane."
    cote = "au-delà" if d > 0 else "en deçà"
    dehors = (p["axe1"] > groupe["position_max"] or p["axe1"] < groupe["position_min"])
    if dehors:
        return (f"{g['Il']} est <b>hors de l'étendue de son propre groupe</b>, à {dec(abs(d))} "
                f"{cote} de sa médiane.")
    return f"{g['Il']} se tient à <b>{dec(abs(d))} {cote}</b> de la médiane de son groupe."


def phrase_incertitude(p: dict, i: dict, a: dict, dist: dict) -> str:
    g = accords(i)
    largeur = p["borne_haute"] - p["borne_basse"]
    etendue = dist["max"] - dist["min"]
    if largeur > 0.22 * etendue:
        return (f"L'intervalle de {echapper(i['nom'])} est large ({dec(p['borne_basse'])} à "
                f"{dec(p['borne_haute'])})&nbsp;: {g['il']} a exprimé {num(a['votes_engageants'])} votes "
                f"qui engagent, ce qui laisse au modèle peu de matière. Le rang {num(p['rang'])} "
                f"doit être lu avec cette réserve.")
    return (f"L'intervalle de {echapper(i['nom'])} est resserré ({dec(p['borne_basse'])} à "
            f"{dec(p['borne_haute'])}), parce qu'{g['il']} a exprimé {num(a['votes_engageants'])} votes "
            f"qui engagent&nbsp;: le modèle a de la matière. Le rang {num(p['rang'])} reste "
            f"néanmoins indicatif.")


def phrase_these(f: dict, d_diss: dict, d_part: dict, rang_diss: int, rang_part: int,
                 groupe: dict | None, groupes: list[dict], n_texte: int) -> tuple[str, str]:
    """La thèse porte sur ce que ce député a de plus remarquable — pas toujours la même mesure.

    Un site qui ouvrirait toujours sur la dissidence produirait 577 fiches identiques,
    et raconterait n'importe quoi pour les députés parfaitement disciplinés. On classe
    donc les angles par écart à la médiane, et on prend le plus fort.
    """
    a, i = f["activite"], f["identite"]
    g = accords(i)
    n = d_diss["n"]
    nom = echapper(i["nom_complet"])

    # Aucun vote nominatif dans les données. Afficher « 0 % de présence » serait
    # l'accusation la plus grave du site, portée sur ce qui est peut-être une
    # lacune de la source. On décrit l'absence de donnée, pas un comportement.
    if not a["votes_exprimes"]:
        return (f"Les données publiées par l'Assemblée ne contiennent "
                f"<em>aucun vote nominatif</em> pour {nom}, alors que son mandat court "
                f"depuis le {jour(i['mandat_debut'])}.",
                "Ce site ne peut donc rien en dire. Une absence de donnée n'est pas une "
                "absence de travail&nbsp;: elle peut venir d'une lacune de la source, et "
                "en aucun cas nous ne la présenterons comme une présence nulle.")

    # `taux_dissidence` vaut None — et non zéro — quand le groupe n'a eu de ligne
    # sur aucun scrutin où le député a voté. Il n'y a alors pas d'angle à tirer
    # de la dissidence : ce n'est pas une discipline parfaite, c'est une absence
    # de mesure. On bascule sur la présence.
    diss_mesurable = a["taux_dissidence"] is not None and bool(a["votes_avec_ligne"])
    ecart_diss = (a["taux_dissidence"] / d_diss["mediane"]
                  if diss_mesurable and d_diss["mediane"] else 1.0)
    ecart_part = (a["participation_engageants"] / d_part["mediane"]
                  if d_part["mediane"] else 1.0)

    def force(r: float) -> float:
        return max(r, 1 / r) if r > 0 else 99.0

    # ── angle « il s'écarte beaucoup de son groupe » ──────────────────────
    if diss_mesurable and force(ecart_diss) >= force(ecart_part) and ecart_diss >= 1.6:
        t1 = (f"{nom} s'écarte de la ligne de son groupe "
              f"<em>{dec(ecart_diss, 1)} fois plus souvent</em> que le député médian. "
              f"À l'Assemblée, {combien(rang_diss, n, 'le fait davantage', 'le font davantage')}.")
        if groupe and groupe["dissidence_moyenne"] > a["taux_dissidence"]:
            # « NI » rassemble les non-inscrits : ce n'est pas un groupe, et sa cohésion
            # n'a pas de sens. Il est donc hors des superlatifs.
            vrais = [g for g in groupes if g["groupe"] != "NI"]
            moins_uni = min(vrais, key=lambda g: g["cohesion"])["groupe"] == i["groupe"]
            precision = ("est le groupe le moins uni de l'Assemblée"
                         if moins_uni else
                         f"n'a que {pct(groupe['cohesion'])}{NBSP}% de cohésion interne")
            t2 = (f"Sauf que son groupe {precision}. Rapporté à {i['groupe']}, "
                  f"le même chiffre devient ordinaire. Un nombre ne dit rien sans ce à quoi "
                  f"on le compare — c'est la raison d'être de ce site.")
        elif groupe:
            t2 = (f"Et cette fois le contexte ne l'atténue pas{NBSP}: son groupe tient à "
                  f"{pct(groupe['cohesion'])}{NBSP}% de cohésion interne, avec "
                  f"{pct(groupe['dissidence_moyenne'])}{NBSP}% de dissidence en moyenne. "
                  f"L'écart se joue bien à l'échelle de la personne.")
        else:
            t2 = ("Il ne siège dans aucun groupe constitué&nbsp;: la notion de ligne à suivre "
                  "ne s'applique donc qu'aux scrutins où les non-inscrits ont voté de concert.")
        return t1, t2

    # ── angle « il vote presque toujours avec son groupe » ────────────────
    if diss_mesurable and force(ecart_diss) >= force(ecart_part) and ecart_diss <= 0.6:
        plus_disciplines = max(n - rang_diss - 1, 0)
        t1 = (f"{nom} suit la ligne de son groupe dans "
              f"<em>{pct(1 - a['taux_dissidence'])}{NBSP}% des cas</em> où celui-ci en avait "
              f"une. {g['Il']} ne s'en écarte que {num(a['votes_dissidents'])} fois sur "
              f"{num(a['votes_avec_ligne'])}.")
        coh = (f" et son groupe affiche {pct(groupe['cohesion'])}{NBSP}% de cohésion interne"
               if groupe else "")
        t2 = (f"C'est peu, mais ce n'est pas rare{NBSP}: {num(plus_disciplines)} députés sur "
              f"{num(n)} sont encore plus disciplinés{coh}. "
              f"<b>La discipline est la règle à l'Assemblée, pas l'exception</b> — c'est le "
              f"contexte qui manque à la plupart des chiffres qu'on lit ailleurs.")
        return t1, t2

    # ── angle « présence aux votes qui engagent » ─────────────────────────
    if ecart_part >= 1:
        t1 = (f"{nom} est {g['present']} à <em>{pct(a['participation_engageants'])}{NBSP}% "
              f"des votes qui engagent</em>, quand le député médian n'atteint que "
              f"{pct(d_part['mediane'])}{NBSP}%.")
        t2 = (f"{combien(rang_part, d_part['n'], 'fait mieux', 'font mieux').capitalize()}. "
              f"Et personne ne dépasse {pct(d_part['max'])}{NBSP}%{NBSP}: à l'Assemblée, "
              f"la présence intégrale n'existe pas. Un taux se lit contre cette réalité, "
              f"pas contre un idéal à 100{NBSP}%.")
        return t1, t2

    quand = ("depuis son entrée en fonction"
             if a["engageants_eligibles"] < n_texte
             else "depuis le début de la législature")
    t1 = (f"{nom} a exprimé <em>{num(a['votes_engageants'])} des "
          f"{num(a['engageants_votables'])} votes qui engagent</em> {quand}, "
          f"soit {pct(a['participation_engageants'])}{NBSP}%.")
    t2 = (f"C'est en dessous de la médiane de l'Assemblée ({pct(d_part['mediane'])}{NBSP}%), "
          f"mais l'étalon n'est pas 100{NBSP}%{NBSP}: le député le plus assidu de la "
          f"législature atteint {pct(d_part['max'])}{NBSP}%. Aucun chiffre de ce site ne mesure "
          f"le travail en commission, en circonscription ou en séance sans vote.")
    return t1, t2


def phrase_ecart(f: dict, communs: int) -> tuple[str, str]:
    tous, texte = f["proches"]["tous"], f["proches"]["texte"]
    if not tous or not texte:
        return ("Il n'a pas assez de scrutins en commun avec ses collègues pour que la "
                "comparaison ait un sens.",
                "Un taux d'accord calculé sur une poignée de votes n'est pas une mesure, "
                "c'est un accident d'échantillon. Nous ne l'affichons donc pas.")
    if communs == 0:
        v1 = "Aucun nom ne figure dans les deux colonnes."
    elif communs == 1:
        v1 = "Un seul nom sur huit figure dans les deux colonnes."
    else:
        v1 = f"Seulement {num(communs)} noms sur huit figurent dans les deux colonnes."
    v1 = f"{v1} Changer l'assiette des scrutins change presque entièrement la réponse."
    v2 = (f"Ce n'est pas une erreur de calcul, c'est le fond du problème. En haut de la colonne de "
          f"gauche, {echapper(tous[0]['nom_complet'])} atteint {pct(tous[0]['accord'])}{NBSP}% — sur "
          f"{num(tous[0]['scrutins_communs'])} scrutins seulement. À droite, "
          f"{echapper(texte[0]['nom_complet'])} atteint {pct(texte[0]['accord'])}{NBSP}% sur "
          f"{num(texte[0]['scrutins_communs'])} scrutins. "
          f"<b>C'est l'écart entre les deux colonnes qui informe</b>, jamais une colonne prise seule. "
          f"Un site qui n'en publierait qu'une vous laisserait croire à une précision qui n'existe pas.")
    return v1, v2


# ── la carte des accords ───────────────────────────────────────────────────

def phrase_carte(m: dict, groupes_axe: list[dict]) -> dict[str, str]:
    """Les phrases de la page « Qui vote avec qui ».

    Tout ce qui est affirmé ici est tiré de `m`, calculé par
    `Donnees.matrice_accords()`. Rien n'y est écrit d'avance : si l'Assemblée
    changeait au point que les blocs disparaissent, ces phrases changeraient.

    Le mot « bloc » est employé pour ce qu'il décrit — des paires qui votent
    ensemble — et jamais comme une étiquette politique. On ne nomme ni gauche,
    ni droite, ni majorité : on cite des groupes et des chiffres.
    """
    haute, basse = m["paire_haute"], m["paire_basse"]
    hors, dedans = m["hors_groupe_haute"], m["meme_groupe_basse"]
    non_mesurables = m["paires"] - m["mesurables"]

    these = (
        f"Entre deux députés pris au hasard, il existe un chiffre&nbsp;: la part des "
        f"scrutins où ils ont voté la même chose. Il y a {num(m['paires'])} paires "
        f"possibles à l'Assemblée, et cette page les montre <em>toutes en même "
        f"temps</em> — sans en résumer aucune."
    )
    these_suite = (
        f"L'écart est immense d'un bout à l'autre&nbsp;: {echapper(haute['a'])} et "
        f"{echapper(haute['b'])} votent pareil {pct(haute['accord'], 0)}{NBSP}% du temps sur "
        f"{num(haute['communs'])} scrutins communs, quand {echapper(basse['a'])} et "
        f"{echapper(basse['b'])} n'y parviennent que {pct(basse['accord'], 0)}{NBSP}% du temps sur "
        f"{num(basse['communs'])}. <b>Aucun de ces deux nombres ne veut dire quoi que ce soit "
        f"seul</b> — c'est leur place dans l'ensemble qui les rend lisibles, et c'est "
        f"l'ensemble qui est dessiné ci-dessous."
    )

    phrase_ordre = (
        f"S'il fait apparaître des blocs, c'est que les votes eux-mêmes en contiennent. "
        f"Et le classement produit ses propres démentis&nbsp;: {echapper(hors['a'])} "
        f"({hors['groupe_a']}) et {echapper(hors['b'])} ({hors['groupe_b']}) ne siègent pas "
        f"dans le même groupe et votent pourtant pareil {pct(hors['accord'], 0)}{NBSP}% du "
        f"temps sur {num(hors['communs'])} scrutins, tandis que {echapper(dedans['a'])} et "
        f"{echapper(dedans['b'])}, tous deux {dedans['groupe_a']}, tombent à "
        f"{pct(dedans['accord'], 0)}{NBSP}% sur {num(dedans['communs'])}. "
        f"Le bouton « rangés par groupe » permet de comparer les deux mises en ordre."
    )

    premier, dernier = groupes_axe[0], groupes_axe[-1]
    phrase_groupes = (
        f"Le tableau des {num(len(groupes_axe))} groupes dit la même chose que les "
        f"{num(m['paires'])} cases, en lisible. Il se lit dans l'ordre où le modèle a rangé "
        f"les groupes, de {premier['g']} à {dernier['g']}&nbsp;: cet ordre n'a pas été choisi, "
        f"il tombe du calcul des médianes."
    )
    phrase_groupes_2 = (
        f"La diagonale porte la cohésion interne — la part des scrutins où deux députés du "
        f"même groupe votent pareil. C'est presque toujours le chiffre le plus élevé de sa "
        f"ligne, et c'est le fait le plus massif de l'Assemblée&nbsp;: <b>on vote d'abord "
        f"avec son groupe</b>. Les écarts individuels se lisent contre cette règle, jamais "
        f"contre une indépendance qui n'existe nulle part."
    )

    legende_ordre = (
        "Rangés le long de l'axe estimé à partir de leurs seuls votes. "
        "Aucune étiquette de groupe n'entre dans ce classement."
    )

    if non_mesurables:
        legende_ordre += (
            f" {num(non_mesurables)} paires sur {num(m['paires'])} restent non mesurables, "
            f"faute d'assez de scrutins en commun."
        )

    alt = (
        f"Tableau à double entrée de {num(m['n'])} députés sur {num(m['n'])}. Chaque case "
        f"porte le taux d'accord d'une paire, du plus clair au plus foncé. Les députés sont "
        f"rangés selon l'axe estimé à partir de leurs votes. Des blocs foncés apparaissent le "
        f"long de la diagonale, séparés par des zones claires."
    )

    return {
        "THESE": these,
        "THESE_SUITE": these_suite,
        "PHRASE_ORDRE": phrase_ordre,
        "PHRASE_GROUPES": phrase_groupes,
        "PHRASE_GROUPES_2": phrase_groupes_2,
        "LEGENDE_ORDRE": legende_ordre,
        "ALT_MATRICE": alt,
    }


# ── la page méthode ────────────────────────────────────────────────────────

def reserve_denominateur(eligibles: int, votables: int, total: int) -> str:
    """Ce que le dénominateur de présence a retiré, nommé retrait par retrait.

    Le nombre affiché sous le taux est celui qui a été divisé. Quand il est
    inférieur au total de la législature, le lecteur a raison de trouver le
    chiffre suspect — et c'est à nous de dire pourquoi, pas à lui de deviner.
    Deux retraits sont possibles, ils peuvent se cumuler, et ils ne disent pas
    la même chose : le premier est une date d'entrée, le second une fonction qui
    interdit de voter.
    """
    lignes = []
    if eligibles < total:
        lignes.append("tenus depuis son entrée en fonction")
    manquants = eligibles - votables
    if manquants > 0:
        s = "s" if manquants > 1 else ""
        lignes.append(f"hors {num(manquants)} scrutin{s} de non-votant structurel")
    return ("<br>" + "<br>".join(lignes)) if lignes else ""


def phrase_portee(apercu: dict) -> tuple[str, str]:
    """Le piège fondateur du site : tous les scrutins ne pèsent pas pareil.

    La phrase se construit sur la répartition réelle des portées. Si l'Assemblée
    votait un jour autant de textes que d'amendements, elle changerait d'elle-même.
    """
    portees = apercu["scrutins_par_portee"]
    detail = portees.get("detail", 0)
    texte = portees.get("texte", 0)
    total = apercu["scrutins"]
    facteur = detail / texte if texte else 0

    p1 = (f"Sur les {num(total)} scrutins publics de la législature, "
          f"<b>{num(detail)} portent sur un détail de texte</b> — le plus souvent un "
          f"amendement — et <b>{num(texte)} seulement</b> sur l'ensemble d'un texte, une "
          f"motion de censure ou une déclaration de politique générale. Il y a donc "
          f"{dec(facteur, 0)} fois plus des premiers que des seconds.")
    p2 = ("C'est le fait qui gouverne tout le reste de ce site. Un chiffre calculé sur "
          "« tous les scrutins » est en réalité un chiffre sur les amendements&nbsp;: ils "
          "décident du résultat par leur nombre. <b>C'est pourquoi nous publions "
          "systématiquement deux lectures</b> — toutes portées, et votes qui engagent "
          "seulement — plutôt qu'une moyenne unique qui aurait l'air plus simple et "
          "dirait moins.")
    return p1, p2
