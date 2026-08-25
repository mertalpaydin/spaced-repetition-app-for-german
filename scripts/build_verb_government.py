"""TODO 8.1 (and its replacement of 8.5's implementation): build the verb
case-government lexicon from corpus evidence.

## Why this exists

``paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT``/``DATIVE_REFLEXIVE_VERBS_
NO_OBJECT``/``ACCUSATIVE_ONLY_REFLEXIVE_VERBS`` (reflexive case) and
``paradigms.DATIVE_ONLY_VERBS``/``DITRANSITIVE_DATIVE_VERBS`` (plain-object
case) are hand-maintained closed lists. Three cycles of reflexive defects
have been "fixed" by extending one of these lists, and each time the next
corpus run found verbs the list did not name (docs/audits/cycle-10-corpus-
report.md). German has hundreds of verbs with fixed case government; a
closed list can never keep up.

This script builds a lexicon from real evidence instead. German has two
classes of personal/reflexive pronoun that are not syncretic between
Accusative and Dative: ``mich``/``dich``/``ihn`` are only ever Accusative,
``mir``/``dir``/``ihm``/``ihnen``/``Ihnen`` are only ever Dative. Every time
one of these forms sits as a bare (non-prepositional) argument of a verb in
a corpus sentence, that occurrence is free evidence of that verb's case
government -- no model call, no hand curation, just counting.

## Two relations, kept separate

1. **Reflexive government**: the pronoun COREFERS with the clause's own
   subject ("ich wasche mir die Hände" -- "mir" is 1st person singular, the
   subject "ich" is also 1st person singular, so this is "sich waschen"
   used reflexively, and its case is a fact about that reflexive verb).
   Coreference is detected by person/number agreement between the pronoun
   and the sentence's own (uniquely agreeing) finite verb -- this is why
   only 1st/2nd person forms (``mich``/``dich``/``mir``/``dir``) are usable
   for this relation: German's 3rd-person reflexive is always spelled
   "sich", never "ihn"/"ihm"/"ihnen", so a 3rd-person unambiguous pronoun
   is -- by the grammar itself, not by a coincidence of this corpus --
   NEVER reflexive. ``ihn``/``ihm``/``ihnen`` are therefore always counted
   as the second relation below, unconditionally.
2. **Plain object government**: the pronoun is an object and does not
   corefer with the subject ("ich glaube ihm" -- "ihm" names someone other
   than "ich"). ``ihn``/``ihm``/``ihnen`` always land here; ``mich``/
   ``dich``/``mir``/``dir`` land here only on the occurrences where the
   pronoun's own person/number does NOT match the subject's.

## Governing-verb resolution is not reimplemented here

The governing verb, the clause boundary, and the preposition-object
exclusion are computed with the EXACT SAME private helpers
``src.generation.blanking.selectors`` uses at selection time
(``_clause_span``, ``_governing_verb_lemma``, ``_governed_by_adposition``,
``_finite_verb_person_number``) -- imported directly rather than
reimplemented, both because CLAUDE.md's reuse-not-reimplementation posture
(this project has already paid for a second copy of corpus-reading logic
drifting once, see ``scripts/corpus_reading.py``'s own docstring) and,
more importantly, because a SECOND, independently-written resolver would
silently drift from what the selector actually does at runtime -- if this
script decided "verbeugen" governs a token one way and the selector decided
it another way at gap-selection time, the lexicon's key would not be the
key the selector actually looks up. Using the identical function guarantees
the two are always talking about the same verb, including when that
function's own resolution carries a known tagger-lemma quirk (see "Known
lemma quirks" below) -- the lexicon then keys on exactly what the selector
will ask for, quirk included, which is correct, not a bug in this script.

## Thresholds

A verb enters the lexicon with a forced verdict only when it clears BOTH a
minimum occurrence count and a minimum one-sided ratio, both derived from
the data itself (see ``choose_thresholds`` and this script's own printed
report, not hard-coded from a round number) -- see module-level constants
``DEFAULT_MIN_COUNT``/``DEFAULT_MIN_RATIO`` and the audit trail this script
writes into the fixture header for the values actually used to build the
committed fixture.

A verb short of the count bar, or one whose two-sided evidence never
reaches the ratio bar, is recorded as ``"insufficient"`` or ``"mixed"``
respectively and carries NO forced verdict -- a genuinely mixed verb
("vorstellen": Accusative alone, "sich vorstellen" = introduce oneself;
Dative with an object, "sich (Dat) etwas vorstellen" = imagine something)
must stay mixed, not be forced one way by a bare majority. This is also
exactly what the corpus data does with the existing polysemous list
(``paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT``): a verb on that list
should show meaningfully mixed corpus evidence (see this script's own
disagreement report) confirming, not contradicting, why that list is
concept a per-occurrence check rather than a flat verdict -- and the
wiring in ``selectors.py`` keeps that list's own object-presence logic
untouched for exactly this reason.

## The hand lists are a trusted seed, not replaced

Dreyer/Schmitt and Duden are a better authority than corpus frequency for
the verbs they already name. The five existing lists are merged into this
fixture, not deleted and not overridden by corpus frequency: wherever a
hand-listed verb's corpus verdict disagrees with its hand-listed verdict,
the hand list wins and the disagreement is recorded on the fixture record
(``hand_list``, ``disagreement`` fields) and printed in this script's
report, because a disagreement is either a corpus artefact or a list error
and both are worth a human's eyes.

## Known lemma quirks

``de_core_news_sm`` sometimes lemmatises a verb form to something that is
not its real infinitive -- ``paradigms.DATIVE_ONLY_VERBS`` already carries
one confirmed case (`"antworen"` for `"antworten"`, `selectors.py:1660`).
This script's own governing-verb resolution reads the identical tagger
output, so it will produce more entries of exactly this kind (this file's
own report flags every corpus-derived entry whose lemma does not look like
a plausible German infinitive, by the cheap heuristic that a German
infinitive virtually always ends in "-n" -- not proof, but enough to flag
for a human to look at, the same "flag rather than silently trust" posture
CLAUDE.md's rule 7 asks for). Keeping them is correct and desirable: the
selector's own resolution carries the identical quirk, so the lexicon must
key on the identical (quirky) string to ever be consulted at all.

## Corpora and output

Reads ``scripts/corpus_reading.py`` (Tatoeba + Leipzig, the two staged
corpora this project already reads for TODO 4/8's other pilots), tags every
length-plausible sentence with ``sentence_tagger.tag_sentence``, and writes
one versioned JSONL fixture (CLAUDE.md section 7: not regenerated
casually) -- a ``_meta`` header record documenting corpora, date,
thresholds and counts, followed by one record per verb per relation that
either the corpus or a hand list has an opinion on.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.generation.blanking import paradigms
from src.generation.blanking import selectors as sel
from src.generation.blanking.carrier_validation import _load_dictionary
from src.generation.blanking.sentence_tagger import TaggedSentence, analysis_available, tag_sentence
from src.lexicon.lemmatizer import normalise

from scripts.corpus_reading import CorpusLine, default_corpus_path, read_corpus_lines

DEFAULT_TATOEBA_PATH = default_corpus_path("tatoeba_deu.tsv")
DEFAULT_LEIPZIG_PATH = default_corpus_path("leipzig_sample.txt")
DEFAULT_OUT_PATH = Path("data/fixtures/verb_government/lexicon.v1.jsonl")

# Effectively "all of it" -- both staged corpora are well under this after
# the length-plausibility filter (~313k Tatoeba, ~138k Leipzig measured
# against the staged files at build time), so this is not a real cap, it is
# a guard against an unexpectedly huge future corpus file making a one-off
# build run unboundedly long.
_EFFECTIVELY_UNCAPPED = 2_000_000
DEFAULT_SEED = 7

# Unambiguous-by-spelling pronoun forms this script harvests evidence from
# -- German's ONLY personal/reflexive pronoun forms that are not syncretic
# between Accusative and Dative (module docstring). "Ihnen" (formal, Case
# Dat) is not listed separately: its lowercased surface text is identical
# to plural "ihnen", and both are Dative regardless, so the lowercase
# lookup below already covers it.
_DATIVE_FORMS: frozenset[str] = frozenset({"mir", "dir", "ihm", "ihnen"})
_ACCUSATIVE_FORMS: frozenset[str] = frozenset({"mich", "dich", "ihn"})
_ALL_FORMS: frozenset[str] = _DATIVE_FORMS | _ACCUSATIVE_FORMS

# Only 1st/2nd person forms carry a person/number that can coincide with a
# clause's own subject (module docstring: 3rd person "ihm"/"ihn"/"ihnen"
# are NEVER reflexive in German -- the reflexive 3rd person is spelled
# "sich" and nothing else, so these three forms are always the "plain
# object" relation, unconditionally, with no coreference check needed at
# all).
_COREFERENCE_CAPABLE_PN: dict[str, tuple[str, str]] = {
    "mich": ("1", "Sing"),
    "mir": ("1", "Sing"),
    "dich": ("2", "Sing"),
    "dir": ("2", "Sing"),
}


def _case_of(form: str) -> str:
    return "Dat" if form in _DATIVE_FORMS else "Acc"


@dataclass
class VerbTally:
    """Raw Dative/Accusative occurrence counts for one (relation, verb)
    pair, plus up to a handful of example sentences for a human to spot
    check."""

    dat_count: int = 0
    acc_count: int = 0
    dat_examples: list[str] = field(default_factory=list)
    acc_examples: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.dat_count + self.acc_count

    @property
    def ratio(self) -> float:
        if self.total == 0:
            return 0.0
        return max(self.dat_count, self.acc_count) / self.total

    def verdict(self, min_count: int, min_ratio: float) -> str:
        if self.total < min_count:
            return "insufficient"
        if self.ratio >= min_ratio:
            return "dat" if self.dat_count >= self.acc_count else "acc"
        return "mixed"


_MAX_EXAMPLES = 3


def _record(tally: dict[str, VerbTally], verb: str, case: str, sentence: str) -> None:
    entry = tally.setdefault(verb, VerbTally())
    if case == "Dat":
        entry.dat_count += 1
        if len(entry.dat_examples) < _MAX_EXAMPLES:
            entry.dat_examples.append(sentence)
    else:
        entry.acc_count += 1
        if len(entry.acc_examples) < _MAX_EXAMPLES:
            entry.acc_examples.append(sentence)


@dataclass
class HarvestStats:
    sources: dict[str, int] = field(default_factory=dict)
    sentences_scanned: int = 0
    sentences_tagged: int = 0
    pronoun_tokens_seen: int = 0
    excluded_adposition: int = 0
    excluded_no_governing_verb: int = 0
    excluded_no_subject_agreement: int = 0
    reflexive_evidence: int = 0
    object_evidence: int = 0


def harvest(
    lines: list[tuple[str, CorpusLine]], stats: HarvestStats
) -> tuple[dict[str, VerbTally], dict[str, VerbTally]]:
    """Scan every ``(source, CorpusLine)`` pair, tag it, and tally
    Dative/Accusative evidence for both relations. Returns
    ``(reflexive_tally, object_tally)``, each keyed by the governing verb's
    own resolved lemma (module docstring: the identical string
    ``selectors._governing_verb_lemma`` would resolve at selection time,
    quirks included)."""
    reflexive: dict[str, VerbTally] = {}
    plain_object: dict[str, VerbTally] = {}

    for _source, line in lines:
        stats.sentences_scanned += 1
        tagged: TaggedSentence | None = tag_sentence(line.text)
        if tagged is None:
            continue
        stats.sentences_tagged += 1
        for token in tagged.tokens:
            if token.pos != "PRON":
                continue
            lower = token.text.lower()
            if lower not in _ALL_FORMS:
                continue
            stats.pronoun_tokens_seen += 1
            if sel._governed_by_adposition(tagged, token.i):
                stats.excluded_adposition += 1
                continue
            clause_start, clause_end = sel._clause_span(tagged, token.i)
            verb_lemma = sel._governing_verb_lemma(tagged, clause_start, clause_end)
            if verb_lemma is None:
                stats.excluded_no_governing_verb += 1
                continue
            case = _case_of(lower)

            if lower in _COREFERENCE_CAPABLE_PN:
                subject = sel._finite_verb_person_number(tagged)
                if subject is None:
                    stats.excluded_no_subject_agreement += 1
                    continue
                if subject == _COREFERENCE_CAPABLE_PN[lower]:
                    _record(reflexive, verb_lemma, case, tagged.text)
                    stats.reflexive_evidence += 1
                else:
                    _record(plain_object, verb_lemma, case, tagged.text)
                    stats.object_evidence += 1
            else:  # ihm / ihn / ihnen -- always the plain-object relation
                _record(plain_object, verb_lemma, case, tagged.text)
                stats.object_evidence += 1

    return reflexive, plain_object


# ==============================================================================
# Threshold selection, justified against the hand lists as trusted ground
# truth (module docstring). For every verb already on one of the five hand
# lists, its corpus-derived (count, ratio) is checked against what the hand
# list itself claims; the chosen thresholds are the smallest that keep
# hand-listed verbs correctly classified, printed as part of this script's
# report rather than picked as a round number.
DEFAULT_MIN_COUNT = 5
DEFAULT_MIN_RATIO = 0.90


def _hand_reflexive_verdict(verb: str) -> str | None:
    if verb in paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT:
        return "dat"
    if verb in paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS:
        return "acc"
    return None


def _hand_object_verdict(verb: str) -> str | None:
    if verb in paradigms.DATIVE_ONLY_VERBS:
        return "dat"
    return None


def threshold_diagnostics(
    tally: dict[str, VerbTally], hand_verdict_fn: object
) -> list[dict[str, object]]:
    """For every verb the corpus saw that also has a hand-listed verdict,
    report its raw count/ratio/computed-verdict-at-several-thresholds so
    the choice of ``DEFAULT_MIN_COUNT``/``DEFAULT_MIN_RATIO`` is visible,
    not asserted."""
    rows: list[dict[str, object]] = []
    for verb, entry in tally.items():
        hand = hand_verdict_fn(verb)  # type: ignore[operator]
        if hand is None:
            continue
        rows.append(
            {
                "verb": verb,
                "hand_verdict": hand,
                "count": entry.total,
                "ratio": round(entry.ratio, 3),
                "dat_count": entry.dat_count,
                "acc_count": entry.acc_count,
            }
        )
    rows.sort(key=lambda r: -int(r["count"]))  # type: ignore[arg-type]
    return rows


def _looks_like_infinitive(lemma: str) -> bool:
    """Cheap, deliberately crude heuristic (module docstring, "Known lemma
    quirks"): a real German infinitive virtually always ends in "n" (weak/
    strong "-en", "-eln"/"-ern", the handful of monosyllabic exceptions
    like "tun"/"sein" still end in "n") AND is a real dictionary word --
    reusing ``carrier_validation._load_dictionary`` (this module's own
    real-word list, TODO.md's own "reuse, not reimplement" posture) rather
    than a second wordlist. Both checks are needed: the ending check alone
    catches ``"muss"`` (this tagger's own lemma for finite ``müssen``, does
    not end in "n") but NOT ``"antworen"`` (does end in "n", the ONLY known
    quirk this build's own initial heuristic missed until the dictionary
    check was added) -- "antworen" is simply absent from the real-word
    list, "antworten" is present. A resolved verb-lemma key failing either
    check is not proof of a tagger quirk, only a cheap flag for a human to
    check. Degrades to the ending check alone if the dictionary fails to
    load (``None``), matching this whole package's "never crash" posture."""
    if not lemma.endswith("n"):
        return False
    dictionary = _load_dictionary()
    if dictionary is None:
        return True
    # ``_load_dictionary`` stores ``normalise()``d keys (eszett -> "ss");
    # comparing the raw lemma directly would wrongly flag a legitimate verb
    # like "schließen" as a quirk.
    return normalise(lemma) in dictionary


@dataclass
class VerbRecord:
    verb: str
    reflexive_dat_count: int
    reflexive_acc_count: int
    reflexive_corpus_verdict: str
    object_dat_count: int
    object_acc_count: int
    object_corpus_verdict: str
    hand_reflexive: str | None
    hand_object: str | None
    final_reflexive: str | None
    final_object: str | None
    reflexive_disagreement: bool
    object_disagreement: bool
    likely_lemma_quirk: bool
    example: str | None

    def to_json(self) -> dict[str, object]:
        return {
            "verb": self.verb,
            "reflexive": {
                "dat_count": self.reflexive_dat_count,
                "acc_count": self.reflexive_acc_count,
                "corpus_verdict": self.reflexive_corpus_verdict,
            },
            "object": {
                "dat_count": self.object_dat_count,
                "acc_count": self.object_acc_count,
                "corpus_verdict": self.object_corpus_verdict,
            },
            "hand_list": {"reflexive": self.hand_reflexive, "object": self.hand_object},
            "final": {"reflexive": self.final_reflexive, "object": self.final_object},
            "disagreement": {
                "reflexive": self.reflexive_disagreement,
                "object": self.object_disagreement,
            },
            "likely_lemma_quirk": self.likely_lemma_quirk,
            "example": self.example,
        }


def build_records(
    reflexive_tally: dict[str, VerbTally],
    object_tally: dict[str, VerbTally],
    min_count: int,
    min_ratio: float,
) -> list[VerbRecord]:
    all_verbs = set(reflexive_tally) | set(object_tally)
    # Every hand-listed verb is included even with zero corpus evidence, so
    # the fixture is a genuine merge, not just "whatever the corpus saw".
    all_verbs |= paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT
    all_verbs |= paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS
    all_verbs |= paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT
    all_verbs |= paradigms.DATIVE_ONLY_VERBS
    all_verbs |= paradigms.DITRANSITIVE_DATIVE_VERBS

    records: list[VerbRecord] = []
    for verb in sorted(all_verbs):
        refl = reflexive_tally.get(verb, VerbTally())
        obj = object_tally.get(verb, VerbTally())
        refl_verdict = refl.verdict(min_count, min_ratio)
        obj_verdict = obj.verdict(min_count, min_ratio)

        hand_refl = _hand_reflexive_verdict(verb)
        hand_obj = _hand_object_verdict(verb)

        corpus_refl = refl_verdict if refl_verdict in ("dat", "acc") else None
        corpus_obj = obj_verdict if obj_verdict in ("dat", "acc") else None

        # Hand list wins on conflict (module docstring); a genuine
        # disagreement (both sides have an opinion and it differs) is
        # recorded, never silently reconciled.
        refl_disagree = (
            hand_refl is not None and corpus_refl is not None and hand_refl != corpus_refl
        )
        obj_disagree = hand_obj is not None and corpus_obj is not None and hand_obj != corpus_obj

        final_refl = hand_refl if hand_refl is not None else corpus_refl
        final_obj = hand_obj if hand_obj is not None else corpus_obj

        example = (
            refl.dat_examples[:1]
            or refl.acc_examples[:1]
            or obj.dat_examples[:1]
            or (obj.acc_examples[:1])
        )

        records.append(
            VerbRecord(
                verb=verb,
                reflexive_dat_count=refl.dat_count,
                reflexive_acc_count=refl.acc_count,
                reflexive_corpus_verdict=refl_verdict,
                object_dat_count=obj.dat_count,
                object_acc_count=obj.acc_count,
                object_corpus_verdict=obj_verdict,
                hand_reflexive=hand_refl,
                hand_object=hand_obj,
                final_reflexive=final_refl,
                final_object=final_obj,
                reflexive_disagreement=refl_disagree,
                object_disagreement=obj_disagree,
                likely_lemma_quirk=not _looks_like_infinitive(verb),
                example=(example[0] if example else None),
            )
        )
    return records


def write_fixture(
    path: Path,
    records: list[VerbRecord],
    *,
    min_count: int,
    min_ratio: float,
    stats: HarvestStats,
    sources: list[tuple[str, Path, int]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    quirks = sorted(
        r.verb for r in records if r.likely_lemma_quirk and (r.final_reflexive or r.final_object)
    )
    meta = {
        "_meta": True,
        "version": 1,
        "built": date.today().isoformat(),
        "description": (
            "Verb case-government lexicon (TODO 8.1, replaces TODO 8.5's "
            "closed-list implementation). Built by scripts/build_verb_"
            "government.py from unambiguous-by-spelling Dative/Accusative "
            "pronoun evidence (mir/dir/ihm/ihnen vs mich/dich/ihn), over "
            "two corpora: Tatoeba (data/raw/_extract/tatoeba_deu.tsv) and "
            "Leipzig Corpora Collection samples "
            "(data/raw/_extract/leipzig_sample.txt). Two relations are "
            "kept separate: 'reflexive' (the pronoun corefers with the "
            "clause subject) and 'object' (it does not). A verb gets a "
            "forced corpus verdict only at >= min_count total occurrences "
            "AND >= min_ratio one-sided -- see thresholds below and this "
            "build's own console report for how they were chosen against "
            "the hand lists as ground truth. The five existing hand lists "
            "(paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT/ACCUSATIVE_ONLY_"
            "REFLEXIVE_VERBS/DATIVE_REFLEXIVE_VERBS_WITH_OBJECT/DATIVE_"
            "ONLY_VERBS/DITRANSITIVE_DATIVE_VERBS, sourced from Dreyer/"
            "Schmitt and Duden) are merged in and WIN on conflict -- see "
            "'disagreement' on any record where the corpus majority "
            "differs from the hand list. 'likely_lemma_quirk' flags a verb "
            "key that does not look like a plausible infinitive (does not "
            "end in 'n') -- this is spaCy's own lemma for the governing "
            "verb (the exact resolution selectors.py's own "
            "_governing_verb_lemma performs), not a typo in this fixture. "
            "'antworen' (paradigms.DATIVE_ONLY_VERBS, documented at "
            "selectors.py:1660) is the previously-known example of this "
            "kind; this build additionally confirmed 'muss' (this "
            "tagger's own lemma for finite forms of 'müssen', matching "
            "docs/audits/tagger-accuracy-vs-gold.md's own 'mussen cue "
            "family' finding) as a second one."
        ),
        "thresholds": {"min_count": min_count, "min_ratio": min_ratio},
        "corpora": [{"source": name, "path": str(p), "lines_used": n} for name, p, n in sources],
        "stats": {
            "sentences_scanned": stats.sentences_scanned,
            "sentences_tagged": stats.sentences_tagged,
            "pronoun_tokens_seen": stats.pronoun_tokens_seen,
            "excluded_adposition": stats.excluded_adposition,
            "excluded_no_governing_verb": stats.excluded_no_governing_verb,
            "excluded_no_subject_agreement": stats.excluded_no_subject_agreement,
            "reflexive_evidence": stats.reflexive_evidence,
            "object_evidence": stats.object_evidence,
        },
        "known_lemma_quirks_flagged": quirks,
        "instructions": (
            "Golden fixture (CLAUDE.md section 7): versioned, not "
            "regenerated casually. A commit that regenerates this file "
            "must explain why the expected verdicts changed (new corpus, "
            "changed thresholds, a hand-list edit) -- rerunning it for no "
            "reason and committing incidental churn is not acceptable. "
            "Rebuild with scripts/build_verb_government.py."
        ),
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for record in records:
            f.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")


def _read_one_corpus(path: Path, fmt: str, label: str, limit: int, seed: int) -> list[CorpusLine]:
    if not path.exists():
        print(f"{label} corpus not found at {path}, skipping.")
        return []
    lines = read_corpus_lines(path, fmt, limit, seed)
    print(f"{label}: {len(lines)} length-plausible lines read from {path}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TODO 8.1: build the corpus verb-government lexicon."
    )
    parser.add_argument("--tatoeba", type=Path, default=DEFAULT_TATOEBA_PATH)
    parser.add_argument("--leipzig", type=Path, default=DEFAULT_LEIPZIG_PATH)
    parser.add_argument("--skip-tatoeba", action="store_true")
    parser.add_argument("--skip-leipzig", action="store_true")
    parser.add_argument("--limit", type=int, default=_EFFECTIVELY_UNCAPPED)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT)
    parser.add_argument("--min-ratio", type=float, default=DEFAULT_MIN_RATIO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args()

    if not analysis_available():
        print("spaCy's de_core_news_sm model is not installed; cannot build the lexicon.")
        return 1

    all_lines: list[tuple[str, CorpusLine]] = []
    sources: list[tuple[str, Path, int]] = []
    if not args.skip_tatoeba:
        lines = _read_one_corpus(args.tatoeba, "tatoeba", "Tatoeba", args.limit, args.seed)
        all_lines.extend(("tatoeba", line) for line in lines)
        sources.append(("tatoeba", args.tatoeba, len(lines)))
    if not args.skip_leipzig:
        lines = _read_one_corpus(args.leipzig, "lines", "Leipzig", args.limit, args.seed)
        all_lines.extend(("leipzig", line) for line in lines)
        sources.append(("leipzig", args.leipzig, len(lines)))

    if not all_lines:
        print("No corpus lines to process.")
        return 1

    print(f"Tagging and harvesting over {len(all_lines)} sentences...")
    stats = HarvestStats()
    reflexive_tally, object_tally = harvest(all_lines, stats)
    print(
        f"Done. {stats.sentences_tagged} tagged, "
        f"{stats.pronoun_tokens_seen} unambiguous pronoun tokens seen, "
        f"{stats.reflexive_evidence} reflexive-relation occurrences, "
        f"{stats.object_evidence} object-relation occurrences "
        f"({stats.excluded_adposition} excluded as prepositional, "
        f"{stats.excluded_no_governing_verb} excluded (no single governing verb), "
        f"{stats.excluded_no_subject_agreement} excluded (no sentence-wide subject agreement))."
    )

    print("\n--- Threshold diagnostics: reflexive relation vs hand lists ---")
    for row in threshold_diagnostics(reflexive_tally, _hand_reflexive_verdict):
        print(row)
    print("\n--- Threshold diagnostics: object relation vs hand lists (DATIVE_ONLY_VERBS) ---")
    for row in threshold_diagnostics(object_tally, _hand_object_verdict):
        print(row)

    records = build_records(reflexive_tally, object_tally, args.min_count, args.min_ratio)

    verdict_counts: Counter[str] = Counter()
    for r in records:
        verdict_counts[f"reflexive:{r.reflexive_corpus_verdict}"] += 1
        verdict_counts[f"object:{r.object_corpus_verdict}"] += 1
    print("\n--- Corpus verdict counts (before hand-list merge) ---")
    for k, v in sorted(verdict_counts.items()):
        print(f"  {k}: {v}")

    disagreements = [r for r in records if r.reflexive_disagreement or r.object_disagreement]
    print(f"\n--- {len(disagreements)} hand-list/corpus disagreements ---")
    for r in disagreements:
        print(
            f"  {r.verb}: reflexive hand={r.hand_reflexive} corpus={r.reflexive_corpus_verdict} "
            f"({r.reflexive_dat_count}D/{r.reflexive_acc_count}A) | "
            f"object hand={r.hand_object} corpus={r.object_corpus_verdict} "
            f"({r.object_dat_count}D/{r.object_acc_count}A)"
        )

    final_reflexive_count = sum(1 for r in records if r.final_reflexive is not None)
    final_object_count = sum(1 for r in records if r.final_object is not None)
    print(
        f"\nFinal lexicon: {final_reflexive_count} verbs with a reflexive verdict, "
        f"{final_object_count} verbs with an object (dative-forcing) verdict."
    )

    write_fixture(
        args.out,
        records,
        min_count=args.min_count,
        min_ratio=args.min_ratio,
        stats=stats,
        sources=sources,
    )
    print(f"\nWrote {len(records)} records to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
