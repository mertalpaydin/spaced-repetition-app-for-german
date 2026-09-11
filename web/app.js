// Phrasen: the browser page for the local server (src/cli/serve.py).
// Grading and scheduling live in Python; this file only draws and posts.

const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const res = await fetch(path, body === undefined
    ? { cache: "no-store" }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// -- theme --------------------------------------------------------------------

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme; else delete document.documentElement.dataset.theme;
  try { if (theme) localStorage.setItem("theme", theme); else localStorage.removeItem("theme"); } catch (e) { /* private mode */ }
}
try { const saved = localStorage.getItem("theme"); if (saved) applyTheme(saved); } catch (e) { /* ignore */ }
$("btn-theme").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme === "dark"
    || (!document.documentElement.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
  applyTheme(dark ? "light" : "dark");
});

// -- views --------------------------------------------------------------------

function show(view) {
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  document.querySelectorAll("nav button[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "practice") loadCard();
  if (view === "triage") loadTriage();
  if (view === "units") loadUnits();
  if (view === "stats") loadStats();
}
document.querySelectorAll("nav button[data-view]").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

function renderToday(t) {
  if (!t) return;
  $("today").textContent = `heute ${t.done}/${t.target} · fällig ${t.due}`;
}

// -- practice -----------------------------------------------------------------

let current = null;   // {unit, card}
let startedAt = 0;
let answered = false;
let overLimit = false;

function renderSentence(card) {
  const el = $("sentence");
  el.textContent = "";
  let last = 0;
  card.gaps.forEach((g, i) => {
    el.append(card.sentence.slice(last, g.start));
    const input = document.createElement("input");
    input.className = "gap";
    input.dataset.index = String(i);
    input.setAttribute("lang", "de");
    input.autocapitalize = "off";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.enterKeyHint = i === card.gaps.length - 1 ? "done" : "next";
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        const next = el.querySelector(`input[data-index="${i + 1}"]`);
        if (next && !answered) next.focus(); else if (!answered) check(); else nextCard();
      }
    });
    el.append(input);
    last = g.end;
  });
  el.append(card.sentence.slice(last));
}

function showPanel(which) {
  $("practice-card").hidden = which !== "card";
  $("practice-limit").hidden = which !== "limit";
  $("practice-empty").hidden = which !== "empty";
  $("history").hidden = which !== "history";
}

async function loadCard() {
  answered = false;
  $("feedback").hidden = true;
  $("btn-next").hidden = true;
  ["btn-check", "btn-reveal", "btn-known"].forEach((id) => { $(id).hidden = false; });
  const data = await api(`/api/next${overLimit ? "?over=1" : ""}`);
  renderToday(data.today);
  if (data.done) { showPanel("empty"); return; }
  if (data.limit_reached) { showPanel("limit"); return; }
  current = data;
  showPanel("card");
  $("context-de").textContent = data.card.context_de || "";
  $("context-de").hidden = !data.card.context_de;
  $("context-en").textContent = data.card.context_en || "";
  $("context-en").hidden = !data.card.context_en;
  $("gloss").textContent = data.card.gloss || "";
  renderSentence(data.card);
  startedAt = performance.now();
  const first = $("sentence").querySelector("input");
  if (first) first.focus();
}

function typedValues(reveal) {
  return Array.from($("sentence").querySelectorAll("input.gap")).map((inp) => reveal ? null : inp.value);
}

async function check(reveal = false) {
  if (!current || answered) return;
  answered = true;
  const typed = typedValues(reveal);
  const result = await api("/api/answer", {
    card_id: current.card.card_id, typed, elapsed_ms: Math.round(performance.now() - startedAt),
  });
  const inputs = $("sentence").querySelectorAll("input.gap");
  result.gaps.forEach((g, i) => {
    const inp = inputs[i];
    inp.value = g.expected;
    inp.readOnly = true;
    inp.classList.add(g.ok ? (g.outcome === "typo" ? "typo" : "ok") : "bad");
  });
  const fb = $("feedback");
  fb.className = `feedback ${result.rating}`;
  const verdict = { good: "Richtig", hard: "Richtig, mit Tippfehler", again: "Falsch" }[result.rating];
  const wrong = result.gaps.filter((g) => !g.ok || g.outcome === "typo")
    .map((g) => `${g.typed || "(gezeigt)"} → ${g.expected}`).join(", ");
  fb.innerHTML = `<div>${verdict}${wrong ? ": " + escapeHtml(wrong) : ""}</div>` +
    `<div class="unit">${escapeHtml(result.unit.display)}` +
    (result.unit.gloss ? ` <span class="muted">= ${escapeHtml(result.unit.gloss)}</span>` : "") + `</div>`;
  fb.hidden = false;
  ["btn-check", "btn-reveal", "btn-known"].forEach((id) => { $(id).hidden = true; });
  $("btn-next").hidden = false;
  $("btn-next").focus();
  api("/api/next").then((d) => renderToday(d.today)).catch(() => {});
}

async function markKnown() {
  if (!current) return;
  await api("/api/mark", { unit_id: current.unit.unit_id, known: true, source: "practice" });
  loadCard();
}

function nextCard() { loadCard(); }

async function showHistory() {
  const data = await api("/api/history");
  const list = $("history-list");
  list.innerHTML = data.items.map((h) =>
    `<li><span class="tag ${h.rating}">${h.rating === "good" ? "richtig" : h.rating === "hard" ? "Tippfehler" : "falsch"}</span>` +
    `${escapeHtml(h.marked || "")}` +
    (h.gloss ? `<br><span class="muted">${escapeHtml(h.gloss)}</span>` : "") +
    `<br><strong>${escapeHtml(h.unit.display)}</strong>` +
    (h.unit.gloss ? ` <span class="muted">= ${escapeHtml(h.unit.gloss)}</span>` : "") +
    (h.rating !== "good" ? `<br><span class="muted">getippt: ${escapeHtml(h.answers.map((a) => a || "(gezeigt)").join(", "))}</span>` : "") +
    `</li>`).join("") || "<li>Noch keine Antworten.</li>";
  showPanel("history");
}

$("btn-check").addEventListener("click", () => check(false));
$("btn-reveal").addEventListener("click", () => check(true));
$("btn-known").addEventListener("click", markKnown);
$("btn-next").addEventListener("click", nextCard);
$("btn-prev").addEventListener("click", showHistory);
$("btn-prev-2").addEventListener("click", showHistory);
$("btn-history-close").addEventListener("click", () => loadCard());
$("btn-more").addEventListener("click", () => { overLimit = true; loadCard(); });
document.querySelectorAll(".umlauts button").forEach((b) => b.addEventListener("click", () => {
  const active = document.activeElement;
  const target = active && active.classList.contains("gap") ? active : $("sentence").querySelector("input.gap");
  if (!target || target.readOnly) return;
  const pos = target.selectionStart ?? target.value.length;
  target.value = target.value.slice(0, pos) + b.dataset.ch + target.value.slice(pos);
  target.focus();
  target.setSelectionRange(pos + 1, pos + 1);
}));

// -- triage -------------------------------------------------------------------

let triageQueue = [];

async function loadTriage() {
  const data = await api("/api/triage?batch=50");
  triageQueue = data.units;
  showTriage();
}

function showTriage() {
  const unit = triageQueue[0];
  $("triage-card").hidden = !unit;
  $("triage-empty").hidden = !!unit;
  if (!unit) return;
  $("triage-rank").textContent = `Rang ${unit.rank} · ${unit.kind} · ${unit.cefr || "-"}`;
  $("triage-display").textContent = unit.display + (unit.gloss ? `  (${unit.gloss})` : "");
}

async function triageAnswer(known) {
  const unit = triageQueue.shift();
  if (!unit) return;
  await api("/api/mark", { unit_id: unit.unit_id, known, source: "triage" });
  if (triageQueue.length === 0) loadTriage(); else showTriage();
}
$("btn-tri-known").addEventListener("click", () => triageAnswer(true));
$("btn-tri-learn").addEventListener("click", () => triageAnswer(false));
document.addEventListener("keydown", (ev) => {
  if ($("view-triage").hidden) return;
  if (ev.key === "1") triageAnswer(true);
  if (ev.key === "2") triageAnswer(false);
});

// -- units --------------------------------------------------------------------

function fmtDue(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const mins = Math.round((d - Date.now()) / 60000);
  if (mins < 1) return "jetzt";
  if (mins < 60) return `in ${mins} min`;
  if (mins < 60 * 36) return `in ${Math.round(mins / 60)} h`;
  return `in ${Math.round(mins / 1440)} Tagen`;
}

async function loadUnits() {
  const g = await api("/api/units");
  const section = (title, items, withDue) =>
    `<h3>${title} (${items.length})</h3>` + (items.length
      ? `<table>${items.map((u) => `<tr><td>${escapeHtml(u.display)}${u.gloss ? ` <span class="muted">${escapeHtml(u.gloss)}</span>` : ""}</td>` +
        `<td class="num muted">${withDue ? fmtDue(u.due) : ""}</td></tr>`).join("")}</table>`
      : `<p class="muted">–</p>`);
  $("units").innerHTML =
    section("Lernend", g.learning, true) + section("Jung", g.young, true) +
    section("Reif", g.mature, true) + section("Bekannt", g.known, false);
}

// -- stats --------------------------------------------------------------------

async function loadStats() {
  const s = await api("/api/stats");
  const rows = [
    ["bekannt", s.known], ["lernend", s.learning], ["jung", s.young], ["reif", s.mature],
    ["fällig jetzt", s.due_now], ["neu verfügbar", s.new_remaining],
    ["heute", s.reviews_today], ["gesamt", s.reviews_total], ["Serie (Tage)", s.streak_days],
    ["Behalten, 30 Tage", s.retention_30d === null ? "-" : `${Math.round(s.retention_30d * 100)} %`],
  ];
  let html = "<table>" + rows.map(([k, v]) => `<tr><td>${k}</td><td class="num">${v}</td></tr>`).join("") + "</table>";
  html += "<h3>Abdeckung nach Rang</h3><table>" + s.coverage.map((c) =>
    `<tr><td>${c.band}–${c.band + 499}</td><td class="num">${c.seen} / ${c.total}</td></tr>`).join("") + "</table>";
  $("stats").innerHTML = html;
}

// -- quit ---------------------------------------------------------------------

$("btn-quit").addEventListener("click", async () => {
  if (!confirm("Server beenden? Alles ist bereits gespeichert.")) return;
  try { await api("/api/quit", {}); } catch (e) { /* server is gone */ }
  document.body.innerHTML = "<main><p>Server beendet. Fenster schließen.</p></main>";
});

// -- boot ---------------------------------------------------------------------

api("/api/deck").then((d) => {
  $("deck-info").textContent = `Deck ${d.deck_version} · ${d.units} Einheiten · ${d.glossed_cards} von ${d.cards} Karten mit Übersetzung`;
}).catch(() => {});
show("practice");
