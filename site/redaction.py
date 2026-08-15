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


def pluriel(n: float) -> str:
    """La marque du pluriel français : rien en deçà de deux.

    « 1 amendements adoptés » sur une page qui promet l'exactitude des chiffres
    coûte plus cher qu'il n'y paraît : un lecteur qui voit la langue mal
    accordée doute du reste.
    """
    return "s" if abs(n) >= 2 else ""


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
    # Parenthèses et non tirets : l'incise est suivie de la virgule de la phrase
    # principale, et un tiret fermant collé à une virgule (« —, ») accroche l'œil.
    assiette = (f"Sur les {num(votables)} votes qui engagent réellement (l'ensemble "
                f"d'un texte, une motion de censure)")
    if restrictions:
        assiette += " " + " et ".join(restrictions)
    # Un dénominateur trop petit ne se compare pas. Le taux reste affiché — il
    # est exact — mais on ne le range pas parmi les autres : dire « au-dessus de
    # la médiane » d'un taux mesuré sur quinze votes serait un classement fondé
    # sur rien. Cf. `vues.MIN_VOTABLES`.
    if not a.get("participation_comparable", True):
        p1 = (f"{assiette}, {g['il']} en a exprimé "
              f"<b>{num(a['votes_engageants'])}</b>.")
        p2 = (f"<b>Ce taux ne se compare pas aux autres.</b> Il porte sur "
              f"{num(votables)} scrutins seulement, quand la plupart des députés "
              f"en ont {num(n_texte)} : une fonction — présider, entrer au "
              f"Gouvernement — a réduit l'assiette au point qu'un rang n'aurait "
              f"plus de sens. Le chiffre est exact, il n'est simplement pas un "
              f"repère. C'est pourquoi cette page ne le situe pas dans "
              f"l'Assemblée.")
        return p1, p2

    p1 = (f"{assiette}, {g['il']} en a exprimé <b>{num(a['votes_engageants'])}</b>. {situation}")

    # L'argument « l'étalon n'est pas 100 % » repose sur le maximum observé. Il
    # ne tient que si ce maximum est effectivement inférieur à 100 %, ce qui
    # cesse d'être vrai dès qu'un député au dénominateur très réduit — la
    # présidente de l'Assemblée, qui ne vote pas tant qu'elle préside — vote à
    # chacun des rares scrutins où il pouvait le faire. Écrire « l'étalon n'est
    # pas 100 %, le plus assidu atteint 100,0 % » serait un non-sens, et le
    # genre de phrase qui décrédibilise tout le reste de la page.
    if dist["max"] < 0.99:
        p2 = (f"L'étalon n'est pas 100{NBSP}%. Le député le plus assidu de la législature "
              f"atteint {pct(dist['max'])}{NBSP}%, et le député médian manque "
              f"{dec(10 * (1 - dist['mediane']), 0)} votes qui engagent sur dix. "
              f"Un taux se lit contre cette réalité, pas contre un idéal.")
    else:
        p2 = (f"L'étalon n'est pas 100{NBSP}%. Le député médian manque "
              f"{dec(10 * (1 - dist['mediane']), 0)} votes qui engagent sur dix, et les "
              f"rares taux qui atteignent 100{NBSP}% sont ceux de députés dont une "
              f"fonction — présider la séance, entrer au Gouvernement — a réduit le "
              f"dénominateur à quelques votes. Un taux se lit avec l'effectif sur lequel "
              f"il porte, pas contre un idéal.")
    return p1, p2


def phrase_dissidence(a: dict, i: dict, dist: dict, au_dessus: int,
                      groupe: dict | None, indiscernables: int = 0) -> tuple[str, str]:
    """La mesure 2, avec la réserve que son rang appelle.

    `indiscernables` est le nombre de députés dont l'intervalle recouvre le
    sien. Le rang reste affiché — il situe —, mais il est immédiatement borné :
    sur un taux mesuré à quelques centaines de votes, des dizaines de positions
    voisines ne sont pas départageables, et un rang nu le laisserait ignorer.
    """
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

    if indiscernables and a.get("dissidence_basse") is not None:
        p1 += (
            f" Ce taux est mesuré, donc incertain&nbsp;: son intervalle à 90{NBSP}% va de "
            f"<b>{pct(a['dissidence_basse'])}{NBSP}%</b> à <b>{pct(a['dissidence_haute'])}"
            f"{NBSP}%</b>, et {num(indiscernables)} députés y ont un intervalle qui recouvre "
            f"le sien. Le rang situe&nbsp;; il ne départage pas ces "
            f"{num(indiscernables)}-là."
        )

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
    # Un taux de présence hors distribution ne peut pas fournir l'angle : il
    # n'est comparable à rien, et l'écart à la médiane qu'on en tirerait serait
    # un artefact de dénominateur. Il vaut 1 — neutre — pour que la dissidence
    # l'emporte, et la dernière branche décrit alors l'assiette sans la classer.
    ecart_part = (a["participation_engageants"] / d_part["mediane"]
                  if d_part["mediane"] and a.get("participation_comparable", True)
                  else 1.0)

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


def mention_delegation(delegues: int, exprimes: int) -> str:
    """« dont N par délégation », accolé au numérateur de présence.

    Le compte va au numérateur, pas au dénominateur : ces votes sont bien
    imputés au député, le règlement le prévoit. Mais ils n'ont pas été émis par
    lui, et « présence aux votes » se lit autrement quand on le sait. Le nombre
    est donc affiché à côté de celui qu'il qualifie, jamais ailleurs.
    """
    if not delegues or not exprimes:
        return ""
    return f", dont <b>{num(delegues)}</b> par délégation"


def grand_chiffre(valeur: str | None, unite: str = "") -> str:
    """Le chiffre de tête d'une mesure — ou le tiret qui dit qu'il n'y en a pas.

    Une mesure qui n'existe pas ne s'affiche pas à zéro. La fiche d'un député
    dont la source ne publie aucun vote nominatif portait « 0,0 % » en corps
    88, au-dessus d'un paragraphe qui expliquait qu'on préfère « une case vide
    à un zéro trompeur ». Le paragraphe avait raison et le chiffre le
    démentait ; c'est le chiffre qu'on lit.

    Le tiret cadratin n'est pas un ornement : c'est la convention typographique
    de la donnée manquante, et le site l'emploie déjà dans ses tableaux.
    """
    if valeur is None:
        return '<div class="valeur valeur-absente" aria-label="mesure absente">—</div>'
    marque = f'<span class="unite">{unite}</span>' if unite else ""
    return f'<div class="valeur">{valeur}{marque}</div>'


def provenance_delegation(delegues: int, exprimes: int) -> str:
    """Le numérateur et le dénominateur de la part déléguée, sous le grand chiffre.

    La ligne est ici et non dans le gabarit parce que sa langue dépend de ses
    nombres : « 0 suffrages » et « 1 suffrages » sont deux fautes qu'un gabarit
    ne peut pas éviter, et que ce site ne peut pas se permettre.
    """
    return (f"<b>{num(delegues)}</b> suffrage{pluriel(delegues)} émis par un "
            f"collègue<br>sur <b>{num(exprimes)}</b> exprimé{pluriel(exprimes)} "
            f"en son nom")


def provenance_amendements(deposes: int, examines: int, adoptes: int) -> str:
    """Ce que le compte de dépôts recouvre, sous le grand chiffre."""
    tete = (f"amendement{pluriel(deposes)} déposé{pluriel(deposes)} comme "
            f"auteur principal")
    if not examines:
        return f"{tete}<br>aucun n'a encore été examiné"
    return (f"{tete}<br>dont <b>{num(adoptes)}</b> adopté{pluriel(adoptes)}, "
            f"sur <b>{num(examines)}</b> examiné{pluriel(examines)}")


def phrase_delegation(a: dict, i: dict, dist: dict, au_dessus: int,
                      groupe: dict | None) -> tuple[str, str]:
    """La mesure des suffrages émis par un mandataire.

    Elle vivait au fond d'un repli, en une phrase, sous la présence aux votes.
    C'était trop peu pour ce qu'elle dit : près d'un quart des suffrages qui
    engagent sont émis par un collègue, et un taux de présence élevé peut ne
    décrire aucune présence.

    Le premier paragraphe situe, le second donne l'objection — ici, que la
    délégation est un droit, et qu'un taux élevé n'est pas en soi un
    manquement.
    """
    g = accords(i)
    exprimes = a.get("votes_engageants") or 0
    delegues = a.get("engageants_delegues") or 0
    if not exprimes:
        return ("Aucun suffrage de ce député ne figure dans les données publiées "
                "sur ces scrutins&nbsp;: il n'y a pas de part à calculer.", "")

    if not delegues:
        p1 = (f"<b>Aucun</b> des {num(exprimes)} suffrages exprimés par "
              f"{echapper(i['nom'])} sur les votes qui engagent n'a été émis par "
              f"un collègue&nbsp;: {g['il']} a voté en personne à chaque fois. "
              f"La médiane de l'Assemblée est à {pct(dist['mediane'])}{NBSP}%.")
        p2 = ("La délégation est un droit, pas un manquement&nbsp;: un député "
              "empêché confie son vote à un collègue, qui l'exprime en son nom. "
              "Ce chiffre ne dit donc pas que ce député travaille davantage — "
              "il dit que sa présence aux votes est une présence en séance.")
        return p1, p2

    part = delegues / exprimes
    situation = situer(
        part, dist["mediane"],
        f"C'est <b>au-dessus</b> de la médiane de l'Assemblée, qui est de "
        f"{pct(dist['mediane'])}{NBSP}%{NBSP}: "
        f"{combien(au_dessus, dist['n'], 'le fait davantage', 'le font davantage')}.",
        f"C'est <b>en dessous</b> de la médiane de l'Assemblée, qui est de "
        f"{pct(dist['mediane'])}{NBSP}%.",
        f"C'est <b>l'ordre de grandeur habituel</b>&nbsp;: la médiane de "
        f"l'Assemblée est à {pct(dist['mediane'])}{NBSP}%.",
    )
    p1 = (f"<b>{num(delegues)} des {num(exprimes)} suffrages</b> exprimés au nom "
          f"de {echapper(i['nom'])} sur les votes qui engagent ont été émis par "
          f"un collègue mandaté. {situation}")

    if part >= 0.5:
        p1 += (f" <b>Au-delà de la moitié, «&nbsp;présence aux votes&nbsp;» cesse "
               f"de décrire une présence</b>&nbsp;: sur ces scrutins, la voix de "
               f"{echapper(i['nom'])} a été portée par quelqu'un d'autre plus "
               f"souvent que par {g['lui']}-même.")

    # Le contexte du groupe, comme pour la dissidence : la délégation est très
    # largement une pratique collective, et un taux personnel lu sans celui de
    # son groupe fait passer une habitude de banc pour un trait de caractère.
    if groupe and groupe.get("part_delegation") is not None:
        p2 = (f"La délégation est d'abord une pratique de groupe. Dans "
              f"{echapper(i['groupe'])}, {pct(groupe['part_delegation'])}{NBSP}% "
              f"des suffrages qui engagent sont émis par un mandataire&nbsp;— "
              f"{num(groupe['delegues_groupe'])} sur "
              f"{num(groupe['engageants_groupe'])}. ")
        p2 += situer(
            part, groupe["part_delegation"],
            f"<b>Chez {g['lui']}, {echapper(i['nom'])} délègue plus que la "
            f"moyenne de son groupe.</b>",
            f"<b>Chez {g['lui']}, {echapper(i['nom'])} délègue moins que la "
            f"moyenne de son groupe.</b>",
            f"<b>{echapper(i['nom'])} y est dans la moyenne de son groupe.</b>",
        )
    else:
        p2 = ("La délégation est un droit&nbsp;: un député empêché confie son "
              "vote à un collègue, qui l'exprime en son nom, et le suffrage lui "
              "est bien imputé. Ce taux ne mesure donc pas un manquement — il "
              "mesure la part de sa voix qu'un autre a portée.")
    return p1, p2


def phrase_delegation_assemblee(delegation: dict, groupes: list[dict]) -> tuple[str, str]:
    """La délégation à l'échelle de l'Assemblée, pour la page des accords.

    Elle a sa place ici et pas seulement sur les fiches&nbsp;: «&nbsp;qui vote
    avec qui&nbsp;» compte des suffrages, et près d'un quart d'entre eux n'ont
    pas été émis par la personne à qui ils sont imputés. Un accord entre deux
    députés est donc en partie un accord entre deux mandataires.
    """
    eng, tous = delegation["engageants"], delegation["tous"]
    mesures = [g for g in groupes if g.get("part_delegation") is not None]
    p1 = (f"<b>{num(eng['delegues'])} des {num(eng['exprimes'])} suffrages</b> "
          f"exprimés sur les votes qui engagent l'ont été par un collègue "
          f"mandaté, soit {pct(eng['part'])}{NBSP}%. Sur l'ensemble des "
          f"scrutins, la part tombe à {pct(tous['part'])}{NBSP}% "
          f"({num(tous['delegues'])} sur {num(tous['exprimes'])})&nbsp;: on "
          f"délègue d'autant plus que le vote compte.")
    if not mesures:
        return p1, ""
    bas = min(mesures, key=lambda g: g["part_delegation"])
    haut = max(mesures, key=lambda g: g["part_delegation"])
    p2 = (f"Aucun groupe n'y échappe. Du moins délégataire au plus délégataire, "
          f"l'écart va de <b>{pct(bas['part_delegation'])}{NBSP}%</b> "
          f"({echapper(bas['groupe'])}) à <b>{pct(haut['part_delegation'])}"
          f"{NBSP}%</b> ({echapper(haut['groupe'])})&nbsp;— soit "
          f"{dec(100 * (haut['part_delegation'] - bas['part_delegation']), 1)} "
          f"points d'écart entre les deux extrêmes, quand la pratique elle-même "
          f"concerne tout le monde.")
    return p1, p2


#: Ce que chaque statut de vote s'appelle sur la page, et l'ordre d'affichage
#: des filtres. Le libellé n'est jamais le code : « empeche » ne se lit pas.
STATUTS = (
    ("pour", "Pour"),
    ("contre", "Contre"),
    ("abstention", "Abstention"),
    ("absent", "Absent"),
    ("empeche", "Ne pouvait pas voter"),
    ("hors_mandat", "Hors mandat"),
)


def phrase_journal(resume: dict, i: dict) -> tuple[str, str]:
    """L'introduction du relevé texte par texte.

    Le relevé est la pièce qui rend les taux opposables&nbsp;: tant qu'un
    lecteur ne voit pas les scrutins, il ne peut que croire ou ne pas croire un
    pourcentage. La phrase dit donc ce qu'il va compter, et ce qu'il ne pourra
    pas y lire.
    """
    g = accords(i)
    presents = resume["total"] - resume["hors_mandat"]
    assiette = (
        f"les {num(resume['total'])} votes qui engagent de la législature"
        if presents == resume["total"] else
        f"les {num(presents)} votes qui engagent tenus pendant son mandat, "
        f"sur {num(resume['total'])}"
    )
    p1 = (f"Voici {assiette}, du plus récent au plus ancien, avec la position de "
          f"{echapper(i['nom'])} sur chacun. <b>{num(resume['pour'])} pour, "
          f"{num(resume['contre'])} contre, {num(resume['abstention'])} "
          f"abstentions</b>, et {num(resume['absent'])} scrutins sans suffrage "
          f"exprimé. C'est le détail de tous les chiffres de cette page&nbsp;: "
          f"chaque taux plus haut se recompte ici, ligne à ligne.")
    ecart = (
        f" {num(resume['dissidents'])} de ces votes s'écartent de la ligne de son "
        f"groupe et sont signalés comme tels."
        if resume["dissidents"] else ""
    )
    p2 = (f"Un vote «&nbsp;pour&nbsp;» n'est pas une adhésion, et un vote "
          f"«&nbsp;contre&nbsp;» n'est pas un rejet du sujet&nbsp;: on vote sur "
          f"un texte tel qu'il ressort des débats, avec ses amendements et ses "
          f"compromis. La mention en capitales dit ce qu'{g['il']} a voté, "
          f"jamais pourquoi.{ecart}")
    return p1, p2


def phrase_amendements(am: dict, i: dict, dist: dict, au_dessus: int,
                       groupe: dict | None) -> tuple[str, str]:
    """Ce que le député a proposé, et pourquoi le nombre ne se lit pas seul.

    C'est la mesure la plus facile à mal lire du site&nbsp;: un compte
    d'amendements ressemble à un compte de travail, alors qu'il additionne le
    travail de rédaction et la tactique de séance. Le contre-argument n'est pas
    une précaution ici, c'est la moitié de l'information.
    """
    g = accords(i)
    deposes = am.get("deposes") or 0
    if not deposes:
        return ("Aucun amendement déposé par ce député ne figure dans les "
                "données publiées par l'Assemblée sur cette législature.",
                "L'absence de dépôt n'est pas l'absence de travail&nbsp;: un "
                "député écrit aussi en commission, en rapport et en "
                "cosignature — ce que ce compte-là ne voit pas.")
    examines, adoptes = am.get("examines") or 0, am.get("adoptes") or 0
    situation = situer(
        deposes, dist["mediane"],
        f"C'est <b>au-dessus</b> de la médiane de l'Assemblée, qui est de "
        f"{num(dist['mediane'])} amendements&nbsp;: "
        f"{combien(au_dessus, dist['n'], 'en dépose davantage', 'en déposent davantage')}.",
        f"C'est <b>en dessous</b> de la médiane de l'Assemblée, qui est de "
        f"{num(dist['mediane'])} amendements.",
        f"C'est <b>dans la moyenne</b> de l'Assemblée, dont la médiane est de "
        f"{num(dist['mediane'])} amendements.",
        seuil=0.2,
    )
    p1 = (f"{echapper(i['nom'])} a déposé <b>{num(deposes)} amendement"
          f"{pluriel(deposes)}</b> comme auteur principal. {situation}")
    if examines and adoptes:
        p1 += (f" {num(adoptes)} {'ont' if adoptes >= 2 else 'a'} été "
               f"adopté{pluriel(adoptes)}, sur {num(examines)} dont l'Assemblée "
               f"a tranché le sort — soit {pct(adoptes / examines)}{NBSP}%.")
    elif examines:
        p1 += (f" Aucun n'a été adopté, sur {num(examines)} dont l'Assemblée a "
               f"tranché le sort.")

    # L'objection, toujours dans le même paragraphe que le chiffre : déposer
    # beaucoup peut être un travail de rédaction comme une tactique de séance,
    # et rien dans les données ne permet de trancher entre les deux.
    p2 = ("<b>Ce nombre ne mesure pas un effort.</b> Déposer trois cents "
          "amendements sur un texte est tantôt un travail de rédaction ligne à "
          "ligne, tantôt une manœuvre pour occuper la séance&nbsp;: les données "
          "ne distinguent pas les deux, et ce site ne prétend pas le faire. ")
    if groupe:
        p2 += (f"Le taux d'adoption, lui, dépend surtout d'un fait extérieur au "
               f"député&nbsp;: un amendement de la majorité passe, un amendement "
               f"de l'opposition tombe. Il se lit donc contre celui de "
               f"{echapper(i['groupe'])}, pas contre celui de l'Assemblée.")
    else:
        p2 += ("Le taux d'adoption, lui, dépend surtout d'un fait extérieur au "
               "député : un amendement de la majorité passe, un amendement de "
               "l'opposition tombe.")
    return p1, p2


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


# ── les phrases d'un scrutin ───────────────────────────────────────────────

def phrase_sort(s: dict) -> str:
    """Ce que l'Assemblée a décidé, dite avec l'écart qui l'a décidé.

    « Adopté » seul ne dit pas si le texte a traversé l'hémicycle sans bruit ou
    s'il est passé à quatre voix. L'écart est donc dans la même phrase que le
    verdict, et il est calculé sur les suffrages exprimés — pas sur les 577
    sièges, dont la plupart n'ont rien exprimé.
    """
    pour, contre = s["n_pour"] or 0, s["n_contre"] or 0
    ecart = abs(pour - contre)
    verdict = (s["sort_libelle"] or "").strip().rstrip(".")
    verdict = verdict[0].upper() + verdict[1:] if verdict else "Résultat inconnu"
    if not (pour or contre):
        return f"{echapper(verdict)}."
    return (f"{echapper(verdict)}, par <b>{num(pour)} voix contre {num(contre)}</b> — "
            f"un écart de {num(ecart)} voix.")


def phrase_delegation_scrutin(resume: dict) -> str:
    """La part déléguée de ce scrutin, quand elle est mesurable.

    Elle n'a pas de sens sans son dénominateur — « 169 délégations » se lit
    comme beaucoup ou comme peu selon qu'on les rapporte à 364 suffrages ou à
    577 sièges — donc les deux nombres sont dans la phrase, ou la phrase
    n'existe pas.
    """
    exprimes, delegues = resume.get("exprimes", 0), resume.get("delegues", 0)
    if not exprimes or not delegues:
        return ""
    # On dit « émis » et non « exprimés ». « Suffrages exprimés » a un sens
    # réglementaire précis, écrit deux lignes plus haut — pour et contre, sans
    # les abstentions — et le dénominateur de la délégation, lui, comprend les
    # abstentions. Deux nombres différents sous le même mot, dans le même bloc,
    # auraient donné une page qui semble se contredire.
    return (f"<b>{num(delegues)} des {num(exprimes)} suffrages émis</b>, "
            f"abstentions comprises ({pct(delegues / exprimes, 0)}{NBSP}%), l'ont "
            f"été par un collègue mandaté&nbsp;: le règlement les impute au "
            f"député, ce site aussi, et le relevé ci-dessous les signale un par un.")


def phrase_groupes_scrutin(groupes: list[dict], resume: dict) -> str:
    """Comment les groupes se sont partagés, sans nommer de camp.

    On compte les groupes de chaque côté et ceux qui se sont divisés. Aucune
    étiquette politique n'entre ici : « la gauche a voté contre » serait une
    lecture, pas une mesure, et ce site n'en publie pas.
    """
    pour = [g for g in groupes if g.get("majoritaire") == "pour"]
    contre = [g for g in groupes if g.get("majoritaire") == "contre"]
    partages = [g for g in groupes if g.get("partage")]

    def dire(gs: list[dict], sens: str) -> str:
        noms = ", ".join(echapper(g["groupe"]) for g in gs if g.get("groupe"))
        return f"<b>{num(len(gs))} groupe{pluriel(len(gs))}</b> {sens} ({noms})"

    morceaux = []
    if pour:
        morceaux.append(dire(pour, "ont majoritairement voté pour"
                             if len(pour) >= 2 else "a majoritairement voté pour"))
    if contre:
        morceaux.append(dire(contre, "contre"))
    if not morceaux:
        return ("Aucun groupe n'a dégagé de position majoritaire sur ce scrutin.")

    phrase = " et ".join(morceaux) + "."
    phrase = phrase[0].upper() + phrase[1:]
    if partages:
        noms = ", ".join(echapper(g["groupe"]) for g in partages if g.get("groupe"))
        phrase += (f" {num(len(partages))} groupe{pluriel(len(partages))} "
                   f"{'se sont partagés' if len(partages) >= 2 else 's est partagé'} "
                   f"à égalité ({noms})&nbsp;: pas de position, donc pas d'écart à "
                   f"la ligne possible ce jour-là.")
    dissidents = resume.get("dissidents", 0)
    if dissidents:
        phrase += (f" <b>{num(dissidents)} député{pluriel(dissidents)}</b> "
                   f"{'se sont séparés' if dissidents >= 2 else 's est séparé'} "
                   f"de la position de son groupe.")
    return phrase


def phrase_releve(resume: dict, apercu: dict) -> str:
    """L'introduction du relevé nominatif : ce qu'on va y compter.

    Le nombre d'absents est le chiffre le plus explosif de la page, et c'est
    celui qui se prête le plus à une lecture fausse. Il est donc donné avec ce
    qui le relativise dans la même phrase : les empêchés, les hors-mandat, et
    le rappel que la source ne publie aucun motif.
    """
    total = resume.get("total", 0)
    hors = resume.get("hors_mandat", 0)
    concernes = total - hors
    exprimes = resume.get("exprimes", 0)
    absents = resume.get("absent", 0)
    empeches = resume.get("empeche", 0)

    p = (f"<b>{num(exprimes)} suffrages exprimés</b> sur "
         f"{num(concernes)} députés dont le mandat courait ce jour-là.")
    if absents:
        p += (f" {num(absents)} n'{'ont' if absents >= 2 else 'a'} pas pris part au "
              f"vote&nbsp;: la source ne publie aucun motif, et ce site n'en "
              f"invente pas.")
    if empeches:
        p += (f" {num(empeches)} ne pouvai{'en' if empeches >= 2 else ''}t pas voter "
              f"— ministre, présidence de séance.")
    if hors:
        p += (f" {num(hors)} des {num(total)} députés de la législature n'étaient "
              f"pas en fonction à cette date&nbsp;; ils sont signalés comme tels "
              f"plutôt que comptés absents.")
    return p


def phrase_loi(liens: dict, s: dict, apercu: dict) -> str:
    """Le rattachement du scrutin à sa loi — ou l'aveu qu'il manque.

    Les deux cas produisent une phrase, et c'est le point. Un lien simplement
    absent laisserait croire que le vote ne porte sur aucun texte&nbsp;; la
    lacune est donc nommée, datée, et rapportée au nombre de scrutins qu'elle
    touche.
    """
    dos = apercu["dossiers"]
    if liens.get("dossier"):
        p = (f"Ce vote est un épisode du dossier législatif "
             f"<b>«&nbsp;{echapper(liens['dossier_titre'])}&nbsp;»</b>, "
             f"que l'Assemblée publie <a href=\"{liens['dossier']}\">ici</a> "
             f"avec l'ensemble de ses étapes.")
        n = liens.get("amendements_du_dossier")
        if n:
            adoptes = liens.get("amendements_adoptes") or 0
            examines = liens.get("amendements_examines") or 0
            p += (f" <b>{num(n)} amendement{pluriel(n)}</b> ont été déposés sur "
                  f"cette loi")
            if examines:
                p += (f" — {num(examines)} ont vu leur sort tranché, "
                      f"{num(adoptes)} {'ont' if adoptes >= 2 else 'a'} été "
                      f"adopté{pluriel(adoptes)}")
            p += (". Ce compte est celui de la loi entière et non de ce scrutin&nbsp;: "
                  "un amendement porte sur un texte, et l'immense majorité n'est "
                  "jamais mise aux voix.")
        return p
    return (f"<b>La source ne rattache pas ce scrutin à un dossier législatif.</b> "
            f"L'Assemblée ne renseigne ce champ que depuis le {jour(dos['depuis'])}&nbsp;: "
            f"{num(dos['scrutins_sans'])} des {num(dos['scrutins_avec'] + dos['scrutins_sans'])} "
            f"scrutins de la législature n'en portent aucun. Ce n'est pas un vote "
            f"sans loi — le titre du scrutin la nomme en toutes lettres ci-dessus — "
            f"c'est un vote dont la source tait la loi, et nous ne fabriquons pas "
            f"le rattachement à sa place.")
