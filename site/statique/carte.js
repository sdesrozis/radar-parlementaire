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

  /* La permutation dit « quelle ligne affiche quel député ». Pour allumer la
     case d'une paire nommée dans les champs de recherche, il faut l'inverse :
     « à quelle ligne se trouve ce député ». On la recalcule à chaque bascule
     d'ordre plutôt que de la déduire à la volée à chaque image. */
  var rangDe = new Int32Array(n);

  function permutation() {
    return ordre === "groupe" ? parGroupe : identite;
  }

  function majRangs() {
    var perm = permutation();
    for (var r = 0; r < n; r++) rangDe[perm[r]] = r;
  }
  majRangs();

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
    var perm = permutation();
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


  // ── la paire retenue : épinglée, survolée, ou aucune ────────────────────

  // Des indices de *députés*, et non de lignes : une paire épinglée doit
  // survivre à la bascule d'ordre, qui ne change que la place des lignes.
  var epingle = null;      // {a, b} ou null
  var survole = null;      // {a, b} ou null

  function courante() {
    return epingle || survole;
  }

  function dessiner() {
    var ctx = toile.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, toile.width, toile.height);
    ctx.drawImage(hors, 0, 0);
    if (epingle) marquer(ctx, epingle);
  }

  /* La paire épinglée reste visible quand la souris s'en va. Deux filets sur
     toute la largeur pour retrouver les deux députés dans le tableau, et un
     carré à leur croisement — de cinq cases de côté, parce qu'une case sur 574
     ne se voit pas. La matrice étant symétrique, on marque les deux
     croisements : celui qu'on a désigné et son reflet. */
  function marquer(ctx, p) {
    var l = rangDe[p.a];
    var c = rangDe[p.b];
    var vif = "rgb(" + rvb("--accent-vif").join(",") + ")";

    ctx.save();
    ctx.globalAlpha = 0.3;
    ctx.fillStyle = vif;
    ctx.fillRect(0, l, n, 1);
    ctx.fillRect(c, 0, 1, n);
    if (p.a !== p.b) {
      ctx.fillRect(0, c, n, 1);
      ctx.fillRect(l, 0, 1, n);
    }
    ctx.restore();

    ctx.save();
    ctx.strokeStyle = vif;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(c - 2.5, l - 2.5, 6, 6);
    if (p.a !== p.b) ctx.strokeRect(l - 2.5, c - 2.5, 6, 6);
    ctx.restore();
  }

  // ── lecture ─────────────────────────────────────────────────────────────

  var lecture = document.getElementById("carte-lecture");
  var invite = lecture.querySelector(".carte-invite");
  var bloc = lecture.querySelector(".carte-paire");
  var nomA = lecture.querySelector(".carte-nom-a");
  var nomB = lecture.querySelector(".carte-nom-b");
  var taux = lecture.querySelector(".carte-taux");
  var denom = lecture.querySelector(".carte-denominateur");
  var ligneEpingle = lecture.querySelector(".carte-epingle");
  var detacher = document.getElementById("carte-detacher");
  var viseur = document.querySelector(".carte-viseur");

  function pourcent(x) {
    return (100 * x).toFixed(1).replace(".", ",") + " %";
  }

  function nombre(x) {
    return String(x).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function decrire(depute) {
    return depute.n + " <span class=\"grp\">" + depute.g + "</span>";
  }

  function afficher() {
    var p = courante();
    if (!p) {
      invite.hidden = false;
      bloc.hidden = true;
      viseur.hidden = true;
      return;
    }

    invite.hidden = true;
    bloc.hidden = false;
    ligneEpingle.hidden = !epingle;
    nomA.innerHTML = decrire(D.deputes[p.a]);
    nomB.innerHTML = p.a === p.b ? "" : decrire(D.deputes[p.b]);

    if (p.a === p.b) {
      taux.textContent = "—";
      denom.textContent = "La diagonale : chacun avec lui-même. Ce n'est pas une mesure.";
    } else {
      var i = paire(p.a, p.b);
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

    // Le viseur suit la paire lue, épinglée ou non : sans cela, nommer une
    // paire dans les champs n'indiquerait rien dans le tableau.
    viseur.hidden = false;
    viseur.style.left = ((rangDe[p.b] + 0.5) / n) * 100 + "%";
    viseur.style.top = ((rangDe[p.a] + 0.5) / n) * 100 + "%";
  }

  function choisir(p) {
    epingle = p;
    if (p) majChamps(p);
    dessiner();
    afficher();
  }

  function detacherPaire() {
    epingle = null;
    survole = null;
    dessiner();
    afficher();
  }

  // ── pointage : souris, tactile, clavier ─────────────────────────────────

  function caseSous(ev) {
    var r = toile.getBoundingClientRect();
    var c = Math.floor(((ev.clientX - r.left) / r.width) * n);
    var l = Math.floor(((ev.clientY - r.top) / r.height) * n);
    if (c < 0 || l < 0 || c >= n || l >= n) return null;
    var perm = permutation();
    return { a: perm[l], b: perm[c] };
  }

  toile.addEventListener("mousemove", function (ev) {
    if (epingle) return;          // une paire épinglée ne se laisse pas balayer
    survole = caseSous(ev);
    afficher();
  });

  toile.addEventListener("mouseleave", function () {
    survole = null;
    afficher();
  });

  // `click` couvre la souris *et* le tactile, et ne bloque pas le défilement
  // de la page comme le ferait un gestionnaire de `touchstart`.
  toile.addEventListener("click", function (ev) {
    var p = caseSous(ev);
    if (p) choisir(p);
  });

  toile.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { detacherPaire(); return; }

    var pas = ev.shiftKey ? 10 : 1;
    var perm = permutation();
    var p = courante() || { a: perm[0], b: perm[0] };
    var l = rangDe[p.a];
    var c = rangDe[p.b];

    if (ev.key === "ArrowUp") l -= pas;
    else if (ev.key === "ArrowDown") l += pas;
    else if (ev.key === "ArrowLeft") c -= pas;
    else if (ev.key === "ArrowRight") c += pas;
    else return;

    ev.preventDefault();
    var borne = function (x) { return x < 0 ? 0 : x > n - 1 ? n - 1 : x; };
    choisir({ a: perm[borne(l)], b: perm[borne(c)] });
  });

  detacher.addEventListener("click", function () {
    detacherPaire();
    toile.focus();
  });

  // ── nommer la paire plutôt que la pointer ───────────────────────────────

  /* `plier` est redéfini ici et non emprunté à `radar.js` : les deux fichiers
     sont des scripts classiques chargés dans un ordre que rien ne garantit, et
     une dépendance implicite entre eux est exactement le genre de lien qui
     casse en silence — c'est déjà arrivé avec `DONNEES_CARTE`. Une ligne
     dupliquée coûte moins cher qu'un couplage invisible. */
  function plier(s) {
    return s.normalize("NFD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  var cles = D.deputes.map(function (d) { return plier(d.n + " " + d.g); });
  var champs = {};

  Array.prototype.forEach.call(
    document.querySelectorAll(".carte-choix"),
    function (hote) {
      var cote = hote.getAttribute("data-cote");
      var champ = hote.querySelector("input");
      var liste = hote.querySelector(".carte-suggestions");
      var vider = hote.querySelector(".effacer");
      champs[cote] = champ;

      function fermer() {
        liste.hidden = true;
        liste.innerHTML = "";
        champ.setAttribute("aria-expanded", "false");
      }

      function proposer() {
        var mots = plier(champ.value).split(" ").filter(Boolean);
        vider.hidden = !champ.value;
        if (!mots.length) { fermer(); return; }

        var trouves = [];
        for (var i = 0; i < n && trouves.length < 8; i++) {
          if (mots.every(function (m) { return cles[i].indexOf(m) >= 0; })) trouves.push(i);
        }
        if (!trouves.length) { fermer(); return; }

        liste.innerHTML = trouves.map(function (i) {
          return '<li role="option" tabindex="-1" data-i="' + i + '">'
            + D.deputes[i].n + '<span class="grp">' + D.deputes[i].g + "</span></li>";
        }).join("");
        liste.hidden = false;
        champ.setAttribute("aria-expanded", "true");
      }

      function retenir(i) {
        var p = courante();
        // Sans second député déjà choisi, on met le même des deux côtés : la
        // diagonale s'affiche comme telle, et la moitié du geste est faite.
        var voisin = p ? (cote === "a" ? p.b : p.a) : i;
        choisir(cote === "a" ? { a: i, b: voisin } : { a: voisin, b: i });
        fermer();
        if (!p) champs[cote === "a" ? "b" : "a"].focus();
      }

      champ.addEventListener("input", proposer);
      champ.addEventListener("focus", proposer);
      // Le délai laisse le clic sur une suggestion se produire avant que le
      // `blur` ne l'efface — sinon la liste disparaît sous le doigt.
      champ.addEventListener("blur", function () { setTimeout(fermer, 150); });
      champ.addEventListener("keydown", function (ev) {
        var premier = liste.querySelector("li");
        if (ev.key === "Escape") { fermer(); return; }
        if (ev.key === "Enter" && premier) {
          ev.preventDefault();
          retenir(parseInt(premier.getAttribute("data-i"), 10));
        }
        if (ev.key === "ArrowDown" && premier) { ev.preventDefault(); premier.focus(); }
      });
      liste.addEventListener("click", function (ev) {
        var li = ev.target.closest("li");
        if (li) retenir(parseInt(li.getAttribute("data-i"), 10));
      });
      liste.addEventListener("keydown", function (ev) {
        var li = document.activeElement;
        if (ev.key === "Enter") {
          ev.preventDefault();
          retenir(parseInt(li.getAttribute("data-i"), 10));
          return;
        }
        if (ev.key === "Escape") { fermer(); champ.focus(); return; }
        var suivant = ev.key === "ArrowDown" ? li.nextElementSibling
          : ev.key === "ArrowUp" ? li.previousElementSibling : null;
        if (suivant) { ev.preventDefault(); suivant.focus(); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); champ.focus(); }
      });
      vider.addEventListener("click", function () {
        champ.value = "";
        vider.hidden = true;
        fermer();
        champ.focus();
      });
    }
  );

  function majChamps(p) {
    if (champs.a) { champs.a.value = D.deputes[p.a].n; }
    if (champs.b) { champs.b.value = D.deputes[p.b].n; }
    Array.prototype.forEach.call(
      document.querySelectorAll(".carte-choix .effacer"),
      function (b) { b.hidden = false; }
    );
  }

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
        // Les rangs changent, la paire épinglée non : elle se retrouve
        // ailleurs dans le tableau, ce qui est précisément ce qu'on veut voir.
        majRangs();
        survole = null;
        peindre();
        afficher();
      });
    }
  );

  // ── convention de calcul du tableau des groupes ─────────────────────────

  /* Les deux valeurs sont dans le HTML dès la génération : basculer ne
     recalcule rien et ne demande rien au serveur, cela change la classe du
     tableau. La légende suit, sans quoi le lecteur verrait des nombres bouger
     sans savoir lesquels il lit. */
  var tableau = document.getElementById("matrice-groupes");
  var legende = document.getElementById("legende-convention");
  var textesConvention = {
    paires: legende ? legende.innerHTML : "",
    agregee: "Part des scrutins o\u00f9 deux d\u00e9put\u00e9s de ces groupes votent pareil, "
      + "<b>rapport\u00e9e \u00e0 l'ensemble de leurs votes communs</b>\u00a0: les bin\u00f4mes "
      + "les plus longuement observ\u00e9s y p\u00e8sent le plus. Survolez une case pour "
      + "son nombre de paires et de scrutins communs."
  };

  Array.prototype.forEach.call(
    document.querySelectorAll(".bascules-convention button"),
    function (bouton) {
      bouton.addEventListener("click", function () {
        var choix = bouton.getAttribute("data-convention");
        if (tableau) tableau.classList.toggle("vue-agregee", choix === "agregee");
        if (legende) legende.innerHTML = textesConvention[choix];
        Array.prototype.forEach.call(
          document.querySelectorAll(".bascules-convention button"),
          function (b) { b.setAttribute("aria-pressed", String(b === bouton)); }
        );
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
