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

const fmt = (v, mode) =>
  mode === "pct" ? (100 * v).toFixed(1).replace(".", ",") + " %" : v.toFixed(2).replace(".", ",");

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

/* Un seul code de recherche pour l'accueil et l'annuaire. Deux implémentations
   divergeraient : le même mot cherché à deux endroits ne doit pas donner deux
   réponses. Les pages ne diffèrent que par deux attributs posés sur l'hôte —
   `data-limite`, combien de résultats afficher, et `data-vide="masquer"`, qui
   attend une frappe au lieu de dérouler les 574 sous le manifeste. */

function recherche() {
  const hote = document.querySelector("[data-annuaire]");
  if (!hote || typeof ANNUAIRE === "undefined") return;

  const champ = document.querySelector("#q");
  const effacer = document.querySelector("#effacer");
  const liste = document.querySelector("#resultats");
  const compteur = document.querySelector("#compteur");
  const deborde = document.querySelector("#deborde");

  const limite = parseInt(hote.dataset.limite || "0", 10) || Infinity;
  const masquerAVide = hote.dataset.vide === "masquer";

  // L'index est plié une fois pour toutes, pas à chaque frappe.
  const index = ANNUAIRE.map((d) => ({ ...d, cle: pliage([d.n, d.d, d.dn, d.r, d.g, d.gl].join(" ")) }));

  /* Les trois signaux d'un député, chacun avec ce qu'il mesure. Un nombre nu
     dans une liste se lit comme une note ; le libellé en dit la nature, et la
     fiche en donne le dénominateur. Une mesure absente affiche « — » et non
     zéro — la règle vaut ici comme ailleurs. */
  const mesures = (d) => [
    ["présence", d.pres],
    ["écart au groupe", d.ecart],
    ["position", d.pos],
  ].map(([libelle, valeur]) =>
    `<span class="an-mesure"><b>${valeur || "—"}</b><i>${libelle}</i></span>`
  ).join("");

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
    compteur.innerHTML = vide
      ? `<b>${index.length}</b> députés en exercice`
      : `<b>${trouves.length}</b> député${trouves.length > 1 ? "s" : ""} sur ${index.length}`;

    liste.innerHTML = montres.map((d) => `<li><a href="${d.u}.html">
          <span class="an-nom">${d.n}</span>
          <span class="an-lieu">${d.d} · ${d.c}${d.c === "1" ? "re" : "e"}</span>
          <span class="an-grp">${d.g}</span>
          <span class="an-chiffres">${mesures(d)}</span>
        </a></li>`).join("");

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

recherche();
