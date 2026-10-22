// The JavaScript grader must agree with the Python grader on every case in
// data/fixtures/grading/grader_parity.json (produced by ScopedTypoGrader).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { gradeCard, gradeText, isInflectionOf, isVerbFormOf, renderMarked } from "../../web/lib/grader.js";

const here = dirname(fileURLToPath(import.meta.url));
const cases = JSON.parse(readFileSync(join(here, "..", "..", "data", "fixtures", "grading", "grader_parity.json"), "utf-8"));

test("grader parity with Python on the fixture cases", () => {
  for (const c of cases) {
    const r = gradeText(c.typed, c.accepted);
    for (const k of ["is_correct", "is_exact", "is_scoped_typo", "is_transliteration", "is_capitalization_error"]) {
      assert.equal(r[k], c[k], `${JSON.stringify(c.typed)} vs ${JSON.stringify(c.accepted)}: ${k}`);
    }
  }
});

test("card grading maps outcomes to ratings and accepts a lower-case sentence opener", () => {
  const card = {
    sentence_de: "Trotzdem wartet er auf den Bus.",
    form_key: "Fin|Pres|3|Sing",
    gaps: [
      { start: 0, end: 8, answer: "Trotzdem", token_index: 0 },
      { start: 9, end: 15, answer: "wartet", token_index: 1 },
      { start: 19, end: 22, answer: "auf", token_index: 3 },
    ],
  };
  assert.equal(gradeCard(card, ["trotzdem", "wartet", "auf"]).rating, "good");
  assert.equal(gradeCard(card, ["Trotzdem", "wertet", "auf"]).rating, "hard");
  // A VERB in the wrong person fails and is a near miss, which offers "Als
  // richtig werten" (owner, 2026-10-22: the old automatic pass was too
  // loose). The Python twin in tests/test_phase2_engine.py asserts the same;
  // the two graders must agree or the phone and the laptop disagree.
  assert.equal(gradeCard(card, ["Trotzdem", "warte", "auf"]).outcome, "wrong");
  assert.equal(gradeCard(card, ["Trotzdem", "warte", "auf"]).rating, "again");
  assert.ok(gradeCard(card, ["Trotzdem", "warte", "auf"]).nearMiss);
  // A different word is still "again", and is not a near miss.
  assert.equal(gradeCard(card, ["Trotzdem", "suchen", "auf"]).rating, "again");
  assert.ok(!gradeCard(card, ["Trotzdem", "suchen", "auf"]).nearMiss);
  assert.equal(gradeCard(card, [null, "wartet", "auf"]).outcome, "revealed");
  assert.ok(!gradeCard(card, [null, "wartet", "auf"]).nearMiss);
  assert.ok(gradeCard(card, ["Trotzdem", "Wartet", "auf"]).nearMiss, "a wrong capital is a near miss");
  assert.equal(renderMarked(card), "[Trotzdem] [wartet] er [auf] den Bus.");
});

test("declension still passes, now with umlaut plurals, and verb forms are near misses", () => {
  // Twins of the Python tests of the same names, 2026-10-22.
  const hand = { sentence_de: "Er hat zwei Hände.", form_key: "Acc|Plur", gaps: [{ start: 12, end: 17, answer: "Hände", token_index: 3 }] };
  assert.equal(gradeCard(hand, ["Hand"]).outcome, "inflection");
  assert.equal(gradeCard(hand, ["Hand"]).rating, "hard");
  assert.ok(isInflectionOf("Häuser", "Haus"));
  assert.ok(!isInflectionOf("schon", "schön"), "an umlaut alone makes a different word");
  assert.ok(!isInflectionOf("hatte", "hätte"));
  assert.ok(isVerbFormOf("macht", "gemacht"));
  assert.ok(isVerbFormOf("aufgemacht", "aufmachen"));
  assert.ok(isVerbFormOf("anrufen", "anzurufen"));
  assert.ok(!isVerbFormOf("suchen", "warten"));
  const zu = { sentence_de: "Er versucht zu schlafen.", form_key: "Inf", gaps: [
    { start: 12, end: 14, answer: "zu", token_index: 2 }, { start: 15, end: 23, answer: "schlafen", token_index: 3 }] };
  assert.ok(gradeCard(zu, ["", "schlafen"]).nearMiss, "a missed zu is a near miss");
});
