/* Gilbert & Co : front-end (vanilla JS, SVG charts drawn by hand). Talks to the FastAPI under /api.
   Every figure comes from the outputs of a run (routes /api/runs/{id}/views/...): the interface computes nothing. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
// Palette : vert émeraude = aujourd'hui et rampe d'intensité, orange = avec Gilbert et jours de pointe, rouge = erreurs.
const C = { ink: "#192D25", label: "#3E5248", muted: "#708579", line: "#DCE5DF", empty: "#EEF2EF",
            before: "#1FA77A", after: "#E8772E", afterDark: "#C95F1C", afterPale: "#F7C9A5",
            g1: "#DFEDE4", g2: "#BCD8C6", g3: "#8FBFA3", g4: "#5E9F7E", g5: "#2E7C5D", g6: "#165E48",
            red: "#C0392B", peak: "#E8772E", ochre: "#E8772E", slate: "#0F5B46" };
const nf0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const eur = (v, d = 0) => new Intl.NumberFormat("fr-FR", { maximumFractionDigits: d, minimumFractionDigits: d }).format(v) + " €";
const num = (v) => nf0.format(v);
const pct = (v) => nf1.format(v) + " %";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const JOURS = ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."];
const dayLabel = (iso) => { const d = new Date(iso + "T12:00:00"); return `${JOURS[d.getDay()]} ${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`; };
const short = (iso) => iso.slice(8, 10) + "/" + iso.slice(5, 7);

const S = { runs: [], sourceRun: null, example: false, quality: null, qualityKey: null, qualityState: "idle", qualityError: "", current: null, summary: null, view: "accueil", files: {}, polling: null, trucksPage: 0, selectedTruck: null, suppliers: [], sort: {} };
const DONE = "succeeded", FAILED = "failed", CANCELLED = "cancelled", FINISHED = [DONE, FAILED, CANCELLED];
// parameters of the planner used by a run (defaults filled in by the API)
const planner = (run) => (run && run.parameters && run.parameters.planner) || {};
const runtime = (run) => Object.values((run && run.timings) || {}).reduce((a, b) => a + b, 0);

// ----------------------------------------------------------------------------- API
async function api(path, opts) {
  const r = await fetch("/api" + path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not JSON */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.status === 204 ? null : r.json();
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 5200);
}

// ----------------------------------------------------------------------------- tooltip
document.addEventListener("mousemove", (e) => {
  const el = e.target.closest("[data-tip]"); const tip = $("#tooltip");
  if (!el) { tip.classList.remove("on"); return; }
  tip.innerHTML = el.getAttribute("data-tip"); tip.classList.add("on");
  const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 10);
  tip.style.left = x + "px"; tip.style.top = (e.clientY + 16) + "px";
});
window.addEventListener("scroll", () => $(".topbar").classList.toggle("scrolled", window.scrollY > 8));

// ----------------------------------------------------------------------------- navigation
const VIEWS = ["accueil", "historique", "running", "apercu", "calendrier", "profils", "camions", "fournisseurs"];
const RESULT_VIEWS = ["apercu", "calendrier", "profils", "camions", "fournisseurs"];   // the screens of ONE scenario
const CRUMBS = { accueil: "Nouveau calcul", historique: "Historique", running: "Calcul en cours", apercu: "Synthèse", calendrier: "Planning", profils: "Profils",
                 camions: "Camions", fournisseurs: "Fournisseurs" };
// address of a screen: #/nouveau, #/historique, or #/runs/<run id>/<tab> for the results of one scenario (back button, reload, bookmark)
const SLUGS = { apercu: "synthese", calendrier: "planning", profils: "profils", camions: "camions", fournisseurs: "fournisseurs" };
const VIEW_OF_SLUG = Object.fromEntries(Object.entries(SLUGS).map(([view, slug]) => [slug, view]));
function routeOf(view) {
  if (RESULT_VIEWS.includes(view) && S.current) return `#/runs/${S.current}/${SLUGS[view]}`;
  return view === "historique" ? "#/historique" : "#/nouveau";
}
function parseRoute(hash) {
  const m = /^#\/runs\/([\w-]+)\/(\w+)$/.exec(hash || "");
  if (m) return { run: m[1], view: VIEW_OF_SLUG[m[2]] || "apercu" };
  return { run: null, view: hash === "#/historique" ? "historique" : "accueil" };
}
function renderCrumbs(view) {
  const sep = `<span class="sep" aria-hidden="true">/</span>`;
  if (RESULT_VIEWS.includes(view))
    $("#crumbs").innerHTML = `<a href="#/historique" data-view="historique">Historique</a>${sep}<span>${esc(S.run ? S.run.label : "Scénario")}</span>${sep}<b aria-current="page">${CRUMBS[view]}</b>`;
  else $("#crumbs").innerHTML = `<b aria-current="page">${CRUMBS[view] || ""}</b>`;
}
function show(view, opts = {}) {
  if (!VIEWS.includes(view)) view = "accueil";
  if (RESULT_VIEWS.includes(view) && !S.summary) view = "accueil";
  S.view = view;
  renderCrumbs(view);
  document.body.dataset.view = view;
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  $("#runBar").hidden = !RESULT_VIEWS.includes(view);
  $$(".sidenav button[data-view], .runbar-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view !== "running" && location.hash !== routeOf(view)) {
    // a new screen is a new entry of the history (back button); a first address or an old one (#apercu) is replaced
    if (location.hash.startsWith("#/")) history.pushState(null, "", routeOf(view));
    else history.replaceState(null, "", routeOf(view));
  }
  window.scrollTo({ top: 0 });
  if (view !== "calendrier") hideDrawer();   // the drawer belongs to the planning; its state is kept for the return
  if (view === "camions") {
    $("#backToPlanning").hidden = !opts.fromPlanning;
    if (!opts.fromPlanning && S.trucksFromPlanning) {   // opened from its tab: the usual Camions page, unfiltered
      S.trucksFromPlanning = false; S.planReturn = null;
      ["#tf-plant", "#tf-supplier", "#tf-day"].forEach((id) => ($(id).value = ""));
      S.trucksPage = 0; S.selectedTruck = null;
    }
  }
  const render = { accueil: renderLaunch, historique: renderHistory, apercu: renderOverview, calendrier: renderCalendarView,
                   profils: renderProfiles, camions: renderTrucks, fournisseurs: renderSuppliers }[view];
  if (render) render();
}
document.addEventListener("click", (e) => {
  // navigation buttons and links only: <body> also carries data-view (current screen) and must not catch every click
  const b = e.target.closest("button[data-view], a[data-view]");
  if (b && !b.disabled) { e.preventDefault(); show(b.dataset.view); }
});
// back / forward buttons of the browser, or an address typed by hand
async function route() {
  if (S.view === "running") return;   // the waiting screen follows its run
  const { run, view } = parseRoute(location.hash);
  if (!run) { show(view); return; }
  if (run === S.current) { show(view); return; }
  const known = S.runs.find((r) => r.run_id === run && r.status === DONE);
  if (known) await openRun(run, view);
  else { toast("Ce calcul n'existe plus."); show("historique"); }
}
window.addEventListener("popstate", () => { route(); });

// ----------------------------------------------------------------------------- new calculation: import + launch
const IMPORT_FILES = [   // key, title, expected file, form field of POST /api/runs
  ["schedule", "Planning de livraison", "released_schedule.csv", "released_schedule"],
  ["distances", "Distances des fournisseurs", "distances.csv", "distances"],
  ["rates", "Grille tarifaire du transporteur", "rate_sheet.pdf", "rate_sheet"],
];
const FILE_ICON = '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 14h6M9 17h4"/></svg>';
const fmtDate = (iso, y = false) => iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}${y ? "/" + iso.slice(0, 4) : ""}` : "?";
const fmtStamp = (iso) => new Date(iso).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" });

function renderSource() {
  const box = $("#sourceBox"), src = S.runs.find((r) => r.run_id === S.sourceRun);
  if (src) {
    const k = src.kpis || {};
    box.innerHTML = `<div class="source-pick"><span class="ico">${FILE_ICON}</span><div><p class="f-lab">Données</p>
      <b>Fichiers de « ${esc(src.label)} »</b><span>importés le ${fmtStamp(src.created_at)}${k.lines ? ` · ${num(k.lines)} lignes · ${num(k.couples)} couples` : ""}</span></div>
      <button class="link" id="changeSource">Importer d'autres fichiers</button></div>`;
    $("#changeSource").addEventListener("click", () => { S.sourceRun = null; S.pendingChoices = null; renderLaunch(); });
    return;
  }
  if (S.example) {
    box.innerHTML = `<div class="source-pick"><span class="ico">${FILE_ICON}</span><div><p class="f-lab">Données</p>
      <b>Fichiers de l'exemple</b><span>planning de novembre 2026, distances et grille tarifaire du projet</span></div>
      <button class="link" id="changeSource">Importer mes fichiers</button></div>`;
    $("#changeSource").addEventListener("click", () => { S.example = false; renderLaunch(); });
    return;
  }
  box.innerHTML = `<p class="f-lab">Vos fichiers</p>
    <label class="drop-all" id="dropAll">
      <span class="ico">${FILE_ICON}</span>
      <span class="txt"><span class="name">Glissez vos 3 fichiers ici, d'un coup</span>
        <span class="state">ou cliquez pour les choisir : planning et distances (CSV), grille tarifaire (PDF), reconnus automatiquement</span></span>
      <input type="file" accept=".csv,.pdf" multiple id="dropAllInput"></label>
    <div class="drops">${IMPORT_FILES.map(([key, title, fname]) => {
    const f = S.files[key];
    const state = f ? esc(f.name) : `à déposer · ${fname}`;
    return `<label class="drop ${f ? "custom" : ""}" data-key="${key}">
      <span class="ico">${FILE_ICON}</span><span class="txt"><span class="name">${title}</span><span class="state">${state}</span></span>
      ${f ? `<button class="clear" data-clear="${key}" title="Retirer ce fichier">×</button>` : ""}
      <input type="file" accept="${key === "rates" ? ".pdf" : ".csv"}" data-file="${key}"></label>`;
  }).join("")}</div>`;
  const set = (key, file) => { S.files[key] = file; S.example = false; renderLaunch(); };
  // several files dropped anywhere in the box: each one goes to its slot; a single file dropped on a slot stays there
  $$("#sourceBox .drop, #dropAll").forEach((z) => {
    z.addEventListener("dragover", (e) => { e.preventDefault(); z.classList.add("over"); });
    z.addEventListener("dragleave", () => z.classList.remove("over"));
    z.addEventListener("drop", (e) => {
      e.preventDefault(); e.stopPropagation(); z.classList.remove("over");
      const files = [...e.dataTransfer.files];
      if (files.length === 1 && z.dataset.key) set(z.dataset.key, files[0]);
      else if (files.length) placeFiles(files);
    });
  });
  $$("#sourceBox .drop input[type=file]").forEach((i) => i.addEventListener("change", () => { if (i.files[0]) set(i.dataset.file, i.files[0]); }));
  $("#dropAllInput").addEventListener("change", (e) => { if (e.target.files.length) placeFiles([...e.target.files]); });
  $$("#sourceBox [data-clear]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); delete S.files[b.dataset.clear]; renderLaunch(); }));
}

// which input is this file? A PDF is the rate sheet; a CSV is recognised by its header, its name as a fallback.
async function detectFile(file) {
  if (/\.pdf$/i.test(file.name)) return "rates";
  let head = "";
  try { head = (await file.slice(0, 4096).text()).replace(/^\uFEFF/, "").split(/\r?\n/)[0].toLowerCase(); } catch (_) { /* unreadable */ }
  const cols = head.split(/[;,\t]/).map((c) => c.trim().replace(/^"|"$/g, ""));
  if (cols.includes("material") || cols.includes("schedule_quantity")) return "schedule";
  if (cols.includes("distance") && cols.includes("supplier_id")) return "distances";
  const name = file.name.toLowerCase();
  if (/distance/.test(name)) return "distances";
  if (/schedule|planning|besoin/.test(name)) return "schedule";
  return null;
}

async function placeFiles(files) {
  const placed = [], unknown = [], twice = [];
  const seen = {};
  for (const f of files) {
    if (!/\.(csv|pdf)$/i.test(f.name)) { unknown.push(f.name); continue; }
    const key = await detectFile(f);
    if (!key) { unknown.push(f.name); continue; }
    if (seen[key]) twice.push(f.name);
    seen[key] = true;
    S.files[key] = f; placed.push(f.name);
  }
  S.sourceRun = null; S.example = false;
  renderLaunch();
  const msg = [];
  if (unknown.length) msg.push(`Fichier${unknown.length > 1 ? "s" : ""} non reconnu${unknown.length > 1 ? "s" : ""} : ${unknown.join(", ")}`);
  if (twice.length) msg.push(`Deux fichiers du même type : ${twice.join(", ")} a été retenu`);
  if (msg.length) toast(msg.join(". ") + ".");
}

function sourceReady() { return S.example || !!S.sourceRun || IMPORT_FILES.every(([key]) => S.files[key]); }
// the missing data of the import, and whether the user has chosen how to complete each kind
function missingKinds() {
  const m = S.quality && S.quality.missing;
  return { load: !!m && m.weight.rows + m.volume.rows > 0, distance: !!m && m.suppliers_without_distance.length > 0 };
}
function choicesDone() {
  const need = missingKinds();
  return (!need.load || !!F.missing_load) && (!need.distance || !!F.missing_distance);
}
function canLaunch() { return sourceReady() && S.qualityState === "ready" && choicesDone(); }

function renderLaunch() {
  refreshQuality();
  renderSource(); renderForm(); renderMissing(); renderQuality();
  const src = S.runs.find((r) => r.run_id === S.sourceRun);
  $("#goTitle").textContent = src ? "Relancer l'optimisation" : S.example ? "Lancer l'optimisation sur l'exemple" : "Importer et lancer l'optimisation";
  let note;
  if (!sourceReady()) note = "Déposez le planning, les distances et la grille tarifaire pour commencer.";
  else if (S.qualityState === "loading") note = "Analyse des données en cours…";
  else if (S.qualityState === "error") note = "Les fichiers ne peuvent pas être utilisés : voir la qualité des données.";
  else if (!choicesDone()) note = "Des données sont absentes : choisissez comment les compléter dans les réglages détaillés.";
  else if (src) note = `Sur les fichiers de « ${src.label} », avec les réglages ci-dessus.`;
  else note = "Les données sont prêtes : un clic suffit.";
  $("#goNote").textContent = note;
  $("#composeBtn").disabled = !canLaunch();
  $("#exampleBtn").hidden = S.example;
}

// ----------------------------------------------------------------------------- data quality of the import
function sourceKey() {
  if (S.example) return "example";
  if (S.sourceRun) return "run:" + S.sourceRun;
  const { schedule, distances, rates } = S.files;
  if (!schedule || !distances) return null;
  return ["files", ...[schedule, distances, rates].map((f) => (f ? `${f.name}:${f.size}:${f.lastModified}` : "-"))].join("|");
}
async function refreshQuality() {
  const key = sourceKey();
  if (key === S.qualityKey) return;
  S.qualityKey = key; S.quality = null; S.qualityError = "";
  F.missing_load = null; F.missing_distance = null;   // new data: the user chooses again
  if (!key) { S.qualityState = "idle"; return; }
  S.qualityState = "loading";
  try {
    let q;
    if (key === "example") q = await api("/quality/example");
    else if (S.sourceRun) q = await api(`/runs/${S.sourceRun}/quality`);
    else {
      const fd = new FormData();
      fd.append("released_schedule", S.files.schedule); fd.append("distances", S.files.distances);
      if (S.files.rates) fd.append("rate_sheet", S.files.rates);
      q = await api("/quality", { method: "POST", body: fd });
    }
    if (S.qualityKey !== key) return;   // the source changed meanwhile
    S.quality = q; S.qualityState = "ready";
    if (S.pendingChoices) {
      const need = missingKinds();
      if (need.load && S.pendingChoices.missing_load) F.missing_load = S.pendingChoices.missing_load;
      if (need.distance && S.pendingChoices.missing_distance) F.missing_distance = S.pendingChoices.missing_distance;
      S.pendingChoices = null;
    }
    if (q.has_missing) $("#tuning").open = true;
  } catch (err) {
    if (S.qualityKey !== key) return;
    S.qualityState = "error"; S.qualityError = err.message;
  }
  if (S.view === "accueil") renderLaunch();
}

const km = (v) => `${nf0.format(v)} km`;
// short labels of the findings of the preparation, in the quality panel
const SHORT = { "Quantité nulle": "Sans besoin (quantité 0)", "Quantité négative": "Quantité négative", "Quantité manquante": "Quantité manquante",
  "Date de cumul en fin de planning": "Cumul de fin de planning", "Ligne plus grande qu'un camion": "Plus grandes qu'un camion",
  "Besoin un week-end": "Besoins un week-end", "Besoin un jour férié": "Besoins un jour férié", "Fournisseur en double dans les distances": "Fournisseurs en double" };

const Q_ICONS = {
  data: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>',
  rows: '<svg viewBox="0 0 24 24"><path d="m12 4 8 4-8 4-8-4z"/><path d="m4 12 8 4 8-4M4 16l8 4 8-4"/></svg>',
  ok: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2v.1"/></svg>',
  ko: '<svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7 7 17"/></svg>',
};
// level of the data from the share of valid rows (score out of 100)
function qualityLevel(score) {
  if (score >= 85) return ["good", "Bon niveau", "Les données sont globalement de bonne qualité et peuvent être utilisées pour l'optimisation."];
  if (score >= 70) return ["fair", "Niveau correct", "Les données sont utilisables : une partie des lignes est rejetée ou doit être complétée."];
  return ["poor", "À surveiller", "Beaucoup de lignes sont rejetées ou incomplètes : vérifiez les fichiers avant de vous fier aux résultats."];
}
function scoreRing(score) {
  const r = 34, c = 2 * Math.PI * r;
  return `<svg class="q-ring" viewBox="0 0 84 84" aria-hidden="true"><circle cx="42" cy="42" r="${r}" class="q-ring-bg"/>
    <circle cx="42" cy="42" r="${r}" class="q-ring-fg" stroke-dasharray="${(c * score / 100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 42 42)"/></svg>`;
}

function renderQuality() {
  const box = $("#qualityPanel"), q = S.quality;
  const head = `<div class="q-head"><span class="q-head-ico">${Q_ICONS.data}</span><h3>Qualité des données chargées</h3></div>`;
  if (S.qualityState === "idle") { box.innerHTML = head + `<p class="q-empty">S'affiche dès l'import des fichiers.</p>`; return; }
  if (S.qualityState === "loading") { box.innerHTML = head + `<p class="q-empty q-loading">Analyse des fichiers…</p>`; return; }
  if (S.qualityState === "error") { box.innerHTML = head + `<p class="q-error">${esc(S.qualityError)}</p>`; return; }
  const [level, levelLabel, levelText] = qualityLevel(q.score);
  const pct = (n) => (100 * n) / Math.max(1, q.rows_raw);
  const pctTxt = (n) => `${nf1.format(pct(n))} %`;
  const tiles = [["rows", num(q.rows_raw), "Lignes importées"], ["ok", num(q.rows_valid), "Validées"],
    ["warn", num(q.rows_to_impute), "À imputer"], ["ko", num(q.rows_rejected), "Rejetées"]];
  const bar = [["ok", q.rows_valid], ["warn", q.rows_to_impute], ["ko", q.rows_rejected]].filter(([, n]) => n > 0)
    .map(([kind, n]) => `<span class="q-seg ${kind}" style="flex:${n}" title="${pctTxt(n)}">${pct(n) >= 6 ? pctTxt(n) : ""}</span>`).join("");
  // what each status means for THIS import (the missing data and the rejection reasons found)
  const m = q.missing, need = missingKinds();
  const missing = [m.weight.rows ? "poids" : "", m.volume.rows ? "volume" : "", need.distance ? "distance" : ""].filter(Boolean);
  const chosen = choicesDone();
  const imputeText = q.rows_to_impute
    ? `${missing.length ? missing.join(", ").replace(/, ([^,]*)$/, " ou $1").replace(/^./, (s) => s.toUpperCase()) : "Donnée"} manquant${missing.length > 1 ? "s" : ""}.
       <em class="${chosen ? "done" : "todo"}">${chosen ? "Méthode choisie dans les réglages." : "Méthode à choisir dans les réglages."}</em>`
    : "Aucune ligne incomplète.";
  const REJECTED = { "Quantité nulle": "quantité = 0", "Quantité négative": "quantité négative", "Quantité manquante": "quantité manquante",
    "Date de cumul en fin de planning": "cumul de fin de planning", "Ligne plus grande qu'un camion": "plus grande qu'un camion" };
  const reasons = q.checks.filter((c) => c.count && ["DQ01", "DQ03", "DQ05"].includes(c.rule))
    .map((c) => `${REJECTED[c.label] || c.label.toLowerCase()} (${num(c.count)})`);
  box.innerHTML = head + `
    <div class="q-score">
      <div class="q-ring-wrap">${scoreRing(q.score)}<div class="q-ring-txt"><b>${q.score}</b><span>/100</span></div></div>
      <div class="q-level ${level}"><span class="q-level-tag">${levelLabel}</span><p>${levelText}</p></div>
    </div>
    <div class="q-tiles">${tiles.map(([kind, value, label]) => `<div class="q-tile ${kind}"><span class="q-tile-ico">${Q_ICONS[kind]}</span><b>${value}</b><span>${label}</span></div>`).join("")}</div>
    <p class="q-sub">Répartition des lignes par statut</p>
    <div class="q-bar">${bar}</div>
    <ul class="q-legend">
      <li><span class="q-dot ok">${Q_ICONS.ok}</span><b>Validées</b><span>Lignes complètes, prêtes pour l'optimisation.</span></li>
      <li><span class="q-dot warn">${Q_ICONS.warn}</span><b>À imputer</b><span>${imputeText}</span></li>
      <li><span class="q-dot ko">${Q_ICONS.ko}</span><b>Rejetées</b><span>${reasons.length ? reasons.join(", ").replace(/^./, (s) => s.toUpperCase()) + "." : "Aucune."}</span></li>
    </ul>`;
}

function renderMissing() {
  const box = $("#missingFiche"), q = S.quality;
  const visible = S.qualityState === "ready" && q.has_missing;
  box.hidden = !visible;
  if (!visible) return;
  const m = q.missing, need = missingKinds();
  const loadHint = { full_truck: "Prudent : la ligne voyage seule.",
    couple_median: `Médiane des lignes du même fournisseur × usine${m.weight.full_truck ? ` ; ${num(m.weight.full_truck)} lignes sans référence restent en camion plein` : ""}.` };
  const distanceHint = { max: "Prudent : chiffré au plus cher.", median: "Distance typique du planning." };
  box.classList.toggle("off", !choicesDone());
  box.innerHTML = `<p class="f-lab">Compléter les données absentes</p>
    ${need.load ? `<div class="m-row"><p class="m-q">Poids et volumes <span class="muted">${num(Math.max(m.weight.rows, m.volume.rows))} lignes</span></p>
      <div class="chips" data-choice="missing_load"><button type="button" data-v="full_truck">Camion plein</button><button type="button" data-v="couple_median">Médiane fournisseur × usine</button></div>
      ${F.missing_load ? `<p class="m-hint">${loadHint[F.missing_load]}</p>` : ""}</div>` : ""}
    ${need.distance ? `<div class="m-row"><p class="m-q">Distances <span class="muted">${m.suppliers_without_distance.map(esc).join(", ")}</span></p>
      <div class="chips" data-choice="missing_distance"><button type="button" data-v="max">La plus longue · ${km(m.distance_max)}</button><button type="button" data-v="median">Médiane · ${km(m.distance_median)}</button></div>
      ${F.missing_distance ? `<p class="m-hint">${distanceHint[F.missing_distance]}</p>` : ""}</div>` : ""}`;
  $$("[data-choice] button", box).forEach((b) => {
    const field = b.parentElement.dataset.choice;
    b.classList.toggle("on", F[field] === b.dataset.v);
    b.addEventListener("click", () => { F[field] = b.dataset.v; renderLaunch(); });
  });
}

const STATUS = { queued: "En attente", running: "En cours", succeeded: "Terminé", failed: "Échec", cancelled: "Annulé" };
// relaunch a run: the form takes its files and its parameters; the user changes a setting and launches
function reuseRun(id) {
  const run = S.runs.find((r) => r.run_id === id);
  if (!run) return;
  const p = planner(run), prep = (run.parameters && run.parameters.preparation) || {};
  S.sourceRun = id; S.files = {}; S.example = false;
  F.anticipation = p.max_advance_days ?? DEFAULT_F.anticipation;
  F.smoothing = p.smoothing_budget ?? DEFAULT_F.smoothing;
  const load = prep.missing_load === "material_median" ? "couple_median" : prep.missing_load;   // former method of the median
  S.pendingChoices = { missing_load: load, missing_distance: prep.missing_distance };   // applied once its quality is known
  $("#f-label").value = `${run.label} (bis)`.slice(0, 60);
  show("accueil");
}

function errorText(run) {
  const e = run.error || "";
  if (/exited with code/.test(e)) return "Le calcul s'est arrêté de façon inattendue.";
  if (/interrupted by a restart/.test(e)) return "Interrompu par un redémarrage du serveur.";
  return e ? "Erreur pendant le calcul." : "";
}

function inputSummary(run) {
  const names = run.input_files || {};
  const files = ["released_schedule", "distances", "rate_sheet"].map((k) => names[k]).filter(Boolean);
  const k = run.kpis || {};
  return `${files.length ? files.map(esc).join(" · ") : "fichiers importés"}${k.lines ? `<br>${num(k.lines)} besoins · ${num(k.couples)} couples` : ""}`;
}

function renderHistory() {
  const list = S.runs || [];
  $("#historyCount").textContent = list.length ? `${list.length} calcul${list.length > 1 ? "s" : ""}` : "";
  $("#historyList").innerHTML = list.length ? `<div class="h-row h-head"><span>Scénario</span><span>Données</span><span>Paramètres</span><span>Résultat</span><span></span></div>` +
    list.map((r) => {
      const ok = r.status === DONE, k = r.kpis || {};
      const result = ok ? `<b class="h-saving">${eur(k.saving)}</b><span>${num(k.trucks_before)} → ${num(k.trucks_after)} camions</span>`
        : `<b class="h-status ${r.status}">${STATUS[r.status] || r.status}</b>${r.error ? `<span title="${esc(r.error)}">${errorText(r)}</span>` : ""}`;
      return `<div class="h-row ${ok ? "ok" : ""} ${r.run_id === S.current ? "current" : ""}" data-run="${r.run_id}">
        <span class="h-name"><b>${esc(r.label)}</b><span>${fmtStamp(r.created_at)}</span></span>
        <span class="h-data">${inputSummary(r)}</span>
        <span class="h-params">${settingsChips(r)}</span>
        <span class="h-result">${result}</span>
        <span class="h-actions">${ok ? `<button class="btn ghost small" data-open-run="${r.run_id}">Voir</button>` : ""}
          ${FINISHED.includes(r.status) ? `<button class="h-icon" data-reuse="${r.run_id}" title="Relancer avec les mêmes fichiers et paramètres" aria-label="Relancer">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4.5h-4.5"/></svg></button>
          <button class="h-icon" data-del-run="${r.run_id}" title="Supprimer" aria-label="Supprimer">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>` : ""}</span>
      </div>`;
    }).join("") : `<p class="empty-note">Aucun calcul pour l'instant. <button class="link" data-view="accueil">Lancer un premier calcul</button></p>`;
  $$("#historyList .h-row.ok").forEach((row) => row.addEventListener("click", (e) => {
    if (!e.target.closest("button")) openRun(row.dataset.run, "apercu");
  }));
  $$("#historyList [data-open-run]").forEach((b) => b.addEventListener("click", () => openRun(b.dataset.openRun, "apercu")));
  $$("#historyList [data-reuse]").forEach((b) => b.addEventListener("click", () => reuseRun(b.dataset.reuse)));
  $$("#historyList [data-del-run]").forEach((b) => b.addEventListener("click", () => deleteRun(b.dataset.delRun)));
}

// confirmation in the style of the interface: resolves true (confirmed) or false (cancel, Échap, click outside)
function confirmDialog({ title, text, ok, cancel = "Annuler" }) {
  return new Promise((resolve) => {
    const box = $("#confirmModal"), last = document.activeElement;
    $("#cmTitle").textContent = title; $("#cmText").textContent = text;
    $("#cmOk").textContent = ok; $("#cmCancel").textContent = cancel;
    box.hidden = false; requestAnimationFrame(() => box.classList.add("on"));
    $("#cmCancel").focus();   // the safe choice has the focus
    const close = (value) => {
      box.classList.remove("on"); box.hidden = true;
      document.removeEventListener("keydown", onKey, true);
      $("#cmOk").onclick = $("#cmCancel").onclick = box.onclick = null;
      if (last && last.isConnected) last.focus();
      resolve(value);
    };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(false); } };
    document.addEventListener("keydown", onKey, true);
    $("#cmOk").onclick = () => close(true);
    $("#cmCancel").onclick = () => close(false);
    box.onclick = (e) => { if (e.target === box) close(false); };
  });
}

async function deleteRun(id) {
  const run = S.runs.find((r) => r.run_id === id);
  if (!(await confirmDialog({ title: `Supprimer « ${run ? run.label : "ce calcul"} » ?`,
    text: "Ses fichiers et ses résultats seront effacés. Cette action est définitive.", ok: "Supprimer" }))) return;
  try {
    await api("/runs/" + id, { method: "DELETE" });
    if (S.sourceRun === id) S.sourceRun = null;
    await refreshRuns();
    if (id === S.current) { S.summary = null; S.current = null; S.run = null; }
    show("historique");
  } catch (err) { toast(err.message); }
}

async function launch() {
  const btn = $("#composeBtn");
  if (!canLaunch()) return;
  btn.disabled = true; btn.classList.add("busy");
  try {
    const fd = new FormData();
    fd.append("label", $("#f-label").value.trim() || (S.example ? "Exemple" : "Scénario"));
    fd.append("parameters", JSON.stringify(runParameters()));
    let path = "/runs";
    if (S.example) path = "/runs/example";
    else if (S.sourceRun) path = `/runs/${S.sourceRun}/rerun`;
    else IMPORT_FILES.forEach(([key, , , field]) => fd.append(field, S.files[key]));
    const run = await api(path, { method: "POST", body: fd });
    S.files = {}; S.example = false; S.sourceRun = null;
    startRunning(run);
  } catch (err) {
    toast(err.message); renderLaunch();
  } finally { btn.disabled = false; btn.classList.remove("busy"); if (S.view === "accueil") renderLaunch(); }
}
$("#composeBtn").addEventListener("click", () => launch());
// files dropped anywhere in the "files" area (also when a previous run was selected)
$("#sourceBox").addEventListener("dragover", (e) => e.preventDefault());
$("#sourceBox").addEventListener("drop", (e) => {
  e.preventDefault();
  const files = [...e.dataTransfer.files];
  if (files.length) placeFiles(files);
});
// the example is a source like the dropped files: its data quality shows, then the user launches
$("#exampleBtn").addEventListener("click", () => { S.example = true; S.sourceRun = null; S.files = {}; renderLaunch(); });

// settings of the form -> parameters of the planner (POST /api/runs, field "parameters")
function runParameters() {
  const preparation = {};   // how the missing data are completed: only asked (and sent) when some are missing
  const need = missingKinds();
  if (need.load && F.missing_load) preparation.missing_load = F.missing_load;
  if (need.distance && F.missing_distance) preparation.missing_distance = F.missing_distance;
  return { planner: { max_advance_days: F.anticipation, smoothing_budget: F.smoothing }, preparation };
}

// ----------------------------------------------------------------------------- settings (folded menus)
let DEFAULT_F = { anticipation: 7, smoothing: 0 };
const F = { ...DEFAULT_F, missing_load: null, missing_distance: null };
function pctTxt(x) { return x === 0 ? "aucun" : `${(x * 100).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %`; }
function placePill(seg) {
  const on = $("button.on", seg), pill = $(".pill", seg);
  if (on && pill) { pill.style.left = on.offsetLeft + "px"; pill.style.width = on.offsetWidth + "px"; }
}
$$("details.fold").forEach((d) => d.addEventListener("toggle", () => $$(".seg", d).forEach(placePill)));

function renderForm() {
  $$(".fiche[data-field]").forEach((f) => {
    const field = f.dataset.field;
    $$(".chips button", f).forEach((b) => b.classList.toggle("on", String(F[field]) === b.dataset.v));
  });
  $("#antVal").textContent = F.anticipation;
  $('.fiche[data-field="anticipation"]').classList.toggle("off", F.anticipation !== DEFAULT_F.anticipation);
  const changed = Object.keys(DEFAULT_F).some((k) => F[k] !== DEFAULT_F[k]);
  $("#tuningSum").textContent = changed ? [`${F.anticipation} j d'avance`,
    F.smoothing ? `lissage ${pctTxt(F.smoothing)}` : "lissage gratuit"].join(" · ") : "par défaut : coût minimal";
  if (F.missing_load === "couple_median" || F.missing_distance === "median")   // a choice of the user: no longer "by default"
    $("#tuningSum").textContent = $("#tuningSum").textContent.replace("par défaut : ", "") + " · médiane si absent";
  const w = $("#tuningWarn"), warnings = [];
  if (F.anticipation !== DEFAULT_F.anticipation)
    warnings.push(`${F.anticipation} jours d'avance : en dehors de la règle des ${DEFAULT_F.anticipation} jours, à présenter comme un scénario.`);
  if (F.smoothing > 0)   // each level of the paid smoothing is an integer programme: about a minute instead of seconds
    warnings.push("Lissage avec surcoût : le calcul est plus long (environ 50 s pour 0,5 % sur le planning du projet, contre 10 s).");
  w.hidden = !warnings.length;
  w.textContent = warnings.join(" ");
}
$$(".fiche[data-field] .chips button").forEach((b) => b.addEventListener("click", () => {
  const field = b.closest(".fiche").dataset.field;
  F[field] = +b.dataset.v;
  renderForm();
}));
$$(".stepper [data-step]").forEach((b) => b.addEventListener("click", () => {
  F.anticipation = Math.max(0, Math.min(21, F.anticipation + +b.dataset.step));
  renderForm();
}));

// ----------------------------------------------------------------------------- current situation, shown with the results
function heatmapSvg(full) {
  const last = full.trucks.reduce((a, row, i) => (row.some((v) => v) ? i : a), 0) + 1;   // no empty distance rows at the bottom
  const h = { ...full, km: full.km.slice(0, last), trucks: full.trucks.slice(0, last), cost: full.cost.slice(0, last), price: full.price.slice(0, last) };
  const rows = h.km.length, cols = h.kg.length, cw = 58, ch = 30, L = 92, T = 46;
  const W = L + cols * cw + 4, H = T + rows * ch + 4, max = Math.max(...h.trucks.flat());
  const tone = (v) => {
    if (!v) return [C.empty, C.muted];
    const r = Math.sqrt(v / max);
    return r < 0.2 ? [C.g1, C.label] : r < 0.4 ? [C.g2, C.ink] : r < 0.6 ? [C.g3, C.ink] : r < 0.8 ? [C.g4, "#fff"] : [C.g6, "#fff"];
  };
  let s = `<text x="${L}" y="14" style="font-weight:700;fill:${C.label}">poids chargé dans le camion →</text>`;
  h.kg.forEach((k, c) => (s += `<text x="${L + c * cw + cw / 2}" y="${T - 10}" text-anchor="middle" style="font-size:10px">${k.replace(" kg", "")}</text>`));
  h.km.forEach((k, r) => {
    const y = T + r * ch;
    s += `<text x="${L - 8}" y="${y + ch / 2 + 4}" text-anchor="end" style="font-size:10.5px">${k}</text>`;
    h.kg.forEach((g, c) => {
      const v = h.trucks[r][c], [bg, fg] = tone(v);
      s += `<g class="hm-cell" style="--d:${(0.006 * (r * cols + c)).toFixed(3)}s" data-tip="${k} · ${g}<br>prix d'un camion : <b>${eur(h.price[r][c], 2)}</b><br>${v ? `<b>${v}</b> camion${v > 1 ? "s" : ""} · ${eur(h.cost[r][c])}` : "aucun camion"}">
        <rect x="${L + c * cw + 1}" y="${y + 1}" width="${cw - 3}" height="${ch - 3}" rx="2" fill="${bg}"/>
        ${v ? `<text x="${L + c * cw + cw / 2}" y="${y + ch / 2 + 4}" text-anchor="middle" style="font-size:11.5px;font-weight:700;fill:${fg}">${v}</text>` : ""}</g>`;
    });
  });
  return { svg: svg(W, H, s), hidden: full.km.length - last };
}

// ---- Executive Dashboard helpers
const frDate = (iso, year = false) => iso ? new Date(iso + "T12:00:00").toLocaleDateString("fr-FR", year ? { day: "2-digit", month: "short", year: "numeric" } : { day: "2-digit", month: "short" }) : "?";
const signed = (v, unit, digits = 0) => {
  const r = Number(v.toFixed(digits));
  const f = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: digits }).format(Math.abs(r));
  return `${r < 0 ? "−" : r > 0 ? "+" : ""}${f}${unit}`;
};
const pctChange = (before, after) => (before ? (100 * (after - before)) / before : 0);
const deltaClass = (v, goodWhenDown) => (Math.abs(v) < 0.05 ? "flat" : (v < 0) === goodWhenDown ? "good" : "bad");

function renderCompare() {   // second row: trucks, deliveries, fill rate (real values only)
  const k = S.summary.kpi, sk = S.sit ? S.sit.kpi : null;
  const card = (label, value, before, delta, cls) => `<div class="ed-card ed-kpi"><p class="ed-label">${label}</p>
    <div class="ed-kpi-value">${value}</div>
    <div class="ed-kpi-foot"><span>Avant : ${before}</span>${delta ? `<b class="ed-delta ${cls}">${delta}</b>` : ""}</div></div>`;
  const dT = pctChange(k.trucks_before, k.trucks_after), dD = pctChange(k.deliveries_before, k.deliveries_after);
  const fillDelta = sk ? k.fill_mean_pct - sk.fill_pct : null;
  $("#kpiRow").innerHTML =
    card("Camions", num(k.trucks_after), num(k.trucks_before), signed(dT, " %"), deltaClass(dT, true)) +
    card("Livraisons", num(k.deliveries_after), num(k.deliveries_before), signed(dD, " %"), deltaClass(dD, true)) +
    card("Remplissage moyen", `${nf1.format(k.fill_mean_pct)} %`, sk ? `${nf1.format(sk.fill_pct)} %` : "non disponible",
         sk ? signed(fillDelta, " pts", Math.abs(fillDelta) < 10 ? 1 : 0) : "", sk ? deltaClass(fillDelta, false) : "");
  const max = Math.max(k.cost_before, k.cost_after) || 1, saved = k.cost_before - k.cost_after;
  $("#costCard").innerHTML = `<div class="ed-card-head"><h2>Coût de transport</h2><span class="ed-chip">Avant / Après</span></div>
    <div class="ed-bar-row"><span>Situation actuelle</span><b>${eur(k.cost_before)}</b></div>
    <div class="ed-bar"><i class="before" style="width:${(100 * k.cost_before / max).toFixed(2)}%"></i></div>
    <div class="ed-bar-row"><span>Plan Gilbert</span><b>${eur(k.cost_after)}</b></div>
    <div class="ed-bar"><i class="after" style="width:${(100 * k.cost_after / max).toFixed(2)}%"></i></div>
    <p class="ed-foot">${saved >= 0 ? `${eur(saved)} économisés à périmètre identique.` : `Surcoût de ${eur(-saved)} à périmètre identique.`}</p>`;
}

function renderSituationBlock() {
  renderCompare();
  const st = S.sit;
  $("#situationBlock").hidden = !st;
  if (!st) return;
  renderSitTab("heat");
  $("#foldRulesSum").textContent = `${st.rules.length} règles`;
  $("#rulesList").innerHTML = st.rules.map((r, i) => `<li style="--d:${0.04 * i}s">${esc(r)}</li>`).join("");
  const KIND = { corrige: ["corrigé", "ok"], info: ["information", "info"] };
  const shown = st.anomalies.filter((a) => a.count);
  $("#anomalyCount").textContent = `${shown.length} types détectés`;
  $("#anomalyTable").innerHTML = `<table class="anomalies"><thead><tr><th>Anomalie</th><th class="num">Lignes</th><th>Règle</th><th>Traitement</th><th></th></tr></thead><tbody>
    ${shown.map((a) => `<tr><td><b>${esc(a.label)}</b></td><td class="num">${num(a.count)}</td><td class="det">${esc(a.rule)}</td>
      <td>${esc(a.treatment)}</td><td><span class="badge ${(KIND[a.kind] || KIND.info)[1]}">${(KIND[a.kind] || KIND.info)[0]}</span></td></tr>`).join("")}</tbody></table>`;
}

function renderSitTab(key) {
  S.sitTab = key;
  const st = S.sit;
  if (key === "calendar") {
    $("#sitPanelTitle").textContent = "Planning actuel des livraisons";
    $("#sitCaption").textContent = "Camions reçus par usine et par jour quand chaque ligne est livrée le jour de son besoin. À comparer avec l'onglet Planning.";
    $("#sitBody").innerHTML = calendarSvg(st.calendar) + `<div class="legend" style="margin-top:1rem"><span class="l-scale">peu à beaucoup de camions</span><span class="l-peak">jour de pointe de l'usine</span></div>`;
    const cal = $("#sitBody svg"); if (cal) cal.style.maxWidth = cal.viewBox.baseVal.width * 1.15 + "px";
  } else {
    const h = heatmapSvg(st.heatmap), light = st.heatmap.trucks.reduce((a, row) => a + row[0] + row[1], 0);
    $("#sitPanelTitle").textContent = "Camions actuels sur la grille tarifaire";
    $("#sitCaption").textContent = `Chaque case est une case de la grille (distance × poids chargé)${h.hidden ? `, sans les ${h.hidden} tranches de distance vides` : ""}. ` +
      `${num(light)} camions sur ${num(st.kpi.trucks)} (${Math.round(100 * light / st.kpi.trucks)} %) partent avec moins de 100 kg : c'est là que le regroupement fait gagner.`;
    $("#sitBody").innerHTML = `<div class="heat-wrap">${h.svg}</div>`;
  }
}

// ----------------------------------------------------------------------------- running
const PHRASES = ["On lit les besoins de chaque fournisseur…", "On essaie des milliers de plans de livraison…",
  "On cherche, pour chaque fournisseur, les bons jours de la semaine…", "On étale les arrivées entre les jours…",
  "On range chaque ligne dans son camion…", "On vérifie chaque règle, une par une…"];
let gatherRaf = null, graphMoment = false;
const GRAPH_PHRASE = "On relie les besoins et on cherche le meilleur chemin…";

// Waiting animation: the needs scatter, then line up in the four trucks; one scatter out of three is a "graph"
// moment (the scattered needs are linked to their nearest neighbours and an orange path lights up) before the trucks.
function buildGather() {
  const box = $("#gather"); box.innerHTML = "";
  // the scene covers the whole loading panel (pixels): the needs scatter everywhere, the trucks stay on the left
  const NS = "http://www.w3.org/2000/svg", W = Math.max(320, box.clientWidth || 1000), H = Math.max(300, box.clientHeight || 520), N = 44, MOVE = 1.3;
  const svgEl = document.createElementNS(NS, "svg");
  svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`); box.appendChild(svgEl);
  const el = (tag, attrs = {}, parent = svgEl) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); parent.appendChild(n); return n; };
  const graph = el("g"), pathLayer = el("g"), truckLayer = el("g", { class: "g-trucks" }), dotLayer = el("g");
  const narrow = W < 760, left = narrow ? W : W * 0.54, cx = left / 2, cy = narrow ? H * 0.42 : H / 2;
  const TRUCKS = [[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([dx, dy]) => [cx + dx * 118 - 82, cy + dy * 72 - 40]);
  const truckEls = TRUCKS.map(([x, y]) => {
    const g = el("g", {}, truckLayer);
    el("rect", { x, y, width: 132, height: 70, rx: 12, class: "g-body" }, g);
    el("path", { d: `M${x + 132} ${y + 24} h20 a12 12 0 0 1 12 12 v34 h-32`, class: "g-body" }, g);
    [x + 28, x + 104, x + 148].forEach((cx) => { el("circle", { cx, cy: y + 72, r: 9, class: "g-wheel" }, g); el("circle", { cx, cy: y + 72, r: 3.5, class: "g-hub" }, g); });
    return g;
  });
  const COLORS = ["#F6C28B", "#E8772E", "#F6C28B", "#F6C28B", "#9FE0C5"];
  const dots = Array.from({ length: N }, (_, i) => el("circle", { r: 6, fill: COLORS[i % 5] }, dotLayer));
  const slot = (i) => { const [tx, ty] = TRUCKS[i % 4], k = Math.floor(i / 4); return [tx + 20 + (k % 6) * 18, ty + 21 + Math.floor(k / 6) * 20]; };
  // the needs scatter everywhere except on the text block (timer, phrase, stop button), which stays readable
  const tb = $("#view-running .running-text"), br = box.getBoundingClientRect(), r = tb ? tb.getBoundingClientRect() : null;
  const keep = r ? { x1: r.left - br.left - 24, x2: r.right - br.left + 24, y1: r.top - br.top - 20, y2: r.bottom - br.top + 20 } : null;
  const free = ([x, y]) => !keep || x < keep.x1 || x > keep.x2 || y < keep.y1 || y > keep.y2;
  const scatter = () => Array.from({ length: N }, () => {
    let pt, tries = 0;
    do { pt = [16 + Math.random() * (W - 32), 16 + Math.random() * (H - 32)]; } while (!free(pt) && ++tries < 60);
    return pt;
  });
  const ease = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - Math.pow(1 - x, 3));
  let k = 0, prev = Array.from({ length: N }, () => [W / 2, H / 2]), seg = null;
  const nextSeg = (start) => {
    const isScatter = k % 2 === 0, graphy = isScatter;   // a graph between the needs at every interval between the trucks
    const s = { start, dur: graphy ? 4.9 : 1.9, scatter: isScatter, graphy, from: prev, pos: isScatter ? scatter() : Array.from({ length: N }, (_, i) => slot(i)) };
    graph.innerHTML = ""; pathLayer.innerHTML = "";
    if (graphy) {   // links to the two nearest neighbours, and a path from left to right
      const P = s.pos, keys = new Set();
      P.forEach((a, i) => P.map((b, j) => [j, (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2]).filter(([j]) => j !== i)
        .sort((u, v) => u[1] - v[1]).slice(0, 2).forEach(([j]) => keys.add(i < j ? `${i}-${j}` : `${j}-${i}`)));
      s.lines = [...keys].map((key) => { const [i, j] = key.split("-").map(Number);
        return el("line", { x1: P[i][0], y1: P[i][1], x2: P[j][0], y2: P[j][1], class: "g-link", opacity: 0 }, graph); });
      let cur = P.map((p, i) => [i, p[0]]).sort((u, v) => u[1] - v[1])[0][0];
      s.route = [cur];
      while (s.route.length < 22) {   // a long path: it crosses the whole panel through many needs
        const nx = P.map((b, j) => [j, b[0] > P[cur][0] + 4 && !s.route.includes(j) ? (P[cur][0] - b[0]) ** 2 + (P[cur][1] - b[1]) ** 2 : Infinity]).sort((u, v) => u[1] - v[1])[0];
        if (!isFinite(nx[1])) break;
        cur = nx[0]; s.route.push(cur);
      }
      s.path = el("path", { d: s.route.map((i, n) => `${n ? "L" : "M"}${P[i][0].toFixed(1)} ${P[i][1].toFixed(1)}`).join(" "), class: "g-path" }, pathLayer);
      s.len = s.path.getTotalLength(); s.path.setAttribute("stroke-dasharray", s.len); s.path.setAttribute("stroke-dashoffset", s.len);
    }
    prev = s.pos; k++;
    return s;
  };
  const t0 = performance.now();
  seg = nextSeg(0);
  const frame = (now) => {
    const t = (now - t0) / 1000;
    while (t >= seg.start + seg.dur) seg = nextSeg(seg.start + seg.dur);
    const local = t - seg.start, p = ease(local / MOVE);
    dots.forEach((d, i) => {
      const a = seg.from[i], b = seg.pos[i];
      d.setAttribute("cx", (a[0] + (b[0] - a[0]) * p).toFixed(1)); d.setAttribute("cy", (a[1] + (b[1] - a[1]) * p).toFixed(1));
      d.setAttribute("r", 6); d.setAttribute("fill", COLORS[i % 5]);
    });
    // as in the original screen: the trucks fade out (0.6 s) when the needs leave them, and fade back in when they come back
    const fade = ease(local / 0.6), first = seg.start === 0;
    truckEls.forEach((g) => g.setAttribute("opacity", (first ? 0 : seg.scatter ? 1 - fade : fade).toFixed(3)));
    graphMoment = seg.graphy && local > MOVE && local < seg.dur - 0.6;
    if (seg.graphy) {
      const links = ease((local - 1.2) / 0.6) * (1 - ease((local - 4.3) / 0.5));
      seg.lines.forEach((l) => l.setAttribute("opacity", links.toFixed(3)));
      const draw = Math.min(1, Math.max(0, (local - 1.9) / 2.2)), fade = 1 - ease((local - 4.3) / 0.5);   // the path is walked slowly, at an even pace
      seg.path.setAttribute("stroke-dashoffset", (seg.len * (1 - draw)).toFixed(1));
      seg.path.setAttribute("opacity", fade.toFixed(3));
      seg.route.forEach((i, n) => { if (draw * (seg.route.length - 1) >= n - 0.01 && fade > 0.05) { dots[i].setAttribute("r", 8); dots[i].setAttribute("fill", "#E8772E"); } });
    }
    gatherRaf = requestAnimationFrame(frame);
  };
  cancelAnimationFrame(gatherRaf);
  gatherRaf = requestAnimationFrame(frame);
}
// the scene follows the size of the loading panel
let gatherResize = null;
window.addEventListener("resize", () => { clearTimeout(gatherResize); gatherResize = setTimeout(() => { if (gatherRaf && S.view === "running") buildGather(); }, 200); });

function startRunning(run) {
  S.runningId = run.run_id;
  $("#runLabel").textContent = run.label;
  $("#runTimer").textContent = "0 s";
  show("running");
  buildGather();
  const t0 = Date.now();
  clearInterval(S.polling);
  S.polling = setInterval(async () => {
    const el = (Date.now() - t0) / 1000;
    $("#runTimer").textContent = Math.floor(el) + " s";
    $("#runPhrase").textContent = graphMoment ? GRAPH_PHRASE : PHRASES[Math.floor(el / 5) % PHRASES.length];
    if (Math.floor(el * 2) % 2) return; // ask the server once a second
    try {
      const st = await api("/runs/" + run.run_id);
      if (st.status === DONE) { stopRunning(); await refreshRuns(); $("#f-label").value = `Scénario ${S.runs.length + 1}`; await openRun(st.run_id, "apercu"); }
      else if (st.status === FAILED) { stopRunning(); toast("Le calcul n'a pas abouti : " + (st.error || st.status)); await refreshRuns(); show("accueil"); }
      else if (st.status === CANCELLED) { stopRunning(); toast("Calcul arrêté."); await refreshRuns(); show("accueil"); }
    } catch (_) { /* server busy: try again */ }
  }, 500);
}
function stopRunning() { clearInterval(S.polling); cancelAnimationFrame(gatherRaf); gatherRaf = null; }

// stop button of the waiting screen: the server kills the run and its worker processes
$("#stopRunBtn").addEventListener("click", async () => {
  if (!S.runningId || !(await confirmDialog({ title: "Arrêter le calcul ?", text: "Le calcul en cours sera interrompu et ne gardera aucun résultat.",
    ok: "Arrêter le calcul", cancel: "Continuer" }))) return;
  const btn = $("#stopRunBtn"); btn.disabled = true; btn.classList.add("busy");
  try {
    await api(`/runs/${S.runningId}/cancel`, { method: "POST" });
    stopRunning(); toast("Calcul arrêté.");
    await refreshRuns(); show("accueil");
  } catch (err) { toast(err.message); }   // e.g. the run ended meanwhile: the polling opens its results
  finally { btn.disabled = false; btn.classList.remove("busy"); }
});

// ----------------------------------------------------------------------------- runs
async function refreshRuns() {
  S.runs = await api("/runs");
  if (S.view === "historique") renderHistory();
}

async function openRun(id, view) {
  S.summary = await api(`/runs/${id}/views/summary`);
  try { S.sit = await api(`/runs/${id}/views/situation`); } catch (_) { S.sit = null; }
  S.current = id; S.trucksPage = 0; S.selectedTruck = null; S.suppliers = [];
  resetPlanning();
  S.run = S.runs.find((r) => r.run_id === id);
  fillFilters(); renderRunBar();
  show(view || "apercu");
}

function renderRunBar() {
  const run = S.run, f = S.summary.filters;
  $("#runBarName").textContent = run ? run.label : "Scénario";
  $("#runBarMeta").textContent = `${run ? fmtStamp(run.created_at) + " · " : ""}${num(f.suppliers.length)} fournisseurs · ${num(f.plants.length)} usine${f.plants.length > 1 ? "s" : ""}`;
  $("#runBarChips").innerHTML = settingsChips(run);
  // export menu: the Excel report, the three outputs and the zip of every table
  const base = `/api/runs/${S.current}`;
  const files = [["/excel", "Rapport complet", "Excel · toutes les sorties, contrôles et tests"],
    ["/files/sortie1_profils", "Jours de livraison par fournisseur", "CSV · profils (sortie 1)"],
    ["/files/sortie2_contenu_camions", "Contenu des camions", "CSV · materials par camion (sortie 2)"],
    ["/files/sortie3_saving", "Économies par fournisseur", "CSV · économies (sortie 3)"],
    ["/download", "Tout télécharger", "Archive ZIP de toutes les tables"]];
  $("#downloads").innerHTML = files.map(([path, title, what]) => `<a role="menuitem" href="${base}${path}" download><b>${title}</b><span>${what}</span></a>`).join("");
}

function fillFilters() {
  const f = S.summary.filters;
  const opts = (arr, label, fmt = (x) => x) => `<option value="">${label}</option>` + arr.map((x) => `<option value="${esc(x)}">${esc(fmt(x))}</option>`).join("");
  $("#pf-supplier").innerHTML = opts(f.suppliers, "Tous les fournisseurs");
  $("#pf-plant").innerHTML = opts(f.plants, "Toutes les usines");
  $("#tf-plant").innerHTML = opts(f.plants, "Toutes les usines");
  $("#tf-supplier").innerHTML = opts(f.suppliers, "Tous les fournisseurs");
  $("#tf-day").innerHTML = opts(f.days, "Tous les jours", dayLabel);
}

// ----------------------------------------------------------------------------- animations
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function countUp(root = document) {
  $$("[data-count]", root).forEach((el) => {
    const end = +el.dataset.count, fmt = { eur, num, s: (v) => Math.round(v) + " s" }[el.dataset.fmt || "num"];
    if (REDUCED) { el.textContent = fmt(end); return; }
    const t0 = performance.now(), dur = +(el.dataset.dur || 1100);
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(end * e);
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}
const counter = (v, fmt = "num", dur) => `<span data-count="${v}" data-fmt="${fmt}"${dur ? ` data-dur="${dur}"` : ""}>${fmt === "eur" ? eur(0) : "0"}</span>`;

// ----------------------------------------------------------------------------- charts
function svg(w, h, body, cls = "chart") { return `<svg class="${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMinYMin meet">${body}</svg>`; }
function niceMax(v) { // a maximum that splits into 4 round ticks
  const p = Math.pow(10, Math.floor(Math.log10(v || 1)));
  return [1, 2, 2.5, 4, 5, 6, 8, 10].map((m) => m * p).find((m) => m >= v && Number.isInteger((m / 4) / (p / 10))) || 10 * p;
}

function splitBar(before, after) {
  const kept = Math.max(0, Math.min(after, before)), avoided = before - kept;
  const p = before ? (kept / before) * 100 : 0;
  return `<div class="tbar-wrap"><div class="tbar" role="img" aria-label="${num(kept)} camions gardés et ${num(avoided)} évités sur ${num(before)}">
      <div class="tbar-kept" style="width:${p.toFixed(2)}%;--grow:${p ? (100 / p).toFixed(3) : 1}"></div><div class="tbar-avoided"></div>
    </div>
    <div class="tbar-keys">
      <div class="k-kept"><b>${counter(kept)}</b><span>gardés</span></div>
      <div class="k-avoided"><b>${counter(avoided)}</b><span>évités</span></div>
    </div>
    <p class="tbar-note">La barre entière = les ${num(before)} camions d'aujourd'hui</p></div>`;
}

function plantBars(plants, W = 640, H = 300) {
  const L = 46, B = 34, T = 34, max = niceMax(Math.max(...plants.map((p) => p.cost_before)));
  const gw = (W - L) / plants.length, bw = Math.min(W / 14, gw * 0.28), y = (v) => T + (H - T - B) * (1 - v / max);
  let s = "";
  for (let k = 0; k <= 4; k++) { const v = (max * k) / 4; s += `<g class="axis"><line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/></g><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${num(v)}</text>`; }
  plants.forEach((p, i) => {
    const cx = L + gw * i + gw / 2, save = 100 * (1 - p.cost_after / p.cost_before);
    s += `<rect x="${cx - bw - 3}" y="${y(p.cost_before)}" width="${bw}" height="${H - B - y(p.cost_before)}" rx="8" class="bar" style="--d:${(0.08 * i).toFixed(2)}s" fill="${C.before}" data-tip="${p.plant} · aujourd'hui<br><b>${eur(p.cost_before)}</b>"/>`;
    s += `<rect x="${cx + 3}" y="${y(p.cost_after)}" width="${bw}" height="${H - B - y(p.cost_after)}" rx="8" class="bar" style="--d:${(0.08 * i + 0.25).toFixed(2)}s" fill="${C.after}" data-tip="${p.plant} · avec Gilbert<br><b>${eur(p.cost_after)}</b>"/>`;
    s += `<text x="${cx + 3 + bw / 2}" y="${y(p.cost_after) - 8}" text-anchor="middle" class="val fade" style="fill:${C.afterDark};--d:${(0.08 * i + 0.8).toFixed(2)}s">−${nf0.format(save)} %</text>`;
    s += `<text x="${cx}" y="${H - 10}" text-anchor="middle" style="font-weight:700;fill:${C.label}">${p.plant}</text>`;
  });
  return svg(W, H, s);
}

function miniHist(rows, W = 300, H = 110) {
  const max = Math.max(...rows.map((r) => r.lines)), bw = W / rows.length;
  return svg(W, H + 18, rows.map((r, i) => {
    const h = (H - (W > 600 ? 28 : 6)) * (r.lines / max);
    const w = Math.min(bw - 6, 70);
    return `<rect x="${i * bw + (bw - w) / 2}" y="${H - h}" width="${w}" height="${h}" rx="2" class="bar" style="--d:${(0.06 * i).toFixed(2)}s" fill="${r.days === 0 ? C.slate : C.after}" data-tip="${r.days} jour(s) d'avance<br><b>${num(r.lines)}</b> lignes"/>
      <text x="${i * bw + bw / 2}" y="${H + 14}" text-anchor="middle">${r.days} j</text>
      ${W > 600 ? `<text x="${i * bw + bw / 2}" y="${H - h - 8}" text-anchor="middle" class="val fade" style="--d:${(0.06 * i + 0.6).toFixed(2)}s">${num(r.lines)}</text>` : ""}`;
  }).join(""));
}

function calendarSvg(cal, { compact = false, big = false, width = 0 } = {}) {
  const { plants, days, trucks } = cal;
  const nWeeks = new Set(days.map((iso) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d.toISOString().slice(0, 10); })).size;
  const bigGap = 4, bigWeekGap = 16, bigL = 76;   // big: cells fill the card, between 20 and 34 px, scroll below 20
  const bigCell = Math.max(20, Math.min(34, Math.floor((width - bigL - nWeeks * bigWeekGap) / (nWeeks * 5) - bigGap)));
  const cell = compact ? 22 : big ? bigCell : 34, gap = compact ? 4 : big ? bigGap : 5, weekGap = compact ? 10 : big ? bigWeekGap : 16,
        L = compact ? 70 : big ? bigL : 84, T = compact ? 20 : big ? 50 : 44;
  const monday = (iso) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d.toISOString().slice(0, 10); };
  const weeks = [...new Set(days.map(monday))];
  const xs = days.map((d) => { const w = weeks.indexOf(monday(d)); const dow = (new Date(d + "T12:00:00").getDay() + 6) % 7; return L + w * (5 * (cell + gap) + weekGap) + dow * (cell + gap); });
  const W = L + weeks.length * (5 * (cell + gap) + weekGap), H = T + plants.length * (cell + gap) + 6;
  const gmax = Math.max(...trucks.flat());
  const peakDays = trucks.map((row) => row.filter((x) => x === Math.max(...row)).length);
  const tone = (v, pmax, r0) => {
    if (!v) return [C.empty, C.muted];
    if (v === pmax && peakDays[r0] <= 2) return [C.peak, "#fff"];   // a real peak: the busiest day, not a flat (smoothed) plant
    const r = v / gmax;
    return r < 0.15 ? [C.g1, C.label] : r < 0.3 ? [C.g2, C.ink] : r < 0.5 ? [C.g3, C.ink] : r < 0.7 ? [C.g4, "#fff"] : [C.g5, "#fff"];
  };
  let s = "";
  weeks.forEach((w, i) => {
    const x = L + i * (5 * (cell + gap) + weekGap);
    s += `<text x="${x}" y="${compact ? 12 : 16}" style="font-weight:700;fill:${C.label}${big ? ";font-size:12.5px" : ""}">${compact || big ? short(w) : "semaine du " + short(w)}</text>`;
    if (!compact) ["L", "M", "M", "J", "V"].forEach((l, k) => (s += `<text x="${x + k * (cell + gap) + cell / 2}" y="${big ? 38 : 36}" text-anchor="middle"${big ? ' style="font-size:11.5px"' : ""}>${l}</text>`));
  });
  plants.forEach((p, r) => {
    const y = T + r * (cell + gap), pmax = Math.max(...trucks[r]);
    s += `<text x="0" y="${y + cell / 2 + 4}" style="font-weight:700;fill:${C.label};font-size:${compact ? 11 : big ? 13 : 12.5}px">${p}</text>`;
    days.forEach((d, c) => {
      const v = trucks[r][c], [bg, fg] = tone(v, pmax, r);
      s += `<g class="cal-cell" style="--d:${(0.015 * c + 0.03 * r).toFixed(3)}s" data-tip="${p} · ${dayLabel(d)}<br><b>${v}</b> camion${v > 1 ? "s" : ""}${v === pmax && peakDays[r] <= 2 ? " · jour de pointe" : ""}">
        <rect x="${xs[c]}" y="${y}" width="${cell}" height="${cell}" rx="${big ? 4 : 2}" fill="${bg}"/>
        ${v && !compact ? `<text x="${xs[c] + cell / 2}" y="${y + cell / 2 + 4}" text-anchor="middle" style="fill:${fg};font-weight:700;font-size:12px">${v}</text>` : ""}</g>`;
    });
  });
  return svg(W, H, s);
}

// Couples per delivery class (deliveries per week), today and with Gilbert: two vertical bars per class, same style as plantBars
function profileBars(rows, W = 430, H = 380) {
  // a scale that stops just above the highest bar (steps of 2, 10, 25 or 50) so that the bars fill the panel
  const top = Math.max(1, ...rows.flatMap((r) => [r.before, r.after])), step = top <= 10 ? 2 : top <= 50 ? 10 : top <= 150 ? 25 : 50;
  const L = 34, B = 46, T = 24, max = Math.ceil((top * 1.08) / step) * step;
  const gw = (W - L) / rows.length, bw = Math.min(40, gw * 0.38), y = (v) => T + (H - T - B) * (1 - v / max);
  let s = "";
  for (let v = 0; v <= max; v += step) { s += `<g class="axis"><line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/></g><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${num(v)}</text>`; }
  rows.forEach((r, i) => {
    const cx = L + gw * i + gw / 2, [n, ...unit] = String(r.profile).split(" ");
    const bar = (v, x, fill, who, d) => `<rect x="${x}" y="${y(v)}" width="${bw}" height="${Math.max(0, H - B - y(v))}" rx="6" class="bar" style="--d:${d}s" fill="${fill}" data-tip="${esc(r.profile)} · ${who}<br><b>${num(v)}</b> couple${v > 1 ? "s" : ""}"/>
      <text x="${x + bw / 2}" y="${y(v) - 6}" text-anchor="middle" class="val fade" style="font-size:12px;--d:${(+d + 0.5).toFixed(2)}s">${num(v)}</text>`;
    s += bar(r.before, cx - bw - 2, C.before, "aujourd'hui", (0.08 * i).toFixed(2));
    s += bar(r.after, cx + 2, C.after, "avec Gilbert", (0.08 * i + 0.2).toFixed(2));
    s += `<text x="${cx}" y="${H - B + 18}" text-anchor="middle" style="font-weight:700;fill:${C.label};font-size:14px">${esc(n)}</text>`;
    s += `<text x="${cx}" y="${H - B + 33}" text-anchor="middle" style="font-size:11.5px">${esc(unit.join(" "))}</text>`;
  });
  return svg(W, H, s) + `<div class="legend" style="margin-top:.6rem"><span class="l-before">aujourd'hui</span><span class="l-after">avec Gilbert</span><span>nombre de couples fournisseur × usine</span></div>`;
}

// Largest savings, as in the output of the brief: per couple, the current cost (reference) against the cost with Gilbert
function topSavingBars(rows, W = 760) {
  const L = 150, R = 70, B = 26, rowH = 52, bh = 17, H = rows.length * rowH + B + 6;
  const max = niceMax(Math.max(...rows.map((r) => r.before))), x = (v) => L + (W - L - R) * (v / max);
  let s = "";
  for (let k = 0; k <= 4; k++) {
    const v = (max * k) / 4;
    s += `<g class="axis"><line x1="${x(v)}" x2="${x(v)}" y1="0" y2="${H - B}"/></g><text x="${x(v)}" y="${H - 8}" text-anchor="middle">${num(v)}</text>`;
  }
  rows.forEach((r, i) => {
    const y = 6 + i * rowH, tip = `${r.supplier} → ${r.plant}<br>${eur(r.before)} → ${eur(r.after)}<br><b>−${nf0.format(r.pct)} %</b>`;
    s += `<text x="${L - 10}" y="${y + bh + 5}" text-anchor="end" style="font-size:12px;font-weight:700;fill:${C.ink}">${r.supplier} → ${r.plant}</text>`;
    s += `<rect class="hbar" style="--d:${(0.06 * i).toFixed(2)}s" x="${L}" y="${y}" width="${x(r.before) - L}" height="${bh}" rx="4" fill="${C.before}" data-tip="${tip}"/>`;
    s += `<text x="${x(r.before) + 6}" y="${y + bh - 4}" class="val fade" style="--d:${(0.06 * i + 0.5).toFixed(2)}s">${eur(r.before)}</text>`;
    s += `<rect class="hbar" style="--d:${(0.06 * i + 0.2).toFixed(2)}s" x="${L}" y="${y + bh + 3}" width="${Math.max(2, x(r.after) - L)}" height="${bh}" rx="4" fill="${C.after}" data-tip="${tip}"/>`;
    s += `<text x="${x(r.after) + 6}" y="${y + 2 * bh - 1}" class="val fade" style="fill:${C.afterDark};--d:${(0.06 * i + 0.7).toFixed(2)}s">${eur(r.after)}</text>`;
  });
  return svg(W, H, s);
}

// ----------------------------------------------------------------------------- views
const STAMP_DELAY = { i: 0, next() { return (0.45 + 0.12 * this.i++).toFixed(2); }, reset() { this.i = 0; } };
function stamp(label, value, target, ok, r) {
  return `<div class="stamp ${ok ? "" : "ko"}" style="--r:${r}deg;--sd:${STAMP_DELAY.next()}s"><div class="s-top"><span>${label}</span><span>${ok ? "✓ atteint" : "✗ manqué"}</span></div>
    <div class="s-val">${value}</div><div class="s-target">cible ${target}</div></div>`;
}

function imputationChips(run) {   // how the missing data were completed, when the user chose the medians
  const prep = (run && run.parameters && run.parameters.preparation) || {};
  return [["couple_median", "material_median"].includes(prep.missing_load) ? "poids absents : médiane" : null, prep.missing_distance === "median" ? "distance absente : médiane" : null];
}
function settingsChips(run) {
  const p = planner(run), freq = p.allowed_frequencies ? Math.max(...p.allowed_frequencies) : 5;
  return [`${p.max_advance_days ?? 7} j d'avance`, p.smoothing_budget ? `lissage ${nf1.format(p.smoothing_budget * 100)} %` : "lissage gratuit",
    freq < 5 ? `≤ ${freq} / semaine` : null, ...imputationChips(run)].filter(Boolean)
    .map((c) => `<span class="tag">${esc(c)}</span>`).join(" ");
}

function renderOverview() {
  const sm = S.summary, k = sm.kpi, run = S.run, p = planner(run);
  const days = sm.calendar.days, first = k.first_need, last = k.last_need;
  $("#ov-sub").textContent = `${frDate(first)} — ${frDate(last, true)} · ${num(k.lines)} besoins · ${num(k.couples)} couples fournisseur–usine`;

  // first row: saving (emerald) and cost (bars on one scale)
  const smoothing = p.smoothing_budget ? `lissage avec ${nf1.format(100 * p.smoothing_budget)} % de surcoût accepté` : "lissage sans surcoût";
  $("#tile-saving").innerHTML = `<span class="ed-ring" aria-hidden="true"></span><p class="ed-label">Économie totale estimée</p>
    <div class="ed-hero-value">${counter(k.saving, "eur", 1200)}</div>
    <span class="ed-hero-pct">${signed(-k.saving_pct, " %", 1)} de coût de transport</span>
    <p class="ed-hero-ctx">Horizon complet · ${smoothing}</p>`;
  renderSituationBlock();   // second row and the detailed sections below

  // third row: daily load of the plants
  renderLoadCalendar();
  $("#loadLegend").innerHTML = `<span class="ed-scale">Peu <i style="background:${C.g1}"></i><i style="background:${C.g2}"></i><i style="background:${C.g3}"></i><i style="background:${C.g4}"></i><i style="background:${C.g6}"></i> Beaucoup de camions</span>
    <span class="ed-peak"><i></i>Jour de pointe</span><span class="ed-range">${frDate(days[0])} – ${frDate(days[days.length - 1], true)}</span>`;

  // fourth row: plants and compliance
  $("#plantCard").innerHTML = `<div class="ed-card-head line"><h2>Répartition par usine</h2><span class="ed-muted">Camions après optimisation</span></div>
    <div class="ed-list">${sm.plants.map((pl) => `<div class="ed-list-row"><span>${esc(pl.plant)}</span><b>${num(pl.trucks_after)} camion${pl.trucks_after > 1 ? "s" : ""}</b><span class="ed-muted">Pic : ${num(pl.peak)}</span></div>`).join("")}</div>`;
  const find = (rule) => sm.controls.find((c) => c.rule === rule);
  const antCtl = find("RG03"), antDays = p.max_advance_days ?? 7;
  const item = (c, title, okText) => {
    const state = !c ? "unknown" : c.ok ? "ok" : "ko";
    const icon = { ok: '<path d="m5 12 4.5 4.5L19 7"/>', ko: '<path d="M6 6l12 12M18 6 6 18"/>', unknown: '<path d="M12 17h.01M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7"/>' }[state];
    const text = { ok: okText, ko: "Contrôle non respecté : voir les contrôles détaillés", unknown: "Résultat indisponible pour ce calcul" }[state];
    return `<div class="ed-check ${state}"><span class="ed-check-ico"><svg viewBox="0 0 24 24">${icon}</svg></span><div><b>${title}</b><span>${text}</span></div></div>`;
  };
  $("#complianceCard").innerHTML = `<div class="ed-card-head"><h2>Conformité du plan</h2></div>
    ${item(find("RG02"), "Aucun retard", "Dates de besoin respectées")}
    ${item(antCtl, `Anticipation ≤ ${antDays} jours`, "Fenêtre de livraison conforme")}
    ${item(find("RG04"), "Capacités des camions", "Poids et volume respectés")}`;

  // detailed sections
  $("#plantsSum").textContent = `${sm.plants.length} usines`;
  $("#plantsBody").innerHTML = `<p class="meta small">Situation actuelle (vert) et plan Gilbert (orange), par usine. Le pourcentage indique l'économie.</p>
    <div class="legend" style="justify-content:flex-end"><span class="l-before">aujourd'hui</span><span class="l-after">avec Gilbert</span></div>${plantBars(sm.plants, 1240, 330)}`;
  const smoothCost = sm.smoothing.reduce((a, s) => a + s.extra_cost, 0), maxPeak = Math.max(1, ...sm.smoothing.map((s) => Math.max(s.peak_before, s.peak_after)));
  $("#smoothSum").textContent = `surcoût ${eur(smoothCost)}`;
  $("#smoothBody").innerHTML = `<p class="meta small">Camions reçus le jour le plus chargé de chaque usine, avant et après le lissage des arrivées entre les jours.</p>
    <div style="max-width:760px">${sm.smoothing.map((s, i) => `<div class="peakrow"><span class="p">${s.plant}</span><span class="bars">
      <i class="before" style="width:${(100 * s.peak_before) / maxPeak}%;--d:${0.1 * i}s"></i><i class="after" style="width:${(100 * s.peak_after) / maxPeak}%;--d:${0.1 * i + 0.25}s"></i></span>
      <span class="v">${s.peak_before === s.peak_after ? "" : `<s>${s.peak_before}</s> → `}${s.peak_after} camions</span></div>`).join("")}
      <div class="legend" style="margin-top:.6rem"><span class="l-before">avant lissage</span><span class="l-after">après lissage</span></div></div>
    <p class="ed-note"><b>${num(k.frequency_changes)} couples</b> changent de profil au lissage ; ${num(Math.max(0, k.weekday_changes - k.frequency_changes))} autres changent seulement de jours.</p>
    <p class="ed-note">${imputedNote(k)}</p>
    <button class="btn small" data-view="camions">Ouvrir les camions</button>`;
  countUp($("#view-apercu"));
  renderControls();
  const all = [...sm.controls, ...sm.output_tests];
  $("#controlsSum").textContent = all.every((c) => c.ok) ? `${sm.controls.length} contrôles et ${sm.output_tests.length} tests, tous respectés` : "au moins une règle à revoir";
}

// daily load calendar: real pixel size (cells computed from the card width), redrawn on resize
function renderLoadCalendar() {
  const box = $("#loadCal");
  box.innerHTML = calendarSvg(S.summary.calendar, { big: true, width: box.clientWidth });
  const svgEl = $("svg", box);
  if (svgEl) { svgEl.style.width = svgEl.viewBox.baseVal.width + "px"; svgEl.style.maxWidth = "none"; }
}
let loadResize = null;
window.addEventListener("resize", () => { clearTimeout(loadResize); loadResize = setTimeout(() => { if (S.view === "apercu" && S.summary) renderLoadCalendar(); }, 150); });

// export menu (keyboard and click)
(function exportMenu() {
  const btn = $("#exportBtn"), menu = $("#downloads");
  const close = () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open; btn.setAttribute("aria-expanded", String(open));
    if (open) { const a = $("a", menu); if (a) a.focus(); }
  });
  menu.addEventListener("keydown", (e) => {
    const items = $$("a", menu), i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".ed-export")) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) { close(); btn.focus(); } });
})();

function imputedNote(k) {
  if (!k.imputed_trucks) return "Aucun camion ne transporte de ligne au poids ou au volume complété.";
  return `<b>${num(k.imputed_trucks)} camions</b> transportent une ligne dont le poids ou le volume manquait : ces lignes sont comptées comme un camion plein, avant comme après.`;
}

// ============================================================================= planning (GILBERT-UI-02)
// State of the planning: selected plant ("" = every plant), week index in the horizon, open day panel.
// It belongs to the active scenario (reset in openRun) and is restored when coming back from a truck.
const MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
const JOURS_LONG = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"];
const TRUCK_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 6h11v10H2zM13 9h4l4 4v3h-8z"/><circle cx="6" cy="18" r="2"/><circle cx="17" cy="18" r="2"/></svg>';
function resetPlanning() {
  S.plan = { plant: "", week: 0, drawer: null };
  S.dayCache = new Map(); S.planToken = (S.planToken || 0) + 1; S.drawerToken = (S.drawerToken || 0) + 1;
  S.planReturn = null; S.trucksFromPlanning = false;
}
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const mondayOf = (iso) => addDays(iso, -((new Date(iso + "T12:00:00").getDay() + 6) % 7));
const isoWeek = (iso) => {
  const d = new Date(iso + "T12:00:00"), t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  t.setUTCDate(t.getUTCDate() + 3 - ((t.getUTCDay() + 6) % 7));
  const jan4 = new Date(Date.UTC(t.getUTCFullYear(), 0, 4));
  return 1 + Math.round(((t - jan4) / 864e5 - 3 + ((jan4.getUTCDay() + 6) % 7)) / 7);
};
const longDate = (iso) => { const d = new Date(iso + "T12:00:00"); return `${JOURS_LONG[(d.getDay() + 6) % 7] || ""} ${d.getDate()} ${MOIS[d.getMonth()]} ${d.getFullYear()}`; };
function planWeeks() { return [...new Set(S.summary.calendar.days.map(mondayOf))].sort(); }
// trucks of one plant on one day, from the calendar of the results (null = outside the horizon)
function dayCount(plant, iso) {
  const cal = S.summary.calendar, r = cal.plants.indexOf(plant), c = cal.days.indexOf(iso);
  return r < 0 || c < 0 ? null : cal.trucks[r][c];
}
// same rule as the heatmap: the busiest day of the plant, unless the plant is flat (more than two days at its maximum)
function isPeak(plant, iso) {
  const cal = S.summary.calendar, row = cal.trucks[cal.plants.indexOf(plant)];
  if (!row) return false;
  const max = Math.max(...row), v = dayCount(plant, iso);
  return !!v && v === max && row.filter((x) => x === max).length <= 2;
}
// every truck of a plant on a day: the trucks endpoint is paginated, so the pages are read until the total
function fetchDayTrucks(plant, day) {
  const run = S.current, key = `${run}|${plant}|${day}`;
  if (!S.dayCache.has(key)) {
    const job = (async () => {
      const rows = [];
      for (let offset = 0, total = Infinity; offset < total; offset += 500) {
        const p = new URLSearchParams({ plant, day, limit: 500, offset });
        const res = await api(`/runs/${run}/views/trucks?${p}`);
        total = res.total; rows.push(...res.rows);
        if (!res.rows.length) break;
      }
      return rows;
    })();
    job.catch(() => S.dayCache.delete(key));   // a failed request is not kept
    S.dayCache.set(key, job);
  }
  return S.dayCache.get(key);
}

function renderPlantTabs() {
  const plants = S.summary.calendar.plants, cur = S.plan.plant;
  $("#plantTabs").innerHTML = [["", "Toutes les usines"], ...plants.map((p) => [p, p])].map(([v, l]) =>
    `<button type="button" role="tab" data-plant="${esc(v)}" aria-selected="${v === cur}" class="${v === cur ? "on" : ""}">${esc(l)}</button>`).join("");
}
function selectPlant(plant) {
  if (plant === S.plan.plant) return;
  S.plan.plant = plant; S.planToken++;
  renderCalendarView({ keepScroll: true });
}
$("#plantTabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-plant]"); if (b) selectPlant(b.dataset.plant); });
$("#plantTabs").addEventListener("keydown", (e) => {   // arrows move between the tabs
  if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
  const tabs = $$("#plantTabs button"), i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  const next = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
  next.focus(); selectPlant(next.dataset.plant);
});

function renderCalendarView() {
  const sm = S.summary;
  if (!S.plan) resetPlanning();
  renderPlantTabs();
  const global = !S.plan.plant;
  $("#planGlobal").hidden = !global; $("#planWeek").hidden = global;
  const pane = global ? $("#planGlobal") : $("#planWeek");
  pane.classList.remove("in"); void pane.offsetWidth; pane.classList.add("in");   // short fade between the views
  if (global) {
    setTimeout(() => countUp($("#peaks")), 0);
    $("#calendarBig").innerHTML = calendarSvg(sm.calendar) +
      `<div class="legend" style="margin-top:1rem"><span class="l-scale">peu à beaucoup de camions</span><span class="l-peak">jour de pointe de l'usine</span></div>`;
    const maxAll = Math.max(...sm.plants.map((p) => p.peak));
    $("#peaks").innerHTML = sm.plants.map((p) => {
      const r = sm.calendar.trucks[sm.calendar.plants.indexOf(p.plant)], d = sm.calendar.days[r.indexOf(p.peak)];
      return `<div class="peak ${p.peak === maxAll ? "hot" : ""}" role="button" tabindex="0" data-plant="${esc(p.plant)}" aria-label="Voir le planning de ${esc(p.plant)}">
        <p class="label">${p.plant}</p><div class="pk-n">${counter(p.peak)} camions</div>
        <div class="meta" style="margin:0">au plus, le ${dayLabel(d)} · ${num(p.trucks_after)} sur l'horizon</div></div>`;
    }).join("");
  } else renderWeek();
  if (S.plan.drawer) openDayDrawer(S.plan.drawer.plant, S.plan.drawer.day);   // restored context
}
// peak cards
$("#peaks").addEventListener("click", (e) => { const c = e.target.closest(".peak[data-plant]"); if (c) selectPlant(c.dataset.plant); });
$("#peaks").addEventListener("keydown", (e) => {
  const c = e.target.closest(".peak[data-plant]");
  if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); selectPlant(c.dataset.plant); }
});

// ---- weekly planning of one plant
function truckCard(t) {
  return `<button type="button" class="truck-card" data-truck="${esc(t.id)}" data-plant="${esc(t.plant)}" data-day="${t.date}" data-supplier="${esc(t.supplier)}">
    <span class="tc-ico">${TRUCK_ICON}</span>
    <span class="tc-body"><b>${esc(t.supplier)}</b><span class="tc-id">${esc(t.id)}</span>
      <span class="tc-meta">${num(t.kg)} kg · ${nf1.format(t.fill)} % de remplissage</span>
      <span class="tc-bar"><i style="width:${Math.min(100, t.fill).toFixed(1)}%"></i></span></span></button>`;
}
function renderWeek() {
  const plant = S.plan.plant, weeks = planWeeks();
  S.plan.week = Math.max(0, Math.min(weeks.length - 1, S.plan.week));
  const mon = weeks[S.plan.week], days = [0, 1, 2, 3, 4].map((k) => addDays(mon, k));
  const counts = days.map((d) => dayCount(plant, d)), total = counts.reduce((a, v) => a + (v || 0), 0);
  const d0 = new Date(days[0] + "T12:00:00"), d4 = new Date(days[4] + "T12:00:00");
  const range = d0.getMonth() === d4.getMonth() ? `${d0.getDate()} – ${d4.getDate()} ${MOIS[d4.getMonth()]} ${d4.getFullYear()}`
    : `${d0.getDate()} ${MOIS[d0.getMonth()]} – ${d4.getDate()} ${MOIS[d4.getMonth()]} ${d4.getFullYear()}`;
  $("#planWeek").innerHTML = `<div class="ed-card week-card">
    <div class="week-head">
      <div><h2>${esc(plant)} — Semaine ${isoWeek(mon)}</h2><p class="ed-muted">${num(total)} camion${total > 1 ? "s" : ""} attendu${total > 1 ? "s" : ""} cette semaine</p></div>
      <div class="week-nav">
        <button type="button" class="ed-btn small" id="weekPrev" aria-label="Semaine précédente" ${S.plan.week === 0 ? "disabled" : ""}>‹</button>
        <span class="week-range">${range}</span>
        <button type="button" class="ed-btn small" id="weekNext" aria-label="Semaine suivante" ${S.plan.week >= weeks.length - 1 ? "disabled" : ""}>›</button>
      </div>
    </div>
    <div class="week-scroll"><div class="week-grid">${days.map((d, k) => {
      const v = counts[k], out = v === null, peak = !out && isPeak(plant, d);
      return `<div class="day-col ${out ? "out" : ""}" data-day="${d}">
        <div class="day-head ${peak ? "peak" : ""}"><b>${JOURS_LONG[k]} ${new Date(d + "T12:00:00").getDate()}</b>
          <span>${out ? "hors horizon" : `${num(v)} camion${v > 1 ? "s" : ""}`}</span>${peak ? '<em>jour de pointe</em>' : ""}</div>
        <div class="day-body">${out ? '<p class="day-empty">Hors de l\'horizon du planning</p>' : v === 0 ? '<p class="day-empty">Aucune réception prévue</p>'
          : Array.from({ length: Math.min(3, v) }, () => '<span class="tc-skel"></span>').join("")}</div></div>`;
    }).join("")}</div></div></div>`;
  $("#weekPrev").onclick = () => { S.plan.week--; S.planToken++; renderCalendarView(); };
  $("#weekNext").onclick = () => { S.plan.week++; S.planToken++; renderCalendarView(); };
  const token = ++S.planToken;
  days.forEach(async (d, k) => {
    if (!counts[k]) return;
    const body = $(`#planWeek .day-col[data-day="${d}"] .day-body`);
    try {
      const rows = await fetchDayTrucks(plant, d);
      if (token !== S.planToken || !body.isConnected) return;   // an older answer: the user has moved on
      const more = rows.length - 3;
      body.innerHTML = rows.slice(0, 3).map(truckCard).join("") +
        (more > 0 ? `<button type="button" class="day-more" data-plant="${esc(plant)}" data-day="${d}">${more > 1 ? `Voir les ${more} autres camions` : "Voir 1 autre camion"}</button>` : "");
    } catch (err) {
      if (token === S.planToken && body.isConnected) body.innerHTML = `<p class="day-empty err">Camions indisponibles : ${esc(err.message)}</p>`;
    }
  });
}
$("#planWeek").addEventListener("click", (e) => {
  const card = e.target.closest(".truck-card"), more = e.target.closest(".day-more");
  if (card) openTruckFromPlanning(card.dataset.truck, card.dataset.plant, card.dataset.day, card.dataset.supplier);
  else if (more) openDayDrawer(more.dataset.plant, more.dataset.day);
});

// ---- day panel: every truck of a plant on one day ("Voir les X autres camions")
let drawerLastFocus = null;
async function openDayDrawer(plant, day) {
  const v = dayCount(plant, day);
  if (v === null) return;   // outside the horizon: no panel
  S.plan.drawer = { plant, day };
  const box = $("#dayDrawer"), token = ++S.drawerToken;
  if (box.hidden) drawerLastFocus = document.activeElement;
  box.hidden = false; requestAnimationFrame(() => box.classList.add("on"));
  $("#ddPlant").textContent = plant;
  $("#ddTitle").textContent = `${plant} — Réceptions attendues`;
  $("#ddDate").textContent = longDate(day);
  const peak = isPeak(plant, day);
  $("#ddSummary").innerHTML = `<b>${num(v)} camion${v > 1 ? "s" : ""}</b>${peak ? '<span class="dd-peak">Jour de pointe de l\'usine</span>' : ""}`;
  $("#ddList").innerHTML = v === 0 ? '<p class="day-empty">Aucune réception prévue</p>' : '<p class="dd-loading">Chargement des camions…</p>';
  $("#ddClose").focus();
  if (v === 0) return;
  try {
    const rows = await fetchDayTrucks(plant, day);
    if (token !== S.drawerToken) return;
    S.drawerRows = rows; renderDrawerList();
  } catch (err) {
    if (token === S.drawerToken) $("#ddList").innerHTML = `<p class="day-empty err">Impossible de charger les camions : ${esc(err.message)}</p>`;
  }
}
function renderDrawerList() {
  const rows = S.drawerRows || [];
  $("#ddList").innerHTML = (rows.length ? rows.map((t) => `<button type="button" class="dd-row" data-truck="${esc(t.id)}" data-plant="${esc(t.plant)}" data-day="${t.date}" data-supplier="${esc(t.supplier)}">
      <span class="tc-ico">${TRUCK_ICON}</span>
      <span class="dd-main"><b>${esc(t.id)}</b><span>${esc(t.supplier)} · ${num(t.lines)} ligne${t.lines > 1 ? "s" : ""}</span></span>
      <span class="dd-nums"><span>${num(t.kg)} kg · ${nf1.format(t.m3)} m³</span><span>${nf1.format(t.fill)} % · ${eur(t.price, 2)}</span></span></button>`).join("")
      : '<p class="day-empty">Aucune réception prévue</p>');
}
function hideDrawer() {
  const box = $("#dayDrawer");
  if (box.hidden) return;
  box.classList.remove("on"); box.hidden = true; S.drawerToken++;
}
function closeDayDrawer() {
  S.plan.drawer = null; hideDrawer();
  if (drawerLastFocus && drawerLastFocus.isConnected) drawerLastFocus.focus();
}
$("#ddClose").addEventListener("click", closeDayDrawer);
$("#dayDrawer").addEventListener("click", (e) => { if (e.target.id === "dayDrawer") closeDayDrawer(); });   // click outside the panel
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#dayDrawer").hidden) closeDayDrawer(); });
$("#ddList").addEventListener("click", (e) => { const r = e.target.closest(".dd-row"); if (r) openTruckFromPlanning(r.dataset.truck, r.dataset.plant, r.dataset.day, r.dataset.supplier); });

// ---- from the planning to the delivery note of a truck, and back
async function openTruckFromPlanning(id, plant, day, supplier = "") {
  S.planReturn = { run: S.current, plan: JSON.parse(JSON.stringify(S.plan)), scrollY: window.scrollY };
  // the Camions table filtered on this plant and day; the page that holds the truck is looked up (it may not be the first)
  // the Camions table shows this truck only: filtered on its plant, its day and its supplier
  $("#tf-plant").value = plant; $("#tf-supplier").value = supplier; $("#tf-day").value = day;
  if ($("#tf-supplier").value !== supplier) $("#tf-supplier").value = "";   // supplier missing from the list: plant and day only
  let page = 0;
  try {
    for (let k = 0; ; k++) {
      const res = await api(`/runs/${S.current}/views/trucks?${new URLSearchParams({ plant, day, ...($("#tf-supplier").value ? { supplier } : {}), limit: 60, offset: k * 60 })}`);
      if (res.rows.some((r) => r.id === id)) { page = k; break; }
      if ((k + 1) * 60 >= res.total) break;
    }
  } catch (_) { /* the table will show the first page */ }
  S.trucksPage = page; S.selectedTruck = id; S.trucksFromPlanning = true;
  show("camions", { fromPlanning: true });
  await openTruck(id);
  const ticket = $("#ticket");
  if (ticket && ticket.getBoundingClientRect().top > window.innerHeight * 0.6) ticket.scrollIntoView({ behavior: "smooth", block: "start" });
}
$("#backToPlanning").addEventListener("click", () => {
  const ret = S.planReturn;
  if (ret && ret.run === S.current) S.plan = ret.plan;   // plant, week, open day panel and its search
  show("calendrier");
  if (ret && ret.run === S.current) requestAnimationFrame(() => window.scrollTo({ top: ret.scrollY }));
});

function table(cols, rows, { onRow, selected } = {}) {
  const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${c.label}</th>`).join("");
  const body = rows.map((r, i) => `<tr data-i="${i}" class="${selected && selected(r) ? "sel" : ""}" style="${onRow ? "cursor:pointer" : ""}">${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.fmt ? c.fmt(r[c.key], r) : esc(r[c.key])}</td>`).join("")}</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${cols.length}" style="color:${C.muted}">Aucune ligne.</td></tr>`}</tbody></table>`;
}

async function renderProfiles() {
  // whole scenario, whatever the filters of the table: couples per weekly class (1 to 5 deliveries a week)
  if (S.profilesChart !== S.current) {   // drawn once per scenario: changing a filter does not redraw it
    $("#dumbbell").innerHTML = profileBars(S.summary.profiles.filter((r) => / semaine$/.test(r.profile)));
    S.profilesChart = S.current;
  }
  const p = new URLSearchParams();
  if ($("#pf-plant").value) p.set("plant", $("#pf-plant").value);
  const supplier = $("#pf-supplier").value;
  if (supplier) p.set("q", supplier);
  let rows = await api(`/runs/${S.current}/views/profiles?${p}`);
  if (supplier) rows = rows.filter((r) => r.supplier === supplier);   // exact supplier (the API search matches a part of the id)
  $("#profilesTable").innerHTML = table([
    { key: "supplier", label: "Fournisseur" }, { key: "plant", label: "Usine" }, { key: "km", label: "km", num: true, fmt: num },
    { key: "before", label: "Aujourd'hui" }, { key: "after", label: "Avec Gilbert", fmt: (v, r) => {
      const moved = r.days !== r.days_before_smoothing;
      return `<span class="tag" ${moved ? `data-tip="Avant lissage : ${esc(r.days_before_smoothing)}"` : ""}>${v}</span>`; } },
    { key: "days", label: "Jours" }, { key: "deliveries_before", label: "Livr. avant", num: true }, { key: "deliveries_after", label: "Livr. après", num: true },
    { key: "trucks", label: "Camions", num: true }, { key: "fill", label: "Remplissage", num: true, fmt: (v) => nf1.format(v) + " %" },
  ], rows);
}
["#pf-supplier", "#pf-plant"].forEach((id) => $(id).addEventListener("input", () => S.summary && renderProfiles()));

async function renderTrucks() {
  const p = new URLSearchParams({ limit: 60, offset: S.trucksPage * 60 });
  if ($("#tf-plant").value) p.set("plant", $("#tf-plant").value);
  if ($("#tf-supplier").value) p.set("supplier", $("#tf-supplier").value);
  if ($("#tf-day").value) p.set("day", $("#tf-day").value);
  const res = await api(`/runs/${S.current}/views/trucks?${p}`);
  $("#trucksTable").innerHTML = table([
    { key: "id", label: "Camion" }, { key: "supplier", label: "Fournisseur" }, { key: "plant", label: "Usine" },
    { key: "date", label: "Jour", fmt: dayLabel }, { key: "lines", label: "Lignes", num: true }, { key: "kg", label: "kg", num: true, fmt: num },
    { key: "fill", label: "Remplissage", num: true, fmt: (v) => `<span class="tag ${v >= 99 ? "rose" : ""}">${nf1.format(v)} %</span>` },
    { key: "price", label: "Prix", num: true, fmt: (v) => eur(v, 2) },
  ], res.rows, { onRow: true, selected: (r) => r.id === S.selectedTruck });
  $$("#trucksTable tbody tr[data-i]").forEach((tr) => tr.addEventListener("click", () => openTruck(res.rows[+tr.dataset.i].id)));
  const pages = Math.max(1, Math.ceil(res.total / 60));
  $("#trucksPager").innerHTML = `<span>${num(res.total)} camions</span><span>
    <button class="btn ghost small" ${S.trucksPage === 0 ? "disabled" : ""} id="prevP">←</button>
    page ${S.trucksPage + 1} / ${pages}
    <button class="btn ghost small" ${S.trucksPage >= pages - 1 ? "disabled" : ""} id="nextP">→</button></span>`;
  $("#prevP").onclick = () => { S.trucksPage--; renderTrucks(); };
  $("#nextP").onclick = () => { S.trucksPage++; renderTrucks(); };
  if (!S.selectedTruck && res.rows.length) openTruck(res.rows[0].id);
}
["#tf-plant", "#tf-supplier", "#tf-day"].forEach((id) => $(id).addEventListener("change", () => { S.trucksPage = 0; S.selectedTruck = null; renderTrucks(); }));

async function openTruck(id) {
  S.selectedTruck = id;
  $$("#trucksTable tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.children[0] && tr.children[0].textContent === id));
  const t = await api(`/runs/${S.current}/views/trucks/${encodeURIComponent(id)}`);
  const gauge = (label, v, rose) => `<div class="gauge ${rose ? "rose" : ""}"><div class="g-l"><span>${label}</span><b>${nf1.format(v)} %</b></div><div class="g-b"><i style="width:${Math.min(100, v)}%"></i></div></div>`;
  $("#ticket").innerHTML = `<div class="ticket">
    <span class="price">${eur(t.price, 2)}</span>
    <div class="t-brand"><img src="brand/gilbert-logo.svg" alt="Gilbert &amp; Co" style="height:26px;vertical-align:middle"> · bon de livraison</div>
    <div class="t-id">${t.id}</div>
    <div class="t-route"><b>${t.supplier}</b> → <b>${t.plant}</b> · ${dayLabel(t.date)}</div>
    <div class="perf"></div>
    <div class="t-grid">${gauge(`${num(t.kg)} kg`, t.fill_weight, false)}${gauge(`${nf1.format(t.m3)} m³`, t.fill_volume, true)}</div>
    <ul>${t.items.map((a) => `<li><span class="a">${esc(a.material)}${a.imputed ? ' <span class="tag warn" data-tip="Poids ou volume manquant : compté comme un camion plein">donnée manquante</span>' : ""}</span>
      <span>${num(a.kg)} kg</span><span class="d">quai ${esc(a.unloading_point)} · quantité ${num(a.quantity)} · besoins du ${a.need_days.join(", ")}</span></li>`).join("")}</ul>
    <div class="replaces">Ce camion regroupe <b>${t.items.length} material${t.items.length > 1 ? "s" : ""}</b> et remplace <b>${t.replaces} livraison${t.replaces > 1 ? "s" : ""}</b> d'aujourd'hui.</div>
  </div>`;
}

async function renderSuppliers() {
  const top = S.summary.top_savings.slice(0, +$("#sf-top").value), k = S.summary.kpi;
  $("#topTitle").textContent = `Les ${top.length} plus grosses économies · coût de transport du ${dayLabel(k.first_need)} au ${dayLabel(k.last_need)}`;
  $("#topChart").innerHTML = top.length ? topSavingBars(top, 1240) : "";
  if (!S.suppliers.length) S.suppliers = await api(`/runs/${S.current}/views/suppliers`);
  const q = $("#sf-q").value.trim().toLowerCase();
  const rows = S.suppliers.filter((r) => !q || (r.supplier + " " + r.plant).toLowerCase().includes(q)).sort((a, b) => b.saving - a.saving);
  $("#suppliersTable").innerHTML = table([
    { key: "supplier", label: "Fournisseur" }, { key: "plant", label: "Usine" },
    { key: "before", label: "Avant", num: true, fmt: (v) => eur(v) }, { key: "after", label: "Après", num: true, fmt: (v) => eur(v) },
    { key: "trucks_before", label: "Camions avant", num: true }, { key: "trucks_after", label: "après", num: true },
    { key: "saving", label: "Économie", num: true, fmt: (v) => `<b>${eur(v)}</b>` }, { key: "pct", label: "%", num: true, fmt: (v) => `<span class="tag rose">−${nf0.format(v)} %</span>` },
  ], rows);
}
$("#sf-q").addEventListener("input", () => S.summary && renderSuppliers());
$("#sf-top").addEventListener("change", () => S.summary && renderSuppliers());

function renderControls() {
  const sm = S.summary;
  const row = (ok, name, text) => `<div class="rule ${ok ? "" : "ko"}"><span class="mark">${ok ? "✓" : "✗"}</span>
    <div><div class="r-name">${esc(name)}</div><div>${esc(text)}</div></div></div>`;
  $("#controlsList").innerHTML = sm.controls.map((c) => row(c.ok, c.rule, c.what)).join("") +
    `<h3 style="margin-top:1rem">Tests des sorties (Milestone 4)</h3>` +
    sm.output_tests.map((c) => row(c.ok, c.suite, `${c.test} : ${c.detail}`)).join("");
  $("#journal").innerHTML = table([{ key: "step", label: "Étape" }, { key: "value", label: "Valeur" }], sm.journal);
}

// ----------------------------------------------------------------------------- start
(async function init() {
  try {   // default parameters of the planner, as the API applies them
    const d = (await api("/config/defaults")).planner || {};
    DEFAULT_F = { anticipation: d.max_advance_days ?? 7, smoothing: d.smoothing_budget ?? 0 };
    Object.assign(F, DEFAULT_F);
  } catch (_) { /* offline: keep the built-in defaults */ }
  setNav(readNav());
  try {
    await refreshRuns();
    const running = S.runs.find((r) => r.status === "running" || r.status === "queued");
    const done = S.runs.find((r) => r.status === DONE);
    $("#f-label").value = done ? `Scénario ${S.runs.length + 1}` : "Plan de référence";
    if (running) { startRunning(running); return; }
    await route();   // the address may name a scenario and a tab (reload, bookmark)
  } catch (err) { toast("Le serveur ne répond pas : " + err.message); show("accueil"); }
})();

// ----------------------------------------------------------------------------- side menu, folded or open
function syncTopbar() { document.documentElement.style.setProperty("--topbar-h", $(".topbar").offsetHeight + "px"); }
window.addEventListener("resize", syncTopbar); syncTopbar();
function readNav() { try { return localStorage.getItem("gilbert-nav") !== "closed"; } catch (_) { return true; } }
function setNav(open) {
  document.body.classList.toggle("nav-closed", !open);
  $("#navToggle").setAttribute("aria-expanded", open);
  $("#navToggle span").textContent = open ? "Replier le menu" : "Déplier le menu";
  try { localStorage.setItem("gilbert-nav", open ? "open" : "closed"); } catch (_) { /* storage unavailable */ }
  setTimeout(() => { $$(".seg").forEach(placePill); }, 260);
}
$("#navToggle").addEventListener("click", () => setNav(document.body.classList.contains("nav-closed")));
