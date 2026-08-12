/* Radar parlementaire — site local.
 *
 * Une page, quatre vues, aucune dépendance. Le serveur sert des chiffres déjà
 * calculés ; ce fichier ne fait que les mettre en forme — avec une règle : tout
 * taux affiché est accompagné de son dénominateur, en clair ou au survol. Un
 * pourcentage sans effectif est une opinion.
 */

const $ = (sel, racine = document) => racine.querySelector(sel);
const vue = $("#vue");

const etat = {
  apercu: null,
  deputes: null,
  triDeputes: { colonne: "nom_complet", desc: false },
  filtresDeputes: { q: "", groupe: "", exercice: true },
  filtresScrutins: { q: "", portee: "", categorie: "" },
  filtresVotes: { portee: "", dissidents: false },
  echelle: { min: -2.5, max: 2.5 },
};

/* -- outils -------------------------------------------------------------- */

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const pct = (x, dec = 1) => (x == null ? "—" : (100 * x).toFixed(dec) + " %");
const num = (x) => (x == null ? "—" : x.toLocaleString("fr-FR"));
const dec = (x, n = 2) => (x == null ? "—" : x.toFixed(n));
/** 1re circonscription, 2e, 3e… — l'ordinal français ne se réduit pas à « e ». */
const ordinal = (n) => (String(n) === "1" ? "re" : "e");
const jour = (s) => (s ? new Date(s + "T00:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" }) : "—");

const POSITIONS = { pour: "pour", contre: "contre", abstention: "abstention", nonVotant: "non-votant" };

async function api(chemin) {
  const r = await fetch("/api" + chemin);
  const donnees = await r.json();
  if (!r.ok) throw new Error(donnees.erreur || r.statusText);
  return donnees;
}

function place(x) {
  const { min, max } = etat.echelle;
  return Math.max(0, Math.min(100, ((x - min) / (max - min)) * 100));
}

/** L'axe des positions : le point, son intervalle, et le zéro comme repère. */
function axe(p, { mini = false, repere = null } = {}) {
  if (p == null || p.axe1 == null) return '<span class="muet petit">non estimée</span>';
  const bb = p.borne_basse ?? p.axe1;
  const bh = p.borne_haute ?? p.axe1;
  const g = place(bb);
  const largeur = Math.max(1.5, place(bh) - g);
  return `<div class="axe ${mini ? "axe-mini" : ""}">
    <div class="rail"></div>
    <div class="zero" style="left:${place(0)}%"></div>
    ${repere != null ? `<div class="repere" style="left:${place(repere)}%" title="médiane du groupe"></div>` : ""}
    <div class="ic" style="left:${g}%;width:${largeur}%" title="intervalle à 90 %"></div>
    <div class="point" style="left:${place(p.axe1)}%" title="${dec(p.axe1)}"></div>
  </div>`;
}

function barreEmpilee(pour, contre, abstention) {
  const total = (pour || 0) + (contre || 0) + (abstention || 0) || 1;
  const l = (n) => (100 * (n || 0)) / total;
  return `<div class="empilee" title="${pour} pour, ${contre} contre, ${abstention} abstentions">
    <span class="p-pour" style="width:${l(pour)}%"></span>
    <span class="p-contre" style="width:${l(contre)}%"></span>
    <span class="p-abstention" style="width:${l(abstention)}%"></span>
  </div>`;
}

const legendePositions = `<div class="legende">
  <span><i class="p-pour"></i>pour</span>
  <span><i class="p-contre"></i>contre</span>
  <span><i class="p-abstention"></i>abstention</span>
</div>`;

function jauge(x) {
  return `<div class="jauge" title="${pct(x)}"><span style="width:${Math.round(100 * (x || 0))}%"></span></div>`;
}

function lienDepute(uid, nom, groupe) {
  return `<a href="#/depute/${esc(uid)}">${esc(nom)}</a>${groupe ? ` <span class="etiquette">${esc(groupe)}</span>` : ""}`;
}

/* -- vue : liste des députés --------------------------------------------- */

const COLONNES_DEPUTES = [
  { cle: "nom_complet", titre: "Député", num: false },
  { cle: "groupe", titre: "Groupe", num: false },
  { cle: "departement", titre: "Circonscription", num: false, optionnelle: true },
  { cle: "participation", titre: "Participation", num: true },
  { cle: "taux_dissidence", titre: "Dissidence", num: true },
  { cle: "part_abstention", titre: "Abstention", num: true, optionnelle: true },
  { cle: "axe1", titre: "Position estimée", num: true },
];

function deputesFiltres() {
  const f = etat.filtresDeputes;
  const q = f.q.trim().toLowerCase();
  let d = etat.deputes.filter((x) =>
    (!f.exercice || x.en_exercice) &&
    (!f.groupe || x.groupe === f.groupe) &&
    (!q || x.nom_complet.toLowerCase().includes(q) || (x.departement || "").toLowerCase().includes(q)));

  const { colonne, desc } = etat.triDeputes;
  d = d.slice().sort((a, b) => {
    const va = a[colonne], vb = b[colonne];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;            // les valeurs manquantes restent en bas
    if (vb == null) return -1;           // dans les deux sens de tri
    const c = typeof va === "string" ? va.localeCompare(vb, "fr") : va - vb;
    return desc ? -c : c;
  });
  return d;
}

function vueDeputes() {
  const groupes = etat.apercu.groupes.map((g) => g.groupe).sort();
  const f = etat.filtresDeputes;

  vue.innerHTML = `
    <h1>Les députés</h1>
    <p class="sous-titre">${num(etat.apercu.en_exercice)} en exercice sur ${num(etat.apercu.deputes)} ayant siégé
      depuis le début de la législature. Cliquez sur un nom pour sa fiche.</p>
    <div class="filtres">
      <input type="search" id="q" placeholder="Nom ou département…" value="${esc(f.q)}">
      <select id="groupe">
        <option value="">Tous les groupes</option>
        ${groupes.map((g) => `<option ${g === f.groupe ? "selected" : ""}>${esc(g)}</option>`).join("")}
      </select>
      <label class="case"><input type="checkbox" id="exercice" ${f.exercice ? "checked" : ""}> en exercice seulement</label>
      <span class="compte" id="compte"></span>
    </div>
    <div class="defile"><table>
      <thead><tr>${COLONNES_DEPUTES.map((c) =>
        `<th class="triable ${c.num ? "num" : ""} ${c.optionnelle ? "colonne-optionnelle" : ""} ${
          etat.triDeputes.colonne === c.cle ? "tri-actif" : ""}" data-cle="${c.cle}">${c.titre}${
          etat.triDeputes.colonne === c.cle ? (etat.triDeputes.desc ? " ↓" : " ↑") : ""}</th>`).join("")}
      </tr></thead>
      <tbody id="corps"></tbody>
    </table></div>
    <p class="note"><strong>Participation</strong> : votes exprimés rapportés aux seuls scrutins où le
      député siégeait, non-votants structurels (ministre, président de séance) retirés du dénominateur.
      <strong>Dissidence</strong> : part des votes qui s'écartent de la ligne du groupe, comptée sur les seuls
      scrutins où le groupe <em>avait</em> une ligne — plus de la moitié de ses suffrages sur une même position.
      <strong>Position</strong> : point idéal estimé sur les 245 votes qui engagent, avec son intervalle à 90 %.</p>`;

  const dessiner = () => {
    const d = deputesFiltres();
    $("#compte").textContent = `${num(d.length)} députés`;
    $("#corps").innerHTML = d.map((x) => `<tr>
      <td>${lienDepute(x.acteur_uid, x.nom_complet)}${x.en_exercice ? "" : ' <span class="etiquette">mandat terminé</span>'}</td>
      <td><span class="etiquette" title="${esc(x.groupe_libelle || "")}">${esc(x.groupe)}</span></td>
      <td class="colonne-optionnelle petit muet">${esc(x.departement || "—")}${x.num_circo ? ` (${esc(x.num_circo)}<sup>${ordinal(x.num_circo)}</sup>)` : ""}</td>
      <td class="num" title="${num(x.votes_exprimes)} votes exprimés">${pct(x.participation)}</td>
      <td class="num">${pct(x.taux_dissidence)}</td>
      <td class="num colonne-optionnelle">${pct(x.part_abstention)}</td>
      <td>${axe(x, { mini: true })}</td>
    </tr>`).join("");
  };

  $("#q").oninput = (e) => { etat.filtresDeputes.q = e.target.value; dessiner(); };
  $("#groupe").onchange = (e) => { etat.filtresDeputes.groupe = e.target.value; dessiner(); };
  $("#exercice").onchange = (e) => { etat.filtresDeputes.exercice = e.target.checked; dessiner(); };
  vue.querySelectorAll("th.triable").forEach((th) => {
    th.onclick = () => {
      const cle = th.dataset.cle;
      const t = etat.triDeputes;
      // Un taux se lit d'abord par le haut, un nom par le début.
      t.desc = t.colonne === cle ? !t.desc : cle !== "nom_complet" && cle !== "groupe" && cle !== "departement";
      t.colonne = cle;
      vueDeputes();
    };
  });
  dessiner();
}

/* -- vue : fiche d'un député --------------------------------------------- */

function listeVoisins(voisins, vide = "pas assez de scrutins communs") {
  if (!voisins.length) return `<p class="note">${vide}</p>`;
  return `<ul class="liste">${voisins.map((v) => `<li>
    ${lienDepute(v.acteur_uid, v.nom_complet, v.groupe)}
    <span class="droite" title="${num(v.scrutins_communs)} scrutins communs">${pct(v.accord)}</span>
  </li>`).join("")}</ul>`;
}

async function vueDepute(uid) {
  vue.innerHTML = '<p class="attente">Chargement de la fiche…</p>';
  const f = await api(`/deputes/${encodeURIComponent(uid)}`);
  const i = f.identite, a = f.activite, p = f.position, g = f.repere_groupe;
  const portees = etat.apercu.scrutins_par_portee;

  vue.innerHTML = `
    <a class="retour" href="#/deputes">← tous les députés</a>
    <div class="fiche-entete">
      <h1>${esc(i.nom_complet)}</h1>
      <span class="etiquette" title="${esc(i.groupe_libelle || "")}">${esc(i.groupe)}${
        i.groupe_qualite && i.groupe_qualite !== "Membre" ? " · " + esc(i.groupe_qualite) : ""}</span>
      ${i.en_exercice ? "" : '<span class="etiquette">mandat terminé</span>'}
    </div>
    <p class="fiche-meta">
      ${esc(i.departement || "")}${i.num_circo ? `, ${esc(i.num_circo)}<sup>${ordinal(i.num_circo)}</sup> circonscription` : ""}
      ${i.age ? ` · ${i.age} ans` : ""}${i.profession ? ` · ${esc(i.profession)}` : ""}
      · mandat depuis le ${jour(i.mandat_debut)}${i.mandat_fin ? `, terminé le ${jour(i.mandat_fin)}` : ""}
      ${i.uri_hatvp ? ` · <a href="${esc(i.uri_hatvp)}">déclaration HATVP</a>` : ""}
    </p>

    <div class="cartes">
      <div class="carte">
        <h3>Participation</h3>
        <div class="chiffre">${pct(a.participation)}</div>
        <div class="chiffre-sous">${num(a.votes_exprimes)} votes exprimés sur ${num(a.scrutins_eligibles)} scrutins
          où le mandat courait${a.non_votants_structurels ? `, moins ${num(a.non_votants_structurels)} non-votants structurels` : ""}</div>
        ${jauge(a.participation)}
        <p class="note">Sur les seuls votes qui engagent : <strong>${pct(a.participation_engageants)}</strong>
          (${num(a.votes_engageants)} sur ${num(portees.texte)}).</p>
      </div>

      <div class="carte">
        <h3>Dissidence</h3>
        <div class="chiffre">${pct(a.taux_dissidence)}</div>
        <div class="chiffre-sous">${num(a.votes_dissidents)} votes contre la ligne, sur
          ${num(a.votes_avec_ligne)} où son groupe en avait une</div>
        ${jauge(a.taux_dissidence)}
        <p class="note">${g ? `Moyenne du groupe : ${pct(g.dissidence_moyenne)}. Cohésion interne : ${pct(g.cohesion)}.` : ""}</p>
      </div>

      <div class="carte">
        <h3>Répartition de ses suffrages</h3>
        ${barreEmpilee(a.part_pour, a.part_contre, a.part_abstention)}
        ${legendePositions}
        <dl class="paires" style="margin-top:12px">
          <dt>pour</dt><dd>${pct(a.part_pour)}</dd>
          <dt>contre</dt><dd>${pct(a.part_contre)}</dd>
          <dt>abstention</dt><dd>${pct(a.part_abstention)}${
            g ? ` <span class="muet">(groupe : ${pct(g.abstention_moyenne)})</span>` : ""}</dd>
        </dl>
      </div>

      <div class="carte">
        <h3>Position estimée</h3>
        ${p.axe1 == null
          ? '<p class="note">Trop peu de votes sur les scrutins qui engagent pour estimer une position.</p>'
          : `<div class="chiffre">${dec(p.axe1)}</div>
             <div class="chiffre-sous">${p.borne_basse != null
               ? `intervalle à 90 % : ${dec(p.borne_basse)} à ${dec(p.borne_haute)}`
               : "sans intervalle"} · rang ${num(p.rang)} sur ${num(p.classes)}</div>
             ${axe(p, { repere: g ? g.position_mediane : null })}
             <p class="note">Point idéal sur l'axe principal, estimé par un modèle de vote à deux
               paramètres. Le trait vert marque la médiane du groupe. Les rangs dont les intervalles
               se recouvrent ne se distinguent pas.</p>`}
      </div>

      ${f.amendements ? `<div class="carte">
        <h3>Amendements</h3>
        <div class="chiffre">${num(f.amendements.deposes || 0)}</div>
        <div class="chiffre-sous">déposés, dont ${num(f.amendements.adoptes || 0)} adoptés
          (${pct(f.amendements.taux_adoption)})</div>
        <dl class="paires" style="margin-top:12px">
          <dt>cosignés</dt><dd>${num(f.amendements.cosignes || 0)}</dd>
          <dt>cosignataires moyens</dt><dd>${dec(f.amendements.cosignataires_moyen, 1)}</dd>
          <dt>ouverture</dt><dd>${dec(f.amendements.ouverture, 2)}
            <span class="muet">× le hasard</span></dd>
        </dl>
        <p class="note">Le compte des cosignatures inclut les dépôts de groupe entier, où
          l'ensemble des membres signe d'un bloc. L'<strong>ouverture</strong> rapporte ses
          cosignatures hors groupe à ce que donnerait un tirage au hasard : au-dessus de 1, il sort
          réellement de son camp.</p>
      </div>` : ""}

      <div class="carte">
        <h3>Responsabilités en cours</h3>
        ${f.responsabilites.length
          ? `<ul class="liste empilee-texte">${f.responsabilites.slice(0, 12).map((r) => `<li>
              <div>${esc(r.libelle)}
                <div class="petit muet">${esc(r.type)}${
                  r.qualite && r.qualite.toLowerCase() !== "membre" ? " · " + esc(r.qualite) : ""}</div>
              </div>
            </li>`).join("")}</ul>
            ${f.responsabilites.length > 12 ? `<p class="note">et ${f.responsabilites.length - 12} autres.</p>` : ""}`
          : '<p class="note">Aucun organe en cours dans les données.</p>'}
      </div>
    </div>

    <h2>Qui vote comme ${esc(i.nom)}</h2>
    <div class="piege">
      <p class="note">Le même binôme n'a pas le même taux d'accord selon les scrutins qu'on retient.
      À gauche, les ${num(etat.apercu.scrutins)} scrutins, dont ${num(portees.detail)} votes d'amendement,
      souvent tactiques. À droite, les ${num(portees.texte)} votes qui engagent — ensemble d'un texte,
      motion de censure. <strong>L'écart entre les deux colonnes est l'information</strong>, pas chacune
      des colonnes prise seule.</p>
    </div>
    <div class="deux-colonnes">
      <div class="carte"><h3>Sur tous les scrutins</h3>${listeVoisins(f.proches.tous)}</div>
      <div class="carte"><h3>Sur les votes qui engagent</h3>${listeVoisins(f.proches.texte)}</div>
      <div class="carte"><h3>Les plus proches hors de son groupe</h3>${listeVoisins(f.proches.hors_groupe)}</div>
      <div class="carte"><h3>Les plus éloignés</h3>${listeVoisins(f.proches.opposes)}</div>
    </div>

    ${f.cosignataires && f.cosignataires.length ? `
    <h2>Avec qui ${esc(i.nom)} travaille</h2>
    <p class="note">Voter ensemble et travailler ensemble sont deux choses différentes. Le vote mesure
      la discipline — l'accord intra-groupe dépasse 90 % partout ; la cosignature mesure l'initiative :
      personne n'est tenu de cosigner l'amendement d'un collègue. Les dépôts de plus de dix signataires
      sont écartés, sans quoi les dépôts de groupe entier relieraient mécaniquement tous les membres
      d'un même groupe, et les amendements de rapporteur aussi : corédiger un texte est un rôle
      institutionnel, pas une alliance.</p>
    <div class="deux-colonnes">
      <div class="carte">
        <h3>Cosignataires les plus fréquents</h3>
        <ul class="liste">${f.cosignataires.map((c) => `<li>
          ${lienDepute(c.acteur_uid, c.nom_complet, c.groupe)}
          <span class="droite" title="${num(c.amendements_communs)} amendements cosignés ensemble">
            ${pct(c.affinite)}</span></li>`).join("")}</ul>
        <p class="note">Indice de Jaccard : amendements cosignés ensemble rapportés à l'union des deux
          répertoires. Le compte brut ne remonterait que les plus prolifiques.</p>
      </div>
    </div>` : ""}

    <h2>Ses votes</h2>
    <div class="filtres">
      <select id="portee-votes">
        <option value="">Toutes les portées</option>
        <option value="texte">Votes qui engagent (${num(portees.texte)})</option>
        <option value="intermediaire">Intermédiaires (${num(portees.intermediaire)})</option>
        <option value="detail">Amendements (${num(portees.detail)})</option>
      </select>
      <label class="case"><input type="checkbox" id="dissidents-seuls"> contre la ligne de son groupe seulement</label>
      <span class="compte" id="compte-votes"></span>
    </div>
    <div id="votes"><p class="attente">Chargement des votes…</p></div>`;

  const charger = async () => {
    const f2 = etat.filtresVotes;
    const q = new URLSearchParams({ limite: "300" });
    if (f2.portee) q.set("portee", f2.portee);
    if (f2.dissidents) q.set("dissidents", "1");
    const d = await api(`/deputes/${encodeURIComponent(uid)}/votes?${q}`);
    $("#compte-votes").textContent =
      `${num(d.retenus)} votes${d.retenus > d.votes.length ? `, les ${num(d.votes.length)} plus récents affichés` : ""}`;
    $("#votes").innerHTML = d.votes.length ? `<div class="defile"><table>
      <thead><tr><th>Date</th><th>Scrutin</th><th>Son vote</th><th class="colonne-optionnelle">Ligne du groupe</th><th>Résultat</th></tr></thead>
      <tbody>${d.votes.map((v) => `<tr>
        <td class="petit muet">${jour(v.date)}</td>
        <td><a href="#/scrutin/${esc(v.scrutin_uid)}" class="tronque">${esc(v.titre)}</a>
          <span class="etiquette">${esc(v.portee)}</span></td>
        <td class="pos-${esc(v.position)} ${v.dissident ? "dissident" : ""}">${esc(POSITIONS[v.position] || v.position)}</td>
        <td class="petit muet colonne-optionnelle">${v.majoritaire == null || v.part_majoritaire <= 0.5
          ? "groupe partagé" : esc(POSITIONS[v.majoritaire] || v.majoritaire) + ` (${pct(v.part_majoritaire, 0)})`}</td>
        <td class="petit muet">${esc(v.sort_libelle)}</td>
      </tr>`).join("")}</tbody></table></div>
      <p class="note">✳ marque un vote qui s'écarte de la ligne du groupe. Quand le groupe est partagé —
      aucune position ne réunit plus de la moitié de ses suffrages — il n'y a pas de ligne, donc pas de
      dissidence à compter : ces scrutins sont hors du taux affiché plus haut.</p>`
      : '<p class="note">Aucun vote pour ces filtres.</p>';
  };

  $("#portee-votes").value = etat.filtresVotes.portee;
  $("#dissidents-seuls").checked = etat.filtresVotes.dissidents;
  $("#portee-votes").onchange = (e) => { etat.filtresVotes.portee = e.target.value; charger(); };
  $("#dissidents-seuls").onchange = (e) => { etat.filtresVotes.dissidents = e.target.checked; charger(); };
  charger();
}

/* -- vue : scrutins ------------------------------------------------------ */

async function vueScrutins() {
  const f = etat.filtresScrutins;
  const portees = etat.apercu.scrutins_par_portee;
  vue.innerHTML = `
    <h1>Les scrutins</h1>
    <p class="sous-titre">${num(etat.apercu.scrutins)} scrutins publics du ${jour(etat.apercu.debut)}
      au ${jour(etat.apercu.fin)}.</p>
    <div class="filtres">
      <input type="search" id="q" placeholder="Mots du titre…" value="${esc(f.q)}">
      <select id="portee">
        <option value="">Toutes les portées</option>
        <option value="texte">Votes qui engagent (${num(portees.texte)})</option>
        <option value="intermediaire">Intermédiaires (${num(portees.intermediaire)})</option>
        <option value="detail">Amendements (${num(portees.detail)})</option>
      </select>
      <select id="categorie">
        <option value="">Toutes les catégories</option>
        ${["ensemble", "motion_censure", "motion_procedure", "declaration", "article", "amendement", "autre"]
          .map((c) => `<option ${c === f.categorie ? "selected" : ""}>${c}</option>`).join("")}
      </select>
      <span class="compte" id="compte"></span>
    </div>
    <div id="tableau"><p class="attente">Chargement…</p></div>
    <p class="note"><strong>Portée</strong> : « texte » désigne un vote sur l'ensemble d'un texte ou une
      motion de censure — ce qui engage ; « détail », un vote d'amendement, souvent tactique. 86 % des
      scrutins sont dans la seconde catégorie : les agréger tous revient à mesurer surtout de la
      tactique parlementaire.</p>`;

  const charger = async () => {
    const q = new URLSearchParams({ limite: "300" });
    if (f.q) q.set("q", f.q);
    if (f.portee) q.set("portee", f.portee);
    if (f.categorie) q.set("categorie", f.categorie);
    const d = await api(`/scrutins?${q}`);
    $("#compte").textContent = `${num(d.total)} scrutins${d.total > d.scrutins.length ? `, ${num(d.scrutins.length)} affichés` : ""}`;
    $("#tableau").innerHTML = d.scrutins.length ? `<div class="defile"><table>
      <thead><tr><th>Date</th><th>Objet</th><th class="num">Pour</th><th class="num">Contre</th>
        <th class="num colonne-optionnelle">Abst.</th><th>Répartition</th><th>Sort</th></tr></thead>
      <tbody>${d.scrutins.map((s) => `<tr>
        <td class="petit muet">${jour(s.date)}</td>
        <td><a href="#/scrutin/${esc(s.scrutin_uid)}" class="tronque">${esc(s.titre)}</a>
          <span class="etiquette">${esc(s.portee)}</span></td>
        <td class="num">${num(s.n_pour)}</td>
        <td class="num">${num(s.n_contre)}</td>
        <td class="num colonne-optionnelle">${num(s.n_abstention)}</td>
        <td style="min-width:110px">${barreEmpilee(s.n_pour, s.n_contre, s.n_abstention)}</td>
        <td class="petit">${esc(s.sort_libelle)}</td>
      </tr>`).join("")}</tbody></table></div>`
      : '<p class="note">Aucun scrutin pour ces filtres.</p>';
  };

  $("#portee").value = f.portee;
  let minuteur;
  $("#q").oninput = (e) => { f.q = e.target.value; clearTimeout(minuteur); minuteur = setTimeout(charger, 200); };
  $("#portee").onchange = (e) => { f.portee = e.target.value; charger(); };
  $("#categorie").onchange = (e) => { f.categorie = e.target.value; charger(); };
  charger();
}

async function vueScrutin(uid) {
  vue.innerHTML = '<p class="attente">Chargement du scrutin…</p>';
  const d = await api(`/scrutins/${encodeURIComponent(uid)}`);
  const s = d.scrutin;
  const dissidents = d.votes.filter((v) => v.dissident);

  vue.innerHTML = `
    <a class="retour" href="#/scrutins">← tous les scrutins</a>
    <h1>Scrutin n° ${num(s.numero)} — ${esc(s.sort_libelle)}</h1>
    <p class="sous-titre">${jour(s.date)} · ${esc(s.type_vote_libelle || "")} ·
      portée « ${esc(s.portee)} » · catégorie « ${esc(s.categorie)} »${
      s.demandeur ? ` · demandé par ${esc(s.demandeur)}` : ""}</p>
    <p style="max-width:80ch">${esc(s.titre)}</p>

    <div class="cartes">
      <div class="carte">
        <h3>Résultat</h3>
        <div class="chiffre">${num(s.n_pour)} pour · ${num(s.n_contre)} contre</div>
        <div class="chiffre-sous">${num(s.n_abstention)} abstentions, ${num(s.nb_votants)} votants,
          ${num(s.suffrages_requis)} suffrages requis</div>
        ${barreEmpilee(s.n_pour, s.n_contre, s.n_abstention)}
        ${legendePositions}
      </div>
      <div class="carte">
        <h3>Contestation</h3>
        <div class="chiffre">${pct(s.contestation)}</div>
        <div class="chiffre-sous">part de la position minoritaire dans les suffrages exprimés</div>
        ${jauge(s.contestation)}
        <p class="note">Un vote joué d'avance gonfle mécaniquement l'accord entre tous les députés : c'est
          pourquoi les analyses de proximité peuvent écarter les scrutins peu contestés.</p>
      </div>
      <div class="carte">
        <h3>Votes contre la ligne</h3>
        <div class="chiffre">${num(dissidents.length)}</div>
        <div class="chiffre-sous">députés s'écartant de la position majoritaire de leur groupe,
          groupes partagés exclus</div>
      </div>
    </div>

    <h2>Position de chaque groupe</h2>
    <table>
      <thead><tr><th>Groupe</th><th class="num">Pour</th><th class="num">Contre</th><th class="num">Abst.</th>
        <th>Répartition</th><th>Ligne</th></tr></thead>
      <tbody>${d.groupes.map((g) => `<tr>
        <td><span class="etiquette" title="${esc(g.groupe_libelle || "")}">${esc(g.groupe || g.groupe_uid)}</span></td>
        <td class="num">${num(g.n_pour)}</td>
        <td class="num">${num(g.n_contre)}</td>
        <td class="num">${num(g.n_abstention)}</td>
        <td style="min-width:120px">${barreEmpilee(g.n_pour, g.n_contre, g.n_abstention)}</td>
        <td class="petit">${g.partage || g.part_majoritaire <= 0.5
          ? '<span class="muet">groupe partagé</span>'
          : `${esc(POSITIONS[g.majoritaire] || g.majoritaire)} <span class="muet">(${pct(g.part_majoritaire, 0)})</span>`}</td>
      </tr>`).join("")}</tbody>
    </table>

    ${dissidents.length ? `<h2>Les ${num(dissidents.length)} votes contre la ligne</h2>
      <ul class="liste">${dissidents.map((v) => `<li>${lienDepute(v.acteur_uid, v.nom_complet, v.groupe)}
        <span class="droite pos-${esc(v.position)}">${esc(POSITIONS[v.position] || v.position)}</span></li>`).join("")}</ul>` : ""}

    <h2>Le dépouillement nominatif</h2>
    <div class="filtres">
      <input type="search" id="q-vote" placeholder="Filtrer par nom ou groupe…">
      <span class="compte">${num(d.votes.length)} votes nominatifs</span>
    </div>
    <div class="defile"><table>
      <thead><tr><th>Député</th><th>Groupe</th><th>Vote</th><th class="colonne-optionnelle">Remarque</th></tr></thead>
      <tbody id="corps-votes"></tbody>
    </table></div>`;

  const dessiner = (q = "") => {
    const filtre = q.trim().toLowerCase();
    const l = d.votes.filter((v) => !filtre ||
      v.nom_complet.toLowerCase().includes(filtre) || (v.groupe || "").toLowerCase().includes(filtre));
    $("#corps-votes").innerHTML = l.map((v) => `<tr>
      <td>${lienDepute(v.acteur_uid, v.nom_complet)}</td>
      <td><span class="etiquette">${esc(v.groupe)}</span></td>
      <td class="pos-${esc(v.position)} ${v.dissident ? "dissident" : ""}">${esc(POSITIONS[v.position] || v.position)}</td>
      <td class="petit muet colonne-optionnelle">${v.par_delegation ? "par délégation" : ""}${
        v.cause ? ` ${esc(v.cause)}` : ""}</td>
    </tr>`).join("");
  };
  $("#q-vote").oninput = (e) => dessiner(e.target.value);
  dessiner();
}

/* -- vue : groupes ------------------------------------------------------- */

function vueGroupes() {
  const g = etat.apercu.groupes;
  vue.innerHTML = `
    <h1>Les groupes</h1>
    <p class="sous-titre">Cohésion, participation et étendue des positions, pour les députés en exercice.</p>
    <table>
      <thead><tr><th>Groupe</th><th class="num">Effectif</th><th class="num">Cohésion</th>
        <th class="num">Participation</th><th class="num">Dissidence</th>
        <th class="num colonne-optionnelle">Abstention</th><th>Étendue des positions</th></tr></thead>
      <tbody>${g.map((x) => `<tr>
        <td><strong>${esc(x.groupe)}</strong><br><span class="petit muet">${esc(x.groupe_libelle || "")}</span></td>
        <td class="num">${num(x.effectif_actuel)}</td>
        <td class="num">${pct(x.cohesion)}</td>
        <td class="num">${pct(x.participation_moyenne)}</td>
        <td class="num">${pct(x.dissidence_moyenne)}</td>
        <td class="num colonne-optionnelle">${pct(x.abstention_moyenne)}</td>
        <td style="min-width:180px">${axe(
          { axe1: x.position_mediane, borne_basse: x.position_min, borne_haute: x.position_max })}</td>
      </tr>`).join("")}</tbody>
    </table>
    <p class="note"><strong>Cohésion</strong> : accord moyen entre deux membres du même groupe, sur les
      scrutins où les deux se prononcent. <strong>Étendue</strong> : la barre va du député le plus à un bout
      de l'axe au plus à l'autre, le point marquant la médiane du groupe — ce n'est pas un intervalle de
      confiance, mais la dispersion réelle des positions estimées à l'intérieur du groupe.</p>`;
}

/* -- vue : méthode ------------------------------------------------------- */

function vueMethode() {
  const a = etat.apercu;
  vue.innerHTML = `
    <h1>Ce que les chiffres disent, et ce qu'ils écartent</h1>
    <p class="sous-titre">Chaque mesure repose sur une définition, et chaque définition écarte quelque
      chose. Les choix ne sont pas supprimables : ils sont ici rendus visibles.</p>

    <h2>Le périmètre</h2>
    <dl class="paires">
      <dt>Législature</dt><dd>${esc(a.legislature)}<sup>e</sup></dd>
      <dt>Période</dt><dd>${jour(a.debut)} → ${jour(a.fin)}</dd>
      <dt>Députés</dt><dd>${num(a.deputes)} ayant siégé, dont ${num(a.en_exercice)} en exercice</dd>
      <dt>Scrutins</dt><dd>${num(a.scrutins)} publics, soit ${num(a.votes)} votes nominatifs</dd>
      <dt>dont votes qui engagent</dt><dd>${num(a.scrutins_par_portee.texte)}</dd>
      <dt>dont intermédiaires</dt><dd>${num(a.scrutins_par_portee.intermediaire)}</dd>
      <dt>dont amendements</dt><dd>${num(a.scrutins_par_portee.detail)}</dd>
      <dt>Amendements</dt><dd>${a.avec_amendements ? "table construite" : "table absente — <code>radar update --amendements</code>"}</dd>
    </dl>

    <h2>La portée du scrutin</h2>
    <p class="note">86 % des scrutins publics portent sur un amendement. Un vote sur un sous-amendement est
      souvent tactique : on approuve un aménagement technique sans approuver le texte. Les agréger tous
      revient à mesurer surtout de la tactique parlementaire, et une seule loi très amendée pèse alors plus
      que dix textes votés. C'est pourquoi chaque fiche affiche la proximité deux fois : sur tous les
      scrutins, et sur les ${num(a.scrutins_par_portee.texte)} votes qui engagent.</p>

    <h2>La participation</h2>
    <p class="note">Le dénominateur ne compte que les scrutins où le mandat du député courait — sinon un
      député élu en cours de législature apparaîtrait comme absent aux votes tenus avant son élection. Les
      non-votants structurels (membre du Gouvernement, président de séance) sont retirés : ce ne sont pas
      des absences mais des fonctions incompatibles avec le vote.</p>

    <h2>La dissidence</h2>
    <p class="note">La ligne du groupe est recalculée depuis le dépouillement nominatif, pas lue dans une
      consigne annoncée. Encore faut-il qu'il y ait une ligne : un groupe qui vote 7 pour, 5 contre et 5
      abstentions a une position dominante à 41 %, et compter les dix autres votes comme dissidents
      traiterait un groupe qui n'a pas su se mettre d'accord comme un groupe dont dix membres auraient
      enfreint une consigne. Seuls les scrutins où une position réunit plus de la moitié des suffrages du
      groupe entrent au dénominateur ; les autres sont marqués « groupe partagé ».</p>

    <h2>Les positions estimées</h2>
    <p class="note">Le point idéal est estimé par un modèle de vote à deux paramètres sur les votes qui
      engagent, avec un intervalle de confiance obtenu par ${num(a.bootstrap)} rééchantillonnages des
      scrutins. Une position estimée n'est pas un point mais une zone : deux députés dont les intervalles
      se recouvrent ne sont pas classables l'un par rapport à l'autre. Le modèle est binaire par
      construction — il ne prédit pas l'abstention, qui porte pourtant de l'information.</p>

    <h2>Les couleurs</h2>
    <p class="note">Aucune couleur ne désigne un groupe sur ce site. Le rouge de deux groupes de gauche,
      comme les trois bleus de droite, sont trop proches pour être distingués de façon fiable, daltonisme
      ou non. La couleur porte donc une grandeur ou une emphase ; l'identité passe par le texte.</p>`;
}

/* -- routage ------------------------------------------------------------- */

const ROUTES = [
  [/^#\/deputes$/, vueDeputes, "deputes"],
  [/^#\/depute\/(.+)$/, vueDepute, "deputes"],
  [/^#\/scrutins$/, vueScrutins, "scrutins"],
  [/^#\/scrutin\/(.+)$/, vueScrutin, "scrutins"],
  [/^#\/groupes$/, vueGroupes, "groupes"],
  [/^#\/methode$/, vueMethode, "methode"],
];

async function router() {
  const h = location.hash || "#/deputes";
  for (const [motif, rendu, onglet] of ROUTES) {
    const m = h.match(motif);
    if (!m) continue;
    document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("actif", a.dataset.vue === onglet));
    try {
      await rendu(m[1] ? decodeURIComponent(m[1]) : undefined);
    } catch (e) {
      vue.innerHTML = `<p class="erreur">Erreur : ${esc(e.message)}</p>`;
    }
    window.scrollTo(0, 0);
    return;
  }
  location.hash = "#/deputes";
}

async function demarrer() {
  try {
    [etat.apercu, etat.deputes] = await Promise.all([api("/apercu"), api("/deputes")]);
  } catch (e) {
    vue.innerHTML = `<p class="erreur">Impossible de charger les données : ${esc(e.message)}</p>`;
    return;
  }
  const positions = etat.deputes.map((d) => d.axe1).filter((x) => x != null);
  if (positions.length) {
    const marge = 0.15 * (Math.max(...positions) - Math.min(...positions) || 1);
    etat.echelle = { min: Math.min(...positions) - marge, max: Math.max(...positions) + marge };
  }
  $("#perimetre").textContent =
    `${etat.apercu.legislature}e législature · ${etat.apercu.en_exercice} députés · ${etat.apercu.scrutins.toLocaleString("fr-FR")} scrutins`;
  window.addEventListener("hashchange", router);
  router();
}

demarrer();
