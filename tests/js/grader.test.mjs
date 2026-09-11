// The JavaScript grader must agree with the Python grader on every case in
// data/fixtures/grading/grader_parity.json (produced by ScopedTypoGrader).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { gradeCard, gradeText, renderMarked } from "../../web/lib/grader.js";

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
    gaps: [
      { start: 0, end: 8, answer: "Trotzdem", token_index: 0 },
      { start: 9, end: 15, answer: "wartet", token_index: 1 },
      { start: 19, end: 22, answer: "auf", token_index: 3 },
    ],
  };
  assert.equal(gradeCard(card, ["trotzdem", "wartet", "auf"]).rating, "good");
  assert.equal(gradeCard(card, ["Trotzdem", "wertet", "auf"]).rating, "hard");
  assert.equal(gradeCard(card, ["Trotzdem", "warte", "auf"]).rating, "again");
  assert.equal(gradeCard(card, [null, "wartet", "auf"]).outcome, "revealed");
  assert.equal(renderMarked(card), "[Trotzdem] [wartet] er [auf] den Bus.");
});
