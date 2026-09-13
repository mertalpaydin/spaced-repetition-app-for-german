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
        if (levenshtein(it, at) === 1 && at.length > 3) { hasTypo = true; } else { allMatch = false; }
      }
      if (!morphemeError && hasTypo && allMatch) return result({ is_correct: true, is_scoped_typo: true });
      continue;
    }
    const li = input.toLowerCase(), la = ans.toLowerCase();
    if (isMinimalPair(li, la)) continue;
    if (GRAMMATICAL_MORPHEMES.has(la) || GRAMMATICAL_MORPHEMES.has(li)) continue;
    if (levenshtein(input, ans) !== 1) continue;
    if (!editOutsideSuffix(input, ans)) continue;
    return result({ is_correct: true, is_scoped_typo: true });
  }
  return result({});
}

// -- cards (src/engine/grading.py) --------------------------------------------

const SEVERITY = { revealed: 5, wrong: 4, case: 3, typo: 2, translit: 1, exact: 0 };

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

export function gradeGap(card, i, typed, alternatives = []) {
  const expected = card.gaps[i].answer;
  if (typed === null || typed === undefined) return { expected, typed: "", outcome: "revealed", accepted: false };
  const r = gradeText(typed, acceptedForms(card, i, alternatives));
  let outcome;
  if (r.is_correct && r.is_exact) outcome = "exact";
  else if (r.is_correct && r.is_transliteration) outcome = "translit";
  else if (r.is_correct && r.is_scoped_typo) outcome = "typo";
  else if (r.is_capitalization_error) outcome = "case";
  else outcome = "wrong";
  return { expected, typed, outcome, accepted: r.is_correct };
}

export function gradeCard(card, typed, alternatives = []) {
  if (typed.length !== card.gaps.length) throw new Error(`${card.gaps.length} gaps, ${typed.length} answers`);
  const gaps = typed.map((t, i) => gradeGap(card, i, t, alternatives));
  const worst = gaps.reduce((w, g) => (SEVERITY[g.outcome] > SEVERITY[w.outcome] ? g : w), gaps[0]);
  const rating = worst.outcome === "exact" || worst.outcome === "translit" ? "good" : worst.outcome === "typo" ? "hard" : "again";
  return { gaps, outcome: worst.outcome, rating };
}

export function renderMarked(card, left = "[", right = "]") {
  let out = "", last = 0;
  for (const g of card.gaps) { out += card.sentence_de.slice(last, g.start) + left + card.sentence_de.slice(g.start, g.end) + right; last = g.end; }
  return out + card.sentence_de.slice(last);
}
