/* ═══════════════════════════════════════════════════════════════════════
   LA MATRICE DES ACCORDS

   Un fichier statique : aucun jeton {{...}} ne doit y entrer. Les données
   variables sont déclarées par la page, dans DONNEES_CARTE, juste avant que
   ce script soit chargé.

   Le tableau fait 574 × 574 cases, soit ~330 000 pixels. On le peint une fois
   dans une ImageData à l'échelle 1:1, puis on l'agrandit sans lissage : c'est
   instantané, et surtout chaque case reste un carré net. Un lissage ferait
   apparaître des dégradés entre des paires qui n'ont rien à voir — de la
   donnée inventée par l'interpolation.
   ═══════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  // `DONNEES_CARTE` est déclaré par la page avec `const` : dans un script
  // classique, cela crée une liaison lexicale globale et **aucune propriété sur
  // `window`**. Passer par `window.DONNEES_CARTE` donnait `undefined`, et ce
  // fichier sortait en silence sans rien dessiner. On lit donc l'identifiant nu,
  // gardé par `typeof` — la même convention que `radar.js` avec `DONNEES`.
  var D = typeof DONNEES_CARTE !== "undefined" ? DONNEES_CARTE : null;
  var toile = document.getElementById("carte");
  if (!D || !toile) return;

  var n = D.n;
  var ABSENT = D.absent;

  // ── décodage ────────────────────────────────────────────────────────────

  function octets(b64) {
    var brut = atob(b64);
    var a = new Uint8Array(brut.length);
    for (var i = 0; i < brut.length; i++) a[i] = brut.charCodeAt(i);
    return a;
  }

  var accord = octets(D.accord_b64);
  var communs = new Uint16Array(octets(D.communs_b64).buffer);

  // Triangle supérieur strict : (i, j) avec i < j.
  function paire(i, j) {
    if (i === j) return -1;
    if (i > j) { var t = i; i = j; j = t; }
    return i * n - (i * (i + 1)) / 2 + (j - i - 1);
  }

  // ── couleurs : lues dans le thème, jamais écrites ici ───────────────────

  function rvb(nom) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(nom).trim();
    if (v.charAt(0) === "#") {
      if (v.length === 4) v = "#" + v[1] + v[1] + v[2] + v[2] + v[3] + v[3];
      return [parseInt(v.substr(1, 2), 16), parseInt(v.substr(3, 2), 16),
              parseInt(v.substr(5, 2), 16)];
    }
    var m = v.match(/\d+/g) || [0, 0, 0];
    return [+m[0], +m[1], +m[2]];
  }

  var hors = document.createElement("canvas");
  hors.width = hors.height = n;
  var hc = hors.getContext("2d");
  var image = hc.createImageData(n, n);

  var ordre = "axe";
  var identite = new Int32Array(n);
  for (var k = 0; k < n; k++) identite[k] = k;
  var parGroupe = Int32Array.from(D.ordre_groupe);

  // L'échelle est linéaire entre les deux bornes affichées dans la légende.
  // Elle ne part pas de zéro : aucune paire de l'Assemblée n'y descend, et
  // étaler la rampe sur une plage vide écraserait tout le reste en une teinte.
  var bas = D.echelle_bas;
  var haut = D.echelle_haut;

  function melanger(a, b, t) {
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  }

  function peindre() {
    var plein = rvb("--accent");
    // Le bas de la rampe est un indigo très pâle, pas le gris du papier. Sans
    // cela, une case à 0 % d'accord et une case *non mesurable* sont deux gris
    // presque identiques — et le site aurait fait exactement ce qu'il reproche
    // aux autres : afficher une absence de donnée comme un zéro. Ici l'absence
    // se distingue par la teinte (grise, jamais colorée), pas par la clarté.
    var fond = melanger(rvb("--papier-creux"), plein, 0.12);
    var vide = rvb("--trait");
    var diag = rvb("--trait-fort");
    var perm = ordre === "groupe" ? parGroupe : identite;
    var d = image.data;
    var etendue = (haut - bas) || 1;

    for (var r = 0; r < n; r++) {
      var p = perm[r];
      var base = r * n * 4;
      for (var c = 0; c < n; c++) {
        var o = base + c * 4;
        var couleur;
        if (r === c) {
          couleur = diag;
        } else {
          var v = accord[paire(p, perm[c])];
          if (v === ABSENT) {
            couleur = vide;
          } else {
            var t = (v / 254 - bas) / etendue;
            t = t < 0 ? 0 : t > 1 ? 1 : t;
            d[o] = fond[0] + (plein[0] - fond[0]) * t;
            d[o + 1] = fond[1] + (plein[1] - fond[1]) * t;
            d[o + 2] = fond[2] + (plein[2] - fond[2]) * t;
            d[o + 3] = 255;
            continue;
          }
        }
        d[o] = couleur[0];
        d[o + 1] = couleur[1];
        d[o + 2] = couleur[2];
        d[o + 3] = 255;
      }
    }
    hc.putImageData(image, 0, 0);
    dessiner();
  }

  function dessiner() {
    var ctx = toile.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, toile.width, toile.height);
    ctx.drawImage(hors, 0, 0);
  }

  // ── lecture au survol ───────────────────────────────────────────────────

  var lecture = document.getElementById("carte-lecture");
  var invite = lecture.querySelector(".carte-invite");
  var bloc = lecture.querySelector(".carte-paire");
  var nomA = lecture.querySelector(".carte-nom-a");
  var nomB = lecture.querySelector(".carte-nom-b");
  var taux = lecture.querySelector(".carte-taux");
  var denom = lecture.querySelector(".carte-denominateur");
  var viseur = document.querySelector(".carte-viseur");

  function pourcent(x) {
    return (100 * x).toFixed(1).replace(".", ",") + " %";
  }

  function nombre(x) {
    return String(x).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function decrire(depute) {
    return depute.n + " <span class=\"grp\">" + depute.g + "</span>";
  }

  function survol(ev) {
    var r = toile.getBoundingClientRect();
    var c = Math.floor(((ev.clientX - r.left) / r.width) * n);
    var l = Math.floor(((ev.clientY - r.top) / r.height) * n);
    if (c < 0 || l < 0 || c >= n || l >= n) return;

    var perm = ordre === "groupe" ? parGroupe : identite;
    var a = D.deputes[perm[l]];
    var b = D.deputes[perm[c]];

    invite.hidden = true;
    bloc.hidden = false;
    nomA.innerHTML = decrire(a);
    nomB.innerHTML = decrire(b);

    if (perm[l] === perm[c]) {
      taux.textContent = "—";
      denom.textContent = "La diagonale : chacun avec lui-même. Ce n'est pas une mesure.";
      nomB.innerHTML = "";
    } else {
      var i = paire(perm[l], perm[c]);
      var v = accord[i];
      var nc = communs[i];
      if (v === ABSENT) {
        taux.textContent = "non mesuré";
        denom.textContent = nombre(nc) + " scrutins en commun, moins que les "
          + D.min_communs + " requis : un taux calculé là-dessus serait un accident d'échantillon.";
      } else {
        taux.textContent = pourcent(v / 254);
        denom.textContent = "sur " + nombre(nc) + " scrutins où les deux ont voté";
      }
    }

    viseur.hidden = false;
    viseur.style.left = ((c + 0.5) / n) * 100 + "%";
    viseur.style.top = ((l + 0.5) / n) * 100 + "%";
  }

  function quitter() {
    invite.hidden = false;
    bloc.hidden = true;
    viseur.hidden = true;
  }

  toile.addEventListener("mousemove", survol);
  toile.addEventListener("mouseleave", quitter);

  // ── bascule d'ordre ─────────────────────────────────────────────────────

  var etat = document.getElementById("carte-etat");
  var legendes = {
    axe: etat ? etat.textContent : "",
    groupe: "Rangés par groupe : les carrés sont dessinés par le classement, "
      + "pas par les votes. C'est la vue de contrôle."
  };

  Array.prototype.forEach.call(
    document.querySelectorAll(".bascules button"),
    function (bouton) {
      bouton.addEventListener("click", function () {
        ordre = bouton.getAttribute("data-ordre");
        Array.prototype.forEach.call(
          document.querySelectorAll(".bascules button"),
          function (b) {
            b.setAttribute("aria-pressed", String(b === bouton));
          }
        );
        if (etat) etat.textContent = legendes[ordre];
        quitter();
        peindre();
      });
    }
  );

  // ── thème ───────────────────────────────────────────────────────────────

  if (window.matchMedia) {
    var sombre = window.matchMedia("(prefers-color-scheme: dark)");
    if (sombre.addEventListener) sombre.addEventListener("change", peindre);
  }
  new MutationObserver(peindre).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"]
  });

  peindre();
})();
