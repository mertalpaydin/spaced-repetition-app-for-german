"""Step 7: verify-only pilot over corpus-extracted sentences (TODO 4).

This is the first time a corpus-derived item is JUDGED rather than merely
COUNTED. ``scripts/eval_corpus_coverage.py`` (TODO 4.3) already answered "does
the corpus contain the construction" for all 49 topics; this script is the
next question -- "is what the corpus and the pipeline together produce any
good" -- and it answers it the same way ``scripts/step6_blank_pilot.py``
answers it for generated carriers: run the model verification backstop over
a sample and report verified/rejected/not-run honestly, never guessed.

## No generation at all

Every other pilot in this project asks a model for sentences first. This one
does not. Sentences come from two corpora already on disk; the ONLY model
call this script makes is the verification pass at the very end. Pipeline:

    read corpus -> length filter -> carrier validation -> selectors/blank/
    uniqueness (``pipeline.blank_sentences``) -> per-topic CEFR filter ->
    balanced per-topic sample -> model verification -> write outputs

## Balanced sampling, not random (the brief's own reasoning)

Candidates are wildly uneven across the 49 topics -- ``nomen_plural`` yields
tens of thousands per corpus, ``zustandspassiv_zeiten`` a handful
(docs/audits/corpus-coverage.md). A random sample of ~400 items would be
almost entirely plural nouns and personal pronouns and would say nothing
about the rare topics an audit most needs to see. ``--per-topic-quota``
(default 10) instead draws up to that many items from EVERY topic that has
any, so a 49-topic run lands near 400 total (comparable to cycle 9's 428,
so defect rates can be compared directly) with every topic represented. A
topic short of its quota contributes everything it has and the shortfall is
recorded, never silently absorbed into a smaller "total" that hides which
topics were thin. Sampling is seeded per topic (``--seed``) so a rerun with
the same seed audits the exact same items.

## Per-topic CEFR filtering, not one global ceiling

docs/audits/corpus-coverage.md's own correction, quoted because it is the
reason this script's filter works the way it does: "a B1 grammar topic
should not be restricted to A1 words... `data/taxonomy.yaml` already assigns
every topic its own level... So the filter that matters is per topic, at
that topic's own level." The SAME sentence can legitimately be an eligible
carrier for a B1 topic and ineligible for an A1 one, so the ceiling is
applied per (item, item's own topic) pair via ``VocabularyStore.
validate_sentence``, never once for the whole run.

**Implementation note, not a contract change**: this filter is applied AFTER
``pipeline.blank_sentences`` has already selected candidates, not by
pre-splitting the sentence pool into 49 differently-filtered pools and
calling ``blank_sentences`` 49 times. The two are equivalent in what they
accept or reject -- a selector's decision depends only on POS tags and
morphology, never on a word's CEFR level, so whether the ceiling check runs
before or after selection changes nothing about whether a given (sentence,
topic) pair passes. Running the tag-and-select pass ONCE over the whole
carrier-valid pool (spaCy tagging is cached per sentence text, see
``sentence_tagger.tag_sentence``) rather than 49 times is what keeps a
"finishes in a few minutes" run finishing in a few minutes -- 49 full passes
over tens of thousands of sentences each would cost roughly 49 times the
tagging work this design actually spends. The one real difference this
ordering can produce: ``blank_sentences``'s own cross-topic-duplicate
resolution runs BEFORE the per-topic CEFR filter, so a winning item that is
later dropped for its own topic's ceiling is not handed back to the topic
that lost the original collision. This is a small, honest cost (documented
here rather than silently accepted) of not re-running cross-topic dedup once
per topic's own filtered pool, which would undo the performance win this
design exists for.

## Lemma diversity within a topic (TODO.md 8.11)

The balanced sampler above fixes topic coverage; it says nothing about what
is INSIDE a topic's own quota. docs/audits/cycle-10-corpus-report.md's own
finding: the corpus is Zipf-distributed, so an unweighted per-topic draw
returns the same dominant lemma over and over -- 7 of 10
``partizip_i_attributiv`` items blanking "laufend", 3 of 9
``verb_praesens_vokalwechsel`` items blanking "gibt" in that cycle's own
sample. Every one of those items is individually correct; a learner meeting
"laufend" seven times in a ten-item topic is still learning far less than
the item count suggests, and an audit sampled from that pool sees the same
word instead of the topic's real range.

``--max-items-per-lemma`` (default ``DEFAULT_MAX_ITEMS_PER_LEMMA``, see that
constant's own comment for why 3) caps how many of a topic's SAMPLED items
may share the same blanked lemma. Keyed on ``CandidateItem.blanked_lemma``
-- the removed token's own spaCy lemma, set once in ``blanker.py`` at the
exact point that token is already in hand, never re-derived here -- not on
``cue`` (a citation-form hint the learner sees, absent for most candidate
kinds, and for a determiner never derived from the answer's own lemma at
all) and not on the unrelated, always-empty ``carrier_lemmas`` field a
different corpus path populates. ``_cap_and_backfill_one_topic`` fills the
slots a capped lemma frees from OTHER lemmas in the same pool (capping
without backfilling would just shrink the topic), and never reduces a topic
below what a plain quota-only sample would have kept it at: a topic whose
candidate pool genuinely has too few distinct lemmas to fill its quota under
the cap still fills its quota, with the cap relaxed only as far as that one
topic needs -- reported per topic (``TopicSampleResult.
lemma_diversity_capped``), never silently absorbed. Deterministic under
``--seed`` exactly like the balanced sampler itself: one full, seeded
shuffle of each topic's own candidate pool, walked once.

## Corpus provenance

Every accepted item records ``corpus_source`` ("tatoeba" or "leipzig") and
``corpus_line_id`` (that corpus's own id for the line the carrier came
from) -- two new ``BankItem`` fields (Pydantic's ``extra="allow"`` on that
model; nothing in ``docs/`` fixes ``BankItem``'s field set as closed), never
overloaded onto ``source_sentence_id`` -- see that field's own use in
``pipeline.py``'s cross-topic-duplicate cap and ``blanker.py``'s
``_source_sentence_id`` for why it already means something else (a content
hash of the carrier sentence text, used as a same-sentence key across
topics, not a corpus locator). This script recovers each item's
``corpus_source``/``corpus_line_id`` by hashing every corpus line's text
with the identical formula ``blanker._source_sentence_id`` uses and matching
it against the item's own ``source_sentence_id`` -- ``_carrier_hash_id``
below is a deliberate, tiny, well-commented duplicate of that one-line
formula (not importable: ``_source_sentence_id`` is private and takes a
``TaggedSentence``, this needs it before tagging, on a raw string), with a
regression test pinning that the two never drift apart.

## CLAUDE.md rule 2

The topic id is never sent to the model. The verification pass
(``model_verification.verify_items``) already enforces this on the prompt
side (that module's own ``_format_item_block``); this script sends it
nothing extra.

## Reuse, not reimplementation

- Corpus readers: ``scripts.corpus_reading`` (lifted out of
  ``eval_corpus_coverage.py`` for this script to share, not copied).
- Carrier validation, selection, blanking: ``src.generation.blanking.
  carrier_validation`` and ``pipeline.blank_sentences`` -- the exact same
  functions ``eval_corpus_coverage.py`` and ``step6_blank_pilot.py`` call.
- Verification: ``src.generation.blanking.model_verification.verify_items``,
  the same backstop ``step6_blank_pilot.py`` uses, over the same
  ``BankItem`` shape.
- Client construction: ``sentence_source.client_from_env``
  (``forbid_batch=True``, ``forbid_paid_lane=False`` -- CLAUDE.md section 9's
  pilot lane policy; this script generates nothing, but the same reasoning
  applies to its one model-backed pass: no real batch job, on-demand once the
  free lane closes).

## Fail loudly (TODO 4.6's own sibling requirement, applied here too)

Exactly ``step6_blank_pilot.py``'s own rule: a nonzero
``verification_report.not_run_count`` fails the run (nonzero exit). A run
whose verification backstop did not execute for some item is not a valid
pilot run, corpus-sourced or not.

## The run report (TODO 4.6)

Every number this script prints is also written to
``data/corpus_pilot_report.json`` -- the same "cost a full round trip
because it only ever existed on the console" lesson TODO 4.6 states for
``step6_blank_pilot.py`` (fixed in the same commit as this script, see that
script's own ``main()``) applies here from day one instead of being learned
the same way twice.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from src.contracts import BankItem, CandidateItem, Topic
from src.generation.batch_client import RejectedCandidateRecord
from src.generation.blanking import carrier_validation
from src.generation.blanking.model_verification import (
    DEFAULT_VERIFICATION_BATCH_SIZE,
    ItemVerdict,
    ModelRejection,
    VerificationReport,
    verify_items,
)
from src.generation.blanking.pipeline import (
    TOPIC_IDS,
    DroppedItem,
    UniquenessSkip,
    blank_sentences,
)
from src.generation.blanking.sentence_source import client_from_env
from src.generation.blanking.sentence_tagger import analysis_available
from src.generation.pilot import _write_rejected_file, _write_review_file
from src.lexicon.vocabulary import VocabularyStore
from src.llm.env import load_env_file
from src.taxonomy.facets import derive_facet
from src.taxonomy.loader import load_taxonomy

from scripts.corpus_reading import CorpusLine, read_corpus_lines

DEFAULT_REVIEW_PATH = Path("data/corpus_pilot_review.jsonl")
DEFAULT_REJECTED_PATH = Path("data/corpus_pilot_rejected.jsonl")
DEFAULT_REPORT_PATH = Path("data/corpus_pilot_report.json")
DEFAULT_VOCAB_PATH = Path("data/fixtures/corpus/vocab_levels.json")

# The staging paths named in this task's own brief -- overridable (the brief:
# "take them as arguments so the script is not bound to a staging path"), but
# a runnable default is worth more than an empty required argument, exactly
# the trade-off ``step6_blank_pilot.py`` and ``eval_corpus_coverage.py`` make
# for their own defaults.
DEFAULT_TATOEBA_PATH = Path(
    "/mnt/user-data/uploads/Language_Learning_App/data/raw/_extract/tatoeba_deu.tsv"
)
DEFAULT_LEIPZIG_PATH = Path(
    "/mnt/user-data/uploads/Language_Learning_App/data/raw/_extract/leipzig_sample.txt"
)

# 40,000 sentences per source, the brief's own default, on the claim that it
# is "plenty to fill a 10-per-topic quota for all but the rarest topics" --
# checked, not assumed, against a real run over the actual staged files (see
# this task's own offline verification report); the timing and per-topic
# numbers from that run are what confirmed the claim rather than just
# repeating it.
DEFAULT_LIMIT_PER_SOURCE = 40_000
DEFAULT_PER_TOPIC_QUOTA = 10
DEFAULT_SEED = 7

# TODO.md 8.11: how many items in ONE topic's sample may share the same
# blanked lemma -- docs/audits/cycle-10-corpus-report.md's own finding (7 of
# 10 partizip_i_attributiv items blank "laufend", 3 of 9
# verb_praesens_vokalwechsel items blank "gibt"). 3, not 2: run both at
# --limit 20000 over the real corpora and compared every topic's own
# post-cap worst single-lemma share, not guessed. The two are NOT simply
# "2 stricter than 3" -- the coverage floor below makes a tighter cap
# actively worse for a topic with only 2-4 distinct lemmas in its whole
# candidate pool, because more of that topic's items are forced through the
# floor (which fills in shuffle order, not evenly) rather than the cap
# itself: at max-items-per-lemma=2, 6 such topics (``passiv_modalverben``,
# ``verben_reflexiv_akk``/``_dat``, and the three ``konjunktiv_ii_*``
# topics, pool sizes 2-4) land on a HIGHER worst-lemma-share than at 3 --
# e.g. ``verben_reflexiv_akk`` at 5 of 10 with cap 2 versus 3 of 10 with cap
# 3. For a topic with real abundance (6+ distinct lemmas in the pool -- 23
# of them at this run's scale), cap 2 does edge out cap 3 (worst share 2 of
# 10 instead of 3 of 10), but every one of those was already far from the
# audit's own complaint. Cap 3 is the value that helps the SCARCE topics
# (where the problem is worst) without over-constraining them, at the cost
# of one extra permitted repeat on the already-healthy ones. The two
# topics the audit actually named land identically either way
# (``partizip_i_attributiv`` 6 of 10, ``verb_praesens_vokalwechsel`` 2 of
# 10 -- the latter was already under either cap in this run's own sample),
# so the choice between 2 and 3 is decided by every OTHER topic's own
# numbers, not by the two named ones. See this task's own verification
# writeup for the full before/after table.
DEFAULT_MAX_ITEMS_PER_LEMMA = 3

# Effectively uncapped, matching eval_corpus_coverage.py's own reasoning: a
# topic/sentence cap here would throw away real candidates before this
# script's own balanced sampler ever gets a chance to choose among them --
# the sampler IS the cap this script wants, applied deliberately and per
# topic, not implicitly by ``blank_sentences``'s own general-purpose
# defaults.
_UNCAPPED = 10**9


def _carrier_hash_id(text: str) -> str:
    """The exact same formula ``blanker._source_sentence_id`` uses on a raw
    sentence string -- see this module's own docstring, "corpus provenance"
    section, for why this is a deliberate small duplicate rather than an
    import, and ``tests/test_step7_corpus_pilot.py`` for the regression test
    that pins the two functions never drifting apart."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"sent_{digest}"


@dataclass(frozen=True)
class CorpusProvenance:
    """One corpus line's identity plus its own text, keyed elsewhere by
    ``_carrier_hash_id(text)`` -- the join key back from a produced
    ``CandidateItem`` (via its ``source_sentence_id``, computed with the
    identical formula) to the exact corpus line its carrier came from."""

    source: str
    line_id: str
    text: str


@dataclass
class TopicSampleResult:
    """One topic's outcome from the balanced sampler: how many candidates
    survived that topic's own CEFR ceiling, how many of those were CEFR
    rejects, the quota, how many were actually sampled, and the shortfall
    (0 unless the topic had fewer candidates than its quota) -- reported,
    never silently absorbed, per this task's own brief."""

    topic_id: str
    cefr: str
    candidates_before_cefr: int
    cefr_rejected: int
    candidates: int
    quota: int
    sampled: int
    # TODO.md 8.11: lemma-diversity diagnostics for this topic's own sample.
    # ``distinct_lemmas_in_pool`` is computed over every CANDIDATE (before
    # sampling), so it is the same number regardless of ``--max-items-per-
    # lemma`` -- it answers "how much real variety did this topic ever have
    # to draw from", independent of the cap. ``distinct_lemmas_sampled`` and
    # ``max_lemma_share_sampled`` describe the SAMPLE actually kept.
    # ``lemma_diversity_capped`` is ``True`` only when this topic's pool did
    # not have enough distinct lemmas to fill its quota while respecting the
    # cap, so the coverage floor (module docstring's own "never below the
    # coverage the quota already guarantees" rule) had to keep a lemma past
    # its cap rather than shrink the topic -- reported per-topic, never
    # silently absorbed, exactly like ``shortfall`` below.
    distinct_lemmas_in_pool: int = 0
    distinct_lemmas_sampled: int = 0
    max_lemma_share_sampled: int = 0
    lemma_diversity_capped: bool = False

    @property
    def shortfall(self) -> int:
        return max(0, self.quota - self.candidates)


@dataclass
class CorpusReadStats:
    """How many lines one corpus contributed, for the report (CLAUDE.md 12:
    every drop is counted, not just the survivors)."""

    source: str
    path: str
    lines_read: int = 0


@dataclass
class CorpusPilotReport:
    """Everything ``main()`` needs to print AND to write to
    ``data/corpus_pilot_report.json`` (TODO 4.6) -- one object, so the two
    can never drift apart the way a hand-console-paste and a committed file
    already have once in this project's history."""

    seed: int = DEFAULT_SEED
    per_topic_quota: int = DEFAULT_PER_TOPIC_QUOTA
    max_items_per_lemma: int = DEFAULT_MAX_ITEMS_PER_LEMMA
    limit_per_source: int = DEFAULT_LIMIT_PER_SOURCE
    ran_live: bool = False
    corpus_reads: list[CorpusReadStats] = field(default_factory=list)
    length_filtered_total: int = 0
    carrier_valid_total: int = 0
    carrier_rejected_by_reason: dict[str, int] = field(default_factory=dict)
    sentences_tagged: int = 0
    raw_candidates_total: int = 0
    cross_topic_duplicates_dropped: dict[str, int] = field(default_factory=dict)
    skips_by_uniqueness: dict[str, int] = field(default_factory=dict)
    skips_by_type_ineligibility: dict[str, int] = field(default_factory=dict)
    topic_results: list[TopicSampleResult] = field(default_factory=list)
    sampled_total: int = 0
    provenance_missing: int = 0
    verification_attempted: bool = False
    verified_count: int = 0
    model_rejected_count: int = 0
    not_run_count: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=dict)
    not_run_reasons: dict[str, int] = field(default_factory=dict)
    accepted_total: int = 0
    review_file: str = ""
    rejected_file: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "run": {
                "seed": self.seed,
                "per_topic_quota": self.per_topic_quota,
                "max_items_per_lemma": self.max_items_per_lemma,
                "limit_per_source": self.limit_per_source,
                "ran_live": self.ran_live,
            },
            "corpus_reads": [
                {"source": c.source, "path": c.path, "lines_read": c.lines_read}
                for c in self.corpus_reads
            ],
            "length_filtered_total": self.length_filtered_total,
            "carrier_validation": {
                "carrier_valid": self.carrier_valid_total,
                "rejected_by_reason": self.carrier_rejected_by_reason,
            },
            "blanking": {
                "sentences_tagged": self.sentences_tagged,
                "raw_candidates_total": self.raw_candidates_total,
                "cross_topic_duplicates_dropped": self.cross_topic_duplicates_dropped,
                "skips_by_uniqueness": self.skips_by_uniqueness,
                "skips_by_type_ineligibility": self.skips_by_type_ineligibility,
            },
            "per_topic": [
                {
                    "topic_id": t.topic_id,
                    "cefr": t.cefr,
                    "candidates_before_cefr": t.candidates_before_cefr,
                    "cefr_rejected": t.cefr_rejected,
                    "candidates": t.candidates,
                    "quota": t.quota,
                    "sampled": t.sampled,
                    "shortfall": t.shortfall,
                    "distinct_lemmas_in_pool": t.distinct_lemmas_in_pool,
                    "distinct_lemmas_sampled": t.distinct_lemmas_sampled,
                    "max_lemma_share_sampled": t.max_lemma_share_sampled,
                    "lemma_diversity_capped": t.lemma_diversity_capped,
                }
                for t in self.topic_results
            ],
            "sampled_total": self.sampled_total,
            "provenance_missing": self.provenance_missing,
            "verification": {
                "attempted": self.verification_attempted,
                "verified_count": self.verified_count,
                "rejected_count": self.model_rejected_count,
                "not_run_count": self.not_run_count,
                "rejected_reasons": self.rejected_reasons,
                "not_run_reasons": self.not_run_reasons,
            },
            "accepted_total": self.accepted_total,
            "output_files": {
                "review": self.review_file,
                "rejected": self.rejected_file,
            },
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class CefrFilterResult:
    """Everything ``_filter_candidates_by_topic_cefr`` produces: the survivors,
    grouped by topic (what the balanced sampler draws from), and enough
    counters/records to report the filter's own cost honestly, per topic."""

    items_by_topic: dict[str, list[CandidateItem]]
    candidates_before_cefr: dict[str, int]
    cefr_rejected: dict[str, int]
    rejection_records: list[RejectedCandidateRecord]
    provenance_missing: int


def _filter_candidates_by_topic_cefr(
    items: list[CandidateItem],
    topics_by_id: dict[str, Topic],
    provenance_by_hash: dict[str, CorpusProvenance],
    vocab_store: VocabularyStore,
) -> CefrFilterResult:
    """The per-topic CEFR filter (module docstring's own section): each
    candidate's ORIGINAL carrier sentence (recovered via ``provenance_by_hash``,
    never the blanked prompt, which is missing the answer word) is checked
    against its OWN topic's ceiling, never one ceiling for the whole run --
    the same sentence can pass for a B1 topic and fail for an A1 one on this
    check, by design (docs/audits/corpus-coverage.md's own correction).

    A pure function of its four arguments (no filesystem, no network, no
    clock) so ``tests/test_step7_corpus_pilot.py`` can exercise it directly,
    with a fake ``VocabularyStore`` and hand-built topics, rather than only
    through a full ``main()`` run."""
    items_by_topic: dict[str, list[CandidateItem]] = {}
    candidates_before_cefr: dict[str, int] = {}
    cefr_rejected: dict[str, int] = {}
    rejection_records: list[RejectedCandidateRecord] = []
    provenance_missing = 0

    for item in items:
        topic = topics_by_id.get(item.topic_id)
        if topic is None:
            # Should not happen -- SELECTORS and taxonomy.yaml are checked
            # for exact agreement elsewhere (pipeline.py's own
            # _eligible_types_by_topic) -- but never assumed.
            continue
        candidates_before_cefr[item.topic_id] = candidates_before_cefr.get(item.topic_id, 0) + 1
        provenance = (
            provenance_by_hash.get(item.source_sentence_id)
            if item.source_sentence_id is not None
            else None
        )
        if provenance is None:
            provenance_missing += 1
            continue
        violations = vocab_store.validate_sentence(provenance.text, topic.cefr)
        if violations:
            cefr_rejected[item.topic_id] = cefr_rejected.get(item.topic_id, 0) + 1
            rejection_records.append(_cefr_rejection_to_record(item, violations, topic))
            continue
        items_by_topic.setdefault(item.topic_id, []).append(item)

    return CefrFilterResult(
        items_by_topic=items_by_topic,
        candidates_before_cefr=candidates_before_cefr,
        cefr_rejected=cefr_rejected,
        rejection_records=rejection_records,
        provenance_missing=provenance_missing,
    )


def _read_one_corpus(
    path: Path, fmt: str, source_name: str, limit: int, seed: int
) -> list[CorpusLine]:
    """One corpus's own lines, or an empty list with a warning printed if the
    file cannot be read -- a missing corpus degrades the run (fewer
    candidates, possibly more topic shortfalls), it never crashes it, so a
    caller missing one of the two files this script defaults to still gets a
    real run over whichever it has."""
    if not path.exists():
        print(f"  WARNING: {source_name} corpus not found at {path}; skipping this source.")
        return []
    return read_corpus_lines(path, fmt, limit, seed)


def _lemma_key(item: CandidateItem) -> str:
    """The diversity-cap grouping key for one candidate (TODO.md 8.11):
    ``CandidateItem.blanked_lemma`` when spaCy resolved one for the blanked
    token -- see that field's own docstring (``src/contracts.py``) for why
    this, and not ``cue`` or ``carrier_lemmas``, is the right key. Falls back
    to the lowercased surface answer for the rare candidate whose token
    lemmatised to nothing (checked against the real corpus, see this
    module's own ``DEFAULT_MAX_ITEMS_PER_LEMMA`` comment: the fallback fires
    for a small minority of items, never the common case, so it does not
    itself reintroduce the monotony this cap exists to fix)."""
    return item.blanked_lemma if item.blanked_lemma else item.proposed_answer.lower()


def _cap_and_backfill_one_topic(
    candidates: list[CandidateItem],
    *,
    quota: int,
    max_items_per_lemma: int,
    rng: random.Random,
) -> tuple[list[CandidateItem], bool]:
    """One topic's own sample, diversified by lemma (TODO.md 8.11). Only
    ever called when ``len(candidates) > quota`` -- the caller
    (``_sample_per_topic``) already takes every candidate, uncapped, when a
    topic is AT OR UNDER quota, so "cap without shrinking" never has to
    explain away a topic that had nothing spare to diversify with in the
    first place.

    Walks a full deterministic shuffle of ``candidates`` once, keeping a
    candidate while its own lemma (``_lemma_key``) has not yet reached
    ``max_items_per_lemma`` and setting every skipped-for-cap candidate
    aside in ``reserve``, in the same shuffle order, rather than discarding
    it. If the cap-respecting pass alone does not reach ``quota`` -- the
    pool genuinely does not have enough DISTINCT lemmas to fill it any other
    way -- the freed slots are filled from ``reserve``, in order, until
    ``quota`` is met. Because ``len(candidates) > quota`` is guaranteed by
    the caller, ``reserve`` always holds enough items to finish the job:
    ``len(reserve) >= len(candidates) - quota >= quota - len(kept before
    backfill)`` whenever the cap-respecting pass falls short. This is the
    "prefer diversity, but never below the coverage the quota already
    guarantees" rule from this task's own brief, and the second return value
    (``True`` only when the backfill actually ran) is exactly "which topics
    hit that condition" for the caller to report.
    """
    order = rng.sample(candidates, len(candidates))
    lemma_counts: Counter[str] = Counter()
    kept: list[CandidateItem] = []
    reserve: list[CandidateItem] = []

    for item in order:
        if len(kept) == quota:
            break
        key = _lemma_key(item)
        if lemma_counts[key] < max_items_per_lemma:
            kept.append(item)
            lemma_counts[key] += 1
        else:
            reserve.append(item)

    floor_applied = len(kept) < quota
    if floor_applied:
        for item in reserve:
            if len(kept) == quota:
                break
            kept.append(item)
            lemma_counts[_lemma_key(item)] += 1

    return kept, floor_applied


def _sample_per_topic(
    items_by_topic: dict[str, list[CandidateItem]],
    candidates_before_cefr: dict[str, int],
    cefr_rejected: dict[str, int],
    topics_by_id: dict[str, Topic],
    topic_ids: Sequence[str],
    *,
    quota: int,
    seed: int,
    max_items_per_lemma: int = DEFAULT_MAX_ITEMS_PER_LEMMA,
) -> tuple[list[CandidateItem], list[TopicSampleResult]]:
    """The balanced sampler (module docstring's own section): up to ``quota``
    items per topic, every topic in ``topic_ids`` represented (even one with
    zero candidates -- it still gets a ``TopicSampleResult`` row, module
    docstring's "shortfall is reported, never silently absorbed").
    ``topic_ids`` is the 49 SELECTORS topics (``pipeline.TOPIC_IDS``), not
    every topic ``data/taxonomy.yaml`` knows about -- this script only ever
    produces candidates for a topic with a computable selector, so sampling
    over the full ~87-topic taxonomy would report a permanent, meaningless
    "shortfall" for every topic this pipeline was never going to reach in
    the first place. ``topics_by_id`` is still the full taxonomy lookup (for
    each sampled topic's own ``cefr``).

    TODO.md 8.11: within the ``quota``, no more than ``max_items_per_lemma``
    sampled items may share the same blanked lemma (``_lemma_key``) -- see
    ``_cap_and_backfill_one_topic`` for how the freed slots are filled from
    OTHER lemmas rather than left empty, and for the coverage floor that
    keeps a lemma-poor topic from being starved below what a plain
    quota-only sampler would have kept it at. A topic AT OR UNDER quota
    (the ``len(candidates) <= quota`` branch, unchanged from before this
    cap existed) already takes every candidate it has regardless of lemma,
    exactly as it always has -- there is nothing to diversify AWAY from
    when nothing is being left out.

    Deterministic per (``seed``, topic id): a rerun with the same seed
    samples the exact same items from the exact same candidate LIST, because
    ``items_by_topic``'s own ordering is itself deterministic (``pipeline.
    blank_sentences`` processes sentences in the fixed order they were
    handed, and this script's own corpus read is already shuffled once,
    deterministically, by ``read_corpus_lines``). Each topic gets its own
    ``random.Random`` instance seeded on ``f"{seed}:{topic_id}"`` rather than
    one shared RNG walked topic by topic, so adding or removing an earlier
    topic from the run can never change a later topic's own sample.
    """
    sampled: list[CandidateItem] = []
    results: list[TopicSampleResult] = []
    for topic_id in sorted(topic_ids):
        topic = topics_by_id[topic_id]
        candidates = items_by_topic.get(topic_id, [])
        rng = random.Random(f"{seed}:{topic_id}")
        if len(candidates) <= quota:
            chosen = list(candidates)
            lemma_diversity_capped = False
        else:
            chosen, lemma_diversity_capped = _cap_and_backfill_one_topic(
                candidates, quota=quota, max_items_per_lemma=max_items_per_lemma, rng=rng
            )
        sampled.extend(chosen)
        sampled_lemma_counts = Counter(_lemma_key(item) for item in chosen)
        results.append(
            TopicSampleResult(
                topic_id=topic_id,
                cefr=topic.cefr,
                candidates_before_cefr=candidates_before_cefr.get(topic_id, 0),
                cefr_rejected=cefr_rejected.get(topic_id, 0),
                candidates=len(candidates),
                quota=quota,
                sampled=len(chosen),
                distinct_lemmas_in_pool=len({_lemma_key(item) for item in candidates}),
                distinct_lemmas_sampled=len(sampled_lemma_counts),
                max_lemma_share_sampled=max(sampled_lemma_counts.values(), default=0),
                lemma_diversity_capped=lemma_diversity_capped,
            )
        )
    return sampled, results


def _corpus_item_id(item: CandidateItem) -> str:
    """Content-addressed id, the same scheme ``step6_blank_pilot._blank_item_id``
    uses (topic/type/difficulty/prompt/answer), with a ``corpus_`` prefix
    distinguishing a corpus-sourced item from a ``blank_``-prefixed
    generate-then-blank one or a ``gen_``-prefixed LLM-direct one in a
    combined audit view."""
    payload = json.dumps(
        {
            "topic_id": item.topic_id,
            "type": item.type,
            "difficulty": item.difficulty,
            "prompt": item.prompt,
            "proposed_answer": item.proposed_answer,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"corpus_{digest}"


def _to_bank_item(
    item: CandidateItem,
    topic: Topic,
    corpus_source: str,
    corpus_line_id: str,
) -> BankItem:
    """``CandidateItem`` -> ``BankItem`` in the same field set
    ``data/blank_pilot_review.jsonl`` uses, PLUS this script's own two
    provenance fields (module docstring's "corpus provenance" section).
    ``topic``/``corpus_source``/``corpus_line_id`` are resolved by the
    caller (``main()``), which already has to look each of them up to decide
    whether to keep the item at all -- this function only ever builds the
    ``BankItem``, it does not decide eligibility.

    ``corpus_source``/``corpus_line_id`` are attached via ``model_copy(update=...)``
    rather than as constructor keywords: mypy --strict synthesises
    ``BankItem.__init__`` from its DECLARED fields (pydantic's
    ``@dataclass_transform`` marker, honoured natively by this project's mypy
    version even with no ``pydantic.mypy`` plugin registered), so it rejects
    an undeclared keyword at the constructor even though the model's own
    ``extra=\"allow\"`` accepts it at runtime -- confirmed directly against
    this project's own mypy config before choosing this shape.
    ``model_copy``'s ``update`` parameter is typed as a generic string-keyed
    mapping, so it does not hit the same synthesised-signature check."""
    bank_item = BankItem(
        id=_corpus_item_id(item),
        topic_id=item.topic_id,
        tag_id=item.topic_id,
        dimension="grammar",
        type=item.type,
        difficulty=item.difficulty,
        cefr=topic.cefr,
        prompt=item.prompt,
        accepted_answers=[item.proposed_answer],
        distractors=item.distractors,
        cue=item.cue,
        rule_hint=item.rule_hint,
        facet=item.facet,
        confusion_group=topic.confusion_group,
        domain=item.domain,
        carrier_lemmas=item.carrier_lemmas,
        source_sentence_id=item.source_sentence_id,
    )
    facet = derive_facet(bank_item, topic)
    update: dict[str, object] = {"corpus_source": corpus_source, "corpus_line_id": corpus_line_id}
    if facet != bank_item.facet:
        update["facet"] = facet
    return bank_item.model_copy(update=update)


def _model_rejection_to_record(rejection: ModelRejection) -> RejectedCandidateRecord:
    """Mirrors ``step6_blank_pilot._model_rejection_to_record`` exactly."""
    return RejectedCandidateRecord(
        topic_id=rejection.topic_id,
        type="cloze_free",
        difficulty=1,
        prompt=rejection.prompt,
        proposed_answer=" / ".join(rejection.accepted_answers),
        layer_failed=None,
        error_type="model_verification_rejected",
        reason=rejection.reason,
    )


def _uniqueness_skip_to_record(skip: UniquenessSkip) -> RejectedCandidateRecord:
    return RejectedCandidateRecord(
        topic_id=skip.topic_id,
        type="cloze_free",
        difficulty=1,
        prompt=skip.prompt,
        proposed_answer=skip.proposed_answer,
        layer_failed=None,
        error_type=skip.reason,
        reason=skip.reason,
    )


def _dropped_item_to_record(dropped: DroppedItem) -> RejectedCandidateRecord:
    return RejectedCandidateRecord(
        topic_id=dropped.topic_id,
        type="cloze_free",
        difficulty=1,
        prompt=dropped.prompt,
        proposed_answer=dropped.proposed_answer,
        layer_failed=None,
        error_type=dropped.reason,
        reason=dropped.reason,
    )


def _cefr_rejection_to_record(
    item: CandidateItem, violations: list[str], topic: Topic
) -> RejectedCandidateRecord:
    """One candidate whose OWN topic's CEFR ceiling it failed (module
    docstring's "per-topic CEFR filtering" section) -- kept as its own
    ``error_type`` (``cefr_ceiling_violation``) distinct from every
    structural/model reason, and carrying the offending words in ``reason``
    so the rejection is diagnosable without re-running the ceiling check by
    hand."""
    return RejectedCandidateRecord(
        topic_id=item.topic_id,
        type=item.type,
        difficulty=item.difficulty,
        prompt=item.prompt,
        proposed_answer=item.proposed_answer,
        layer_failed=None,
        error_type="cefr_ceiling_violation",
        reason=f"above {topic.cefr} ceiling: {', '.join(violations)}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 7: corpus-sourced verify-only pilot.")
    parser.add_argument("--tatoeba", type=Path, default=DEFAULT_TATOEBA_PATH)
    parser.add_argument("--leipzig", type=Path, default=DEFAULT_LEIPZIG_PATH)
    parser.add_argument("--skip-tatoeba", action="store_true")
    parser.add_argument("--skip-leipzig", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT_PER_SOURCE,
        help="Corpus lines to scan PER SOURCE (default 40,000).",
    )
    parser.add_argument("--per-topic-quota", type=int, default=DEFAULT_PER_TOPIC_QUOTA)
    parser.add_argument(
        "--max-items-per-lemma",
        type=int,
        default=DEFAULT_MAX_ITEMS_PER_LEMMA,
        help=(
            "Cap on how many sampled items in one topic may share the same "
            "blanked lemma (TODO.md 8.11; default 3). Freed slots are "
            "backfilled from other lemmas; a topic with too few distinct "
            "lemmas to fill its quota under the cap still fills its quota "
            "(the cap never reduces a topic below what a plain quota-only "
            "sample would have kept it at)."
        ),
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--vocab-path", type=Path, default=DEFAULT_VOCAB_PATH)
    parser.add_argument("--review-file", type=str, default=str(DEFAULT_REVIEW_PATH))
    parser.add_argument("--rejected-file", type=str, default=str(DEFAULT_REJECTED_PATH))
    parser.add_argument("--report-file", type=str, default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args()

    load_env_file()
    llm_client = client_from_env()
    ran_live = llm_client is not None

    report = CorpusPilotReport(
        seed=args.seed,
        per_topic_quota=args.per_topic_quota,
        max_items_per_lemma=args.max_items_per_lemma,
        limit_per_source=args.limit,
        ran_live=ran_live,
    )

    if not analysis_available():
        print(
            "spaCy's de_core_news_sm model is not installed; no items can be "
            "produced (degrading cleanly, not crashing). Install it "
            "(`python -m spacy download de_core_news_sm`) and re-run."
        )
        return 0

    all_lines: list[tuple[str, CorpusLine]] = []
    if not args.skip_tatoeba:
        lines = _read_one_corpus(args.tatoeba, "tatoeba", "Tatoeba", args.limit, args.seed)
        report.corpus_reads.append(
            CorpusReadStats(source="tatoeba", path=str(args.tatoeba), lines_read=len(lines))
        )
        all_lines.extend(("tatoeba", line) for line in lines)
    if not args.skip_leipzig:
        lines = _read_one_corpus(args.leipzig, "lines", "Leipzig", args.limit, args.seed)
        report.corpus_reads.append(
            CorpusReadStats(source="leipzig", path=str(args.leipzig), lines_read=len(lines))
        )
        all_lines.extend(("leipzig", line) for line in lines)

    if not all_lines:
        print("No corpus lines read from either source; nothing to do.")
        report.write(Path(args.report_file))
        return 1

    report.length_filtered_total = len(all_lines)
    print(f"  Corpus lines read and length-filtered: {len(all_lines)}")
    for stats in report.corpus_reads:
        print(f"    - {stats.source}: {stats.lines_read} ({stats.path})")

    # Provenance lookup: every corpus line's own hash (module docstring's
    # "corpus provenance" section) -> (source, corpus line id, text).
    # First-seen wins on an exact text collision (two corpus lines with
    # identical text, rare but possible) -- both are equally legitimate
    # sources of that exact sentence, so which one a downstream item is
    # attributed to is arbitrary but never wrong.
    provenance_by_hash: dict[str, CorpusProvenance] = {}
    sentences: list[str] = []
    for source, line in all_lines:
        sentences.append(line.text)
        key = _carrier_hash_id(line.text)
        provenance_by_hash.setdefault(key, CorpusProvenance(source, line.line_id, line.text))

    validation = carrier_validation.validate_carriers(sentences)
    report.carrier_valid_total = len(validation.accepted)
    report.carrier_rejected_by_reason = dict(validation.rejected_by_reason)
    print(f"  Carrier-valid: {len(validation.accepted)} of {len(sentences)}")
    for reason, count in validation.rejected_by_reason.most_common():
        print(f"    - {reason}: {count}")

    if not validation.accepted:
        print("No sentences survived carrier validation; nothing to do.")
        report.write(Path(args.report_file))
        return 1

    # ONE tag-and-select pass over every carrier-valid sentence, every topic
    # -- see module docstring's "per-topic CEFR filtering" section for why
    # this is one pass rather than 49. Uncapped: this script's own sampler is
    # the cap that matters (module docstring's own note on ``_UNCAPPED``).
    blanking_report = blank_sentences(
        validation.accepted,
        max_items_per_topic=_UNCAPPED,
        max_items_per_sentence=_UNCAPPED,
    )
    report.sentences_tagged = blanking_report.sentences_tagged
    report.raw_candidates_total = blanking_report.total_items
    report.cross_topic_duplicates_dropped = dict(blanking_report.cross_topic_duplicates_dropped)
    report.skips_by_uniqueness = dict(blanking_report.skips_by_uniqueness)
    report.skips_by_type_ineligibility = dict(blanking_report.skips_by_type_ineligibility)
    print(f"  Sentences tagged: {blanking_report.sentences_tagged}")
    print(
        f"  Raw candidates (all 49 topics, before the per-topic CEFR filter): "
        f"{blanking_report.total_items}"
    )

    topics_by_id = {t.id: t for t in load_taxonomy()}
    vocab_store = VocabularyStore.load(args.vocab_path)

    cefr_filter = _filter_candidates_by_topic_cefr(
        blanking_report.items, topics_by_id, provenance_by_hash, vocab_store
    )
    items_by_topic = cefr_filter.items_by_topic
    candidates_before_cefr = cefr_filter.candidates_before_cefr
    cefr_rejected = cefr_filter.cefr_rejected
    cefr_rejection_records = cefr_filter.rejection_records
    report.provenance_missing += cefr_filter.provenance_missing

    sampled_items, topic_results = _sample_per_topic(
        items_by_topic,
        candidates_before_cefr,
        cefr_rejected,
        topics_by_id,
        TOPIC_IDS,
        quota=args.per_topic_quota,
        seed=args.seed,
        max_items_per_lemma=args.max_items_per_lemma,
    )
    report.topic_results = topic_results
    report.sampled_total = len(sampled_items)

    print("\n  Per-topic balanced sample:")
    print(
        "    topic_id                                  cefr  candidates  quota  sampled  "
        "shortfall  lemmas  max_share"
    )
    for t in topic_results:
        print(
            f"    {t.topic_id:<42} {t.cefr:>4} {t.candidates:>10} {t.quota:>6} "
            f"{t.sampled:>8} {t.shortfall:>9} {t.distinct_lemmas_sampled:>7} "
            f"{t.max_lemma_share_sampled:>9}"
        )
    short = [t for t in topic_results if t.shortfall > 0]
    print(f"\n  Topics short of quota: {len(short)}")
    for t in short:
        print(f"    - {t.topic_id}: {t.candidates} of {t.quota} (shortfall {t.shortfall})")

    # TODO.md 8.11: topics whose candidate pool did not have enough DISTINCT
    # lemmas to fill its quota while respecting --max-items-per-lemma -- the
    # coverage floor kept a lemma past the cap rather than shrink the topic
    # (this task's own brief: "Report which topics hit that condition").
    lemma_starved = [t for t in topic_results if t.lemma_diversity_capped]
    print(
        f"\n  Topics where the lemma cap could not be fully honoured "
        f"(too few distinct lemmas, quota kept anyway): {len(lemma_starved)}"
    )
    for t in lemma_starved:
        print(
            f"    - {t.topic_id}: {t.distinct_lemmas_sampled} distinct lemma(s) "
            f"for {t.sampled} item(s), max share {t.max_lemma_share_sampled} "
            f"(cap {args.max_items_per_lemma}, pool had "
            f"{t.distinct_lemmas_in_pool} distinct lemma(s) total)"
        )

    bank_items: list[BankItem] = []
    for item in sampled_items:
        provenance = (
            provenance_by_hash.get(item.source_sentence_id)
            if item.source_sentence_id is not None
            else None
        )
        topic = topics_by_id.get(item.topic_id)
        if provenance is None or topic is None:
            # Already counted above (either provenance_missing or the
            # unknown-topic continue); a sampled item was only ever built
            # from ``items_by_topic``, which only ever received items that
            # already passed both checks, so this branch should be
            # unreachable -- kept anyway rather than asserted, per this
            # package's own "never raise on a data surprise" posture.
            report.provenance_missing += 1
            continue
        bank_items.append(_to_bank_item(item, topic, provenance.source, provenance.line_id))

    try:
        verification_report = verify_items(
            bank_items, llm_client, batch_size=DEFAULT_VERIFICATION_BATCH_SIZE
        )
    except Exception as exc:  # noqa: BLE001 -- mirrors scripts/eval_verifier.py's own
        # precedent (TODO 3.3): an API key IS configured in this container's
        # own .env, so ``llm_client`` is not ``None`` and ``verify_items``
        # actually attempts a call, but the outbound request hits this
        # sandbox's proxy with a 403 -- an error shape none of
        # ``verify_items``'s own five caught transport/budget exceptions
        # cover, since it never reaches Gemini at all. Without this guard
        # the script crashes here with a raw traceback before ever writing
        # the review/rejected/report files below, which this task's own
        # verification requirement depends on existing even when the model
        # pass legitimately could not run -- degrading exactly as honestly
        # as the already-documented no-client case, not silently and not by
        # crashing.
        verification_report = VerificationReport(
            attempted=True,
            verdicts=[
                ItemVerdict(outcome="not_run", reason=f"transport_error:{type(exc).__name__}")
                for _ in bank_items
            ],
        )
    report.verification_attempted = verification_report.attempted
    report.verified_count = verification_report.verified_count
    report.model_rejected_count = verification_report.rejected_count
    report.not_run_count = verification_report.not_run_count
    report.rejected_reasons = dict(verification_report.rejected_reasons)
    report.not_run_reasons = dict(verification_report.not_run_reasons)

    final_items = [
        item
        for item, verdict in zip(bank_items, verification_report.verdicts, strict=True)
        if verdict.outcome != "rejected"
    ]
    report.accepted_total = len(final_items)

    rejected_records = (
        cefr_rejection_records
        + [_uniqueness_skip_to_record(s) for s in blanking_report.uniqueness_skips]
        + [
            _dropped_item_to_record(d)
            for d in blanking_report.dropped_details
            if d.reason == "cross_topic_duplicate"
        ]
        + [_model_rejection_to_record(r) for r in verification_report.rejections]
    )

    review_path = Path(args.review_file)
    rejected_path = Path(args.rejected_file)
    batch_id = f"corpus_pilot_{hashlib.sha256(str(args.seed).encode()).hexdigest()[:8]}"
    _write_review_file(review_path, final_items, batch_id)
    _write_rejected_file(rejected_path, rejected_records, batch_id)
    report.review_file = str(review_path)
    report.rejected_file = str(rejected_path)

    print("\n  Model verification pass:")
    if not verification_report.attempted:
        print("    NOT RUN: no LLM client configured (no API key).")
    print(f"    Verified:  {verification_report.verified_count}")
    print(f"    Rejected:  {verification_report.rejected_count}")
    for reason, count in sorted(report.rejected_reasons.items(), key=lambda kv: -kv[1]):
        print(f"      - {reason}: {count}")
    print(f"    Not run:   {verification_report.not_run_count}")
    for reason, count in sorted(report.not_run_reasons.items(), key=lambda kv: -kv[1]):
        print(f"      - {reason}: {count}")

    print(f"\n  Review file:   {review_path}")
    print(f"  Rejected file: {rejected_path}")

    report_path = Path(args.report_file)
    report.write(report_path)
    print(f"  Report file:   {report_path}")

    if verification_report.not_run_count > 0:
        print()
        print(
            "  FAILING: the model verification backstop did not run for "
            f"{verification_report.not_run_count} item(s). A run where this "
            "backstop did not execute is not a valid pilot run."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
