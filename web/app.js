// Phrasen: the page. Everything runs in the browser: the deck is fetched from
// ./data/deck, grading and scheduling are web/lib (ports of src/engine), the
// review log lives in IndexedDB and, when a token is set, in a private GitHub
// Gist so every device replays the same log. No server, no build step.

import { loadDeck, loadReserveIndex, lookupReserve, withAdded } from "./lib/deck.js";
import { createEngine } from "./lib/engine.js";
import { gradeCard, renderMarked } from "./lib/grader.js";
import { deriveState, entriesSinceReset, makeMark, makeRequest, makeReset, makeReview, mergeEntries, parseJsonl, toJsonl } from "./lib/log.js";
import { QuotaExhaustedError, callSummary, writeCards } from "./lib/gemini.js";
import {
  DEFAULT_SETTINGS, budgetLeft, computeStats, dueUnits, nextUnit, pickCard, reviewsToday, unitsByStage, untriagedUnits,
} from "./lib/session.js";
import { appendEntries, kv, loadSettings, readLog, replaceLog, saveSettings } from "./lib/store.js";
import { syncLog } from "./lib/sync.js";

const $ = (id) => document.getElementById(id);
const now = () => new Date();
//: which of the new units on offer to introduce next; see session.nextUnit.
const pickAny = (n) => Math.floor(Math.random() * n);

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// -- state --------------------------------------------------------------------

const settings = { ...DEFAULT_SETTINGS, token: "", gistId: "", autoSync: true, geminiKey: "", ...loadSettings() };
// The deck as exported, and the deck the rest of the page sees: the second
// is the first plus whatever the learner added from this screen. Kept apart
// so a new request entry only has to refold, never refetch.
let baseDeck = null;
let deck = null;
let reserveIndex = null;
let engine = createEngine({ retention: settings.retention });
let entries = [];
let state = null;
let overLimit = false;

function refresh() {
  state = deriveState(entries, engine);
  if (baseDeck) deck = withAdded(baseDeck, state);
}

async function record(entry) {
  entries.push(entry);
  await appendEntries([entry]);
  refresh();
  if (settings.token && unpushed()) setSyncStatus(`${unpushed()} ungesichert`, "busy");
  if (settings.autoSync && settings.token) scheduleSync();
}

function unitPayload(unit) {
  return { unit_id: unit.unit_id, kind: unit.kind, display: unit.display_de + (unit.case ? ` +${unit.case}` : ""), rank: unit.rank, cefr: unit.cefr, gloss: unit.gloss_en };
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

// -- sync ---------------------------------------------------------------------

let syncTimer = null;
let syncing = false;

function setSyncStatus(text, cls = "") {
  $("sync-status").textContent = text;
  $("sync-status").className = `sync ${cls}`;
  const el = $("settings-sync-status");
  if (el) el.textContent = text;
}

function scheduleSync() {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => sync().catch(() => {}), 1500);
}

// How many entries have never reached the gist. Shown in the status line,
// because "gesichert 14:32" said nothing about the twelve answers given
// since, and a tab closed inside the debounce window pushed none of them:
// the owner reloaded the page repeatedly to force a sync he could not see
// the state of (2026-10-06). Nothing was ever lost locally; appendEntries
// awaits the IndexedDB write before record() returns.
let pushedCount = 0;

function unpushed() { return Math.max(0, entries.length - pushedCount); }

// A tab going away is the last chance to push. visibilitychange fires on
// mobile where pagehide and beforeunload do not, so both are wired; the
// debounce is skipped because there may be no next tick.
function flushBeforeUnload() {
  if (!settings.token || !unpushed()) return;
  clearTimeout(syncTimer);
  sync().catch(() => {});
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushBeforeUnload();
});
window.addEventListener("pagehide", flushBeforeUnload);

async function sync() {
  if (!settings.token) { setSyncStatus("lokal", "off"); return; }
  if (syncing) { scheduleSync(); return; }
  syncing = true;
  setSyncStatus("sync…", "busy");
  try {
    const r = await syncLog(settings, entries);
    if (settings.gistId) saveSettings(settings);
    if (r.pulled > 0) { entries = r.merged; await replaceLog(entries); refresh(); }
    // Everything that was in the merge is now in the gist, pushed by this
    // call or already there.
    pushedCount = r.merged.length;
    const at = new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    setSyncStatus(`gesichert ${at}`, "ok");
    return r;
  } catch (err) {
    const pending = unpushed() ? ` (${unpushed()} ungesichert)` : "";
    setSyncStatus(
      (navigator.onLine ? `nicht gesichert: ${err.message}` : "offline, später") + pending, "bad",
    );
    throw err;
  } finally {
    syncing = false;
  }
}

// -- views --------------------------------------------------------------------

function show(view) {
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  document.querySelectorAll("nav button[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "practice") loadCard();
  if (view === "triage") loadTriage();
  if (view === "units") loadUnits();
  if (view === "stats") loadStats();
  if (view === "settings") loadSettingsView();
}
document.querySelectorAll("nav button[data-view]").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

function renderToday() {
  const t = now();
  const due = dueUnits(deck, state, engine, t, 0).length;
  $("today").textContent = `heute ${reviewsToday(state, t)}/${settings.cardsPerDay} · fällig ${due}`;
}

// -- practice -----------------------------------------------------------------

let current = null;   // {unit, card}
let startedAt = 0;
let answered = false;

function renderSentence(card) {
  const el = $("sentence");
  el.textContent = "";
  let last = 0;
  card.gaps.forEach((g, i) => {
    el.append(card.sentence_de.slice(last, g.start));
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
  el.append(card.sentence_de.slice(last));
}

function showPanel(which) {
  $("practice-card").hidden = which !== "card";
  $("practice-limit").hidden = which !== "limit";
  $("practice-empty").hidden = which !== "empty";
  $("history").hidden = which !== "history";
}

function loadCard() {
  answered = false;
  $("feedback").hidden = true;
  $("btn-next").hidden = true;
  // "Kannte ich schon" belongs after the answer, not before it: offered
  // beforehand it asks the learner to judge a unit they have not been shown
  // (owner, 2026-09-21).
  $("btn-known").hidden = true;
  ["btn-check", "btn-reveal", "btn-defer"].forEach((id) => { $(id).hidden = false; });
  renderToday();
  const t = now();
  let unit = nextUnit(deck, state, engine, settings, t, { overLimit, choose: pickAny });
  if (!overLimit && budgetLeft(state, settings, t) === 0) {
    // The day's budget is spent: stop and ask, unless the unit is mid
    // learning step. Its due time is not consulted any more: nextUnit already
    // decided the unit is eligible, on spacing rather than on the clock
    // (owner, 2026-09-22).
    const record = unit ? state.records[unit.unit_id] : null;
    const midStep = record && record.state !== "review";
    const more = unit || nextUnit(deck, state, engine, settings, t, { overLimit: true });
    if (more && !midStep) {
      const due = dueUnits(deck, state, engine, t, 0).length;
      $("limit-text").textContent = due
        ? `Tagesziel erreicht (${settings.cardsPerDay} Karten). ${due} Einheit${due === 1 ? " ist" : "en sind"} noch fällig.`
        : `Tagesziel erreicht (${settings.cardsPerDay} Karten). Nichts ist mehr fällig; neue Einheiten kommen morgen.`;
      showPanel("limit");
      return;
    }
  }
  const card = unit ? pickCard(deck, unit, state) : null;
  if (!unit || !card) { showPanel("empty"); return; }
  current = { unit, card };
  showPanel("card");
  $("context-de").textContent = card.context_de || "";
  $("context-de").hidden = !card.context_de;
  $("context-en").textContent = card.context_en || "";
  $("context-en").hidden = !card.context_en;
  $("gloss").textContent = card.gloss_en || "";
  // The phrase's own English is shown before the answer (owner's decision,
  // 2026-09-13): the sentence gloss alone did not always pin the phrase.
  $("hint").textContent = unit.gloss_en ? `Gesucht: ${unit.gloss_en}` : "";
  $("hint").hidden = !unit.gloss_en;
  renderSentence(card);
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
  const { card, unit } = current;
  const result = gradeCard(card, typed, unit.also_accepted || []);
  await record(makeReview(entries, {
    unit_id: unit.unit_id, card_id: card.card_id, rating: result.rating, outcome: result.outcome,
    answers: result.gaps.map((g) => g.typed), expected: result.gaps.map((g) => g.expected),
    elapsed_ms: Math.max(0, Math.round(performance.now() - startedAt)), deck_version: deck.manifest.deck_version,
  }, now()));
  const inputs = $("sentence").querySelectorAll("input.gap");
  result.gaps.forEach((g, i) => {
    const inp = inputs[i];
    inp.value = g.expected;
    inp.readOnly = true;
    inp.classList.add(g.accepted ? (g.outcome === "typo" ? "typo" : "ok") : "bad");
  });
  const fb = $("feedback");
  fb.className = `feedback ${result.rating}`;
  const verdict = { good: "Richtig", hard: "Richtig, mit Tippfehler", again: "Falsch" }[result.rating];
  const wrong = result.gaps.filter((g) => !g.accepted || g.outcome === "typo")
    .map((g) => `${g.typed || "(gezeigt)"} → ${g.expected}`).join(", ");
  const synonym = result.gaps.filter((g) => g.accepted && g.typed.toLowerCase() !== g.expected.toLowerCase()
    && g.outcome !== "typo").map((g) => `${g.typed} gilt auch; im Satz: ${g.expected}`).join(", ");
  const u = unitPayload(unit);
  fb.innerHTML = `<div>${verdict}${wrong ? ": " + escapeHtml(wrong) : ""}${synonym ? " (" + escapeHtml(synonym) + ")" : ""}</div>` +
    `<div class="unit">${escapeHtml(u.display)}` +
    (u.gloss ? ` <span class="muted">= ${escapeHtml(u.gloss)}</span>` : "") + `</div>`;
  fb.hidden = false;
  ["btn-check", "btn-reveal", "btn-defer"].forEach((id) => { $(id).hidden = true; });
  $("btn-known").hidden = false;
  $("btn-next").hidden = false;
  $("btn-next").focus();
  renderToday();
}

async function markKnown() {
  if (!current) return;
  await record(makeMark(entries, current.unit.unit_id, true, "practice", now()));
  loadCard();
}

async function defer() {
  if (!current) return;
  await record(makeMark(entries, current.unit.unit_id, true, "defer", now()));
  loadCard();
}

async function relearn(unitId) {
  await record(makeMark(entries, unitId, false, "practice", now()));
  loadUnits();
}

function nextCard() { loadCard(); }

function showHistory() {
  const reviews = entriesSinceReset(entries).filter((e) => e.type === "review").slice(-20).reverse();
  const list = $("history-list");
  list.innerHTML = reviews.map((e) => {
    const card = deck.cardById[e.card_id];
    const unit = deck.byId[e.unit_id];
    const u = unit ? unitPayload(unit) : { display: e.unit_id, gloss: null };
    return `<li><span class="tag ${e.rating}">${e.rating === "good" ? "richtig" : e.rating === "hard" ? "Tippfehler" : "falsch"}</span>` +
      `${escapeHtml(card ? renderMarked(card) : "")}` +
      (card && card.gloss_en ? `<br><span class="muted">${escapeHtml(card.gloss_en)}</span>` : "") +
      `<br><strong>${escapeHtml(u.display)}</strong>` +
      (u.gloss ? ` <span class="muted">= ${escapeHtml(u.gloss)}</span>` : "") +
      (e.rating !== "good" ? `<br><span class="muted">getippt: ${escapeHtml(e.answers.map((a) => a || "(gezeigt)").join(", "))}</span>` : "") +
      `</li>`;
  }).join("") || "<li>Noch keine Antworten.</li>";
  showPanel("history");
}

$("btn-check").addEventListener("click", () => check(false));
$("btn-reveal").addEventListener("click", () => check(true));
$("btn-known").addEventListener("click", markKnown);
$("btn-defer").addEventListener("click", defer);
$("btn-next").addEventListener("click", nextCard);
$("btn-prev").addEventListener("click", showHistory);
$("btn-prev-2").addEventListener("click", showHistory);
$("btn-history-close").addEventListener("click", () => loadCard());
$("btn-more").addEventListener("click", () => { overLimit = true; loadCard(); });
// Which gap an umlaut button types into. document.activeElement is useless
// here: pressing a button moves focus off the input before the handler runs,
// so the old code fell through to the FIRST gap and a card with two gaps
// always got the character in the wrong one (owner, 2026-10-06). Two guards,
// because one is not enough on every device: mousedown is prevented so focus
// never leaves the input at all, and the last focused gap is remembered for
// the touch case, where that does not hold.
let lastGap = null;
document.addEventListener("focusin", (ev) => {
  if (ev.target instanceof HTMLInputElement && ev.target.classList.contains("gap")) lastGap = ev.target;
});

document.querySelectorAll(".umlauts button").forEach((b) => {
  b.addEventListener("mousedown", (ev) => ev.preventDefault());
  b.addEventListener("click", () => {
  const active = document.activeElement;
  const focused = active instanceof HTMLInputElement && active.classList.contains("gap") ? active : null;
  const remembered = lastGap && lastGap.isConnected ? lastGap : null;
  const target = focused || remembered || $("sentence").querySelector("input.gap");
  if (!target || target.readOnly) return;
  const pos = target.selectionStart ?? target.value.length;
  target.value = target.value.slice(0, pos) + b.dataset.ch + target.value.slice(pos);
  target.focus();
  target.setSelectionRange(pos + 1, pos + 1);
  lastGap = target;
  });
});

// -- triage -------------------------------------------------------------------

let triageQueue = [];

function loadTriage() {
  triageQueue = untriagedUnits(deck, state).slice(0, 50);
  showTriage();
}

function showTriage() {
  const unit = triageQueue[0];
  $("triage-card").hidden = !unit;
  $("triage-empty").hidden = !!unit;
  if (!unit) return;
  const u = unitPayload(unit);
  $("triage-rank").textContent = `Rang ${u.rank} · ${u.kind} · ${u.cefr || "-"}`;
  $("triage-display").textContent = u.display + (u.gloss ? `  (${u.gloss})` : "");
}

async function triageAnswer(known) {
  const unit = triageQueue.shift();
  if (!unit) return;
  await record(makeMark(entries, unit.unit_id, known, "triage", now()));
  if (triageQueue.length === 0) loadTriage(); else showTriage();
}
$("btn-tri-known").addEventListener("click", () => triageAnswer(true));
$("btn-tri-learn").addEventListener("click", () => triageAnswer(false));
document.addEventListener("keydown", (ev) => {
  if ($("view-triage").hidden || ev.target.tagName === "INPUT") return;
  if (ev.key === "1") triageAnswer(true);
  if (ev.key === "2") triageAnswer(false);
});

// -- units --------------------------------------------------------------------

// How soon a unit is due, in the coarsest unit that is still true. Minutes
// and hours were shown until 2026-10-06 and the number moved while the
// learner watched it, which read as the schedule being unstable; nothing
// below a day is actionable, since the scheduler itself now works in
// sessions and days.
function fmtDue(due) {
  if (!due) return "";
  const days = Math.round((new Date(due) - now()) / 86400000);
  if (days <= 0) return new Date(due) <= now() ? "jetzt" : "heute";
  if (days === 1) return "morgen";
  if (days < 14) return `in ${days} Tagen`;
  if (days < 60) return `in ${Math.round(days / 7)} Wochen`;
  if (days < 365) return `in ${Math.round(days / 30)} Monaten`;
  return `in ${(days / 365).toFixed(1).replace(".", ",")} Jahren`;
}

function loadUnits() {
  const g = unitsByStage(deck, state, now().toISOString());
  const section = (title, items, withDue, relearnable = false) =>
    `<h3>${title} (${items.length})</h3>` + (items.length
      ? `<table>${items.map(([unit, due]) => { const u = unitPayload(unit); return `<tr><td>${escapeHtml(u.display)}${u.gloss ? ` <span class="muted">${escapeHtml(u.gloss)}</span>` : ""}</td>` +
        `<td class="num muted">${withDue ? fmtDue(due) : relearnable ? `<button class="small" data-relearn="${escapeHtml(u.unit_id)}">Wieder lernen</button>` : ""}</td></tr>`; }).join("")}</table>`
      : `<p class="muted">–</p>`);
  // Four stability bands, not one "Jung" spanning nought to 21 days: the
  // owner had 110 units in that bucket and 19 in "Reif", which said nothing
  // about what was settling (2026-10-06). The day ranges are in the heading
  // so the bands explain themselves.
  $("units").innerHTML =
    section("Lernend", g.learning, true) +
    section("Frisch (unter 7 Tagen)", g.fresh, true) +
    section("Jung (7 bis 21 Tage)", g.young, true) +
    section("Reif (21 bis 90 Tage)", g.mature, true) +
    section("Fest (über 90 Tage)", g.solid, true) +
    section("Zurückgestellt", g.deferred, false, true) +
    section("Bekannt", g.known, false, true);
  $("units").querySelectorAll("button[data-relearn]").forEach((b) => b.addEventListener("click", () => relearn(b.dataset.relearn)));
}

// -- adding a word by hand ----------------------------------------------------

// Three cases, in the order they are tried. The deck already teaches the
// word, so it only needs moving to the front. The reserve has it, with cards
// or without. Or nothing has it, and the learner says which kind of word it
// is so the unit id matches what a real build would later mine.
//
// The unit id is always unit_id_for(kind, lemma), which is why the kind is
// asked for rather than guessed: guess it wrong and the same word gets two
// ids and two histories (rule 5).
const KIND_PREFIX = { noun: "nn", verb: "vb", adjective: "aj", adverb: "av" };
const KIND_LABEL = { noun: "Nomen", verb: "Verb", adjective: "Adjektiv", adverb: "Adverb" };

function unitIdFor(kind, lemma) { return `${KIND_PREFIX[kind]}:${lemma}`; }

function addStatus(html) { $("add-result").innerHTML = html; }

async function addWord() {
  const typed = $("add-word").value.trim();
  const lemma = typed.toLowerCase();
  if (!lemma) return;
  addStatus(`<p class="muted">„${escapeHtml(typed)}“ wird gesucht…</p>`);

  const inDeck = deck.units.filter((u) => u.lemma_key === lemma);
  const already = new Set(state.requested);
  if (inDeck.length) {
    addStatus(inDeck.map((u) => {
      const done = already.has(u.unit_id) || u.unit_id in state.records;
      return `<p>${escapeHtml(u.display_de)} <span class="muted">(${KIND_LABEL[u.kind] || u.kind}, schon im Deck)</span> ` +
        (done ? '<span class="muted">wird bereits gelernt</span>'
              : `<button class="small" data-front="${escapeHtml(u.unit_id)}">Als Nächstes lernen</button>`) + "</p>";
    }).join(""));
    wireAddButtons();
    return;
  }

  let readings = [];
  try {
    readings = await lookupReserve(lemma, { index: reserveIndex });
  } catch (err) {
    addStatus(`<p class="muted">Die Reserve konnte nicht geladen werden: ${escapeHtml(err.message)}</p>`);
    return;
  }

  if (readings.length) {
    // Every reading, because the corpus disagrees with itself: "stur" is an
    // adjective and also a surname the Tagger read as a noun. The learner
    // knows which one was meant.
    addStatus(
      `<p class="muted">${readings.length > 1 ? "Welches Wort meinst du?" : "Gefunden:"}</p>` +
      readings.map(({ unit, cards }, i) => {
        const sample = cards.find((c) => c.gloss_en);
        const done = already.has(unit.unit_id);
        return `<p><strong>${escapeHtml(unit.display_de)}</strong> <span class="muted">${KIND_LABEL[unit.kind] || unit.kind}, ` +
          `${unit.sentence_count} Belege</span><br>` +
          (sample ? `<span class="muted">${escapeHtml(sample.sentence_de)}</span><br>` :
            '<span class="muted">Kein übersetzter Satz im Korpus; die Sätze werden geschrieben.</span><br>') +
          (done ? '<span class="muted">bereits hinzugefügt</span>'
                : `<button class="small" data-add="${i}">${sample ? "Hinzufügen" : "Sätze schreiben lassen"}</button>`) +
          "</p>";
      }).join(""),
    );
    wireAddButtons(readings);
    return;
  }

  // Nothing has it. The corpus may not use the word at all, so the kind
  // cannot be read off anything and the learner picks.
  addStatus(
    `<p>„${escapeHtml(typed)}“ steht weder im Deck noch in der Reserve.</p>` +
    '<p class="muted">Welche Wortart ist es? Die Sätze werden dann geschrieben.</p><p>' +
    Object.keys(KIND_LABEL).map((k) => `<button class="small" data-kind="${k}">${KIND_LABEL[k]}</button>`).join(" ") +
    "</p>",
  );
  $("add-result").querySelectorAll("button[data-kind]").forEach((b) =>
    b.addEventListener("click", () => generateFor({
      unit_id: unitIdFor(b.dataset.kind, lemma), kind: b.dataset.kind, lemma_key: lemma,
      parts: [lemma], display_de: typed, sentence_count: 0, rank: 900000, source: "mined",
      card_count: 0, glossed_card_count: 0, also_accepted: [], count_by_source: {}, per_million: 0,
      trivial: false, trivial_reason: null, requested_order: null, case: null, cefr: null, gloss_en: null,
    }, null)));
}

function wireAddButtons(readings = []) {
  $("add-result").querySelectorAll("button[data-front]").forEach((b) =>
    b.addEventListener("click", () => activate(deck.byId[b.dataset.front], [], "deck")));
  $("add-result").querySelectorAll("button[data-add]").forEach((b) => b.addEventListener("click", () => {
    const { unit, cards } = readings[Number(b.dataset.add)];
    const usable = cards.filter((c) => c.gloss_en);
    if (usable.length) activate(unit, usable, "reserve");
    else generateFor(unit, "reserve");
  }));
}

// The one model call the page makes (CLAUDE.md rule 4 names the exception).
async function generateFor(unit, origin) {
  if (!settings.geminiKey) {
    addStatus('<p>Dafür fehlt der Gemini-Schlüssel. Trage ihn unter ⚙ ein.</p>');
    return;
  }
  addStatus(`<p class="muted">Sätze für „${escapeHtml(unit.display_de)}“ werden geschrieben…</p>`);
  try {
    const { cards, fromCache } = await writeCards(unit, {
      apiKey: settings.geminiKey,
      model: deck.manifest.model_generate,
      store: kv,
    });
    await activate(unit, cards, origin || "generated");
    if (fromCache) showGeminiUsage();
  } catch (err) {
    const hint = err instanceof QuotaExhaustedError ? "" : " Versuch es später noch einmal.";
    addStatus(`<p>${escapeHtml(err.message)}${hint}</p>`);
  }
}

async function activate(unit, cards, origin) {
  if (!unit) return;
  await record(makeRequest(entries, { unitId: unit.unit_id, origin, unit, cards }, now()));
  addStatus(`<p>„${escapeHtml(unit.display_de)}“ wird als Nächstes gelehrt` +
    `${cards.length ? ` (${cards.length} Karten)` : ""}.</p>`);
  $("add-word").value = "";
  loadUnits();
  showGeminiUsage();
  if (settings.autoSync && settings.token) scheduleSync();
}

async function showGeminiUsage() {
  try {
    const s = await callSummary(kv);
    $("set-gemini-usage").textContent = s.total
      ? `${s.today} von ${s.limit} Aufrufen heute · ${s.total} insgesamt · ${s.cacheHits} aus dem Zwischenspeicher`
      : "Noch keine Aufrufe.";
  } catch (e) { /* the store is not there yet */ }
}

$("btn-add-word").addEventListener("click", addWord);
$("add-word").addEventListener("keydown", (ev) => { if (ev.key === "Enter") addWord(); });

// -- stats --------------------------------------------------------------------

function loadStats() {
  const s = computeStats(deck, state, entries, engine, now());
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

// -- settings -----------------------------------------------------------------

function loadSettingsView() {
  $("set-token").value = settings.token || "";
  $("set-gemini").value = settings.geminiKey || "";
  showGeminiUsage();
  $("set-gist").value = settings.gistId || "";
  $("set-cards").value = settings.cardsPerDay;
  $("set-new").value = settings.newPerDay === null ? "" : settings.newPerDay;
  $("set-pool").value = settings.newPool;
  $("set-retention").value = settings.retention;
  $("set-autosync").checked = !!settings.autoSync;
  $("set-log-info").textContent = `${entries.length} Einträge im Log auf diesem Gerät.`;
}

$("btn-settings-save").addEventListener("click", () => {
  settings.token = $("set-token").value.trim();
  settings.gistId = $("set-gist").value.trim();
  settings.cardsPerDay = Math.max(1, parseInt($("set-cards").value, 10) || DEFAULT_SETTINGS.cardsPerDay);
  const n = $("set-new").value.trim();
  settings.newPerDay = n === "" ? null : Math.max(0, parseInt(n, 10) || 0);
  settings.newPool = Math.max(1, parseInt($("set-pool").value, 10) || DEFAULT_SETTINGS.newPool);
  const ret = parseFloat($("set-retention").value);
  settings.retention = Number.isFinite(ret) ? Math.min(0.97, Math.max(0.8, ret)) : DEFAULT_SETTINGS.retention;
  engine = createEngine({ retention: settings.retention });
  refresh();
  settings.autoSync = $("set-autosync").checked;
  settings.geminiKey = $("set-gemini").value.trim();
  saveSettings(settings);
  $("settings-sync-status").textContent = "gespeichert";
  if (settings.token) sync().then(loadSettingsView).catch(() => {});
  else setSyncStatus("lokal", "off");
});

$("btn-sync-now").addEventListener("click", () => sync().then(loadSettingsView).catch(() => {}));

$("btn-reset").addEventListener("click", async () => {
  if (!confirm("Von vorne anfangen? Alle Einheiten gelten wieder als neu und die Zähler beginnen bei null. Das gilt auch auf deinen anderen Geräten.")) return;
  await record(makeReset(entries, "restart", now()));
  overLimit = false;
  loadSettingsView();
  $("settings-sync-status").textContent = "Lernstand zurückgesetzt.";
  show("practice");
});
$("btn-sync").addEventListener("click", () => sync().catch(() => {}));

$("btn-export").addEventListener("click", async () => {
  const text = toJsonl(entries);
  try {
    await navigator.clipboard.writeText(text);
    $("settings-sync-status").textContent = `${entries.length} Einträge in die Zwischenablage kopiert.`;
  } catch (e) {
    $("set-export").value = text;
    $("set-export").hidden = false;
  }
});

$("set-import").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const incoming = parseJsonl(await file.text());
  const before = entries.length;
  entries = mergeEntries(entries, incoming);
  await replaceLog(entries);
  refresh();
  $("settings-sync-status").textContent = `${entries.length - before} neue Einträge aus ${file.name} übernommen.`;
  loadSettingsView();
  if (settings.autoSync && settings.token) scheduleSync();
  ev.target.value = "";
});

// -- boot ---------------------------------------------------------------------

async function boot() {
  $("deck-info").textContent = "Deck wird geladen…";
  try {
    [baseDeck, entries] = await Promise.all([loadDeck("./data/deck"), readLog()]);
    deck = baseDeck;
    // Assume what is already on disk reached the gist; the boot sync below
    // corrects this within a second either way. Starting at zero would
    // greet the learner with "1158 ungesichert" on every load.
    pushedCount = entries.length;
  } catch (err) {
    $("deck-info").textContent = `Deck konnte nicht geladen werden: ${err.message}`;
    return;
  }
  // The reserve is optional: a deck built before it existed simply has none,
  // and the add-word screen says so instead of the page failing to load.
  loadReserveIndex("./data/deck/reserve").then((index) => { reserveIndex = index; }).catch(() => {});
  try { localStorage.setItem("phrasen.deckVersion", deck.manifest.deck_version); } catch (e) { /* ignore */ }
  refresh();
  const m = deck.manifest;
  $("deck-info").textContent = `Deck ${m.deck_version} · ${m.unit_count} Einheiten · ${m.glossed_card_count} von ${m.card_count} Karten mit Übersetzung`;
  setSyncStatus(settings.token ? "…" : "lokal", settings.token ? "busy" : "off");
  show("practice");
  if (settings.token) sync().then((r) => { if (r && r.pulled > 0 && !answered) loadCard(); }).catch(() => {});
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./sw.js").then((reg) => {
      if (reg.active) reg.active.postMessage({ type: "deck", version: m.deck_version });
      navigator.serviceWorker.ready.then((r) => r.active && r.active.postMessage({ type: "deck", version: m.deck_version }));
    }).catch(() => {});
  }
  window.addEventListener("online", () => { if (settings.token && settings.autoSync) scheduleSync(); });
}

boot();
