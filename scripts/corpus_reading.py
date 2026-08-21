"""Shared corpus-reading helpers for TODO 4 (``scripts.eval_corpus_coverage``,
``scripts.step7_corpus_pilot``).

CLAUDE.md's reuse-do-not-reimplement posture (this project has already paid
for a second copy drifting out of sync more than once): the length-plausible-
carrier filter and the two supported line formats (Tatoeba's ``id\\tlang\\t
sentence``, Leipzig's ``id\\tsentence``) used to live only inside
``eval_corpus_coverage.py``, which discarded each line's own id the moment it
read it -- fine for that script (it only ever counted candidates), wrong for
``step7_corpus_pilot.py``, which has to trace an accepted item back to the
exact corpus line its carrier came from. Rather than copy the reader and
silently let the two drift, this module is the one reader both scripts call:
``read_corpus_lines`` keeps the id, ``eval_corpus_coverage.read_sentences``
is now a one-line wrapper that drops it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

# Carrier-plausible bounds, matching scripts/eval_tagger_vs_gold.py so every
# measurement in this project's TODO 4 work is talking about the same kind
# of sentence.
MIN_CHARS = 25
MAX_CHARS = 160
MIN_WORDS = 5
MAX_WORDS = 18

# The two corpus line formats this project reads. ``lines`` is one sentence
# per line, optionally ``<id>\t<sentence>`` (Leipzig's own
# ``*-sentences.txt`` shape); ``tatoeba`` is Tatoeba's per-language export,
# ``<id>\t<lang>\t<sentence>``.
CorpusFormat = str


@dataclass(frozen=True)
class CorpusLine:
    """One corpus line that survived the length-plausibility filter: its own
    id (the corpus's own line/sentence id, empty string if the format has
    none) and its text."""

    line_id: str
    text: str


def is_plausible_carrier(text: str) -> bool:
    """Cheap pre-tagging filter: length, terminal punctuation, no markup.

    Deliberately crude. Real judgement is carrier validation's job; this only
    exists so the expensive tagging pass is not spent on obvious junk such as
    Leipzig's price fragments and table rows.
    """
    if not (MIN_CHARS <= len(text) <= MAX_CHARS):
        return False
    if not text.endswith((".", "!", "?")):
        return False
    if not (MIN_WORDS <= len(text.split()) <= MAX_WORDS):
        return False
    # A sentence that opens mid-thought, or carries markup or tabular debris,
    # cannot stand alone as a carrier no matter how well it tags.
    return not any(ch in text for ch in "|<>[]{}\t")


def _split_line(text: str, fmt: CorpusFormat) -> tuple[str, str] | None:
    """One raw file line to ``(line_id, sentence)``, or ``None`` if the line
    does not carry the fields ``fmt`` requires."""
    if fmt == "tatoeba":
        parts = text.split("\t")
        if len(parts) < 3:
            return None
        return parts[0], parts[2]
    if "\t" in text:
        line_id, sentence = text.split("\t", 1)
        return line_id, sentence
    return "", text


def read_corpus_lines(path: Path, fmt: CorpusFormat, limit: int, seed: int) -> list[CorpusLine]:
    """Read up to ``limit`` length-plausible ``CorpusLine``s from ``path``.

    Reads the whole file, filters, then samples, rather than taking the first
    ``limit`` lines: both corpora this project reads are ordered (Tatoeba by
    contribution id, Leipzig by source document), so a prefix is not a sample
    of the corpus.
    """
    raw: list[CorpusLine] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.rstrip("\n")
            if not text:
                continue
            split = _split_line(text, fmt)
            if split is None:
                continue
            line_id, sentence = split
            if not is_plausible_carrier(sentence):
                continue
            raw.append(CorpusLine(line_id, sentence))
    random.Random(seed).shuffle(raw)
    return raw[:limit]


def read_sentence_texts(path: Path, fmt: CorpusFormat, limit: int, seed: int) -> list[str]:
    """``read_corpus_lines``, discarding each line's own id -- what a caller
    that only ever needs the sentence text (``eval_corpus_coverage.py``)
    wants."""
    return [line.text for line in read_corpus_lines(path, fmt, limit, seed)]
