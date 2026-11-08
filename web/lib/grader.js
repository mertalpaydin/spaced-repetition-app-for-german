// The grader, matching src/engine/typo_grader.py (ScopedTypoGrader, topic
// None) and src/engine/grading.py. tests/js/grader.test.mjs pins it against
// a fixture produced by the Python grader.

import { CRITICAL_MINIMAL_PAIRS, GRAMMATICAL_MORPHEMES, SUFFIX_TOLERANCE_WINDOW } from "./grader-tables.js";

function normalizeWhitespace(t) { return t.replace(/\s+/g, " ").trim(); }

function levenshtein(s1, s2) {
  if (s1.length < s2.length) return levenshtein(s2, s1);
  if (s2.length === 0) return s1.length;
  let prev = Array.from({ length: s2.length + 1 }, (_, i) => i);
  for (let i = 0; i < s1.length; i++) {
    const cur = [i + 1];
    for (let j = 0; j < s2.length; j++) {
      cur.push(Math.min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (s1[i] !== s2[j] ? 1 : 0)));
    }
    prev = cur;
  }
  return prev[prev.length - 1];
}

// Do a and b differ only by swapping one pair of adjacent characters? The
// commonest typing slip there is, and plain Levenshtein scores it 2, so the
// distance-1 rule rejected it: "Gefarh" for "Gefahr" was marked wrong on
// 2026-10-11. It is also the one edit the suffix window must not veto, since
// a swap cannot turn one valid German ending into another. Mirrors
// ScopedTypoGrader._is_adjacent_transposition.
function isAdjacentTransposition(a, b) {
  if (a.length !== b.length || a.length < 4 || a === b) return false;
  const differing = [];
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) differing.push(i);
  if (differing.length !== 2) return false;
  const [i, j] = differing;
  return j === i + 1 && a[i] === b[j] && a[j] === b[i];
}

function commonPrefixLen(a, b) {
  let n = 0;
  while (n < a.length && n < b.length && a[n] === b[n]) n++;
  return n;
}

function editOutsideSuffix(a, b, suffixLen = SUFFIX_TOLERANCE_WINDOW) {
  const longer = Math.max(a.length, b.length);
  if (longer <= suffixLen) return false;
  return commonPrefixLen(a, b) < longer - suffixLen;
}

function canon(t) {
  return t.replace(/ß/g, "ss").replace(/ae/g, "ä").replace(/oe/g, "ö").replace(/ue/g, "ü");
}

function isMinimalPair(a, b) {
  return CRITICAL_MINIMAL_PAIRS.has(`${a}|${b}`) || CRITICAL_MINIMAL_PAIRS.has(`${b}|${a}`);
}

function result(fields) {
  return { is_correct: false, is_exact: false, is_scoped_typo: false, is_transliteration: false, is_capitalization_error: false, ...fields };
}

export function gradeText(userInput, acceptedAnswers) {
  const input = normalizeWhitespace(userInput);
  const accepted = acceptedAnswers.map(normalizeWhitespace);
  // 1. exact
  if (accepted.includes(input)) return result({ is_correct: true, is_exact: true });
  // 2. transliteration (ae/oe/ue <-> ä/ö/ü, ss <-> ß)
  for (const ans of accepted) {
    const li = input.toLowerCase(), la = ans.toLowerCase();
    if ((li === "grosser" || li === "groesser") && (la === "größer" || la === "grösser")) {
      return result({ is_correct: true, is_transliteration: true });
    }
    if (CRITICAL_MINIMAL_PAIRS.has(`${li}|${la}`)) continue;
    const ci = canon(input), ca = canon(ans);
    if (ci.toLowerCase() === ca.toLowerCase()) {
      if (ci !== ca) return result({ is_capitalization_error: true });
      return result({ is_correct: true, is_transliteration: true });
    }
  }
  // 3. capitalisation error fails
  for (const ans of accepted) {
    if (input.toLowerCase() === ans.toLowerCase() && input !== ans) return result({ is_capitalization_error: true });
  }
  // 4. scoped typo
  for (const ans of accepted) {
    const inTokens = input.split(" "), ansTokens = ans.split(" ");
    if (inTokens.length === ansTokens.length && ansTokens.length > 1) {
      let hasTypo = false, morphemeError = false, allMatch = true;
      for (let i = 0; i < ansTokens.length; i++) {
        const it = inTokens[i], at = ansTokens[i];
        if (it === at) continue;
        if (GRAMMATICAL_MORPHEMES.has(it.toLowerCase()) || GRAMMATICAL_MORPHEMES.has(at.toLowerCase())) { morphemeError = true; break; }
        if (isAdjacentTransposition(it, at) || (levenshtein(it, at) === 1 && at.length > 3)) { hasTypo = true; } else { allMatch = false; }
      }
      if (!morphemeError && hasTypo && allMatch) return result({ is_correct: true, is_scoped_typo: true });
      continue;
    }
    const li = input.toLowerCase(), la = ans.toLowerCase();
    if (isMinimalPair(li, la)) continue;
    if (GRAMMATICAL_MORPHEMES.has(la) || GRAMMATICAL_MORPHEMES.has(li)) continue;
    const transposed = isAdjacentTransposition(input, ans);
    if (!transposed && levenshtein(input, ans) !== 1) continue;
    if (!transposed && !editOutsideSuffix(input, ans)) continue;
    return result({ is_correct: true, is_scoped_typo: true });
  }
  return result({});
}

// -- cards (src/engine/grading.py) --------------------------------------------

// "inflection" sits with "typo": both are the right word, so both cost a
// harder rating and not a restart. Matches _SEVERITY in src/engine/grading.py.
const SEVERITY = { revealed: 6, wrong: 5, case: 4, inflection: 3, typo: 2, translit: 1, exact: 0 };

// The endings a German noun, adjective, determiner or verb takes. The owner
// knows "stark" and still types "starken", because the ending is agreement
// with a noun phrase and not part of the vocabulary item; grading that
// "again" sent the unit back to learning step 0, where it returned every few
// cards (owner, 2026-10-06). Not a morphology engine: both forms must share
// a stem of at least MIN_STEM characters and differ only by these endings,
// so "starken"/"stark" matches and "Hand"/"Hund", "legen"/"liegen" and
// "geben"/"gegeben" do not. Matches src/engine/grading.is_inflection_of.
const INFLECTION_ENDINGS = ["", "e", "en", "em", "er", "es", "s", "n", "ern", "t", "te", "st", "ten", "et"];
const MIN_STEM = 3;

function stems(word) {
  const out = new Set();
  for (const ending of INFLECTION_ENDINGS) {
    if (!ending) out.add(word);
    else if (word.endsWith(ending) && word.length - ending.length >= MIN_STEM) {
      out.add(word.slice(0, word.length - ending.length));
    }
  }
  return out;
}

const foldUmlauts = (w) => w.replace(/ä/g, "a").replace(/ö/g, "o").replace(/ü/g, "u").replace(/ß/g, "ss");

// Umlauts are folded inside the stem, so "Hände"/"Hand" and "Häuser"/"Haus"
// match; two words that differ ONLY by an umlaut ("schon"/"schön",
// "hatte"/"hätte") stay two words. Matches is_inflection_of in
// src/engine/grading.py.
// One letter added, dropped, changed or two swapped, inside the ending
// where the typo grader does not count it: "vertraure" for "vertraue"
// (2026-10-30). A near miss, the learner decides. Matches
// is_one_letter_slip in src/engine/grading.py.
export function isOneLetterSlip(typed, expected) {
  const a = (typed || "").trim().toLowerCase();
  const b = (expected || "").trim().toLowerCase();
  if (a === b || b.length < 4) return false;
  if (levenshtein(a, b) === 1) return true;
  return a.length === b.length && isAdjacentTransposition(a, b);
}

export function isInflectionOf(typed, expected) {
  const a = (typed || "").trim().toLowerCase();
  const b = (expected || "").trim().toLowerCase();
  if (!a || a === b) return false;
  if (foldUmlauts(a) === foldUmlauts(b)) return false;
  // A noun in -nis doubles its s in the plural: "Kenntnisse" (2026-10-30).
  for (const [short, long] of [[a, b], [b, a]]) {
    if (short.endsWith("nis") && (long === `${short}se` || long === `${short}sen`)) return true;
  }
  const sb = new Set([...stems(b)].map(foldUmlauts));
  for (const stem of stems(a)) if (sb.has(foldUmlauts(stem))) return true;
  return false;
}

const PARTICLES = ["zurück", "zusammen", "weiter", "vorbei", "heraus", "herein", "hinaus", "hinein",
  "nach", "fort", "weg", "los", "auf", "aus", "ein", "mit", "vor", "zu", "ab", "an",
  "um", "hin", "her", "da", "dar", "durch"];

function withoutInfix(word, infix) {
  if (word.startsWith(infix) && word.length > infix.length + 2) return word.slice(infix.length);
  for (const particle of PARTICLES) {
    const rest = word.slice(particle.length);
    if (word.startsWith(particle) && rest.startsWith(infix) && rest.length > infix.length + 2) {
      return particle + rest.slice(infix.length);
    }
  }
  return null;
}

// The same verb in another form: an ending, the participle's "ge" ("macht"
// for "gemacht"), or the infinitive's "zu" ("anrufen" for "anzurufen").
// Matches is_verb_form_of in src/engine/grading.py.
export function isVerbFormOf(typed, expected) {
  const a = (typed || "").trim().toLowerCase();
  const b = (expected || "").trim().toLowerCase();
  if (!a || a === b) return false;
  if (isInflectionOf(a, b)) return true;
  for (const infix of ["ge", "zu"]) {
    for (const [x, y] of [[a, b], [b, a]]) {
      const stripped = withoutInfix(y, infix);
      if (stripped !== null && (stripped === x || isInflectionOf(x, stripped))) return true;
    }
  }
  return false;
}

// A preposition fused with its article, to the bare preposition. Matches
// _FUSED_PREPOSITIONS in src/engine/grading.py.
const FUSED_PREPOSITIONS = { zum: "zu", zur: "zu", vom: "von", am: "an", ans: "an", im: "in", ins: "in",
  beim: "bei", aufs: "auf", ums: "um", durchs: "durch", "fürs": "für", "übers": "über", unters: "unter",
  unterm: "unter", vors: "vor", vorm: "vor", hinters: "hinter", hinterm: "hinter" };

// "zum"/"zu", "zum"/"zur", "vom"/"von". Matches is_fused_preposition_of.
export function isFusedPrepositionOf(typed, expected) {
  const a = (typed || "").trim().toLowerCase();
  const b = (expected || "").trim().toLowerCase();
  if (!a || a === b) return false;
  return (FUSED_PREPOSITIONS[a] || a) === (FUSED_PREPOSITIONS[b] || b) && (a in FUSED_PREPOSITIONS || b in FUSED_PREPOSITIONS);
}

// "Nachweiß" for "Nachweis". Matches is_sharp_s_slip.
export function isSharpSSlip(typed, expected) {
  const a = (typed || "").trim().toLowerCase();
  const b = (expected || "").trim().toLowerCase();
  if (!a || a === b || !(a + b).includes("ß")) return false;
  const fold = (w) => w.replace(/ß/g, "s").replace(/ss/g, "s");
  return fold(a) === fold(b);
}

// "von" or "vom" for "vor". Matches is_preposition_slip.
const PREPOSITIONS = new Set(["an", "auf", "aus", "bei", "bis", "durch", "für", "gegen", "hinter", "in", "mit",
  "nach", "neben", "ohne", "seit", "über", "um", "unter", "von", "vor", "zu", "zwischen"]);

export function isPrepositionSlip(typed, expected) {
  const a = (typed || "").trim().toLowerCase();
  const b = (expected || "").trim().toLowerCase();
  const known = (w) => PREPOSITIONS.has(w) || w in FUSED_PREPOSITIONS;
  if (a === b || !known(a) || !known(b)) return false;
  return levenshtein(a, b) === 1;
}

// "Beraterinnen" for "Berater". Matches is_feminine_form_of.
const FEMININE_ENDINGS = ["innen", "inen", "in"];

export function isFeminineFormOf(typed, expected) {
  const a = foldUmlauts((typed || "").trim().toLowerCase());
  const b = foldUmlauts((expected || "").trim().toLowerCase());
  if (!a || a === b) return false;
  return FEMININE_ENDINGS.some((end) => a === b + end || b === a + end);
}

const VERBAL_FORMS = new Set(["Fin", "Part", "Inf"]);

// `alternatives` are the unit's accepted near-synonyms ("deswegen" for
// "deshalb"); single-gap cards only, capitalised like the answer.
export function acceptedForms(card, i, alternatives = []) {
  const gap = card.gaps[i];
  const forms = [gap.answer];
  const first = gap.answer.slice(0, 1), second = gap.answer.slice(1, 2);
  const initial = gap.start === 0 && first !== first.toLowerCase() && second === second.toLowerCase();
  if (initial) forms.push(first.toLowerCase() + gap.answer.slice(1));
  if (card.gaps.length === 1) {
    for (const alt of alternatives) {
      if (alt.toLowerCase() === gap.answer.toLowerCase()) continue;
      forms.push(alt);
      if (initial) forms.push(alt.slice(0, 1).toUpperCase() + alt.slice(1));
    }
  }
  return forms;
}

// The unit's citation words that name it: "bitten" of "bitten um".
function contentParts(unit) {
  const words = new Set((unit.parts || []).flatMap((p) => p.toLowerCase().split(/\s+/)));
  return new Set([...words].filter((w) => w.length >= 4 && !NOT_A_RIVAL_FORM.has(w)));
}

// Every word in the deck, lower-cased, to the content words of the units it
// is a form of. Matches deck_words in src/engine/grading.py.
export function deckWords(units, cardsByUnit) {
  const owners = new Map();
  for (const unit of units) {
    const parts = contentParts(unit);
    if (!parts.size) continue;
    const words = new Set((unit.parts || []).flatMap((p) => p.toLowerCase().split(/\s+/)));
    for (const card of cardsByUnit[unit.unit_id] || []) for (const a of card.answers) words.add(a.toLowerCase());
    for (const w of words) {
      if (!owners.has(w)) owners.set(w, new Set());
      for (const p of parts) owners.get(w).add(p);
    }
  }
  return owners;
}

// What the grader knows beyond the card. Matches unit_words.
export function unitWords(unit, cards, deck) {
  return { own: unitForms(unit, cards), parts: contentParts(unit), deck };
}

// [particle, base] of a separable verb, "aussehen" -> ["aus", "sehen"].
function baseOf(word) {
  for (const particle of PARTICLES) {
    if (word.startsWith(particle) && word.length - particle.length >= 4) return [particle, word.slice(particle.length)];
  }
  return ["", word];
}

// A letter away from the answer, and a word of another unit in the deck,
// not a slip: "bieten" for "bitten", "Absage" for "Ansage" (2026-11-08).
// Matches is_other_word in src/engine/grading.py.
export function isOtherWord(typed, expected, words) {
  const a = (typed || "").trim().toLowerCase();
  const b = (expected || "").trim().toLowerCase();
  if (!a || a === b || foldUmlauts(a) === foldUmlauts(b)) return false;
  if (words.own.has(a) || isVerbFormOf(a, b)) return false;
  const owners = words.deck.get(a);
  if (!owners || !owners.size) return false;
  const mine = new Set([...words.parts, ...[...words.parts].map((p) => baseOf(p)[1])]);
  for (const part of owners) {
    if (mine.has(part)) return false;
    const [particle, base] = baseOf(part);
    if (particle && !a.startsWith(particle) && mine.has(base)) return false;
  }
  return true;
}

// The unit's own word in a changed stem: "zugeben" for "zugibt". Matches
// _own_verb_form.
function ownVerbForm(typed, expected, words) {
  const a = (typed || "").trim().toLowerCase();
  const b = (expected || "").trim().toLowerCase();
  return a !== b && words.own.has(a) && a.slice(0, 2) === b.slice(0, 2);
}

const NO_WORDS = { own: new Set(), parts: new Set(), deck: new Map() };

export function gradeGap(card, i, typed, alternatives = [], words = null) {
  const expected = card.gaps[i].answer;
  if (typed === null || typed === undefined) return { expected, typed: "", outcome: "revealed", accepted: false };
  const known = words || NO_WORDS;
  const forms = acceptedForms(card, i, alternatives);
  const r = gradeText(typed, forms);
  let accepted = r.is_correct;
  const verbal = VERBAL_FORMS.has((card.form_key || "").split("|")[0]);
  let outcome, near = false;
  if (r.is_correct && r.is_exact) outcome = "exact";
  else if (r.is_correct && r.is_transliteration) outcome = "translit";
  else if (r.is_correct && r.is_scoped_typo) {
    // Another word, not a slip: it fails, and "Als richtig werten" is offered.
    if (forms.every((f) => isOtherWord(typed, f, known))) { outcome = "wrong"; near = true; accepted = false; }
    else outcome = "typo";
  }
  else if (r.is_capitalization_error) { outcome = "case"; near = true; }
  // The right verb in the wrong tense or person passes as "hard", like an
  // ending (2026-11-08; from 10-22 it failed and offered the button, which
  // the owner then pressed 18 times in four days). Matches grade_gap.
  else if (verbal && (forms.some((f) => isVerbFormOf(typed, f)) || ownVerbForm(typed, expected, known))) outcome = "inflection";
  else if (!verbal && forms.some((f) => isInflectionOf(typed, f))) outcome = "inflection";
  else if (expected.toLowerCase() === "zu" && ["", "zu"].includes(typed.trim().toLowerCase())) { outcome = "wrong"; near = true; }
  else if (forms.some((f) => isFusedPrepositionOf(typed, f) || isSharpSSlip(typed, f)
    || isPrepositionSlip(typed, f) || isFeminineFormOf(typed, f) || isOneLetterSlip(typed, f))) { outcome = "wrong"; near = true; }
  else outcome = "wrong";
  return { expected, typed, outcome, accepted, near };
}

// Too short or too common to say which unit the learner meant. Matches
// _NOT_A_RIVAL_FORM in src/engine/grading.py.
const NOT_A_RIVAL_FORM = new Set(["sich", "mich", "dich", "uns", "euch", "mir", "dir", "ihm", "ihr", "es",
  "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "nicht", "und",
  "aber", "oder", "auch", "noch", "schon", "mit", "von", "aus", "auf", "für", "über", "unter", "nach",
  "bei", "vor", "zum", "zur", "vom", "beim"]);

// Every word a learner could type for a unit. Matches unit_forms.
export function unitForms(unit, cards) {
  const words = new Set((unit.parts || []).map((w) => w.toLowerCase()));
  for (const card of cards || []) for (const a of card.answers) words.add(a.toLowerCase());
  return new Set([...words].filter((w) => w.length >= 3 && !NOT_A_RIVAL_FORM.has(w)));
}

// The rival unit the learner typed into a failed gap, or null: the right
// idea and the wrong word, which earns another try. Matches rival_typed.
export function rivalTyped(unit, grade, formsOf) {
  const words = unit.rival_words || [];
  if (grade.rating !== "again" || !((unit.rivals || []).length || words.length)) return null;
  const own = formsOf(unit.unit_id);
  for (const gap of grade.gaps) {
    if (gap.outcome !== "wrong" || gap.near) continue;
    const typed = gap.typed.trim().toLowerCase();
    if (NOT_A_RIVAL_FORM.has(typed) || typed.length < 3 || closeTo(typed, own)) continue;
    for (const rival of unit.rivals || []) if (closeTo(typed, formsOf(rival))) return rival;
    // A rival the deck has no unit for (rivals.yaml, 2026-11-04): the word itself.
    for (const word of words) if (closeTo(typed, new Set([word]))) return word;
  }
  return null;
}

// One of the forms, in another ending, or with a letter slipped. Matches
// _close_to in src/engine/grading.py.
export function closeTo(typed, forms) {
  if (forms.has(typed)) return true;
  // isVerbFormOf covers the ending test and the "ge"/"zu" of a verb:
  // "aufzuhören" is the rival "aufhören" (2026-11-08).
  for (const f of forms) {
    if (isVerbFormOf(typed, f) || (f.length >= 5 && levenshtein(typed, f) === 1)) return true;
  }
  return false;
}

// `words` (unitWords) lets the grader tell a slip from another word and see
// the unit's own verb in a changed stem; without it the grader is as before.
export function gradeCard(card, typed, alternatives = [], words = null) {
  if (typed.length !== card.gaps.length) throw new Error(`${card.gaps.length} gaps, ${typed.length} answers`);
  const gaps = typed.map((t, i) => gradeGap(card, i, t, alternatives, words));
  const worst = gaps.reduce((w, g) => (SEVERITY[g.outcome] > SEVERITY[w.outcome] ? g : w), gaps[0]);
  const good = worst.outcome === "exact" || worst.outcome === "translit";
  const hard = worst.outcome === "typo" || worst.outcome === "inflection";
  const rating = good ? "good" : hard ? "hard" : "again";
  // Failed only on gaps where the learner had the right word: this is when
  // "Als richtig werten" is offered. Never for a wrong word or a revealed
  // card. Matches CardGrade.near_miss in src/engine/grading.py.
  const failed = gaps.filter((g) => !g.accepted && g.outcome !== "inflection");
  const nearMiss = rating === "again" && failed.length > 0 && failed.every((g) => g.near);
  return { gaps, outcome: worst.outcome, rating, nearMiss };
}

export function renderMarked(card, left = "[", right = "]") {
  let out = "", last = 0;
  for (const g of card.gaps) { out += card.sentence_de.slice(last, g.start) + left + card.sentence_de.slice(g.start, g.end) + right; last = g.end; }
  return out + card.sentence_de.slice(last);
}
