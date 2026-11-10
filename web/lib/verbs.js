// The verb drill (owner, 2026-11-04): a verb and its English, and the
// learner types the Präteritum and the Perfekt for er/sie/es. No sentence.
// The forms come from web/data/deck/verbs.json (src/phrases/verb_forms.py);
// scheduling is session.js over a deck of its own, whose units are the
// "pf:" ids, and the page replays the log for it with entriesForMode, so a
// verb review never spends the sentence deck's budget.

import { gradeText } from "./grader.js";

// The manifest names the file and carries a hash of it, because the service
// worker caches deck files by URL and deck_version covers units and cards
// only. null when the deck has no verbs.
export async function loadVerbs(manifest, base = "./data/deck") {
  if (!manifest.verbs_file) return null;
  const v = encodeURIComponent(manifest.verbs_version || "");
  const res = await fetch(`${base}/${manifest.verbs_file}?v=${v}`);
  return res.ok ? res.json() : null;
}

// The shape session.js schedules over: units with a rank, one card each.
// A card needs English to be showable, and every verb has its gloss.
export function verbDeck(index, version = "") {
  const units = [...(index?.verbs || [])]
    .map((v) => ({ ...v, kind: "verb_drill", display_de: v.infinitive, trivial: false }))
    .sort((a, b) => a.rank - b.rank);
  const byId = Object.fromEntries(units.map((u) => [u.unit_id, u]));
  // One card per verb, named after it: the review log needs a card id and
  // the drill has nothing else to tell its cards apart by.
  const cardsByUnit = Object.fromEntries(units.map((u) => [
    u.unit_id, [{ card_id: u.unit_id, unit_id: u.unit_id, gloss_en: u.gloss_en }],
  ]));
  const cardById = Object.fromEntries(Object.values(cardsByUnit).flat().map((c) => [c.card_id, c]));
  return { manifest: { deck_version: version }, units, byId, cardsByUnit, cardById };
}

const SEVERITY = { revealed: 3, wrong: 2, typo: 1, exact: 0 };
const tidy = (s) => s.trim().replace(/\s+/g, " ").toLowerCase();

// A form against its accepted spellings: the same typo grader the sentence
// cards use, lower case on both sides (a capital is no mistake here).
function gradeForm(typed, accepted) {
  if (typed === null || typed === undefined) return "revealed";
  const t = tidy(typed);
  if (!t) return "wrong";
  const r = gradeText(t, accepted.map(tidy));
  if (r.is_correct && (r.is_exact || r.is_transliteration)) return "exact";
  if (r.is_correct && r.is_scoped_typo) return "typo";
  return "wrong";
}

// "sein" for "ist" is the right auxiliary in the wrong person: a pass, not
// a perfect one, like a typo.
const AUX_INFINITIVE = { haben: "hat", sein: "ist" };

// One letter added, dropped or changed: "is" for "ist" (2026-11-06). The
// auxiliaries are three letters, below what the typo grader looks at, and
// "hat" and "ist" are three letters apart, so this cannot pass the wrong one.
function oneLetterApart(a, b) {
  if (Math.abs(a.length - b.length) > 1 || a === b) return false;
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  const rest = (s, skip) => s.slice(i + skip);
  return rest(a, 1) === rest(b, 1) || rest(a, 1) === rest(b, 0) || rest(a, 0) === rest(b, 1);
}

function gradeAuxiliary(typed, accepted) {
  if (typed === null || typed === undefined) return "revealed";
  const t = tidy(typed);
  if (accepted.includes(t)) return "exact";
  if (accepted.includes(AUX_INFINITIVE[t])) return "typo";
  if (t && accepted.some((a) => oneLetterApart(t, a))) return "typo";
  return "wrong";
}

// A reflexive verb's forms are graded without the pronoun, so "fühlte",
// "fühlte sich" and "gefühlt sich" all count. The screen asks for er/sie/es
// and nothing says to type "sich" or where (2026-11-07, 11-09).
const dropSich = (s) => s.split(/\s+/).filter((w) => w.toLowerCase() !== "sich").join(" ");

// typed: {praeteritum, auxiliary, partizip}, each a string or null when the
// learner pressed "Zeigen". Rated as a sentence card is: all exact is good,
// a typo is hard, anything wrong or shown is again.
export function gradeVerb(verb, typed) {
  const reflexive = (verb.infinitive || "").startsWith("sich ");
  const form = (input, accepted) => (reflexive
    ? gradeForm(input === null || input === undefined ? input : dropSich(input), accepted.map(dropSich))
    : gradeForm(input, accepted));
  const fields = [
    ["praeteritum", form(typed.praeteritum, verb.praeteritum), verb.praeteritum],
    ["auxiliary", gradeAuxiliary(typed.auxiliary, verb.auxiliary), verb.auxiliary],
    ["partizip", form(typed.partizip, verb.partizip), verb.partizip],
  ];
  const gaps = fields.map(([field, outcome, accepted]) => ({
    field, outcome, accepted: outcome === "exact" || outcome === "typo",
    typed: typed[field] ?? "", expected: accepted[0],
  }));
  const worst = gaps.reduce((w, g) => (SEVERITY[g.outcome] > SEVERITY[w] ? g.outcome : w), "exact");
  const rating = worst === "exact" ? "good" : worst === "typo" ? "hard" : "again";
  return { gaps, rating, outcome: worst };
}
