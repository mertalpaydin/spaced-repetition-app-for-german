// The letter-by-letter marks shown after a typo (owner, 2026-11-04: "for
// some long words it's hard to find"). Display only: nothing here grades.
import test from "node:test";
import assert from "node:assert/strict";

import { isCloseEnough, letterDiff } from "../../web/lib/letterdiff.js";

const plain = (segs) => segs.map((s) => `${s.op}:${s.typed ?? ""}>${s.expected ?? ""}`).join(" ");

test("a missing letter is marked missing and the rest is the same", () => {
  assert.equal(plain(letterDiff("Verhältnise", "Verhältnisse")), "same:Verhältnis>Verhältnis missing:>s same:e>e");
});

test("an extra letter is marked extra", () => {
  const segs = letterDiff("Beratunng", "Beratung");
  assert.deepEqual(segs.map((s) => s.op), ["same", "extra", "same"]);
  assert.equal(segs.filter((s) => s.op === "extra")[0].typed, "n");
});

test("a wrong letter shows what was typed and what was wanted", () => {
  assert.equal(plain(letterDiff("berühnt", "berühmt")), "same:berüh>berüh wrong:n>m same:t>t");
});

test("two swapped neighbours are one swap, not two wrong letters", () => {
  assert.equal(plain(letterDiff("Bezeihung", "Beziehung")), "same:Bez>Bez swap:ei>ie same:hung>hung");
});

test("a wrong capital is a wrong letter", () => {
  assert.equal(plain(letterDiff("beratung", "Beratung")), "wrong:b>B same:eratung>eratung");
});

test("reading the kept and missing letters gives the right word back", () => {
  for (const [typed, expected] of [["Unterrschied", "Unterschied"], ["gebn", "geben"], ["Zukuft", "Zukunft"]]) {
    const rebuilt = letterDiff(typed, expected).map((s) => (s.op === "extra" ? "" : s.expected)).join("");
    assert.equal(rebuilt, expected);
    const typedBack = letterDiff(typed, expected).map((s) => (s.op === "missing" ? "" : s.typed)).join("");
    assert.equal(typedBack, typed);
  }
});

test("only a near spelling is marked; a different word is not", () => {
  assert.equal(isCloseEnough("Verhältnise", "Verhältnisse"), true);
  assert.equal(isCloseEnough("beratung", "Beratung"), true);
  assert.equal(isCloseEnough("gehte", "ging"), false);
  assert.equal(isCloseEnough("kriegt", "bekommt"), false);
  assert.equal(isCloseEnough("", "Haus"), false);
  assert.equal(isCloseEnough("Haus", "Haus"), false, "nothing to mark");
});
