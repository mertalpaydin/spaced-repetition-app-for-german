// Letter-by-letter marks for a near spelling (owner, 2026-11-04: in a long
// word a typo was hard to find). Display only: grading stays in grader.js.
//
// An optimal string alignment (Levenshtein plus swapped neighbours) between
// what was typed and what was wanted, read back as segments:
//   same    letters typed as wanted
//   missing letters wanted and not typed
//   extra   letters typed and not wanted
//   wrong   letters typed in place of others
//   swap    two neighbours typed the wrong way round
// A wrong capital is a wrong letter. Where two alignments cost the same, the
// gap goes to the right ("Verhältnis·s·e", not "Verhältni·s·se"), which is
// where a reader looks for a doubled letter.

function table(a, b) {
  const d = Array.from({ length: a.length + 1 }, (_, i) => {
    const row = new Array(b.length + 1).fill(0);
    row[0] = i;
    return row;
  });
  for (let j = 0; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1] && a[i - 1] !== a[i - 2]) {
        d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
      }
    }
  }
  return d;
}

export function letterDistance(typed, expected) {
  const a = Array.from(typed), b = Array.from(expected);
  return table(a, b)[a.length][b.length];
}

export function letterDiff(typed, expected) {
  const a = Array.from(typed), b = Array.from(expected);
  const d = table(a, b);
  const ops = [];
  let i = a.length, j = b.length;
  while (i > 0 || j > 0) {
    const here = d[i][j];
    if (i > 0 && d[i - 1][j] + 1 === here) {
      ops.push({ op: "extra", typed: a[i - 1], expected: "" }); i -= 1;
    } else if (j > 0 && d[i][j - 1] + 1 === here) {
      ops.push({ op: "missing", typed: "", expected: b[j - 1] }); j -= 1;
    } else if (i > 0 && j > 0 && a[i - 1] === b[j - 1] && d[i - 1][j - 1] === here) {
      ops.push({ op: "same", typed: a[i - 1], expected: b[j - 1] }); i -= 1; j -= 1;
    } else if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1] && d[i - 2][j - 2] + 1 === here) {
      ops.push({ op: "swap", typed: a[i - 2] + a[i - 1], expected: b[j - 2] + b[j - 1] }); i -= 2; j -= 2;
    } else {
      ops.push({ op: "wrong", typed: a[i - 1], expected: b[j - 1] }); i -= 1; j -= 1;
    }
  }
  ops.reverse();
  // Neighbouring segments of one kind read as one; a swap stays on its own.
  const merged = [];
  for (const o of ops) {
    const last = merged[merged.length - 1];
    if (last && last.op === o.op && o.op !== "swap") {
      last.typed += o.typed;
      last.expected += o.expected;
    } else {
      merged.push({ ...o });
    }
  }
  return merged;
}

// Worth marking letter by letter: the same word misspelt, not another word
// ("gehte" for "ging" is a wrong form, and marks would only scatter colour).
export function isCloseEnough(typed, expected) {
  if (!typed || !expected || typed === expected) return false;
  const limit = Math.max(2, Math.floor(Array.from(expected).length * 0.4));
  return letterDistance(typed, expected) <= limit;
}
