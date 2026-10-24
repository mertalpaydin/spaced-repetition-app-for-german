// The JavaScript grader must agree with the Python grader on every case in
// data/fixtures/grading/grader_parity.json (produced by ScopedTypoGrader).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { gradeCard, gradeText, isInflectionOf, isVerbFormOf, renderMarked, rivalTyped, unitForms } from "../../web/lib/grader.js";

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

test("a fused preposition or a sharp s is a near miss", () => {
  // Twin of test_grade_card_fused_preposition_or_sharp_s_is_a_near_miss: the
  // owner's three reports of 2026-10-23/24.
  const fuehren = { sentence_de: "Ich versuche alles, aber es führt zu nichts.", form_key: "Fin|Pres|3|Sing", gaps: [
    { start: 28, end: 33, answer: "führt", token_index: 5 }, { start: 34, end: 36, answer: "zu", token_index: 6 }] };
  assert.equal(gradeCard(fuehren, ["führt", "zum"]).rating, "again");
  assert.ok(gradeCard(fuehren, ["führt", "zum"]).nearMiss);
  const erzaehlen = { sentence_de: "Ich begann, von unseren Abenteuern zu erzählen.", form_key: "Inf", gaps: [
    { start: 12, end: 15, answer: "von", token_index: 3 }, { start: 38, end: 46, answer: "erzählen", token_index: 7 }] };
  assert.ok(gradeCard(erzaehlen, ["vom", "erzählen"]).nearMiss);
  const nachweis = { sentence_de: "Alle erhalten unter Nachweis ihres Wohnsitzes einen Rabatt.", form_key: "Dat|Sing", gaps: [
    { start: 20, end: 28, answer: "Nachweis", token_index: 3 }] };
  assert.equal(gradeCard(nachweis, ["Nachweiß"]).rating, "again");
  assert.ok(gradeCard(nachweis, ["Nachweiß"]).nearMiss);
  assert.ok(!gradeCard(fuehren, ["führt", "von"]).nearMiss, "a different preposition is a different word");
  assert.ok(!gradeCard(erzaehlen, ["über", "erzählen"]).nearMiss);
});

test("kriegen typed for bekommen is a rival, and earns another try", () => {
  // Twin of test_rival_typed_kriegen_for_bekommen_is_a_rival in
  // tests/test_phrase_rivals.py (owner, 2026-10-24).
  const card = (unit_id, sentence_de, answer) => {
    const start = sentence_de.indexOf(answer);
    return { unit_id, sentence_de, form_key: "Fin|Pres|3|Sing", answers: [answer], gaps: [{ start, end: start + answer.length, answer, token_index: 0 }] };
  };
  const units = {
    "vb:bekommen": { unit_id: "vb:bekommen", parts: ["bekommen"], rivals: ["vb:kriegen"] },
    "vb:kriegen": { unit_id: "vb:kriegen", parts: ["kriegen"], rivals: [] },
  };
  const cards = { "vb:bekommen": [card("vb:bekommen", "Er bekommt ein Buch.", "bekommt")], "vb:kriegen": [card("vb:kriegen", "Er kriegt ein Buch.", "kriegt")] };
  const formsOf = (id) => unitForms(units[id], cards[id]);
  const c = cards["vb:bekommen"][0];
  const bekommen = units["vb:bekommen"];
  assert.equal(rivalTyped(bekommen, gradeCard(c, ["kriegt"]), formsOf), "vb:kriegen");
  assert.equal(rivalTyped(bekommen, gradeCard(c, ["kriegen"]), formsOf), "vb:kriegen");
  assert.equal(rivalTyped(bekommen, gradeCard(c, ["kauft"]), formsOf), null);
  assert.equal(rivalTyped(bekommen, gradeCard(c, ["bekommt"]), formsOf), null);
  assert.equal(rivalTyped(bekommen, gradeCard(c, [null]), formsOf), null);
  assert.equal(rivalTyped(bekommen, gradeCard(c, ["bekommen"]), formsOf), null);
});
