"use strict";
// Chancenfinder – Frontend ohne Abhängigkeiten. Quelltexte werden ausschließlich als Text
// (textContent) eingefügt, nie als HTML.

const PAGE = 20;
const state = { options: null, status: null, foerder: null, auftrag: null, lastFoerder: null, lastAuftrag: null };

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
const $ = (sel) => document.querySelector(sel);

function safeUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch { return null; }
}
function extLink(url, text, cls) {
  const href = safeUrl(url);
  if (!href) return null;
  return el("a", { href, target: "_blank", rel: "noopener noreferrer", class: cls }, text);
}
function fmtDate(iso) {
  if (!iso) return "unbekannt";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Berlin" });
}
function fmtDay(day) {
  if (!day) return "–";
  const [y, m, d] = day.split("-");
  return `${d}.${m}.${y}`;
}
function list(items, empty) {
  if (!items || !items.length) return el("p", { class: "muted small" }, empty || "Nicht angegeben");
  return el("ul", {}, items.map((t) => el("li", {}, t)));
}

async function api(path, body) {
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* leer */ }
  if (!res.ok) {
    let msg = "Die Anfrage ist fehlgeschlagen.";
    if (data && typeof data.detail === "string") msg = data.detail;
    else if (res.status === 422) msg = "Bitte prüfe deine Eingaben.";
    throw new Error(msg);
  }
  return data;
}

// --- Routing -----------------------------------------------------------------
const VIEWS = { "": "view-home", "foerderung": "view-foerderung", "auftraege": "view-auftraege", "quellen": "view-quellen" };
function route() {
  const key = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "";
  const id = VIEWS[key] || "view-home";
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== id; });
  document.querySelectorAll("nav a").forEach((a) => {
    if (a.getAttribute("href") === "#/" + key) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  const titles = { "view-home": "Chancenfinder", "view-foerderung": "Förderung finden – Chancenfinder",
    "view-auftraege": "Öffentliche Aufträge finden – Chancenfinder", "view-quellen": "Quellen & Lizenzen – Chancenfinder" };
  document.title = titles[id];
  if (id !== "view-home") $("#main").focus({ preventScroll: true });
}

// --- Datenstand ----------------------------------------------------------------
function sourceCard(title, s, extra) {
  const ok = s.last_success;
  const last = s.last_run;
  const failed = last && last.status === "failed";
  return el("div", { class: "card" },
    el("h3", {}, title),
    s.running ? el("p", {}, el("span", { class: "spinner", "aria-hidden": "true" }), "Import läuft gerade …") : null,
    el("p", {}, "Letzte erfolgreiche Aktualisierung: ", el("strong", {}, ok ? fmtDate(ok.finished_at) : "noch keine")),
    extra,
    failed ? el("p", { class: "notice error small" }, "Letzter Importversuch fehlgeschlagen (", fmtDate(last.finished_at), "): ", last.message || "unbekannter Fehler",
      ok ? " – angezeigt wird der letzte gültige Datenstand." : "") : null,
    last && last.status === "partial" ? el("p", { class: "notice warn small" }, last.message) : null,
  );
}
function renderStatus() {
  const s = state.status;
  if (!s) return;
  const cov = s.vergabe.coverage || {};
  const fCard = sourceCard("Förderdatenbank des Bundes", s.foerder,
    el("p", {}, `${s.foerder.count.toLocaleString("de-DE")} Förderprogramme im Bestand`));
  const vCard = sourceCard("Bekanntmachungsservice", s.vergabe, el("div", {},
    el("p", {}, "Importzeitraum (Veröffentlichungstage): ", el("strong", {}, cov.first_day ? `${fmtDay(cov.first_day)} – ${fmtDay(cov.last_day)}` : "noch keiner")),
    el("p", {}, `${(cov.notices || 0).toLocaleString("de-DE")} Bekanntmachungen, ${s.vergabe.count.toLocaleString("de-DE")} Verfahren, davon ${s.vergabe.open_count.toLocaleString("de-DE")} mit möglicher Teilnahme`),
    cov.failed_days && cov.failed_days.length ? el("p", { class: "notice warn small" }, "Nicht importierte Tage: " + cov.failed_days.map((d) => fmtDay(d.day)).join(", ")) : null,
    el("p", { class: "muted small" }, "Es werden nur Bekanntmachungen aus diesem Zeitraum berücksichtigt – keine vollständige Abdeckung aller öffentlichen Aufträge."),
  ));
  for (const id of ["#status-content", "#status-content-2"]) {
    const box = $(id);
    box.replaceChildren(fCard.cloneNode(true), vCard.cloneNode(true));
  }
  const banner = $("#import-banner");
  const running = s.foerder.running || s.vergabe.running;
  const empty = s.foerder.count === 0 || s.vergabe.count === 0;
  if (running) {
    banner.className = "banner";
    banner.replaceChildren(el("span", { class: "spinner", "aria-hidden": "true" }),
      empty ? "Die Daten werden gerade erstmals aus den offiziellen Quellen importiert. Das kann einige Minuten dauern." : "Die Daten werden gerade aktualisiert. Bis zum Abschluss wird der bisherige Stand angezeigt.");
    banner.hidden = false;
  } else if (empty) {
    banner.className = "banner error";
    banner.textContent = "Für mindestens eine Quelle liegen noch keine Daten vor. Details unter „Datenstand“.";
    banner.hidden = false;
  } else banner.hidden = true;
  return running;
}
async function loadStatus() {
  try {
    state.status = await api("/api/status");
    const running = renderStatus();
    setTimeout(loadStatus, running ? 5000 : 60000);
  } catch {
    $("#status-content").replaceChildren(el("p", { class: "notice error" }, "Der Datenstand konnte nicht geladen werden."));
    setTimeout(loadStatus, 15000);
  }
}

// --- Formulare -------------------------------------------------------------------
function fillOptions() {
  const o = state.options;
  const appl = $("#applicant-options");
  Object.entries(o.applicants).forEach(([k, label]) => {
    appl.append(el("label", {}, el("input", { type: "radio", name: "applicant", value: k, required: true }), label));
  });
  appl.addEventListener("change", () => {
    $("#employees-field").hidden = document.querySelector('input[name="applicant"]:checked')?.value !== "unternehmen";
  });
  const land = $("#f-land");
  land.append(el("option", { value: "" }, "Bitte wählen"));
  Object.entries(o.bundeslaender).forEach(([k, v]) => land.append(el("option", { value: k }, v)));
  const topic = $("#f-topic");
  Object.entries(o.topics).forEach(([k, v]) => topic.append(el("option", { value: k }, v)));
  const grid = $("#land-checkboxes");
  Object.entries(o.bundeslaender).forEach(([k, v]) => {
    grid.append(el("label", {}, el("input", { type: "checkbox", name: "lands", value: k }), v));
  });
  const all = $("#a-bundesweit");
  all.addEventListener("change", () => { if (all.checked) grid.querySelectorAll("input").forEach((i) => { i.checked = false; }); });
  grid.addEventListener("change", () => { all.checked = !grid.querySelector("input:checked"); });
}

function showError(id, msg) {
  const box = $(id);
  box.textContent = msg || "";
  box.hidden = !msg;
}
function setBusy(form, busy, label) {
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = busy;
  if (!btn.dataset.label) btn.dataset.label = btn.textContent;
  btn.replaceChildren(busy ? el("span", { class: "spinner", "aria-hidden": "true" }) : "", busy ? label : btn.dataset.label);
}
function loading(container, text) {
  container.replaceChildren(el("p", { class: "notice", role: "status" }, el("span", { class: "spinner", "aria-hidden": "true" }), text));
}
const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

function foerderPayload() {
  const f = $("#form-foerderung");
  const applicant = f.querySelector('input[name="applicant"]:checked')?.value;
  const description = $("#f-description").value.trim();
  const land = $("#f-land").value;
  if (!applicant) return { error: "Bitte wähle aus, wer den Antrag stellt." };
  if (!land) return { error: "Bitte wähle ein Bundesland." };
  if (description.length < 3) return { error: "Bitte beschreibe kurz, was du umsetzen möchtest." };
  return {
    applicant, land, description,
    topic: $("#f-topic").value || null,
    budget: num($("#f-budget").value),
    employees: applicant === "unternehmen" ? num($("#f-employees").value) : null,
    started: f.querySelector('input[name="started"]:checked')?.value || "unbekannt",
  };
}
function auftragPayload() {
  const offering = $("#a-offering").value.trim();
  if (offering.length < 3) return { error: "Bitte beschreibe deine Leistungen oder Produkte." };
  const lands = [...document.querySelectorAll('#land-checkboxes input:checked')].map((i) => i.value);
  return {
    offering, lands,
    keywords: $("#a-keywords").value.trim() || null,
    cpv: $("#a-cpv").value.trim() || null,
    qualifications: $("#a-qualifications").value.trim() || null,
  };
}

async function runFoerder(payload, filters) {
  const box = $("#foerder-results");
  const form = $("#form-foerderung");
  const body = { ...payload, ...(filters || {}) };
  state.lastFoerder = { payload, filters: filters || {} };
  setBusy(form, true, "Suche läuft …");
  loading(box, "Förderprogramme werden durchsucht …");
  try {
    const res = await api("/api/foerderung/suche", body);
    renderFoerderResults(res, body);
  } catch (e) {
    box.replaceChildren(el("p", { class: "notice error" }, e.message));
  } finally { setBusy(form, false); }
}
async function runAuftrag(payload, filters) {
  const box = $("#auftrag-results");
  const form = $("#form-auftraege");
  const body = { ...payload, ...(filters || {}) };
  state.lastAuftrag = { payload, filters: filters || {} };
  setBusy(form, true, "Suche läuft …");
  loading(box, "Bekanntmachungen werden durchsucht …");
  try {
    const res = await api("/api/auftraege/suche", body);
    renderAuftragResults(res, body);
  } catch (e) {
    box.replaceChildren(el("p", { class: "notice error" }, e.message));
  } finally { setBusy(form, false); }
}

// --- Ergebnisanzeige: Förderung ---------------------------------------------------
function select(id, label, options, value) {
  const s = el("select", { id }, options.map(([v, t]) => el("option", { value: v, selected: v === (value || "") }, t)));
  return el("div", { class: "field" }, el("label", { for: id }, label), s);
}
function textFilter(id, label, value) {
  return el("div", { class: "field" }, el("label", { for: id }, label), el("input", { id, type: "text", value: value || "", maxlength: "200" }));
}
function excludedNote(ex, labels) {
  const parts = Object.entries(ex || {}).filter(([, n]) => n > 0).map(([k, n]) => `${n} wegen ${labels[k] || k}`);
  return parts.length ? el("p", { class: "muted small" }, "Ausgeschlossen: " + parts.join(", ") + ".") : null;
}
function explainBlock(why, check) {
  return el("div", { class: "explain" },
    el("section", {}, el("h4", {}, "Warum wird dieser Treffer angezeigt?"), list(why)),
    el("section", {}, el("h4", {}, "Das musst du noch prüfen"), list(check)));
}
function renderPaged(container, items, renderItem) {
  let shown = 0;
  const listBox = el("div", {});
  const more = el("button", { type: "button", class: "btn" }, "Weitere Treffer anzeigen");
  const step = () => {
    items.slice(shown, shown + PAGE).forEach((it) => listBox.append(renderItem(it)));
    shown += PAGE;
    more.hidden = shown >= items.length;
  };
  more.addEventListener("click", step);
  container.append(listBox, more);
  step();
}

function renderFoerderResults(res, body) {
  const box = $("#foerder-results");
  const o = state.options;
  const filterBar = el("form", { class: "card filters", "aria-label": "Ergebnisse filtern" },
    select("ff-land", "Bundesland", Object.entries(o.bundeslaender), body.land),
    select("ff-type", "Förderart", [["", "Alle"], ...Object.entries(o.funding_types)], body.funding_type),
    textFilter("ff-keyword", "Stichwort", body.keyword),
    el("button", { type: "submit", class: "btn" }, "Filter anwenden"));
  filterBar.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = { ...state.lastFoerder.payload, land: $("#ff-land").value };
    $("#f-land").value = p.land;
    runFoerder(p, { funding_type: $("#ff-type").value || null, keyword: $("#ff-keyword").value.trim() || null });
  });
  const head = el("div", { class: "results-head" },
    el("h2", {}, `${res.total.toLocaleString("de-DE")} mögliche Förderprogramme`),
    el("span", { class: "muted small" }, "Stand der Daten: ", fmtDate(res.last_update)));
  const children = [head,
    el("p", { class: "notice" }, "Dies ist eine Vorauswahl auf Basis der Förderdatenbank – keine Förderzusage. Maßgeblich sind die Richtlinien und die Auskunft der Fördergeber."),
    filterBar,
    excludedNote(res.excluded, { region: "Fördergebiet", applicant: "Antragsteller", company_size: "Unternehmensgröße (ab 250 Beschäftigten kein KMU)", funding_type: "Förderart", keyword: "Stichwort" })];
  box.replaceChildren(...children.filter(Boolean));
  if (!res.data_available) {
    box.append(el("p", { class: "notice warn" }, "Es sind noch keine Förderprogramme importiert. Bitte später erneut versuchen oder den Datenstand prüfen."));
    return;
  }
  if (!res.total) {
    box.append(el("div", { class: "notice warn" },
      el("strong", {}, "Keine passenden Förderprogramme gefunden."),
      list(["Beschreibe dein Vorhaben mit allgemeineren Begriffen (z. B. „Heizung“ statt Produktname).",
        "Entferne den Themenbereich oder die Filter für Förderart und Stichwort.",
        "Prüfe, ob der gewählte Antragstellertyp passt.",
        "Bei Unternehmen ab 250 Beschäftigten kommen KMU-Programme nicht in Frage."])));
    return;
  }
  renderPaged(box, res.results, foerderCard);
}

function foerderCard(r) {
  const funder = r.funder.length ? r.funder.join("; ") : "Nicht angegeben";
  const level = r.funder_level.length ? ` (${r.funder_level.join(", ")})` : "";
  return el("article", { class: "card result" },
    el("div", { class: "result-top" },
      el("h3", {}, r.title),
      el("div", { class: "badges" }, r.funding_types.map((t) => el("span", { class: "badge" }, t)))),
    el("p", {}, r.description),
    el("dl", { class: "facts" },
      el("div", {}, el("dt", {}, "Fördergeber"), el("dd", {}, funder + level)),
      el("div", {}, el("dt", {}, "Fördergebiet"), el("dd", {}, r.regions.join(", "))),
      el("div", {}, el("dt", {}, "Förderart"), el("dd", {}, r.funding_types.join(", "))),
      el("div", {}, el("dt", {}, "Frist / Status"), el("dd", {}, "Frist: Nicht angegeben · ", r.status))),
    el("div", {}, el("dt", { class: "small muted" }, "Förderhöhe (Originalauszug)"),
      r.amount_excerpts.length ? r.amount_excerpts.map((t) => el("p", { class: "excerpt" }, t)) : el("p", { class: "small" }, "Nicht angegeben")),
    explainBlock(r.why, r.check),
    el("div", { class: "result-actions" },
      el("button", { type: "button", class: "btn", onclick: () => openFoerderDetail(r.id) }, "Details"),
      extLink(r.source_url, "Zur Originalquelle (Förderdatenbank)"),
      el("span", { class: "muted small" }, "Quelle: Förderdatenbank des Bundes, BMWE, CC BY 4.0")));
}

// --- Ergebnisanzeige: Aufträge ---------------------------------------------------
function statusBadge(r) {
  const cls = r.status === "offen" ? "badge" : (r.status === "frist_unbekannt" ? "badge warn" : "badge danger");
  return el("span", { class: cls }, r.status_label);
}
function renderAuftragResults(res, body) {
  const box = $("#auftrag-results");
  const o = state.options;
  const filterBar = el("form", { class: "card filters", "aria-label": "Ergebnisse filtern" },
    select("af-land", "Bundesland", [["", "Alle aus dem Einsatzgebiet"], ...Object.entries(o.bundeslaender)], body.land_filter),
    textFilter("af-keyword", "Stichwort", body.keyword),
    select("af-status", "Status", [["teilnahme", "Teilnahme möglich (Standard)"], ["offen", "Nur bekannte offene Fristen"], ["alle", "Alle Bekanntmachungen"]], body.status),
    el("button", { type: "submit", class: "btn" }, "Filter anwenden"));
  filterBar.addEventListener("submit", (e) => {
    e.preventDefault();
    runAuftrag(state.lastAuftrag.payload, { land_filter: $("#af-land").value || null, keyword: $("#af-keyword").value.trim() || null, status: $("#af-status").value });
  });
  const cov = res.coverage || {};
  const head = el("div", { class: "results-head" },
    el("h2", {}, `${res.total.toLocaleString("de-DE")} passende Verfahren`),
    el("span", { class: "muted small" }, "Stand der Daten: ", fmtDate(res.last_update)));
  const children = [head,
    el("p", { class: "notice" }, `Berücksichtigt werden Bekanntmachungen, die vom ${fmtDay(cov.first_day)} bis ${fmtDay(cov.last_day)} beim Bekanntmachungsservice veröffentlicht wurden – keine vollständige Abdeckung. Die Ergebnisse sind eine Vorauswahl, keine bestätigte Teilnahmeberechtigung.`),
    filterBar,
    excludedNote(res.excluded, { status: "Status/Frist", region: "Ausführungsort", keyword: "Stichwort" })];
  box.replaceChildren(...children.filter(Boolean));
  if (!res.data_available) {
    box.append(el("p", { class: "notice warn" }, "Es sind noch keine Bekanntmachungen importiert. Bitte später erneut versuchen oder den Datenstand prüfen."));
    return;
  }
  if (!res.total) {
    box.append(el("div", { class: "notice warn" },
      el("strong", {}, "Keine passenden Verfahren gefunden."),
      list(["Verwende allgemeinere Begriffe oder ergänze Suchbegriffe.",
        "Wähle „Bundesweit“ oder weitere Bundesländer.",
        "Gib einen kürzeren CPV-Code an (z. B. 909 statt 90911200).",
        "Stelle den Status auf „Alle Bekanntmachungen“, um auch Ergebnisse und abgelaufene Verfahren zu sehen."])));
    return;
  }
  renderPaged(box, res.results, auftragCard);
}

function auftragCard(r) {
  const deadlines = r.deadlines.length
    ? el("ul", { class: "small" }, r.deadlines.slice(0, 6).map((d) => el("li", {},
      `${r.lot_count > 1 ? (d.lot_title || d.lot) + ": " : ""}${d.kind} ${d.text}${d.expired ? " (abgelaufen)" : ""}`)),
      r.deadlines.length > 6 ? el("li", {}, `… weitere ${r.deadlines.length - 6} Fristen in den Details`) : null)
    : el("span", {}, "Frist nicht angegeben");
  return el("article", { class: "card result" },
    el("div", { class: "result-top" },
      el("h3", {}, r.title),
      el("div", { class: "badges" }, statusBadge(r), el("span", { class: "badge" }, r.category_label))),
    el("p", {}, r.description),
    el("dl", { class: "facts" },
      el("div", {}, el("dt", {}, "Öffentlicher Auftraggeber"), el("dd", {}, r.buyer + (r.buyer_city ? ` (Sitz: ${r.buyer_city})` : ""))),
      el("div", {}, el("dt", {}, "Ausführungsort"), el("dd", {}, r.places.join("; "))),
      el("div", {}, el("dt", {}, "Art / Auftragswert"), el("dd", {}, `${r.nature} · ${r.value}`)),
      el("div", {}, el("dt", {}, `Frist${r.lot_count > 1 ? "en (" + r.lot_count + " Lose)" : ""}`), el("dd", {}, deadlines))),
    explainBlock(r.why, r.check),
    el("div", { class: "result-actions" },
      el("button", { type: "button", class: "btn", onclick: () => openAuftragDetail(r.id) }, "Details"),
      extLink(r.notice_url, "Bekanntmachung (Original)"),
      extLink(r.document_url, "Vergabeunterlagen"),
      el("span", { class: "muted small" }, `Veröffentlicht: ${fmtDay(r.published)}`),
      r.duplicates && r.duplicates.length ? el("span", { class: "muted small" }, `Auch ${r.duplicates.length}× identisch veröffentlicht`) : null));
}

// --- Details --------------------------------------------------------------------
function section(title, ...content) {
  return el("section", {}, el("h3", {}, title), ...content);
}
function textBlock(text) {
  return text ? el("div", { class: "pre" }, text) : el("p", { class: "muted" }, "Nicht angegeben");
}
async function openDialog(title, loader) {
  const dlg = $("#detail");
  $("#detail-title").textContent = title;
  const body = $("#detail-body");
  body.replaceChildren(el("p", {}, el("span", { class: "spinner", "aria-hidden": "true" }), "Details werden geladen …"));
  if (!dlg.open) dlg.showModal();
  try { body.replaceChildren(...(await loader()).filter(Boolean)); } catch (e) { body.replaceChildren(el("p", { class: "notice error" }, e.message)); }
}
function openFoerderDetail(id) {
  openDialog("Förderprogramm", async () => {
    const d = await api("/api/foerderung/detail?id=" + encodeURIComponent(id));
    $("#detail-title").textContent = d.title;
    return [
      d.active ? null : el("p", { class: "notice warn" }, "Dieses Programm ist im aktuellen Export nicht mehr enthalten."),
      el("dl", { class: "facts" },
        el("div", {}, el("dt", {}, "Fördergeber"), el("dd", {}, (d.funder.join("; ") || "Nicht angegeben") + (d.funder_level.length ? ` (${d.funder_level.join(", ")})` : ""))),
        el("div", {}, el("dt", {}, "Fördergebiet"), el("dd", {}, d.regions.join(", ") || "Nicht angegeben")),
        el("div", {}, el("dt", {}, "Förderart"), el("dd", {}, d.funding_types.join(", ") || "Nicht angegeben")),
        el("div", {}, el("dt", {}, "Förderberechtigte"), el("dd", {}, d.eligible.join(", ") || "Nicht angegeben")),
        el("div", {}, el("dt", {}, "Förderbereich"), el("dd", {}, d.areas.join(", ") || "Nicht angegeben")),
        el("div", {}, el("dt", {}, "Unternehmensgröße"), el("dd", {}, d.company_sizes.join(", ") || "Nicht angegeben"))),
      section("Kurzzusammenfassung", textBlock(d.summary || d.teaser)),
      section("Voraussetzungen (laut Quelle)", textBlock(d.eligibility)),
      section("Rechtsgrundlage / Richtlinie", el("details", {}, el("summary", {}, "Volltext anzeigen"), textBlock(d.legal_text))),
      d.procedure_text ? section("Verfahren", textBlock(d.procedure_text)) : null,
      section("Ansprechpartner", d.contacts.length ? el("ul", {}, d.contacts.map((c) => el("li", {},
        [c.name, c.unit, c.email, c.phone].filter(Boolean).join(" · ")))) : el("p", { class: "muted" }, "Nicht angegeben")),
      section("Weiterführende Links (laut Quelle)", d.links.length ? el("ul", {}, d.links.map((l) => el("li", {}, extLink(l.url, l.title)))) : el("p", { class: "muted" }, "Nicht angegeben")),
      section("Quelle", el("p", {}, extLink(d.source_url, "Originaleintrag in der Förderdatenbank")),
        el("p", { class: "muted small" }, `Quellkennung: ${d.source_id} · Datenstand: ${fmtDate(d.last_update)} · Quelle: Förderdatenbank des Bundes, BMWE, CC BY 4.0`)),
    ];
  });
}
function openAuftragDetail(id) {
  openDialog("Verfahren", async () => {
    const d = await api("/api/auftraege/detail?id=" + encodeURIComponent(id));
    $("#detail-title").textContent = d.title || "Verfahren";
    return [
      el("dl", { class: "facts" },
        el("div", {}, el("dt", {}, "Status"), el("dd", {}, d.status_label)),
        el("div", {}, el("dt", {}, "Bekanntmachungsart"), el("dd", {}, `${d.category_label} (${d.notice_type})`)),
        el("div", {}, el("dt", {}, "Öffentlicher Auftraggeber"), el("dd", {}, (d.buyer || "Nicht angegeben") + (d.buyer_city ? ` (Sitz: ${d.buyer_city})` : ""))),
        el("div", {}, el("dt", {}, "Ausführungsort"), el("dd", {}, d.places.join("; "))),
        el("div", {}, el("dt", {}, "Art / Auftragswert"), el("dd", {}, `${d.nature} · ${d.value}`)),
        el("div", {}, el("dt", {}, "CPV-Codes"), el("dd", {}, d.cpv.join(", ") || "Nicht angegeben"))),
      d.change_reason ? el("p", { class: "notice warn" }, "Änderung/Berichtigung: ", d.change_reason) : null,
      section("Beschreibung", textBlock(d.description)),
      section(`Lose (${d.lots.length})`, d.lots.map((l) => el("div", { class: "lot" },
        el("h3", {}, `${l.id}${l.title ? ": " + l.title : ""}`),
        el("dl", { class: "facts" },
          el("div", {}, el("dt", {}, "Angebotsfrist"), el("dd", {}, l.tender_deadline)),
          l.participation_deadline ? el("div", {}, el("dt", {}, "Teilnahmefrist"), el("dd", {}, l.participation_deadline)) : null,
          el("div", {}, el("dt", {}, "Ausführungsort"), el("dd", {}, l.places.join("; "))),
          el("div", {}, el("dt", {}, "Auftragswert"), el("dd", {}, l.value))),
        l.description ? el("details", {}, el("summary", {}, "Losbeschreibung"), textBlock(l.description)) : null,
        l.criteria.length ? el("details", {}, el("summary", {}, `Eignungskriterien (${l.criteria.length})`),
          el("ul", {}, l.criteria.map((c) => el("li", {}, el("strong", {}, c.label), c.description ? el("div", { class: "pre small" }, c.description) : null)))) : el("p", { class: "muted small" }, "Eignungskriterien: Nicht angegeben"),
        l.document_url ? el("p", {}, extLink(l.document_url, "Vergabeunterlagen zu diesem Los")) : null))),
      section("Bekannte Bekanntmachungen zu diesem Verfahren", el("ul", {}, d.history.map((h) => el("li", {},
        `${fmtDay(h.pub_day)} · ${h.category_label} (${h.notice_type})${h.superseded ? " – durch spätere Bekanntmachung ersetzt" : ""} · `,
        extLink(h.notice_url, "Original"), " · ", extLink(h.xml_url, "XML"))))),
      section("Quelle", el("p", {}, extLink(d.notice_url, "Bekanntmachung beim Bekanntmachungsservice"), d.document_url ? " · " : "", extLink(d.document_url, "Vergabeunterlagen")),
        el("p", { class: "muted small" }, `Quellkennung: ${d.source_id} · Verfahren: ${d.id} · Datenstand: ${fmtDate(d.last_update)}`)),
    ];
  });
}

// --- Start ------------------------------------------------------------------------
async function init() {
  window.addEventListener("hashchange", route);
  route();
  $("#detail-close").addEventListener("click", () => $("#detail").close());
  $("#detail").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  loadStatus();
  try {
    state.options = await api("/api/options");
    fillOptions();
  } catch {
    $("#foerder-error").textContent = "Die Anwendung konnte nicht geladen werden. Bitte Seite neu laden.";
    $("#foerder-error").hidden = false;
    return;
  }
  $("#form-foerderung").addEventListener("submit", (e) => {
    e.preventDefault();
    const p = foerderPayload();
    showError("#foerder-error", p.error);
    if (!p.error) runFoerder(p, {});
  });
  $("#form-auftraege").addEventListener("submit", (e) => {
    e.preventDefault();
    const p = auftragPayload();
    showError("#auftrag-error", p.error);
    if (!p.error) runAuftrag(p, { status: "teilnahme" });
  });
}
init();
