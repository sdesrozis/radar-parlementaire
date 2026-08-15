/* ═══════════════════════════════════════════════════════════════════════
   La bande des 577 — le dispositif central du site.
   Un chiffre isolé se lit comme un verdict. Le même chiffre posé sur la
   distribution de l'Assemblée se lit comme une position. On ne juge pas :
   on situe.
   ═══════════════════════════════════════════════════════════════════════ */

/* `DONNEES` est défini par la page elle-même : les distributions changent à chaque
   génération, ce fichier ne change pas. C'est la frontière entre statique et dynamique. */

const SVGNS = "http://www.w3.org/2000/svg";
const el = (nom, attrs = {}) => {
  const n = document.createElementNS(SVGNS, nom);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

/* Trois formats, parce que trois natures de mesure : une part, un compte, une
   coordonnée sur l'axe. Un compte affiché « 121,00 » ferait douter du chiffre. */
const fmt = (v, mode) =>
  mode === "pct" ? (100 * v).toFixed(1).replace(".", ",") + " %"
  : mode === "num" ? Math.round(v).toLocaleString("fr-FR")
  : v.toFixed(2).replace(".", ",");

function bande(hote) {
  const serie = DONNEES[hote.dataset.bande];
  const valeurs = serie.valeurs;
  const mode = hote.dataset.format;
  const valeur = parseFloat(hote.dataset.valeur);
  const bas = hote.dataset.bas ? parseFloat(hote.dataset.bas) : null;
  const haut = hote.dataset.haut ? parseFloat(hote.dataset.haut) : null;

  const H = 56, MARGE = 4;
  const HAUT_T = 6, BAS_T = 44;            /* zone des barres */

  const min = Math.min(serie.min, bas ?? valeur);
  const max = Math.max(serie.max, haut ?? valeur);
  const etendue = max - min || 1;

  /* Le repère du SVG est en pixels réels, redéfini à chaque redimensionnement.
     Sans cela il faudrait étirer le tracé (preserveAspectRatio="none"), ce qui
     crénelle les barres et oblige à contre-déformer le texte. Ici, une unité
     SVG vaut un pixel : les barres tombent sur la grille et restent nettes. */
  const svg = el("svg", {
    class: "bande-svg", role: "img",
    "aria-label":
      `Répartition des ${valeurs.length} députés en exercice. ` +
      `${hote.dataset.etiquette} : ${fmt(valeur, mode)}. ` +
      `Médiane de l'Assemblée : ${fmt(serie.mediane, mode)}.`
  });
  hote.appendChild(svg);

  const etiquettes = ["etiq-sujet", "etiq-mediane"].map((classe) => {
    const s = document.createElement("span");
    s.className = "etiq " + classe;
    hote.appendChild(s);
    return s;
  });

  const dessiner = () => {
    const L = hote.clientWidth;
    if (!L) return;
    svg.setAttribute("viewBox", `0 0 ${L} ${H}`);
    svg.setAttribute("width", L);
    svg.setAttribute("height", H);
    svg.replaceChildren();

    const x = (v) => MARGE + ((v - min) / etendue) * (L - 2 * MARGE);

    /* L'intervalle de confiance, posé derrière tout le reste. */
    if (bas !== null && haut !== null) {
      svg.appendChild(el("rect", {
        class: "bande-ic", x: x(bas), y: HAUT_T - 4,
        width: Math.max(2, x(haut) - x(bas)), height: BAS_T - HAUT_T + 8
      }));
    }

    /* Une barre par député, en rectangles distincts et non en un tracé unique :
       là où plusieurs députés se serrent, les barres se recouvrent et le gris
       s'accumule. La densité devient lisible — c'est tout l'intérêt. */
    const g = el("g", { class: "bande-ticks" });
    for (const v of valeurs) {
      g.appendChild(el("rect", {
        x: Math.round(x(v)), y: HAUT_T, width: 1, height: BAS_T - HAUT_T
      }));
    }
    svg.appendChild(g);

    /* La ligne de base, puis la médiane. */
    svg.appendChild(el("line", {
      class: "bande-axe", x1: MARGE, y1: BAS_T + 8, x2: L - MARGE, y2: BAS_T + 8
    }));
    svg.appendChild(el("line", {
      class: "bande-mediane", x1: Math.round(x(serie.mediane)) + 0.5, y1: HAUT_T - 4,
      x2: Math.round(x(serie.mediane)) + 0.5, y2: BAS_T + 8
    }));

    /* Le sujet : un losange posé au-dessus de la population. */
    const cx = x(valeur), cy = HAUT_T - 4;
    svg.appendChild(el("path", {
      class: "bande-sujet",
      d: `M${cx} ${cy - 6}L${cx + 4.5} ${cy}L${cx} ${cy + 6}L${cx - 4.5} ${cy}Z`
    }));

    const placer = (s, pos, texte) => {
      s.textContent = texte;
      s.style.left = ((100 * pos) / L).toFixed(2) + "%";
      delete s.dataset.ancre;
      if (pos > L - 90) s.dataset.ancre = "fin";
      else if (pos < 90) s.dataset.ancre = "debut";
    };
    placer(etiquettes[0], x(valeur), `${hote.dataset.etiquette} · ${fmt(valeur, mode)}`);
    placer(etiquettes[1], x(serie.mediane), `médiane ${fmt(serie.mediane, mode)}`);
  };

  dessiner();
  new ResizeObserver(dessiner).observe(hote);
}


if (typeof DONNEES !== "undefined") document.querySelectorAll("[data-bande]").forEach(bande);

/* Révélation à l'entrée dans le champ : les barres d'abord, le sujet ensuite.
   C'est la thèse du site jouée en deux temps — la population, puis l'individu. */
const oeil = new IntersectionObserver((entrees) => {
  for (const e of entrees) {
    if (e.isIntersecting) { e.target.classList.add("vu"); oeil.unobserve(e.target); }
  }
}, { threshold: 0.25 });
document.querySelectorAll(".bande").forEach((b) => oeil.observe(b));

/* ═══════════════════════════════════════════════════════════════════════
   La recherche — nom, département, numéro, région, groupe.
   Tout est côté client : l'index des 577 est inclus dans la page, il n'y a
   ni serveur ni requête. Le site reste un dossier de fichiers.
   ═══════════════════════════════════════════════════════════════════════ */

/** « Jean-Luc Mélenchon » et « melenchon » doivent trouver la même chose. */
const pliage = (s) =>
  s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/* Un seul code de recherche pour l'accueil, l'annuaire et les votes. Deux
   implémentations divergeraient : le même mot cherché à deux endroits ne doit
   pas donner deux réponses, et la troisième page l'aurait prouvé — chercher
   « Marne » dans une liste de lois n'a pas à se comporter autrement que dans
   une liste de députés.

   Ce qui varie tient en trois fonctions passées en argument : de quoi est
   faite la clé de recherche, comment se dessine une ligne, comment se dit le
   compte. Ce qui ne varie pas — le pliage des accents, la conjonction des
   mots, la limite, le clavier — n'existe qu'ici.

   Les pages diffèrent en outre par deux attributs posés sur l'hôte :
   `data-limite`, combien de résultats afficher, et `data-vide="masquer"`, qui
   attend une frappe au lieu de dérouler les 574 sous le manifeste. */

function recherche(sujet) {
  const hote = document.querySelector(sujet.hote);
  if (!hote || !sujet.donnees) return;

  const champ = document.querySelector("#q");
  const effacer = document.querySelector("#effacer");
  const liste = document.querySelector("#resultats");
  const compteur = document.querySelector("#compteur");
  const deborde = document.querySelector("#deborde");

  const limite = parseInt(hote.dataset.limite || "0", 10) || Infinity;
  const masquerAVide = hote.dataset.vide === "masquer";

  // L'index est plié une fois pour toutes, pas à chaque frappe.
  const index = sujet.donnees.map((d) => ({ ...d, cle: pliage(sujet.cle(d)) }));

  const dessiner = (q) => {
    const mots = pliage(q).split(" ").filter(Boolean);
    const vide = !mots.length;
    if (vide && masquerAVide) {
      liste.innerHTML = "";
      compteur.hidden = true;
      document.querySelector("#rien").hidden = true;
      if (deborde) deborde.hidden = true;
      effacer.hidden = true;
      return;
    }

    const trouves = vide ? index : index.filter((d) => mots.every((m) => d.cle.includes(m)));
    const montres = trouves.slice(0, limite);

    compteur.hidden = false;
    compteur.innerHTML = sujet.compte(trouves.length, index.length, vide);

    liste.innerHTML = montres.map(sujet.ligne).join("");

    document.querySelector("#rien").hidden = trouves.length > 0;
    if (!trouves.length) document.querySelector("#rien-q").textContent = q;
    if (deborde) {
      deborde.hidden = trouves.length <= montres.length;
      document.querySelector("#deborde-n").textContent = trouves.length;
    }
    effacer.hidden = !q;
  };

  let minuteur;
  champ.addEventListener("input", (e) => {
    clearTimeout(minuteur);
    minuteur = setTimeout(() => dessiner(e.target.value), 90);
  });
  effacer.addEventListener("click", () => { champ.value = ""; dessiner(""); champ.focus(); });
  document.querySelectorAll("[data-exemple]").forEach((b) => {
    b.addEventListener("click", () => { champ.value = b.dataset.exemple; dessiner(b.dataset.exemple); champ.focus(); });
  });

  /* Entrée sur un résultat unique : c'est le cas le plus fréquent quand on
     tape un nom de famille, et rien n'oblige alors à quitter le clavier. */
  champ.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const premier = liste.querySelector("a");
    if (premier) { e.preventDefault(); premier.click(); }
  });

  // « / » met le curseur dans la recherche, comme partout ailleurs.
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== champ) { e.preventDefault(); champ.focus(); }
  });

  dessiner(champ.value || "");
}

/* L'annuaire des députés. Les trois signaux, chacun avec ce qu'il mesure : un
   nombre nu dans une liste se lit comme une note, le libellé en dit la nature,
   et la fiche en donne le dénominateur. Une mesure absente affiche « — » et
   non zéro — la règle vaut ici comme ailleurs. Les valeurs arrivent déjà mises
   en forme par Python : pas de pourcentage fabriqué dans le navigateur. */
recherche({
  hote: "[data-annuaire]",
  donnees: typeof ANNUAIRE === "undefined" ? null : ANNUAIRE,
  cle: (d) => [d.n, d.d, d.dn, d.r, d.g, d.gl].join(" "),
  compte: (n, total, vide) =>
    vide ? `<b>${total}</b> députés en exercice`
         : `<b>${n}</b> député${n > 1 ? "s" : ""} sur ${total}`,
  ligne: (d) => `<li><a href="${d.u}.html">
          <span class="an-nom">${d.n}</span>
          <span class="an-lieu">${d.d} · ${d.c}${d.c === "1" ? "re" : "e"}</span>
          <span class="an-grp">${d.g}</span>
          <span class="an-chiffres">${[
            ["présence", d.pres],
            ["écart au groupe", d.ecart],
            ["position", d.pos],
          ].map(([libelle, valeur]) =>
            `<span class="an-mesure"><b>${valeur || "—"}</b><i>${libelle}</i></span>`
          ).join("")}</span>
        </a></li>`,
});

/* Les votes qui engagent. On cherche dans le titre du scrutin *et* dans le
   titre du dossier législatif : l'un est la formule de séance — « l'ensemble
   de la proposition de loi visant à… » —, l'autre le nom sous lequel la presse
   et le public désignent la loi. Un lecteur qui tape « fin de vie » cherche le
   second et ne connaît pas le premier.

   Le numéro de scrutin est dans la clé parce qu'il est l'identifiant par
   lequel l'Assemblée cite ses propres votes, et donc celui qu'on recopie
   depuis un article de presse. */
recherche({
  hote: "[data-votes]",
  donnees: typeof VOTES === "undefined" ? null : VOTES,
  cle: (v) => [v.t, v.loi, v.n, v.d].join(" "),
  compte: (n, total, vide) =>
    vide ? `<b>${total}</b> votes qui engagent`
         : `<b>${n}</b> vote${n > 1 ? "s" : ""} sur ${total}`,
  ligne: (v) => `<li><a href="${v.u}">
          <span class="vo-date">${v.d}</span>
          <span class="vo-titre">${v.t}${v.loi ? `<i>${v.loi}</i>` : ""}</span>
          <span class="vo-sort ${v.a ? "adopte" : "rejete"}">${v.s}</span>
          <span class="vo-chiffres">${[
            ["pour", v.p],
            ["contre", v.c],
            ["abstentions", v.ab],
          ].map(([libelle, valeur]) =>
            `<span class="an-mesure"><b>${valeur}</b><i>${libelle}</i></span>`
          ).join("")}</span>
        </a></li>`,
});

/* ═══════════════════════════════════════════════════════════════════════
   Le filtre du relevé des votes.

   Il ne fabrique rien : les 245 lignes sont écrites dans la page par le
   générateur, avec leur statut en classe. Le bouton pose un attribut sur la
   liste, et la feuille de style masque le reste. Sans JavaScript, le relevé
   reste entier — c'est la seule façon acceptable de filtrer une pièce
   justificative, qui doit rester lisible et indexable telle quelle.
   ═══════════════════════════════════════════════════════════════════════ */

/* Le filtre sert les deux relevés — les 245 scrutins d'un député, et les 648
   députés d'un scrutin. C'est le même geste sur les deux axes du même tableau,
   donc le même code : `data-cible` désigne la liste à filtrer. */

function filtreReleve(cible) {
  const barre = document.querySelector(`.filtres[data-cible='${cible}']`);
  const liste = document.getElementById(cible);
  if (!barre || !liste) return;
  const vide = document.querySelector(".journal-vide");

  const appliquer = (bouton) => {
    const filtre = bouton.dataset.filtre;
    for (const b of barre.querySelectorAll("button")) {
      b.setAttribute("aria-pressed", String(b === bouton));
    }
    if (filtre === "tous") delete liste.dataset.filtre;
    else liste.dataset.filtre = filtre;
    /* Le message d'impasse ne devrait jamais paraître — un bouton n'existe
       qu'au-dessus de zéro — mais il coûte une ligne et évite qu'une liste
       vide passe pour une page cassée. On compte les classes, pas les pixels. */
    if (vide) {
      const restants = filtre === "tous"
        ? liste.children.length
        : liste.querySelectorAll("li." + filtre).length;
      vide.hidden = restants > 0;
    }
  };

  barre.addEventListener("click", (e) => {
    const bouton = e.target.closest("button");
    if (bouton) appliquer(bouton);
  });

  /* Le relevé s'ouvre sur « Pour ». C'est la question que le lecteur se pose
     en arrivant — qui a voté cette loi — et la liste complète, à 648 lignes,
     ne la lui répond qu'après un long défilement.

     **Ce choix est posé ici et non dans le HTML**, et la nuance n'est pas
     technique. Écrit dans le document, `data-filtre="pour"` masquerait les
     « contre » pour un lecteur sans JavaScript, pour un moteur de recherche et
     dans une page enregistrée : le site publierait une pièce justificative
     amputée de la moitié de son contenu. Posé au chargement, le document reste
     entier — le filtre redevient ce qu'il doit être, un confort d'affichage
     qui ne retire rien à la preuve. Un clic sur « Tous » rend la liste. */
  const defaut = barre.querySelector("button[data-filtre='pour']");
  if (defaut) appliquer(defaut);
}

filtreReleve("journal");
filtreReleve("nominatif-liste");
