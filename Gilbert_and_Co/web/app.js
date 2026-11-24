/* Gilbert & Co : front-end (vanilla JS, SVG charts drawn by hand). Talks to the FastAPI under /api. */
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

const S = { runs: [], imports: [], importId: null, situation: null, pending: null, treat: { weight: "full_truck", volume: "full_truck", distance: "max" }, current: null, summary: null, view: "accueil", files: {}, polling: null, trucksPage: 0, selectedTruck: null, suppliers: [], sort: {} };

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
const VIEWS = ["accueil", "running", "apercu", "calendrier", "profils", "camions", "fournisseurs", "scenarios"];
const CRUMBS = { accueil: "Nouveau calcul", running: "Calcul en cours", apercu: "Synthèse", calendrier: "Planning", profils: "Profils",
                 camions: "Camions", fournisseurs: "Fournisseurs", scenarios: "Scénarios" };
function show(view) {
  if (!VIEWS.includes(view)) view = "accueil";
  if (!["accueil", "running"].includes(view) && !S.summary) view = "accueil";
  S.view = view;
  $("#crumbCurrent").textContent = CRUMBS[view] || "";
  document.body.dataset.view = view;   // the scenario selector is only shown on the synthesis of a scenario
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  $$(".sidenav button[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (location.hash !== "#" + view && view !== "running") history.replaceState(null, "", "#" + view);
  window.scrollTo({ top: 0 });
  const render = { accueil: () => { renderLaunch(); renderHistory(); }, apercu: renderOverview, calendrier: renderCalendarView, profils: renderProfiles, camions: renderTrucks,
                   fournisseurs: renderSuppliers, scenarios: renderScenarios }[view];
  if (render) render();
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-view]");
  if (b && !b.disabled) { e.preventDefault(); show(b.dataset.view); }
});
window.addEventListener("hashchange", () => show(location.hash.slice(1)));
function updateDock() {
  $$(".sidenav .needs-run").forEach((b) => (b.disabled = !S.summary));
}

// ----------------------------------------------------------------------------- new calculation: import + launch
const IMPORT_FILES = [
  ["schedule", "Planning de livraison", "released_schedule.csv", true],
  ["distances", "Distances des fournisseurs", "distances.csv", true],
  ["rates", "Grille tarifaire", "rate_sheet.csv", false],
];
const FILE_ICON = '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 14h6M9 17h4"/></svg>';
let DEFAULTS = { files: {} };
const fmtDate = (iso, y = false) => iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}${y ? "/" + iso.slice(0, 4) : ""}` : "?";
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

function renderSource() {
  const box = $("#sourceBox"), imp = S.imports.find((i) => i.id === S.sourceImport);
  if (imp) {
    box.innerHTML = `<div class="source-pick"><span class="ico">${FILE_ICON}</span><div><p class="f-lab">Données</p>
      <b>${esc(imp.label)}</b><span>${fmtDate(imp.first)} → ${fmtDate(imp.last, true)} · ${num(imp.need_lines)} lignes · ${imp.suppliers} fournisseurs</span></div>
      <button class="link" id="changeSource">Importer d'autres fichiers</button></div>`;
    $("#changeSource").addEventListener("click", () => { S.sourceImport = null; renderLaunch(); });
    return;
  }
  box.innerHTML = `<p class="f-lab">Vos fichiers</p>
    <label class="drop-all" id="dropAll">
      <span class="ico">${FILE_ICON}</span>
      <span class="txt"><span class="name">Glissez vos 3 fichiers CSV ici, d'un coup</span>
        <span class="state">ou cliquez pour les choisir : planning, distances et grille tarifaire sont reconnus automatiquement</span></span>
      <input type="file" accept=".csv" multiple id="dropAllInput"></label>
    <div class="drops">${IMPORT_FILES.map(([key, title, fname, req]) => {
    const f = S.files[key];
    const state = f ? esc(f.name) : req ? `à déposer · ${fname}` : "facultatif · sinon la grille du projet";
    return `<label class="drop ${f ? "custom" : ""}" data-key="${key}">
      <span class="ico">${FILE_ICON}</span><span class="txt"><span class="name">${title}</span><span class="state">${state}</span></span>
      ${f ? `<button class="clear" data-clear="${key}" title="Retirer ce fichier">×</button>` : ""}
      <input type="file" accept=".csv" data-file="${key}"></label>`;
  }).join("")}</div>`;
  const set = (key, file) => { S.files[key] = file; renderLaunch(); };
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

// which import file is this CSV? Its header first, its name as a fallback.
async function detectFile(file) {
  let head = "";
  try { head = (await file.slice(0, 4096).text()).replace(/^\uFEFF/, "").split(/\r?\n/)[0].toLowerCase(); } catch (_) { /* unreadable */ }
  const cols = head.split(/[;,\t]/).map((c) => c.trim().replace(/^"|"$/g, ""));
  if (cols[0] === "km_up_to" || cols.includes("km_up_to")) return "rates";
  if (cols.includes("material") || cols.includes("schedule_quantity")) return "schedule";
  if (cols.includes("distance") && cols.includes("supplier_id")) return "distances";
  const name = file.name.toLowerCase();
  if (/distance/.test(name)) return "distances";
  if (/rate|tarif|grille/.test(name)) return "rates";
  if (/schedule|planning|besoin/.test(name)) return "schedule";
  return null;
}

async function placeFiles(files) {
  const placed = [], unknown = [], twice = [];
  const seen = {};
  for (const f of files) {
    if (!/\.csv$/i.test(f.name)) { unknown.push(f.name); continue; }
    const key = await detectFile(f);
    if (!key) { unknown.push(f.name); continue; }
    if (seen[key]) twice.push(f.name);
    seen[key] = true;
    S.files[key] = f; placed.push(f.name);
  }
  renderLaunch();
  const msg = [];
  if (unknown.length) msg.push(`Fichier${unknown.length > 1 ? "s" : ""} non reconnu${unknown.length > 1 ? "s" : ""} : ${unknown.join(", ")}`);
  if (twice.length) msg.push(`Deux fichiers du même type : ${twice.join(", ")} a été retenu`);
  if (msg.length) toast(msg.join(". ") + ".");
}

function canLaunch() { return !!S.sourceImport || !!(S.files.schedule && S.files.distances); }

function renderLaunch() {
  renderSource(); renderForm();
  const imp = S.imports.find((i) => i.id === S.sourceImport);
  $("#goTitle").textContent = imp ? "Lancer l'optimisation" : "Importer et lancer l'optimisation";
  $("#goNote").textContent = imp ? `Sur « ${imp.label} », avec les réglages ci-dessus.` :
    canLaunch() ? "Les fichiers sont prêts : un clic suffit." : "Déposez le planning et les distances pour commencer.";
  $("#composeBtn").disabled = !canLaunch();
}

function renderHistory() {
  const list = S.imports || [];
  $("#historyCount").textContent = list.length ? `${list.length} import${list.length > 1 ? "s" : ""}` : "";
  const lastRun = (id) => S.runs.find((r) => r.status === "termine" && r.settings && r.settings.import_id === id);
  $("#importList").innerHTML = list.length ? list.map((i) => {
    const run = lastRun(i.id);
    return `<div class="imp ${i.id === S.sourceImport ? "current" : ""}">
      <div class="imp-main"><div class="imp-name">${esc(i.label)}</div>
        <div class="imp-meta">importé le ${new Date(i.created_at).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" })}</div></div>
      <div class="imp-period"><b>${fmtDate(i.first)} → ${fmtDate(i.last, true)}</b><span>${num(i.need_lines)} lignes · ${i.suppliers} fournisseurs · ${i.plants.length} usines</span></div>
      <div class="imp-actions">
        ${run ? `<button class="btn ghost small" data-open-run="${run.id}">Voir les résultats</button>` : ""}
        <button class="btn ghost small" data-reuse="${i.id}">Relancer</button>
        <button class="c-del" data-del-imp="${i.id}" title="Supprimer cet import">×</button></div>
    </div>`;
  }).join("") : `<p class="empty-note">Aucun import pour l'instant.</p>`;
  $$("[data-open-run]").forEach((b) => b.addEventListener("click", () => openRun(b.dataset.openRun, "apercu")));
  $$("[data-reuse]").forEach((b) => b.addEventListener("click", () => {
    S.sourceImport = b.dataset.reuse; renderLaunch(); renderHistory(); window.scrollTo({ top: 0, behavior: "smooth" });
  }));
  $$("[data-del-imp]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Supprimer cet import ? Les calculs déjà faits restent consultables.")) return;
    try {
      await api("/imports/" + b.dataset.delImp, { method: "DELETE" });
      if (S.sourceImport === b.dataset.delImp) S.sourceImport = null;
      await refreshImports(); renderLaunch(); renderHistory();
    } catch (err) { toast(err.message); }
  }));
}

async function refreshImports() { S.imports = await api("/imports"); }

async function launch(example = false) {
  const btn = example ? $("#exampleBtn") : $("#composeBtn");
  if (!example && !canLaunch()) return;
  btn.disabled = true; btn.classList.add("busy");
  try {
    let importId = example ? null : S.sourceImport;
    if (!importId) {
      const fd = new FormData();
      if (example) fd.append("example", "true");
      else IMPORT_FILES.forEach(([key]) => { if (S.files[key]) fd.append(key, S.files[key]); });
      importId = (await api("/imports", { method: "POST", body: fd })).id;
      S.files = {};
      await refreshImports();
    }
    S.sourceImport = importId;
    const fd = new FormData();
    fd.append("settings", JSON.stringify(runSettings(importId, $("#f-label").value.trim() || (example ? "Exemple" : "Scénario"))));
    const run = await api("/runs", { method: "POST", body: fd });
    startRunning(run);
  } catch (err) {
    toast(err.message); renderLaunch(); renderHistory();
  } finally { btn.disabled = false; btn.classList.remove("busy"); if (S.view === "accueil") renderLaunch(); }
}
$("#composeBtn").addEventListener("click", () => launch(false));
// files dropped anywhere in the "files" area (also when a previous import was selected)
$("#sourceBox").addEventListener("dragover", (e) => e.preventDefault());
$("#sourceBox").addEventListener("drop", (e) => {
  e.preventDefault();
  const files = [...e.dataTransfer.files];
  if (!files.length) return;
  S.sourceImport = null;
  placeFiles(files);
});
$("#exampleBtn").addEventListener("click", () => launch(true));

function runSettings(importId, label, treat = S.treat) {
  return { label, import_id: importId, anticipation_days: F.anticipation, deliver_before_first_need: F.early,
           smoothing_max_extra_cost: F.smoothing, max_frequency: F.frequency,
           missing_weight: treat.weight, missing_volume: treat.volume, missing_distance: treat.distance };
}

// ----------------------------------------------------------------------------- settings (folded menus)
const DEFAULT_F = { anticipation: 7, early: true, smoothing: 0, frequency: 5 };
const F = { ...DEFAULT_F };
const DEFAULT_TREAT = { weight: "full_truck", volume: "full_truck", distance: "max" };
function pctTxt(x) { return x === 0 ? "aucun" : `${(x * 100).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %`; }

const TREAT_OPTIONS = {
  weight: [["full_truck", "Camion plein"], ["max", "Maximum"], ["median", "Médiane"], ["drop", "Retirer"]],
  volume: [["full_truck", "Camion plein"], ["max", "Maximum"], ["median", "Médiane"], ["drop", "Retirer"]],
  distance: [["max", "Maximum"], ["median", "Médiane"], ["drop", "Retirer"]],
};
const TREAT_TITLES = { weight: "Poids manquant", volume: "Volume manquant", distance: "Distance inconnue" };
const TREAT_HELP = {
  full_truck: "la ligne compte comme un camion entier : le plus prudent",
  max: "valeur la plus haute connue pour ce fournisseur et cette usine",
  median: "valeur habituelle de ce fournisseur pour cette usine",
  drop: "la ligne n'est pas livrée par le calcul",
};
const DIST_HELP = { max: "distance la plus longue connue : le plus prudent", median: "distance médiane des fournisseurs", drop: "les lignes de ce fournisseur ne sont pas livrées par le calcul" };
const TREAT_SHORT = { full_truck: "camion plein", max: "maximum", median: "médiane", drop: "retirées" };

function treatControls(treat, counts) {
  return ["weight", "volume", "distance"].map((f) => {
    const n = counts ? counts[f] : null, help = f === "distance" ? DIST_HELP : TREAT_HELP;
    return `<div class="treat-row ${n === 0 ? "none" : ""}"><div class="treat-head"><b>${TREAT_TITLES[f]}</b>${n === null ? "" : `<span>${n ? `${num(n)} lignes` : "aucune ligne"}</span>`}</div>
      <div class="seg treat-seg" data-field="${f}"><span class="pill"></span>
        ${TREAT_OPTIONS[f].map(([v, l]) => `<button type="button" data-v="${v}" class="${treat[f] === v ? "on" : ""}" ${n === 0 ? "disabled" : ""}>${l}</button>`).join("")}</div>
      <p class="treat-help">${help[treat[f]]}</p></div>`;
  }).join("");
}
function treatSummary(t) {
  const same = JSON.stringify(t) === JSON.stringify(DEFAULT_TREAT);
  return same ? "par défaut" : `poids : ${TREAT_SHORT[t.weight]} · volume : ${TREAT_SHORT[t.volume]} · distance : ${TREAT_SHORT[t.distance]}`;
}
function bindTreat(root, treat, onChange) {
  root.addEventListener("click", (e) => {
    const b = e.target.closest(".treat-seg button");
    if (!b || b.disabled || b.classList.contains("on")) return;
    const seg = b.closest(".treat-seg"), f = seg.dataset.field;
    treat[f] = b.dataset.v;
    $$("button", seg).forEach((x) => x.classList.toggle("on", x === b)); placePill(seg);
    $(".treat-help", seg.parentElement).textContent = (f === "distance" ? DIST_HELP : TREAT_HELP)[treat[f]];
    onChange && onChange();
  });
}
function placePill(seg) {
  const on = $("button.on", seg), pill = $(".pill", seg);
  if (on && pill) { pill.style.left = on.offsetLeft + "px"; pill.style.width = on.offsetWidth + "px"; }
}
$$("details.fold").forEach((d) => d.addEventListener("toggle", () => $$(".treat-seg, .seg", d).forEach(placePill)));

function renderForm() {
  $$(".fiche[data-field]").forEach((f) => {
    const field = f.dataset.field;
    $$(".chips button", f).forEach((b) => b.classList.toggle("on", String(F[field]) === b.dataset.v));
  });
  $("#antVal").textContent = F.anticipation;
  $('.fiche[data-field="anticipation"]').classList.toggle("off", F.anticipation !== 7);
  const changed = Object.keys(DEFAULT_F).some((k) => F[k] !== DEFAULT_F[k]);
  $("#tuningSum").textContent = changed ? [`${F.anticipation} j d'avance`,
    F.smoothing ? `lissage ${pctTxt(F.smoothing)}` : "lissage gratuit", `≤ ${F.frequency} / sem.`].join(" · ") : "par défaut : coût minimal";
  const w = $("#tuningWarn");
  w.hidden = F.anticipation === 7;
  w.textContent = `${F.anticipation} jours d'avance : en dehors de la règle des 7 jours, à présenter comme un scénario.`;
  $("#missingSum").textContent = treatSummary(S.treat);
  if (!$("#treatControls").children.length) $("#treatControls").innerHTML = treatControls(S.treat, null);
}
$$(".fiche[data-field] .chips button").forEach((b) => b.addEventListener("click", () => {
  const field = b.closest(".fiche").dataset.field, raw = b.dataset.v;
  F[field] = field === "early" ? raw === "true" : +raw;
  renderForm();
}));
$$(".stepper [data-step]").forEach((b) => b.addEventListener("click", () => {
  F.anticipation = Math.max(0, Math.min(21, F.anticipation + +b.dataset.step));
  renderForm();
}));
bindTreat($("#treatControls"), S.treat, () => ($("#missingSum").textContent = treatSummary(S.treat)));

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
  // folded menus
  const counts = Object.fromEntries(st.anomalies.filter((a) => a.field).map((a) => [a.field, a.count]));
  S.reTreat = { ...st.params };
  $("#foldMissingSum").textContent = treatSummary(st.params);
  $("#foldMissingBody").innerHTML = `<p class="meta small">Traitement utilisé pour ce calcul. Changez-le et relancez pour comparer.</p>
    <div id="reTreat">${treatControls(S.reTreat, counts)}</div>
    <button class="btn rose" id="reRunBtn" disabled>Relancer avec ces choix</button>`;
  bindTreat($("#reTreat"), S.reTreat, () => ($("#reRunBtn").disabled = JSON.stringify(S.reTreat) === JSON.stringify(st.params)));
  $("#reRunBtn").addEventListener("click", rerunWithTreat);
  $("#foldRulesSum").textContent = `${st.rules.length} règles`;
  $("#rulesList").innerHTML = st.rules.map((r, i) => `<li style="--d:${0.04 * i}s">${esc(r)}</li>`).join("");
  const KIND = { corrige: ["corrigé", "ok"], choix: ["à votre choix", "choice"], info: ["information", "info"], bloquant: ["bloquant", "bad"] };
  const shown = st.anomalies.filter((a) => a.count || a.kind === "choix");
  $("#anomalyCount").textContent = `${st.anomalies.filter((a) => a.count).length} types détectés`;
  $("#anomalyTable").innerHTML = `<table class="anomalies"><thead><tr><th>Anomalie</th><th class="num">Lignes</th><th>Détail</th><th>Traitement</th><th></th></tr></thead><tbody>
    ${shown.map((a) => `<tr class="${a.count ? "" : "zero"}"><td><b>${esc(a.label)}</b></td><td class="num">${num(a.count)}</td><td class="det">${esc(a.detail || "")}</td>
      <td>${esc(a.treatment)}</td><td><span class="badge ${KIND[a.kind][1]}">${KIND[a.kind][0]}</span></td></tr>`).join("")}</tbody></table>`;
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

async function rerunWithTreat() {
  const st = S.run.settings, t = S.reTreat;
  const label = `${S.run.label} · poids ${TREAT_SHORT[t.weight]}`.slice(0, 60);
  const settings = { ...st, label, missing_weight: t.weight, missing_volume: t.volume, missing_distance: t.distance };
  const fd = new FormData(); fd.append("settings", JSON.stringify(settings));
  try { startRunning(await api("/runs", { method: "POST", body: fd })); } catch (err) { toast(err.message); }
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

// stop button of the waiting screen
$("#stopRunBtn").addEventListener("click", async () => {
  if (!S.runningId || !confirm("Arrêter le calcul en cours ? Les résultats partiels ne sont pas conservés.")) return;
  const btn = $("#stopRunBtn"); btn.disabled = true; btn.classList.add("busy");
  try {
    await api(`/runs/${S.runningId}/stop`, { method: "POST" });
    stopRunning(); toast("Calcul arrêté.");
    await Promise.all([refreshRuns(), refreshImports()]); show("accueil");
  } catch (err) { toast(err.message); }
  finally { btn.disabled = false; btn.classList.remove("busy"); }
});

function startRunning(run) {
  S.runningId = run.id;
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
      const st = await api("/runs/" + run.id);
      if (st.status === "termine") { stopRunning(); await Promise.all([refreshRuns(), refreshImports()]); $("#f-label").value = `Scénario ${S.runs.length + 1}`; await openRun(st.id, "apercu"); }
      else if (st.status === "arrete") { stopRunning(); toast("Calcul arrêté."); await Promise.all([refreshRuns(), refreshImports()]); show("accueil"); }
      else if (st.status === "erreur" || st.status === "interrompu") { stopRunning(); toast("Le calcul n'a pas abouti : " + (st.error || st.status)); await refreshRuns(); show("accueil"); }
    } catch (_) { /* server busy: try again */ }
  }, 500);
}
function stopRunning() { clearInterval(S.polling); cancelAnimationFrame(gatherRaf); gatherRaf = null; }

// ----------------------------------------------------------------------------- runs
async function refreshRuns() {
  S.runs = await api("/runs");
  const done = S.runs.filter((r) => r.status === "termine");
  $("#scenarioSwitch").hidden = !done.length;
  $("#scenarioSelect").innerHTML = done.map((r) => `<option value="${r.id}">${esc(r.label)} · ${eur(r.kpi.saving)}</option>`).join("");
  if (S.current) $("#scenarioSelect").value = S.current;
}
$("#scenarioSelect").addEventListener("change", (e) => openRun(e.target.value, S.view === "accueil" ? "apercu" : S.view));

async function openRun(id, view) {
  S.summary = await api(`/runs/${id}/summary`);
  try { S.sit = await api(`/runs/${id}/situation`); } catch (_) { S.sit = null; }   // runs made before the imports have none
  S.current = id; S.trucksPage = 0; S.selectedTruck = null; S.suppliers = [];
  S.run = S.runs.find((r) => r.id === id);
  $("#scenarioSelect").value = id;
  updateDock(); fillFilters(); renderNavScenario();
  show(view || "apercu");
}

function renderNavScenario() {
  const f = S.summary.filters;
  $("#navScenario").hidden = false;
  $("#navScenarioName").textContent = S.run ? S.run.label : "Scénario";
  $("#navScenarioMeta").textContent = `${num(f.suppliers.length)} fournisseurs · ${num(f.plants.length)} usine${f.plants.length > 1 ? "s" : ""}`;
}

function fillFilters() {
  const f = S.summary.filters;
  const opts = (arr, label, fmt = (x) => x) => `<option value="">${label}</option>` + arr.map((x) => `<option value="${esc(x)}">${esc(fmt(x))}</option>`).join("");
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
  const L = 46, B = 34, T = 34, max = niceMax(Math.max(...plants.map((p) => p.avant)));
  const gw = (W - L) / plants.length, bw = Math.min(W / 14, gw * 0.28), y = (v) => T + (H - T - B) * (1 - v / max);
  let s = "";
  for (let k = 0; k <= 4; k++) { const v = (max * k) / 4; s += `<g class="axis"><line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/></g><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${num(v)}</text>`; }
  plants.forEach((p, i) => {
    const cx = L + gw * i + gw / 2, save = 100 * (1 - p.apres / p.avant);
    s += `<rect x="${cx - bw - 3}" y="${y(p.avant)}" width="${bw}" height="${H - B - y(p.avant)}" rx="8" class="bar" style="--d:${(0.08 * i).toFixed(2)}s" fill="${C.before}" data-tip="${p.plant} · aujourd'hui<br><b>${eur(p.avant)}</b>"/>`;
    s += `<rect x="${cx + 3}" y="${y(p.apres)}" width="${bw}" height="${H - B - y(p.apres)}" rx="8" class="bar" style="--d:${(0.08 * i + 0.25).toFixed(2)}s" fill="${C.after}" data-tip="${p.plant} · avec Gilbert<br><b>${eur(p.apres)}</b>"/>`;
    s += `<text x="${cx + 3 + bw / 2}" y="${y(p.apres) - 8}" text-anchor="middle" class="val fade" style="fill:${C.afterDark};--d:${(0.08 * i + 0.8).toFixed(2)}s">−${nf0.format(save)} %</text>`;
    s += `<text x="${cx}" y="${H - 10}" text-anchor="middle" style="font-weight:700;fill:${C.label}">${p.plant}</text>`;
  });
  return svg(W, H, s);
}

function miniHist(rows, W = 300, H = 110) {
  const max = Math.max(...rows.map((r) => r.lignes)), bw = W / rows.length;
  return svg(W, H + 18, rows.map((r, i) => {
    const h = (H - (W > 600 ? 28 : 6)) * (r.lignes / max);
    const w = Math.min(bw - 6, 70);
    return `<rect x="${i * bw + (bw - w) / 2}" y="${H - h}" width="${w}" height="${h}" rx="2" class="bar" style="--d:${(0.06 * i).toFixed(2)}s" fill="${r.jours === 0 ? C.slate : C.after}" data-tip="${r.jours} jour(s) d'avance<br><b>${num(r.lignes)}</b> lignes"/>
      <text x="${i * bw + bw / 2}" y="${H + 14}" text-anchor="middle">${r.jours} j</text>
      ${W > 600 ? `<text x="${i * bw + bw / 2}" y="${H - h - 8}" text-anchor="middle" class="val fade" style="--d:${(0.06 * i + 0.6).toFixed(2)}s">${num(r.lignes)}</text>` : ""}`;
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

function dumbbell(rows) {
  const W = 520, rowH = 46, L = 92, R = 40, H = rows.length * rowH + 40, max = niceMax(Math.max(...rows.flatMap((r) => [r.avant, r.apres])));
  const x = (v) => L + (W - L - R) * (v / max);
  let s = "";
  for (let k = 0; k <= 4; k++) { const v = (max * k) / 4; s += `<g class="axis"><line x1="${x(v)}" x2="${x(v)}" y1="10" y2="${H - 26}"/></g><text x="${x(v)}" y="${H - 8}" text-anchor="middle">${num(v)}</text>`; }
  rows.forEach((r, i) => {
    const y = 24 + i * rowH;
    s += `<text x="0" y="${y + 4}" style="font-weight:700;fill:${C.label};font-size:12.5px">${r.profil}</text>`;
    s += `<line x1="${x(r.avant)}" x2="${x(r.apres)}" y1="${y}" y2="${y}" stroke="${C.line}" stroke-width="6" stroke-linecap="round"/>`;
    s += `<circle class="pop" style="--d:${(0.08 * i).toFixed(2)}s" cx="${x(r.avant)}" cy="${y}" r="9" fill="${C.before}" data-tip="${r.profil} · aujourd'hui<br><b>${r.avant}</b> couples"/>`;
    s += `<circle class="pop" style="--d:${(0.08 * i + 0.3).toFixed(2)}s" cx="${x(r.apres)}" cy="${y}" r="9" fill="${C.after}" data-tip="${r.profil} · avec Gilbert<br><b>${r.apres}</b> couples"/>`;
  });
  return svg(W, H, s) + `<div class="legend" style="margin-top:.6rem"><span class="l-before">aujourd'hui</span><span class="l-after">avec Gilbert</span><span>nombre de couples fournisseur × usine</span></div>`;
}

function lollipop(rows) {
  const W = 560, rowH = 30, L = 170, R = 70, H = rows.length * rowH + 10, max = Math.max(...rows.map((r) => r.saving));
  const x = (v) => L + (W - L - R) * (v / max);
  return svg(W, H, rows.map((r, i) => {
    const y = 14 + i * rowH;
    return `<text x="0" y="${y + 4}" style="font-size:11.5px;fill:${C.ink}">${r.fournisseur} · ${r.usine}</text>
      <line class="hbar" style="--d:${(0.04 * i).toFixed(2)}s" x1="${L}" x2="${x(r.saving)}" y1="${y}" y2="${y}" stroke="${C.afterPale}" stroke-width="3" stroke-linecap="round"/>
      <circle class="pop" style="--d:${(0.04 * i + 0.5).toFixed(2)}s" cx="${x(r.saving)}" cy="${y}" r="7" fill="${C.after}" data-tip="${r.fournisseur} → ${r.usine}<br>${eur(r.avant)} → ${eur(r.apres)}<br><b>−${nf0.format(r.pct)} %</b>"/>
      <text x="${x(r.saving) + 12}" y="${y + 4}" class="val fade" style="--d:${(0.04 * i + 0.6).toFixed(2)}s">${eur(r.saving)}</text>`;
  }).join(""));
}

function scenarioBars(runs) {
  const W = 900, rowH = 50, L = 200, R = 250, H = runs.length * rowH + 10, max = Math.max(...runs.map((r) => r.kpi.saving));
  const x = (v) => L + (W - L - R) * (v / max);
  return svg(W, H, runs.map((r, i) => {
    const y = 8 + i * rowH, cur = r.id === S.current;
    return `<text x="0" y="${y + 22}" style="font-weight:700;font-size:13px;fill:${C.ink}">${esc(r.label).slice(0, 26)}</text>
      <rect class="hbar" style="--d:${(0.08 * i).toFixed(2)}s" x="${L}" y="${y + 6}" width="${x(r.kpi.saving) - L}" height="26" rx="13" fill="${cur ? C.after : C.before}"/>
      <text x="${x(r.kpi.saving) + 10}" y="${y + 24}" class="val">${eur(r.kpi.saving)}</text>
      <text x="${x(r.kpi.saving) + 10}" y="${y + 40}">${num(r.kpi.trucks_after)} camions · pics ${r.peaks.join(" / ")}</text>`;
  }).join(""));
}

// ----------------------------------------------------------------------------- views
const STAMP_DELAY = { i: 0, next() { return (0.45 + 0.12 * this.i++).toFixed(2); }, reset() { this.i = 0; } };
function stamp(label, value, target, ok, r) {
  return `<div class="stamp ${ok ? "" : "ko"}" style="--r:${r}deg;--sd:${STAMP_DELAY.next()}s"><div class="s-top"><span>${label}</span><span>${ok ? "✓ atteint" : "✗ manqué"}</span></div>
    <div class="s-val">${value}</div><div class="s-target">cible ${target}</div></div>`;
}

function settingsChips(st, run) {
  const miss = st.missing_weight ? `poids : ${TREAT_SHORT[st.missing_weight]}` : null;
  return [run && run.import_label ? run.import_label : null, `${st.anticipation_days} j d'avance`,
    st.smoothing_max_extra_cost ? `lissage ${nf1.format(st.smoothing_max_extra_cost * 100)} %` : "lissage gratuit",
    `≤ ${st.max_frequency} / semaine`, miss].filter(Boolean)
    .map((c) => `<span class="tag">${esc(c)}</span>`).join(" ");
}

function renderOverview() {
  const sm = S.summary, k = sm.kpi, run = S.run, st = run.settings || {};
  const sk = S.sit ? S.sit.kpi : null, days = sm.calendar.days;
  const first = sk ? sk.first : days[0], last = sk ? sk.last : days[days.length - 1];
  $("#ov-sub").textContent = `${frDate(first)} — ${frDate(last, true)} · ${num(k.lines)} besoins · ${num(k.couples)} couples fournisseur–usine`;

  // export menu: the five existing files
  const base = `/api/runs/${S.current}/files/`;
  const files = [["resultats_consolidation.xlsx", "Rapport complet", "Excel · toutes les sorties et contrôles"],
    ["sortie1_profils.csv", "Jours de livraison par fournisseur", "CSV · profils (sortie 1)"],
    ["sortie2_camions.csv", "Liste des camions", "CSV · contenu des camions (sortie 2)"],
    ["sortie3_saving.csv", "Économies par fournisseur", "CSV · économies (sortie 3)"],
    ["gilbert_resultats.zip", "Tout télécharger", "Archive ZIP complète"]];
  $("#downloads").innerHTML = files.map(([f, t, d]) => `<a role="menuitem" href="${base}${f}" download><b>${t}</b><span>${d}</span></a>`).join("");

  // first row: saving (emerald) and cost (bars on one scale)
  const smoothing = st.smoothing_max_extra_cost ? `lissage avec ${nf1.format(100 * st.smoothing_max_extra_cost)} % de surcoût accepté` : "lissage sans surcoût";
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
    <div class="ed-list">${sm.plants.map((p) => `<div class="ed-list-row"><span>${esc(p.plant)}</span><b>${num(p.camions_apres)} camion${p.camions_apres > 1 ? "s" : ""}</b><span class="ed-muted">Pic : ${num(p.pic)}</span></div>`).join("")}</div>`;
  const find = (rule) => sm.controls.find((c) => c.regle === rule);
  const antCtl = find("Règle 12"), antDays = antCtl && /≤\s*(\d+)/.exec(antCtl.controle) ? /≤\s*(\d+)/.exec(antCtl.controle)[1] : st.anticipation_days;
  const item = (c, title, okText) => {
    const state = !c ? "unknown" : c.ok ? "ok" : "ko";
    const icon = { ok: '<path d="m5 12 4.5 4.5L19 7"/>', ko: '<path d="M6 6l12 12M18 6 6 18"/>', unknown: '<path d="M12 17h.01M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7"/>' }[state];
    const text = { ok: okText, ko: "Contrôle non respecté : voir les contrôles détaillés", unknown: "Résultat indisponible pour ce calcul" }[state];
    return `<div class="ed-check ${state}"><span class="ed-check-ico"><svg viewBox="0 0 24 24">${icon}</svg></span><div><b>${title}</b><span>${text}</span></div></div>`;
  };
  $("#complianceCard").innerHTML = `<div class="ed-card-head"><h2>Conformité du plan</h2></div>
    ${item(find("Règle 11"), "Aucun retard", "Dates de besoin respectées")}
    ${item(antCtl, `Anticipation ≤ ${antDays} jours`, "Fenêtre de livraison conforme")}
    ${item(find("Règle 13"), "Capacités des camions", "Poids et volume respectés")}`;

  // detailed sections
  $("#plantsSum").textContent = `${sm.plants.length} usines`;
  $("#plantsBody").innerHTML = `<p class="meta small">Situation actuelle (vert) et plan Gilbert (orange), par usine. Le pourcentage indique l'économie.</p>
    <div class="legend" style="justify-content:flex-end"><span class="l-before">aujourd'hui</span><span class="l-after">avec Gilbert</span></div>${plantBars(sm.plants, 1240, 330)}`;
  const smoothCost = sm.smoothing.reduce((a, p) => a + p.surcout_lissage, 0), maxPeak = Math.max(1, ...sm.smoothing.map((p) => p.pic_avant_lissage));
  $("#smoothSum").textContent = `surcoût ${eur(smoothCost)}`;
  $("#smoothBody").innerHTML = `<p class="meta small">Camions reçus le jour le plus chargé de chaque usine, avant et après le lissage des arrivées entre les jours.</p>
    <div style="max-width:760px">${sm.smoothing.map((p, i) => `<div class="peakrow"><span class="p">${p.usine}</span><span class="bars">
      <i class="before" style="width:${(100 * p.pic_avant_lissage) / maxPeak}%;--d:${0.1 * i}s"></i><i class="after" style="width:${(100 * p.pic_apres_lissage) / maxPeak}%;--d:${0.1 * i + 0.25}s"></i></span>
      <span class="v">${p.pic_avant_lissage === p.pic_apres_lissage ? "" : `<s>${p.pic_avant_lissage}</s> → `}${p.pic_apres_lissage} camions</span></div>`).join("")}
      <div class="legend" style="margin-top:.6rem"><span class="l-before">avant lissage</span><span class="l-after">après lissage</span></div></div>
    <p class="ed-note"><b>${num(k.frequency_changes)} couples</b> changent de fréquence au lissage ; ${num(k.weekday_changes - k.frequency_changes)} autres changent seulement de jours.</p>
    <p class="ed-note">${imputedNote(k, st)}</p>
    <button class="btn small" data-view="camions">Ouvrir les camions</button>`;
  countUp($("#view-apercu"));
  renderControls();
  $("#controlsSum").textContent = sm.controls.every((c) => c.ok) ? `${sm.controls.length} contrôles, tous respectés` : "au moins une règle à revoir";
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

function imputedNote(k, st) {
  const w = st.missing_weight || st.missing_load || "full_truck";
  if (w === "drop" || !k.imputed_trucks) return "Aucun camion ne transporte de ligne au poids complété : les lignes incomplètes ont été retirées.";
  const how = { full_truck: "comptées comme un camion plein", max: "complétées par le maximum connu", median: "complétées par la médiane" }[w] || "complétées";
  return `<b>${k.imputed_trucks} camions</b> transportent une ligne dont le poids ou le volume manquait : ces lignes sont ${how}, avant comme après.`;
}

function renderCalendarView() {
  const sm = S.summary;
  setTimeout(() => countUp($("#peaks")), 0);
  $("#calendarBig").innerHTML = calendarSvg(sm.calendar) +
    `<div class="legend" style="margin-top:1rem"><span class="l-scale">peu à beaucoup de camions</span><span class="l-peak">jour de pointe de l'usine</span></div>`;
  const maxAll = Math.max(...sm.plants.map((p) => p.pic));
  $("#peaks").innerHTML = sm.plants.map((p) => {
    const r = sm.calendar.trucks[sm.calendar.plants.indexOf(p.plant)], d = sm.calendar.days[r.indexOf(p.pic)];
    return `<div class="peak ${p.pic === maxAll ? "hot" : ""}"><p class="label">${p.plant}</p><div class="pk-n">${counter(p.pic)} camions</div>
      <div class="meta" style="margin:0">au plus, le ${dayLabel(d)} · ${num(p.camions_apres)} sur l'horizon</div></div>`;
  }).join("");
}

function table(cols, rows, { onRow, selected } = {}) {
  const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${c.label}</th>`).join("");
  const body = rows.map((r, i) => `<tr data-i="${i}" class="${selected && selected(r) ? "sel" : ""}" style="${onRow ? "cursor:pointer" : ""}">${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.fmt ? c.fmt(r[c.key], r) : esc(r[c.key])}</td>`).join("")}</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${cols.length}" style="color:${C.muted}">Aucune ligne.</td></tr>`}</tbody></table>`;
}

async function renderProfiles() {
  $("#dumbbell").innerHTML = dumbbell(S.summary.profiles);
  const p = new URLSearchParams();
  if ($("#pf-plant").value) p.set("plant", $("#pf-plant").value);
  if ($("#pf-q").value) p.set("q", $("#pf-q").value);
  let rows = await api(`/runs/${S.current}/profiles?${p}`);
  if ($("#pf-changed").checked) rows = rows.filter((r) => r.frequence_changee);
  $("#profilesTable").innerHTML = table([
    { key: "fournisseur", label: "Fournisseur" }, { key: "usine", label: "Usine" }, { key: "km", label: "km", num: true, fmt: num },
    { key: "avant", label: "Aujourd'hui" }, { key: "apres", label: "Avec Gilbert", fmt: (v, r) => `<span class="tag ${r.frequence_changee ? "rose" : ""}" ${r.frequence_changee ? `data-tip="Avant lissage : ${esc(r.jours_avant_lissage)}"` : ""}>${v}</span>` },
    { key: "jours", label: "Jours" }, { key: "livraisons_avant", label: "Livr. avant", num: true }, { key: "livraisons_apres", label: "Livr. après", num: true },
    { key: "camions", label: "Camions", num: true }, { key: "remplissage", label: "Remplissage", num: true, fmt: (v) => nf1.format(v) + " %" },
  ], rows);
}
["#pf-plant", "#pf-q", "#pf-changed"].forEach((id) => $(id).addEventListener("input", () => S.summary && renderProfiles()));

async function renderTrucks() {
  const p = new URLSearchParams({ limit: 60, offset: S.trucksPage * 60 });
  if ($("#tf-plant").value) p.set("plant", $("#tf-plant").value);
  if ($("#tf-supplier").value) p.set("supplier", $("#tf-supplier").value);
  if ($("#tf-day").value) p.set("day", $("#tf-day").value);
  const res = await api(`/runs/${S.current}/trucks?${p}`);
  $("#trucksTable").innerHTML = table([
    { key: "id", label: "Camion" }, { key: "fournisseur", label: "Fournisseur" }, { key: "usine", label: "Usine" },
    { key: "date", label: "Jour", fmt: dayLabel }, { key: "lignes", label: "Lignes", num: true }, { key: "kg", label: "kg", num: true, fmt: num },
    { key: "remplissage", label: "Remplissage", num: true, fmt: (v) => `<span class="tag ${v >= 99 ? "rose" : ""}">${nf1.format(v)} %</span>` },
    { key: "prix", label: "Prix", num: true, fmt: (v) => eur(v, 2) },
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
  const t = await api(`/runs/${S.current}/trucks/${encodeURIComponent(id)}`);
  const gauge = (label, v, rose) => `<div class="gauge ${rose ? "rose" : ""}"><div class="g-l"><span>${label}</span><b>${nf1.format(v)} %</b></div><div class="g-b"><i style="width:${Math.min(100, v)}%"></i></div></div>`;
  $("#ticket").innerHTML = `<div class="ticket">
    <span class="price">${eur(t.prix, 2)}</span>
    <div class="t-brand"><img src="brand/gilbert-logo.svg" alt="Gilbert &amp; Co" style="height:26px;vertical-align:middle"> · bon de livraison</div>
    <div class="t-id">${t.id}</div>
    <div class="t-route"><b>${t.fournisseur}</b> → <b>${t.usine}</b> · ${dayLabel(t.date)}</div>
    <div class="perf"></div>
    <div class="t-grid">${gauge(`${num(t.kg)} kg`, t.remplissage_poids, false)}${gauge(`${nf1.format(t.m3)} m³`, t.remplissage_volume, true)}</div>
    <ul>${t.articles.map((a) => `<li><span class="a">${a.article}${a.manquant ? ' <span class="tag warn" data-tip="Poids manquant : compté comme un camion plein">poids manquant</span>' : ""}</span>
      <span>${num(a.kg)} kg</span><span class="d">quai ${a.quai} · quantité ${num(a.quantite)} · besoins du ${a.besoins.join(", ")}</span></li>`).join("")}</ul>
    <div class="replaces">Ce camion regroupe <b>${t.articles.length} article${t.articles.length > 1 ? "s" : ""}</b> et remplace <b>${t.remplace} livraison${t.remplace > 1 ? "s" : ""}</b> d'aujourd'hui.</div>
  </div>`;
}

async function renderSuppliers() {
  $("#lollipop").innerHTML = lollipop(S.summary.top_savings);
  if (!S.suppliers.length) S.suppliers = await api(`/runs/${S.current}/suppliers`);
  const q = $("#sf-q").value.trim().toLowerCase();
  const rows = S.suppliers.filter((r) => !q || (r.fournisseur + " " + r.usine).toLowerCase().includes(q)).sort((a, b) => b.saving - a.saving);
  $("#suppliersTable").innerHTML = table([
    { key: "fournisseur", label: "Fournisseur" }, { key: "usine", label: "Usine" },
    { key: "avant", label: "Avant", num: true, fmt: (v) => eur(v) }, { key: "apres", label: "Après", num: true, fmt: (v) => eur(v) },
    { key: "camions_avant", label: "Camions avant", num: true }, { key: "camions_apres", label: "après", num: true },
    { key: "saving", label: "Économie", num: true, fmt: (v) => `<b>${eur(v)}</b>` }, { key: "pct", label: "%", num: true, fmt: (v) => `<span class="tag rose">−${nf0.format(v)} %</span>` },
  ], rows);
}
$("#sf-q").addEventListener("input", () => S.summary && renderSuppliers());

function renderControls() {
  const sm = S.summary;
  $("#controlsList").innerHTML = sm.controls.map((c) => `<div class="rule ${c.ok ? "" : "ko"}"><span class="mark">${c.ok ? "✓" : "✗"}</span>
    <div><div class="r-name">${esc(c.regle)}</div><div>${esc(c.controle)}</div></div></div>`).join("");
  const JL = { lignes_brutes: "Lignes importées", exclues_cumul: "Lignes de cumul retirées", exclues_quantite_0: "Lignes à quantité nulle retirées",
    exclues_plus_grandes_qu_un_camion: "Lignes plus grandes qu'un camion retirées", poids_ou_volume_manquant: "Lignes au poids ou volume manquant",
    exclues_weight_manquant: "Lignes au poids manquant retirées", exclues_volume_manquant: "Lignes au volume manquant retirées",
    distance_manquante: "Distance inconnue", exclues_distance_manquante: "Lignes sans distance retirées", lignes_retenues: "Lignes de besoin retenues",
    horizon: "Période des besoins", cout_actuel_estimateur_M2_eur: "Coût actuel, estimé en charge divisible (€)",
    validation_fenetre_0: "Contrôle : sans avance, le moteur retrouve la situation actuelle", plans_generes: "Plans de livraison étudiés",
    gain_amelioration_locale_eur: "Gain de l'amélioration locale (€)", couples_ameliores_par_le_modele_exact: "Couples améliorés par le modèle exact",
    "gain_chargement_exact_eur (couples non réoptimisés)": "Gain du chargement exact (€)", source: "Source" };
  $("#journal").innerHTML = table([{ key: 0, label: "Étape" }, { key: 1, label: "Valeur" }], Object.entries(sm.journal).map(([a, b]) => [JL[a] || a.replaceAll("_", " "), b]));
}

function renderScenarios() {
  const done = S.runs.filter((r) => r.status === "termine");
  $("#scenarioChart").innerHTML = done.length ? `<h3>Économies par scénario</h3>${scenarioBars(done)}` : "";
  $("#scenarioCards").innerHTML = S.runs.map((r) => {
    const ok = r.status === "termine";
    return `<div class="card ${r.id === S.current ? "current" : ""} ${ok ? "" : "failed"}" data-run="${r.id}" data-ok="${ok}">
      <button class="c-del" data-del="${r.id}" title="Supprimer">×</button>
      <div class="c-name">${esc(r.label)}</div><div class="c-date">${new Date(r.created_at).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" })}</div>
      <div class="c-chips">${settingsChips(r.settings, r)}</div>
      ${ok ? `<div class="c-saving">${eur(r.kpi.saving)}</div><div class="c-meta">${num(r.kpi.trucks_after)} camions · pics ${r.peaks.join(" / ")} · ${Math.round(r.kpi.runtime_s)} s</div>`
           : `<div class="c-saving">${r.status === "erreur" ? "Erreur : " + esc(r.error || "") : ({ arrete: "Arrêté à la demande", interrompu: "Interrompu", en_cours: "En cours", en_attente: "En attente" }[r.status] || r.status)}</div>`}
    </div>`;
  }).join("");
  $$("#scenarioCards .card").forEach((c) => c.addEventListener("click", (e) => {
    if (e.target.closest("[data-del]")) return;
    if (c.dataset.ok === "true") openRun(c.dataset.run, "apercu");
  }));
  $$("#scenarioCards [data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Supprimer ce calcul et ses fichiers ?")) return;
    try {
      await api("/runs/" + b.dataset.del, { method: "DELETE" });
      await refreshRuns();
      if (b.dataset.del === S.current) {
        const next = S.runs.find((r) => r.status === "termine");
        if (next) await openRun(next.id, "scenarios"); else { S.summary = null; S.current = null; updateDock(); show("accueil"); }
      } else renderScenarios();
    } catch (err) { toast(err.message); }
  }));
}

// ----------------------------------------------------------------------------- start
(async function init() {
  try { DEFAULTS = { ...DEFAULTS, ...(await api("/defaults")) }; } catch (_) { /* offline */ }
  setNav(readNav());
  try {
    await Promise.all([refreshRuns(), refreshImports()]);
    const running = S.runs.find((r) => r.status === "en_cours" || r.status === "en_attente");
    const done = S.runs.find((r) => r.status === "termine");
    $("#f-label").value = done ? `Scénario ${S.runs.length + 1}` : "Plan de référence";
    if (running) { startRunning(running); return; }
    const want = location.hash.slice(1);
    if (done) await openRun(done.id, want && want !== "running" ? want : "accueil");
    else show("accueil");
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
