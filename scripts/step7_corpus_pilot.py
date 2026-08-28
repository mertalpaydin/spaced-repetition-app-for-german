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

## The English gloss (TODO.md 2.1b)

Every item this project builds carries a ``gloss_en``, and until this change
it was ``None`` on all 396 items of the last corpus pilot -- so nothing that
consumes it had ever run on corpus-sourced content. Between the point where
``bank_items`` is assembled and the verification call, each item's
``gloss_en`` is filled with the English translation of ITS OWN CARRIER
SENTENCE: the full, unblanked German sentence recovered through
``provenance_by_hash``, never ``item.prompt``, which is the same sentence
with the answer word replaced by ``___``. Glossing the prompt would attach a
subtly wrong English sentence (one whose meaning is missing exactly the word
the exercise is about) to every item in the bank, so
``_populate_glosses`` takes the carrier from provenance and a test pins that
it is not the prompt.

Three sources, in ``scripts/build_translations.py``'s own priority order:

1. The store on disk (``--translations``, default
   ``build_translations.DEFAULT_STORE_PATH``), looked up by the carrier's
   exact text -- the same key the store itself uses. **A stored record whose
   ``source`` is ``"tatoeba"`` is treated as a cache MISS**, see below.
2. Machine translation of whatever the store lacks, through
   ``build_translations.translator_from_env`` (Azure primary, Gemini
   fallback) and ``build_translations._run_machine_translation``, with the
   new records written back into the store via ``_write_store_atomic`` so the
   next run finds them for free. Both functions are imported, never
   reimplemented: the store format, the whole-batch-boundary character
   budget and the "a failed batch spent nothing" accounting are all that
   module's, not a second copy of them here.
3. Anything still untranslated stays ``None``, exactly as before.

``--no-translate`` looks the store up and never calls out.
``--max-translation-characters`` (default
``build_translations.DEFAULT_MAX_CHARACTERS_PER_RUN``, 60,000) is a runaway
guard, not a working limit; the arithmetic under "What the character budget
now has to cover" below is what says the default still holds. A
``TranslationError`` on a batch leaves that batch's items at
``gloss_en=None`` and the pilot continues to verification, matching this
script's posture everywhere else (``_read_one_corpus``, the ``verify_items``
guard).

## A stored Tatoeba gloss is a cache MISS (owner's decision, 2026-08-27)

A hand audit of all 430 accepted exercises from the last pilot found ten
defects. Four were items whose German is correct and whose English gloss is
wrong, and **three of those four came from Tatoeba's own human translations,
not from machine translation**:

    Wenn ich im Lotto gewaenne, wuerde ich mir ein neues Auto kaufen.
    "If I won the lottery, I'd buy you a new car."      <- mir is himself

    Ich habe eine Freundin, die sich selbst die Haare schneidet.
    "I have a friend who cuts his own hair."            <- Freundin is female

    Das Haus, in dem man lacht, wird vom Glueck bedacht.
    "The house in which one laughs is considered by luck."  <- meaningless

(The fourth, ``Aber: Das Thema ist damit nicht beendet ...`` glossed "But:
The topic is not over there ...", is a machine translation reading ``damit``
as a place adverb.) A separate hand check of 120 Tatoeba pairs found 2
outright wrong and 6 loose. The owner's two decisions follow: **a wrong
gloss replaces the English and keeps the item** (the German still works as
an exercise; only the translation failed), and **Tatoeba translations stop
being used for exercises**.

So ``_populate_glosses`` treats a store record with ``source="tatoeba"`` as
though it were absent: the carrier is re-translated and the record is
overwritten with the machine translation. A record whose ``source`` is
``azure`` or ``gemini`` is used exactly as before.
``--trust-stored-tatoeba`` restores the old behaviour, for reproducing an
earlier run against it.

**What this does NOT do is delete anything.** The Tatoeba records are the
only thing feeding planned feature 5.3 (click a word, see it in several
corpus sentences with their translations), which needs breadth far more than
precision. They stay in the store and are distrusted on the exercise path
only. Re-translating all 200,555 of them would be about 13,000,000
characters, roughly half a year of Azure F0, and is emphatically not what
this is: the store converts itself over time, for exactly the sentences that
become exercises, roughly 475 a cycle.

**A distrusted gloss that could not be replaced does not get used.** If the
re-translation fails, is skipped for budget, or no translator is configured,
the item keeps ``gloss_en=None`` rather than falling back on the Tatoeba
English. Shipping the distrusted gloss anyway would make the flag
decorative, and ``None`` is a state every consumer here already handles
(the gloss check returns immediately for it, the app renders nothing). Those
items are counted in ``gloss_missing`` and reported separately as
``gloss_missing_stale_tatoeba`` so the number is visible rather than merged
into "no gloss anywhere".

## What the character budget now has to cover

Re-translating the Tatoeba ones is new demand on ``--max-translation-
characters``. The arithmetic, for one 475-item pilot cycle:

* The last pilot's 392 review items measure at a mean carrier length of
  **61.7 characters** (median 54, max 144).
* The worst case is a store that helps not at all: 475 carriers x 61.7 =
  **about 29,300 characters**.
* The owner's expected case is roughly 300 carriers whose Tatoeba gloss now
  gets replaced, about **18,500 characters**, plus whatever the store has
  never held.

So 29,300 is the ceiling for a whole cycle against a 60,000 default: **the
default still holds, with roughly 2x headroom**, and it is not raised. It
also stops being purely decorative -- it used to be five times the expected
need and is now about twice it, which is the right size for a runaway guard.

## What changes for an item once ``gloss_en`` stops being ``None``

This is the risk in TODO.md 2.1b, and it is worth being exact about, because
the answer for THIS script is not the answer for the generation pipeline.

``src.verification.pipeline.VerificationPipeline`` -- whose ``_gloss_check``
rejects an item whose gloss contradicts the answer's tense or person, and
whose ``layer3_solver`` may now ACCEPT an item it previously rejected as an
open lexical slot -- is not on this script's path at all. This pilot's only
verification is ``model_verification.verify_items``, a model backstop that
never reads ``gloss_en`` (``_format_item_block`` deliberately sends the model
only the prompt, the cue and the answer). Populating the field therefore
changes NOTHING here on its own, which would make the risk invisible rather
than absent -- the same items go on to a bank whose other consumers do run
that chain.

So the check is run here, explicitly, as its own free deterministic pass
between glossing and verification, through the exact seam the chain uses
(``src.verification.pipeline.check_gloss``, which
``VerificationPipeline._gloss_check`` now also calls, so the two can never
drift).

**It is measure-only by default.** The check always runs and its numbers are
always printed; ``--enforce-gloss-check`` is what lets it actually drop an
item. That default is deliberate and is the owner's standing bar, not
timidity: this is a brand new rejection path on a script that has never had
one, judging machine translations whose quality on this corpus is not yet
measured, and turning it on blind would trade an unknown number of false
negatives for an unknown number of true ones. A fix must not increase false
negatives while decreasing false positives. One measure-only run tells us
exactly what enforcing would cost, per topic, for free; then it is a
decision rather than a gamble.

``rejected_by_gloss_check`` and its per-topic breakdown are printed
prominently, not buried, because some of those rejections would be correct
(the machine translation really is wrong) and some would be the check
misreading a correct but loose translation, and only the per-topic shape
tells the two apart.

One known limit of the check itself, found while building this: it compares
the gloss against the ANSWER's own tense and person features, so it only
bites on items whose answer carries that morphology. A wrong gloss on an
item that blanks a determiner or an adjective ending passes it untouched.
That caps its recall structurally, and it bears directly on TODO.md 2.2.

## Repeated verification passes (TODO.md 2.3, 2.1c)

``--verification-passes N`` runs the model verification pass N times over
the same items and rejects an item that ANY pass rejects: union of
rejections, intersection of acceptances, the reason kept from the first
pass that rejected. Default 1, which is byte-for-byte the behaviour every
run before this flag had.

It exists because the verifier is not deterministic on this corpus. The
owner ran the identical 475 candidates at batch size 20 and at batch size
5, everything else held fixed:

    batch 20:  444 accepted, 31 rejected
    batch  5:  438 accepted, 37 rejected

The counts hide the finding. Diffed item by item, 9 items were accepted at
batch 20 and rejected at batch 5, and 3 were accepted at batch 5 and
rejected at batch 20. All 12 were read by hand and all 12 are genuinely bad
items: fragmented quotations, a wrong preposition in "Eindruck über", an
archaic Dante line, wrong word order, a Swiss-formatted number, a genuine
tense ambiguity the gloss does not settle. Neither batch size catches
everything; the two runs together catch strictly more than either alone.
12 defects in 475 items, 2.5%, were decided by which run you happened to
look at, and the union of two passes is the instrument that catches all of
them.

**Passes after the first bypass the local cache, deliberately.**
``src/llm/cache.py`` is content-addressed on a hash of the full request and
a repeated pass over identical items builds a byte-identical prompt. Left
on, pass 2 would hit the cache, return pass 1's verdict verbatim, log a
``lane="cache"`` row, cost nothing and report zero disagreements no matter
how unstable the verifier actually is -- a feature that looks like it
worked while measuring nothing. Pass 1 keeps the cache, because a rerun
after a crash must still be cheap (CLAUDE.md section 9); every later pass
passes ``use_cache=False`` through ``verify_items`` to ``generate_many``.

**What it costs.** Measured from the owner's own ``cost_log``, against a
$7.50/month ceiling: about $0.18 per pilot cycle at batch size 20, about
$0.36 at batch size 5. Both figures are read off cost_log rows written
before the 2026-08-27 pricing fix, which billed every paid on-demand call at
the batch discount: treat them as a lower bound, up to 2x low for a run that
spent on the paid lane synchronously. Each extra pass adds roughly one more
of whichever batch size the run uses, so ``--verification-passes 2`` roughly
doubles it.
Nobody should enable this without knowing that, which is why the number is
in the flag's own help text as well as here.

**A later pass that cannot run degrades, it does not crash.**
``BudgetExceeded`` (or any of ``verify_items``'s other four caught
transport failures, or the sandbox-proxy error this script has always
guarded against) on pass 2 leaves pass 1's real verdicts intact: the run
reports what it managed, prints that the pass did not run, and records
``passes_completed`` below ``passes_requested`` in the report file. An item
one pass verified and another could not judge is verified, not not-run --
the opposite conflation would be as dishonest as the one
``model_verification`` exists to prevent. An item NO pass could judge stays
not-run and still fails the run.

The report carries ``verification_passes`` in its ``run`` block beside
``verification_batch_size``, each pass's own verified/rejected/not-run
counts plus how many rejections were unique to that pass,
``rejected_by_any_pass`` (the union, which is the number that decides the
run) and ``pass_disagreements`` (how many items at least one pass rejected
and at least one accepted -- the direct measure of verifier instability,
and the reason the flag exists).

## Writing the accepted items into the bank (``--write-bank``)

Until this change the chain from corpus to browser had exactly one missing
hop. Migration v4 gave ``items`` its ``gloss_en`` column, ``BankExporter.
EXPORTED_BANK_ITEM_FIELDS`` ships that column, and
``scripts/step3_export_web_data.py`` writes ``web/data/*.json`` from the
bank -- but nothing ever put a corpus-pilot item INTO ``data/bank.db``.
``scripts/step2_build_item_bank.py`` ingests a golden fixture and
``step6_blank_pilot.py`` only READS stock from a bank it is pointed at
(``SqliteItemBank(args.db)`` there is a ``stock`` lookup, not an insert), so
the owner's "ship with a full bank, top up nightly as a last resort" is not
reachable from any script in the repository. ``--write-bank PATH`` is that
hop, and nothing else changes: the flag is **off by default**, and with it
off this script behaves byte for byte as it did before.

**Identity is ``BankItem.id``, and a collision SKIPS.** ``_corpus_item_id``
is a content hash of exactly the five fields that define the exercise
(topic, type, difficulty, prompt, answer), so the same carrier sentence
blanked for the same topic produces the same id on every run, at any seed,
from either corpus. Two items with one id are therefore the same exercise,
not two -- there is nothing to merge and re-inserting is a no-op.
``SqliteItemBank.insert_item`` already implements exactly that rule
(``INSERT OR IGNORE`` plus "do not touch distractors/carrier_lemmas for an
existing row"), so this script reuses it rather than inventing a second
idempotency mechanism. Replace-on-collision was rejected for one concrete
reason: an item already in the bank may already have ``review_logs`` rows
against it, and rewriting the row a learner has been scheduled against, to
identical content, buys nothing and risks everything.

**The one field that legitimately changes without changing the id is
``gloss_en``**, because the gloss is not part of the hash. A first run with
no translator configured banks the item with ``gloss_en = NULL``; a second
run that now has a gloss for that carrier skips the row as a duplicate and
the NULL stays. That is not silently accepted: ``bank_write.
stale_gloss_rows`` counts exactly it (an offered item that carries a gloss
whose already-banked row does not) and prints a warning. Backfilling it
automatically would mean an UPDATE path this script has no mandate to own;
``docs/building-the-bank.md`` says what to do about it instead.

**A bank write cannot cost the run its review file.** The insert happens
AFTER ``_write_review_file``/``_write_rejected_file`` and is wrapped whole,
migrations included, in the same catch-all guard every other failure path in
this script uses, so a broken or unwritable database leaves the review, the
rejected file and the report exactly where they would otherwise be, with the
error recorded in ``bank_write.error``. It does fail the run (nonzero exit,
alongside the not-run rule below) once the files are on disk: a run that was
asked for a bank and did not produce one is not a successful run, and
exiting 0 would be the same "looks like it worked" failure the report file
exists to prevent.

**A run that failed its own backstop rule writes nothing.** ``final_items``
is "everything the model did not reject", which includes items no
verification pass could judge -- correct for a review file, wrong for the
bank the app ships from. When ``not_run_count`` is nonzero (the same
condition that already fails the run, see "Fail loudly" below) the bank write
is refused outright, with the reason in ``bank_write.error``, rather than
putting unverified items in front of a learner on exactly the runs that rule
exists to catch.

**Migrations run.** ``SqliteItemBank.__init__`` calls ``run_migrations``, so
a ``bank.db`` this flag creates from nothing comes up at
``CURRENT_SCHEMA_VERSION`` (gloss column included), not as a bare v1 table.
The version actually reached is recorded in the report.

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
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.bank.migrations import CURRENT_SCHEMA_VERSION, schema_version
from src.bank.storage import SqliteItemBank
from src.contracts import BankItem, CandidateItem, Topic
from src.generation.batch_client import RejectedCandidateRecord
from src.generation.blanking import carrier_validation, sentence_tagger
from src.generation.blanking.model_verification import (
    DEFAULT_VERIFICATION_BATCH_SIZE,
    REASON_NO_CLIENT,
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
from src.generation.blanking.sentence_source import FreeLaneKeyMissingError, client_from_env
from src.generation.blanking.sentence_tagger import analysis_available
from src.generation.candidate_pool import (
    DEFAULT_POOL_PATH,
    CandidatePool,
    PooledProvenance,
    PoolFormatError,
    fingerprint_inputs,
)
from src.generation.pilot import _write_rejected_file, _write_review_file
from src.lexicon.vocabulary import VocabularyStore
from src.llm.client import GeminiLlmClient
from src.llm.env import load_env_file
from src.llm.translation import AZURE_MAX_BATCH, Translator
from src.taxonomy.facets import derive_facet
from src.taxonomy.loader import load_taxonomy
from src.verification.pipeline import check_gloss

from scripts.build_translations import (
    DEFAULT_MAX_CHARACTERS_PER_RUN,
    TranslationBackfillReport,
    TranslatorMode,
    _default_batch_size,
    _load_store,
    _run_machine_translation,
    _write_store_atomic,
    translator_from_env,
)
from scripts.build_translations import (
    DEFAULT_STORE_PATH as DEFAULT_TRANSLATION_STORE_PATH,
)
from scripts.corpus_reading import (
    SOURCE_LEIPZIG,
    SOURCE_TATOEBA,
    CorpusLine,
    default_corpus_path,
    read_corpus_lines,
)

DEFAULT_REVIEW_PATH = Path("data/corpus_pilot_review.jsonl")
DEFAULT_REJECTED_PATH = Path("data/corpus_pilot_rejected.jsonl")
DEFAULT_REPORT_PATH = Path("data/corpus_pilot_report.json")
DEFAULT_VOCAB_PATH = Path("data/fixtures/corpus/vocab_levels.json")

# The staging paths named in this task's own brief -- overridable (the brief:
# "take them as arguments so the script is not bound to a staging path"), but
# a runnable default is worth more than an empty required argument, exactly
# the trade-off ``step6_blank_pilot.py`` and ``eval_corpus_coverage.py`` make
# for their own defaults.
DEFAULT_TATOEBA_PATH = default_corpus_path("tatoeba_deu.tsv")
DEFAULT_LEIPZIG_PATH = default_corpus_path("leipzig_sample.txt")

# 40,000 sentences per source, the brief's own default, on the claim that it
# is "plenty to fill a 10-per-topic quota for all but the rarest topics" --
# checked, not assumed, against a real run over the actual staged files (see
# this task's own offline verification report); the timing and per-topic
# numbers from that run are what confirmed the claim rather than just
# repeating it.
DEFAULT_LIMIT_PER_SOURCE = 40_000
DEFAULT_PER_TOPIC_QUOTA = 10
DEFAULT_SEED = 7

# TODO.md 2.3/2.1c: how many times the model verification pass runs over the
# SAME items. 1 is today's behaviour exactly -- one pass, cache on, byte-for-
# byte what every previous run did -- and the default stays 1 because every
# extra pass is another full cycle's spend against a $7.50/month ceiling. See
# ``run_verification_passes`` for what N > 1 buys and what it costs.
DEFAULT_VERIFICATION_PASSES = 1

# The owner's decision, 2026-08-27: a stored gloss whose source is Tatoeba is
# a cache MISS on the exercise path. See the module docstring for the four
# audited defects behind it, three of which were Tatoeba's own translations.
DEFAULT_TRUST_STORED_TATOEBA = False

#: The one store source value this script refuses to reuse as a gloss.
#: Matches ``build_translations.TranslationRecord.source``'s own literal.
STORED_SOURCE_TATOEBA = "tatoeba"


def stored_tatoeba_policy_sentence(trust_stored_tatoeba: bool) -> str:
    """One plain sentence saying what this run's setting MEANS, not just
    which way the switch was thrown. Printed and written to the report from
    this one function so the console and the JSON can never disagree."""
    if trust_stored_tatoeba:
        return (
            "--trust-stored-tatoeba GIVEN: a stored gloss was used whatever its "
            "source, including Tatoeba's own human translations. This is the OLD "
            "behaviour, kept only so an earlier run can be reproduced. A hand audit "
            "of 430 accepted items found 4 wrong glosses and 3 of the 4 were "
            "Tatoeba's."
        )
    return (
        "--trust-stored-tatoeba NOT given (the default): a stored gloss whose "
        "source is Tatoeba was treated as absent, re-translated, and the store "
        "record overwritten with the machine translation. The Tatoeba records are "
        "distrusted here, not deleted: they still feed feature 5.3."
    )


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
class PassOutcome:
    """One model-verification pass's own numbers, kept separately from every
    other pass's (TODO.md 2.3). ``rejected`` is what THIS pass alone decided;
    ``rejected_only_by_this_pass`` is the subset no other pass rejected, which
    is what makes a pass's marginal contribution visible rather than implied
    by a difference of totals."""

    index: int
    attempted: bool
    use_cache: bool
    verified: int = 0
    rejected: int = 0
    not_run: int = 0
    rejected_only_by_this_pass: int = 0
    not_run_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "pass": self.index,
            "attempted": self.attempted,
            "use_cache": self.use_cache,
            "verified": self.verified,
            "rejected": self.rejected,
            "not_run": self.not_run,
            "rejected_only_by_this_pass": self.rejected_only_by_this_pass,
            "not_run_reasons": self.not_run_reasons,
        }


@dataclass
class MultiPassVerification:
    """The result of ``run_verification_passes``: the combined report the rest
    of the run consumes, plus the per-pass numbers that are the entire point
    of running more than one pass.

    ``pass_disagreements`` is the measurement TODO.md 2.3 actually wants: how
    many items at least one pass rejected AND at least one pass accepted. It
    is the direct measure of how unstable the verifier is on this corpus. A
    single-pass run has nothing to disagree with, so it is always 0 there."""

    combined: VerificationReport
    passes: list[PassOutcome] = field(default_factory=list)
    rejected_by_any_pass: int = 0
    pass_disagreements: int = 0

    @property
    def passes_requested(self) -> int:
        return len(self.passes)

    @property
    def passes_completed(self) -> int:
        """Passes that produced at least one real verdict. A pass that
        degraded wholesale (``BudgetExceeded`` on a later pass, a transport
        error) counts as requested but not completed, and the run says so
        rather than quietly reporting a two-pass union it never measured."""
        return sum(1 for p in self.passes if p.verified or p.rejected)

    def to_dict(self) -> dict[str, object]:
        return {
            "passes_requested": self.passes_requested,
            "passes_completed": self.passes_completed,
            "rejected_by_any_pass": self.rejected_by_any_pass,
            "pass_disagreements": self.pass_disagreements,
            "per_pass": [p.to_dict() for p in self.passes],
        }


def _one_verification_pass(
    items: Sequence[BankItem],
    llm_client: GeminiLlmClient | None,
    *,
    batch_size: int,
    use_cache: bool,
) -> VerificationReport:
    """One call to ``verify_items``, wrapped in this script's own long-standing
    catch-all guard (see ``main()``'s original comment, preserved here
    verbatim in spirit): an API key IS configured in this container's own
    ``.env``, so ``llm_client`` is not ``None`` and ``verify_items`` really
    attempts a call, but the outbound request hits this sandbox's proxy with a
    403 -- an error shape none of ``verify_items``'s own five caught
    transport/budget exceptions cover, since it never reaches Gemini at all.
    Without the guard the script crashes with a raw traceback before writing
    the review/rejected/report files, which this task's verification
    requirement depends on existing even when the model pass legitimately
    could not run.

    Guarding each pass INDIVIDUALLY, rather than the whole loop, is what makes
    a later pass's failure degrade instead of erasing the passes that already
    succeeded: ``BudgetExceeded`` on pass 2 leaves pass 1's real verdicts
    intact and reports pass 2 as not-run, which is what the brief for
    ``--verification-passes`` asks for."""
    try:
        return verify_items(items, llm_client, batch_size=batch_size, use_cache=use_cache)
    except Exception as exc:  # noqa: BLE001 -- mirrors scripts/eval_verifier.py's own precedent
        return VerificationReport(
            attempted=True,
            verdicts=[
                ItemVerdict(outcome="not_run", reason=f"transport_error:{type(exc).__name__}")
                for _ in items
            ],
        )


def run_verification_passes(
    items: Sequence[BankItem],
    llm_client: GeminiLlmClient | None,
    *,
    batch_size: int,
    passes: int,
) -> MultiPassVerification:
    """Run the model verification pass ``passes`` times over the SAME items and
    combine the results by union of rejections (TODO.md 2.3, 2.1c).

    **Why more than one pass.** The owner ran the identical 475 candidates at
    batch size 20 and at batch size 5, everything else held fixed: 444
    accepted / 31 rejected against 438 accepted / 37 rejected. The totals hide
    the finding. Diffed item by item, 9 items were accepted at 20 and rejected
    at 5, and 3 were accepted at 5 and rejected at 20; all 12 were read by
    hand and all 12 are genuinely bad items (fragmented quotations, a wrong
    preposition in "Eindruck über", an archaic Dante line, wrong word order, a
    Swiss-formatted number, a genuine tense ambiguity the gloss does not
    settle). Neither run catches everything. The union of two runs catches all
    12. 12 defects in 475 items, 2.5%, were decided by which run you happened
    to look at, and the union of two passes is the instrument that catches
    them all.

    **The combination rule.** An item is rejected if ANY pass rejected it:
    union of rejections, intersection of acceptances. The recorded reason is
    the one from the FIRST pass that rejected it. A pass that could not run
    contributes nothing in either direction rather than poisoning the item to
    not-run -- an item one pass verified and another could not judge has still
    been verified once, and reporting it as unverified would be the same
    conflation of "nothing objected" and "a model read this" that
    ``model_verification``'s own docstring exists to prevent, just inverted.
    An item NO pass could judge stays not-run, with the first such pass's
    reason, and still fails the run at the bottom of ``main()``.

    **The cache.** Pass 1 runs with the cache on; every later pass runs with
    it OFF. ``src/llm/cache.py`` is content-addressed on a hash of the full
    request and a repeated pass builds a byte-identical prompt, so a cached
    pass 2 would replay pass 1's verdict exactly, log a ``lane="cache"`` row,
    cost nothing, and report zero disagreements no matter how unstable the
    verifier really is. Pass 1 keeps the cache because that is the crash-rerun
    idempotency CLAUDE.md section 9 asks the cache for in the first place.

    **The cost.** Measured from the owner's own ``cost_log``: about $0.18 per
    pilot cycle at batch size 20, about $0.36 at batch size 5, against a
    $7.50/month ceiling. Both figures are read off cost_log rows written
    before the 2026-08-27 pricing fix, which billed every paid on-demand call
    at the batch discount: treat them as a lower bound, up to 2x low for a run
    that spent on the paid lane synchronously. Each additional pass adds
    roughly one more of whichever figure applies. ``passes <= 1`` short-circuits to exactly one
    cached pass, which is the behaviour every run before this flag had."""
    effective_passes = max(1, passes)
    reports: list[VerificationReport] = []
    outcomes: list[PassOutcome] = []

    for pass_index in range(1, effective_passes + 1):
        use_cache = pass_index == 1
        report = _one_verification_pass(
            items, llm_client, batch_size=batch_size, use_cache=use_cache
        )
        reports.append(report)
        outcomes.append(
            PassOutcome(
                index=pass_index,
                attempted=report.attempted,
                use_cache=use_cache,
                verified=report.verified_count,
                rejected=report.rejected_count,
                not_run=report.not_run_count,
                not_run_reasons=dict(report.not_run_reasons),
            )
        )

    combined_verdicts: list[ItemVerdict] = []
    combined_rejections: list[ModelRejection] = []
    rejected_by_any = 0
    disagreements = 0
    rejecting_pass_positions: list[int] = []

    for position, item in enumerate(items):
        per_pass = [
            report.verdicts[position] for report in reports if len(report.verdicts) == len(items)
        ]
        rejecting = [i for i, v in enumerate(per_pass) if v.outcome == "rejected"]
        verifying = [i for i, v in enumerate(per_pass) if v.outcome == "verified"]

        if rejecting:
            rejected_by_any += 1
            if verifying:
                disagreements += 1
            if len(rejecting) == 1:
                rejecting_pass_positions.append(rejecting[0])
            reason = per_pass[rejecting[0]].reason or "Kein Grund vom Modell angegeben."
            combined_verdicts.append(ItemVerdict(outcome="rejected", reason=reason))
            combined_rejections.append(
                ModelRejection(
                    topic_id=item.topic_id,
                    prompt=item.prompt,
                    accepted_answers=tuple(item.accepted_answers),
                    reason=reason,
                )
            )
        elif verifying:
            combined_verdicts.append(ItemVerdict(outcome="verified"))
        else:
            first_not_run = next((v for v in per_pass if v.outcome == "not_run"), None)
            combined_verdicts.append(
                ItemVerdict(
                    outcome="not_run",
                    reason=(first_not_run.reason if first_not_run else None) or REASON_NO_CLIENT,
                )
            )

    unique_counts = Counter(rejecting_pass_positions)
    for position, outcome in enumerate(outcomes):
        outcome.rejected_only_by_this_pass = unique_counts.get(position, 0)

    combined = VerificationReport(
        attempted=any(report.attempted for report in reports),
        verdicts=combined_verdicts,
        rejections=combined_rejections,
    )
    return MultiPassVerification(
        combined=combined,
        passes=outcomes,
        rejected_by_any_pass=rejected_by_any,
        pass_disagreements=disagreements,
    )


@dataclass
class GlossReport:
    """TODO.md 2.1b: everything the gloss step did, and everything the gloss
    check cost, as its own section of the run report and its own block of
    printed output.

    The FOUR fill counters (``gloss_from_store``,
    ``gloss_retranslated_tatoeba``, ``gloss_newly_translated``,
    ``gloss_missing``) are disjoint and always sum to ``items_total``, the
    same "disjoint counts that must sum" discipline
    ``model_verification.VerificationReport`` already holds this package to.
    There were three until the owner's 2026-08-27 decision to distrust stored
    Tatoeba glosses; "re-translated because the stored gloss was Tatoeba's"
    is deliberately its own line rather than being folded into either
    neighbour, because it is the whole point of the change and its size is
    what says whether the character budget is under strain.
    """

    store_path: str = ""
    translator_mode: str = "none"
    translation_batch_size: int = 0
    max_translation_characters: int = 0
    # ``False`` is the new default: a stored Tatoeba gloss is a cache miss
    # (module docstring). Recorded so two runs that differ only in this can
    # be told apart from the report file alone.
    trust_stored_tatoeba: bool = False
    items_total: int = 0
    gloss_from_store: int = 0
    gloss_retranslated_tatoeba: int = 0
    gloss_newly_translated: int = 0
    gloss_missing: int = 0
    carriers_needing_translation: int = 0
    # Subset of carriers_needing_translation: how many of them needed it only
    # because their stored gloss was Tatoeba's. Zero under
    # --trust-stored-tatoeba.
    carriers_distrusted_tatoeba: int = 0
    # Subset of gloss_missing: items whose distrusted Tatoeba gloss could NOT
    # be replaced this run (failure, budget, or no translator), and which
    # therefore carry no gloss rather than the distrusted one. A later run
    # picks them up.
    gloss_missing_stale_tatoeba: int = 0
    skipped_for_budget: int = 0
    # Every character this run actually translated, Azure and Gemini
    # fallback together -- the same total build_translations.py's own budget
    # gates on (that script reports the two separately because only the
    # Azure half spends the F0 allowance; this one has no F0 accounting to
    # do, it only needs to know what the run cost against its own guard).
    characters_spent: int = 0
    translation_failures: int = 0
    translation_failure_examples: list[str] = field(default_factory=list)
    # ``False`` under --no-gloss-check: the check still RAN and its numbers
    # below are still real, it simply did not remove any item. See the module
    # docstring -- measuring is what makes the change reversible without
    # making it invisible.
    gloss_check_enforced: bool = True
    items_gloss_checked: int = 0
    rejected_by_gloss_check: int = 0
    rejected_by_gloss_check_by_topic: dict[str, int] = field(default_factory=dict)
    gloss_unverified_dimensions: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "store_path": self.store_path,
            "translator_mode": self.translator_mode,
            "translation_batch_size": self.translation_batch_size,
            "max_translation_characters": self.max_translation_characters,
            "trust_stored_tatoeba": self.trust_stored_tatoeba,
            "stored_tatoeba_policy": stored_tatoeba_policy_sentence(self.trust_stored_tatoeba),
            "items_total": self.items_total,
            "gloss_from_store": self.gloss_from_store,
            "gloss_retranslated_tatoeba": self.gloss_retranslated_tatoeba,
            "gloss_newly_translated": self.gloss_newly_translated,
            "gloss_missing": self.gloss_missing,
            "carriers_needing_translation": self.carriers_needing_translation,
            "carriers_distrusted_tatoeba": self.carriers_distrusted_tatoeba,
            "gloss_missing_stale_tatoeba": self.gloss_missing_stale_tatoeba,
            "skipped_for_budget": self.skipped_for_budget,
            "characters_spent": self.characters_spent,
            "translation_failures": self.translation_failures,
            "translation_failure_examples": self.translation_failure_examples,
            "gloss_check_enforced": self.gloss_check_enforced,
            "items_gloss_checked": self.items_gloss_checked,
            "rejected_by_gloss_check": self.rejected_by_gloss_check,
            "rejected_by_gloss_check_by_topic": self.rejected_by_gloss_check_by_topic,
            "gloss_unverified_dimensions": self.gloss_unverified_dimensions,
        }


@dataclass
class BankWriteReport:
    """What ``--write-bank`` did, or why it did nothing (module docstring's
    own section).

    ``inserted``/``skipped_already_present``/``failed`` are disjoint and sum
    to ``items_offered`` whenever ``attempted`` is true, the same "disjoint
    counts that must sum" discipline ``GlossReport`` and
    ``VerificationReport`` are already held to. They map one to one onto
    ``InsertReport.inserted``/``duplicates``/``rejected``: a skip is an id
    that was already in the bank (idempotency working, not an error), a
    failure is an item ``SqliteItemBank._validate_for_insert`` refused (today
    only "the prompt contains its own accepted answer outside the gap").
    """

    requested: bool = False
    db_path: str = ""
    attempted: bool = False
    schema_version: int = 0
    items_offered: int = 0
    inserted: int = 0
    skipped_already_present: int = 0
    failed: int = 0
    failure_reasons: list[str] = field(default_factory=list)
    # Offered items that carry a gloss whose ALREADY-BANKED row does not.
    # Only ever nonzero for a skipped (duplicate) id, because a freshly
    # inserted row carries whatever gloss it was inserted with. See the
    # module docstring for why this is counted rather than repaired here.
    stale_gloss_rows: int = 0
    total_items_in_bank: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "db_path": self.db_path,
            "attempted": self.attempted,
            "schema_version": self.schema_version,
            "items_offered": self.items_offered,
            "inserted": self.inserted,
            "skipped_already_present": self.skipped_already_present,
            "failed": self.failed,
            "failure_reasons": self.failure_reasons,
            "stale_gloss_rows": self.stale_gloss_rows,
            "total_items_in_bank": self.total_items_in_bank,
            "error": self.error,
        }


def _count_stale_gloss_rows(bank: SqliteItemBank, items: Sequence[BankItem]) -> int:
    """How many of ``items`` carry a gloss the bank's own row for that id does
    not (module docstring: ``gloss_en`` is the one field that can change
    without changing the content-addressed id, so a duplicate skip can leave
    a NULL gloss standing).

    Only worth calling when something was actually skipped as a duplicate --
    a row this run inserted carries the gloss it was inserted with by
    construction -- and the caller does exactly that.
    """
    stale = 0
    for item in items:
        if not item.gloss_en:
            continue
        stored = bank.get_item(item.id)
        if stored is not None and not stored.gloss_en:
            stale += 1
    return stale


def write_accepted_to_bank(
    items: Sequence[BankItem], db_path: Path, *, source_batch_id: str
) -> BankWriteReport:
    """Insert every accepted item into the SQLite bank at ``db_path``, and
    report what happened (module docstring's ``--write-bank`` section).

    Idempotent on ``BankItem.id``, which is ``_corpus_item_id``: a content
    hash of the five fields that define the exercise, so a rerun over the
    same corpus offers the same ids and every one of them is skipped rather
    than duplicated. The rule is ``SqliteItemBank.insert``'s own, reused, not
    a second implementation of the same idea.

    Never raises. Every failure -- an unwritable path, a corrupt database, a
    migration that cannot apply -- is caught and recorded in ``error``, so
    the caller's review/rejected/report files are already on disk and stay
    there. Creating ``SqliteItemBank`` inside the guard is deliberate: it is
    the call that runs the migrations, and a migration failure is exactly the
    kind of thing that must not take the run's outputs down with it.
    """
    report = BankWriteReport(requested=True, db_path=str(db_path), items_offered=len(items))
    try:
        bank = SqliteItemBank(db_path)
        report.schema_version = schema_version(db_path)
        insert_report = bank.insert(list(items), source_batch_id=source_batch_id)
        report.attempted = True
        report.inserted = insert_report.inserted
        report.skipped_already_present = insert_report.duplicates
        report.failed = insert_report.rejected
        report.failure_reasons = list(insert_report.rejection_reasons)
        if report.skipped_already_present:
            report.stale_gloss_rows = _count_stale_gloss_rows(bank, items)
        report.total_items_in_bank = bank.count_total()
    except Exception as exc:  # noqa: BLE001 -- same posture as _one_verification_pass
        report.error = f"{type(exc).__name__}: {exc}"
    return report


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
    # Recorded so two runs that differ ONLY in this can be told apart from
    # the report alone (TODO.md 2.3, 2.1c). Without it, an A/B on batch size
    # is two files with no note of which is which.
    verification_batch_size: int = DEFAULT_VERIFICATION_BATCH_SIZE
    # TODO.md 2.3: how many times the verification pass ran over the same
    # items. Sits beside ``verification_batch_size`` for the same reason it
    # does -- two runs that differ only in this must be tellable apart from
    # the report file alone.
    verification_passes: int = DEFAULT_VERIFICATION_PASSES
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
    multi_pass: MultiPassVerification | None = None
    accepted_total: int = 0
    review_file: str = ""
    rejected_file: str = ""
    gloss: GlossReport = field(default_factory=GlossReport)
    bank_write: BankWriteReport = field(default_factory=BankWriteReport)

    #: The report fields phase A is the only thing that fills. Carried across
    #: the pool so a phase-B run writes one report describing the whole
    #: pipeline, not a half-report that silently reads as a corpus run that
    #: found nothing.
    PHASE_A_FIELDS = (
        "seed",
        "per_topic_quota",
        "max_items_per_lemma",
        "limit_per_source",
        "length_filtered_total",
        "carrier_valid_total",
        "carrier_rejected_by_reason",
        "sentences_tagged",
        "raw_candidates_total",
        "cross_topic_duplicates_dropped",
        "skips_by_uniqueness",
        "skips_by_type_ineligibility",
        "sampled_total",
        "provenance_missing",
    )

    def phase_a_fields(self) -> dict[str, object]:
        """Phase A's own numbers, for the pool to carry to phase B.

        ``corpus_reads`` and ``topic_results`` are lists of dataclasses and go
        through ``asdict`` so the split is lossless. It has to be: the report
        is this run's audit artefact, and a phase-B report missing the per-topic
        table would read as a run that sampled nothing rather than as a run
        whose sampling happened yesterday.
        """
        fields: dict[str, object] = {name: getattr(self, name) for name in self.PHASE_A_FIELDS}
        fields["corpus_reads"] = [asdict(read) for read in self.corpus_reads]
        fields["topic_results"] = [asdict(result) for result in self.topic_results]
        return fields

    def to_dict(self) -> dict[str, object]:
        return {
            "run": {
                "seed": self.seed,
                "per_topic_quota": self.per_topic_quota,
                "max_items_per_lemma": self.max_items_per_lemma,
                "limit_per_source": self.limit_per_source,
                "verification_batch_size": self.verification_batch_size,
                "verification_passes": self.verification_passes,
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
            "gloss": self.gloss.to_dict(),
            "verification": {
                "attempted": self.verification_attempted,
                "verified_count": self.verified_count,
                "rejected_count": self.model_rejected_count,
                "not_run_count": self.not_run_count,
                "rejected_reasons": self.rejected_reasons,
                "not_run_reasons": self.not_run_reasons,
                # TODO.md 2.3. Always present, even for a single-pass run,
                # so a consumer never has to branch on whether the key
                # exists; a one-pass run reports one pass and zero
                # disagreements, which is a real measurement, not a gap.
                "multi_pass": (
                    self.multi_pass.to_dict()
                    if self.multi_pass is not None
                    else MultiPassVerification(
                        combined=VerificationReport(attempted=False)
                    ).to_dict()
                ),
            },
            "accepted_total": self.accepted_total,
            # Always present, even for a run that did not pass --write-bank,
            # so a consumer never has to branch on whether the key exists:
            # ``requested: false`` is a real answer, not a gap.
            "bank_write": self.bank_write.to_dict(),
            "output_files": {
                "review": self.review_file,
                "rejected": self.rejected_file,
                "bank": self.bank_write.db_path,
            },
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _propn_surface_forms(text: str) -> frozenset[str]:
    """Surface forms spaCy tagged ``PROPN`` in ``text`` -- the
    ``VocabularyStore.check_ceiling_budget``/``validate_sentence``
    ``proper_nouns`` carve-out (task/owner decision D3, docs/audits/
    cycle-11-corpus-report.md): a news carrier's untagged proper noun
    (``Herzogenaurach``, ``Ljubljana``, ``Klum``) should not be charged the
    frequency fallback's worst-case band just for carrying no vocabulary
    difficulty of its own.

    Reuses ``sentence_tagger.tag_sentence`` -- ``lru_cache``d on the exact
    sentence text (that module's own docstring) -- rather than a second
    tagger. This function is called ONLY from ``_filter_candidates_by_topic_
    cefr``, and only for a sentence that already has at least one
    preliminary ceiling violation (see that function): the blanking stage
    already tagged every carrier-valid sentence once, but its
    ``CandidateItem`` output carries no tagging info to reuse directly (see
    this module's own report -- re-tagging was investigated and rejected as
    a blanket second pass; this lazy, violations-only call is the
    alternative that was chosen instead), and the tag cache (maxsize 8192)
    is far smaller than a full corpus pilot's sentence count, so a sentence
    tagged early in the run is usually already evicted by the time this
    runs -- meaning most calls here ARE a genuine second parse of that one
    sentence, not a free cache hit. Bounding this to violating sentences
    only (not to every candidate) is what keeps that cost proportional to
    the rejection rate instead of the corpus size.

    Returns an empty set (never raises) if spaCy is unavailable or the text
    fails to parse, matching every other degrade contract in this package.
    """
    tagged = sentence_tagger.tag_sentence(text)
    if tagged is None:
        return frozenset()
    return frozenset(tok.text for tok in tagged.tokens if tok.pos == "PROPN")


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
    through a full ``main()`` run.

    Passes ``VocabularyStore``'s ``proper_nouns`` parameter (task/owner
    decision D3) for a sentence that would otherwise be rejected, via
    ``_propn_surface_forms`` -- see that function's own docstring for why
    this is lazy (violations-only) rather than a second full spaCy tagging
    pass over every candidate."""
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
            # Only reached for a sentence that already has a preliminary
            # violation -- see ``_propn_surface_forms``'s own docstring for
            # why this check is deliberately lazy rather than tagging every
            # candidate up front. Removing a token can only ever shrink
            # ``violations``, never add to it, so re-checking with the
            # PROPN set found is always safe to do in place of the
            # preliminary result, not just an optional refinement of it.
            propn = _propn_surface_forms(provenance.text)
            if propn:
                violations = vocab_store.validate_sentence(
                    provenance.text, topic.cefr, proper_nouns=propn
                )
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
    path: Path, fmt: str, source_name: str, limit: int, seed: int, source: str
) -> list[CorpusLine]:
    """One corpus's own lines, or an empty list with a warning printed if the
    file cannot be read -- a missing corpus degrades the run (fewer
    candidates, possibly more topic shortfalls), it never crashes it, so a
    caller missing one of the two files this script defaults to still gets a
    real run over whichever it has.

    ``source_name`` is the human label in the warning; ``source`` is the
    machine-readable corpus name stamped on every returned ``CorpusLine``,
    which is what keeps a Leipzig line id out of a Tatoeba id lookup
    downstream (``build_translations._fill_from_tatoeba``)."""
    if not path.exists():
        print(f"  WARNING: {source_name} corpus not found at {path}; skipping this source.")
        return []
    return read_corpus_lines(path, fmt, limit, seed, source=source)


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
    # ``blanked_lemma`` rides along the same ``extra="allow"`` route as the
    # two provenance fields. docs/audits/cycle-11-corpus-report.md's own
    # finding: the diversity cap is keyed on it (``_diversity_key``) but it
    # was never serialised, so lemma diversity could only be checked from
    # ``_report.json``'s aggregate counts and never audited item by item
    # against the review file. Carrying it costs nothing and makes the cap
    # falsifiable from the output alone.
    update: dict[str, object] = {
        "corpus_source": corpus_source,
        "corpus_line_id": corpus_line_id,
        "blanked_lemma": item.blanked_lemma,
    }
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


def _populate_glosses(
    items: list[BankItem],
    provenance_by_hash: dict[str, CorpusProvenance],
    *,
    store_path: Path,
    translator: Translator | None,
    translator_mode: TranslatorMode,
    max_characters: int,
    now: datetime,
    batch_size: int = AZURE_MAX_BATCH,
    trust_stored_tatoeba: bool = DEFAULT_TRUST_STORED_TATOEBA,
) -> tuple[list[BankItem], GlossReport]:
    """TODO.md 2.1b: fill every item's ``gloss_en`` with the English
    translation of its OWN CARRIER SENTENCE, from the store first and the
    machine translator second, and return the re-stamped items plus the
    numbers that make the step measurable.

    The carrier is ``provenance_by_hash[item.source_sentence_id].text`` --
    the complete, unblanked German sentence the corpus supplied. It is
    emphatically NOT ``item.prompt``, which is that same sentence with the
    answer replaced by ``___``: translating the prompt would produce fluent
    English for a sentence missing exactly the word the exercise is about,
    and would attach that to every item in the bank. See the module
    docstring, and ``tests/test_step7_corpus_pilot.py``'s own test for this.

    ``translator``/``now``/``store_path`` are injected (CLAUDE.md section 8:
    network and clock are dependencies, not ambient facts) so a test drives
    the whole function with a fake translator, a ``tmp_path`` store and a
    fixed timestamp, never the network. ``translator=None`` is the
    ``--no-translate`` path and also the no-credentials path: the store is
    still consulted, nothing is called, and whatever the store lacks stays
    ``None``.

    Never raises on a translation failure. ``_run_machine_translation``
    already converts a ``TranslationError`` on one batch into a counted
    failure and moves to the next batch, leaving those carriers out of the
    store; this function simply reports what it was told and leaves the
    corresponding items at ``gloss_en=None``.

    ``trust_stored_tatoeba=False`` (the default) is the owner's 2026-08-27
    decision, argued in full in the module docstring: a store record whose
    ``source`` is ``"tatoeba"`` is treated as a cache MISS, re-translated,
    and overwritten. A record from ``azure`` or ``gemini`` is used as-is.
    Nothing is deleted either way -- an unreplaceable Tatoeba record stays on
    disk for feature 5.3, it simply does not become this item's gloss.
    """
    report = GlossReport(
        store_path=str(store_path),
        translator_mode=translator_mode,
        translation_batch_size=batch_size,
        max_translation_characters=max_characters,
        trust_stored_tatoeba=trust_stored_tatoeba,
        items_total=len(items),
    )

    carriers: list[str | None] = []
    for item in items:
        provenance = (
            provenance_by_hash.get(item.source_sentence_id)
            if item.source_sentence_id is not None
            else None
        )
        carriers.append(provenance.text if provenance is not None else None)

    store = _load_store(store_path)
    # Captured BEFORE translation so "came from the store" and "this run
    # translated it" stay honestly distinguishable afterwards -- the
    # translation step writes into the same dict.
    in_store_before = frozenset(store)
    # The records the owner's decision says not to believe. Empty under
    # --trust-stored-tatoeba, which is what makes that flag reproduce the old
    # behaviour exactly rather than approximately.
    distrusted = (
        frozenset()
        if trust_stored_tatoeba
        else frozenset(
            german for german, record in store.items() if record.source == STORED_SOURCE_TATOEBA
        )
    )
    # What a lookup may actually use. A distrusted record is deliberately NOT
    # in here, so every "is this already glossed?" question below asks the
    # policy rather than asking the filesystem.
    usable_before = in_store_before - distrusted

    needed: dict[str, CorpusLine] = {}
    for item, carrier in zip(items, carriers, strict=True):
        if carrier is None or carrier in usable_before:
            continue
        provenance = provenance_by_hash[item.source_sentence_id or ""]
        # ``source`` is carried, not dropped: a CorpusLine that knows only
        # its id is exactly what let a Leipzig line id be read as a Tatoeba
        # sentence id in build_translations._fill_from_tatoeba. This path
        # only reaches the machine translator today, but the field is what
        # makes that safe rather than lucky.
        needed.setdefault(
            carrier,
            CorpusLine(line_id=provenance.line_id, text=carrier, source=provenance.source),
        )
    report.carriers_needing_translation = len(needed)
    report.carriers_distrusted_tatoeba = sum(1 for carrier in needed if carrier in distrusted)

    if needed:
        # build_translations.py's own machine-translation step, imported
        # rather than reimplemented (module docstring): the whole-batch-
        # boundary character budget, the "a failed batch spent nothing"
        # accounting, the azure/gemini source labelling and the
        # TranslationError degradation all come from there unchanged. Its
        # report object is this call's only output channel, so one is built
        # here purely to receive those counters; the fields that describe a
        # corpus backfill run (seed, limit_per_source) are not meaningful
        # for a pilot and are left at zero rather than invented.
        backfill = TranslationBackfillReport(
            seed=0,
            limit_per_source=0,
            max_characters=max_characters,
            batch_size=batch_size,
            store_path=str(store_path),
            translator_mode=translator_mode,
            carriers_source="step7_corpus_pilot",
        )
        _run_machine_translation(
            list(needed.values()),
            translator,
            max_characters=max_characters,
            batch_size=batch_size,
            store=store,
            report=backfill,
            now=now,
        )
        report.characters_spent = backfill.characters_spent + backfill.gemini_fallback_characters
        report.translation_failures = backfill.failed
        report.translation_failure_examples = list(backfill.failure_examples)
        report.skipped_for_budget = backfill.skipped_for_budget
        if backfill.machine_translated:
            _write_store_atomic(store_path, store)

    glossed: list[BankItem] = []
    for item, carrier in zip(items, carriers, strict=True):
        record = store.get(carrier) if carrier is not None else None
        if record is None:
            report.gloss_missing += 1
            glossed.append(item)
            continue
        if carrier in distrusted:
            # ``record`` is whatever is in the store NOW. If the machine
            # translation landed, that is the new one and its source says so;
            # if the batch failed, was skipped for budget, or there was no
            # translator at all, it is still the Tatoeba record, and this item
            # gets no gloss rather than the one the owner said to stop using.
            if record.source == STORED_SOURCE_TATOEBA:
                report.gloss_missing += 1
                report.gloss_missing_stale_tatoeba += 1
                glossed.append(item)
                continue
            report.gloss_retranslated_tatoeba += 1
        elif carrier in usable_before:
            report.gloss_from_store += 1
        else:
            report.gloss_newly_translated += 1
        glossed.append(item.model_copy(update={"gloss_en": record.english}))

    return glossed, report


@dataclass(frozen=True)
class GlossCheckOutcome:
    """One item's verdict from the gloss consistency pass, by position in the
    list handed to ``_run_gloss_check``. ``reason`` is ``None`` for an item
    that passed OR carried no gloss at all -- the two are told apart by
    ``checked`` , since only an item with a gloss is ever checked."""

    index: int
    topic_id: str
    checked: bool
    reason: str | None
    error_type: str | None
    unverified_dimensions: int


def _run_gloss_check(
    items: Sequence[BankItem], topics_by_id: dict[str, Topic]
) -> list[GlossCheckOutcome]:
    """Run ``src.verification.pipeline.check_gloss`` -- the exact function
    ``VerificationPipeline._gloss_check`` calls -- over every item, and
    return one outcome per item, in order.

    Pure and offline: ``check_gloss`` is free and deterministic, and it
    returns immediately for an item whose ``gloss_en`` is ``None``, so this
    pass costs nothing at all on a run where no gloss was found.

    This function only JUDGES. Whether a rejection is acted on is
    ``main()``'s decision (``--no-gloss-check``), which is what keeps the
    change reversible without making it invisible.
    """
    outcomes: list[GlossCheckOutcome] = []
    for index, item in enumerate(items):
        topic = topics_by_id.get(item.topic_id)
        answer = item.accepted_answers[0] if item.accepted_answers else ""
        rejection, unverified = check_gloss(item.prompt, answer, item.gloss_en, topic)
        outcomes.append(
            GlossCheckOutcome(
                index=index,
                topic_id=item.topic_id,
                checked=bool(item.gloss_en and item.gloss_en.strip()),
                reason=rejection.reason if rejection is not None else None,
                error_type=rejection.error_type if rejection is not None else None,
                unverified_dimensions=unverified,
            )
        )
    return outcomes


def _gloss_rejection_to_record(
    item: BankItem, outcome: GlossCheckOutcome
) -> RejectedCandidateRecord:
    """One item the gloss consistency check rejected. Carries the offending
    ``gloss_en`` in its own field so the rejected file is diagnosable without
    re-deriving the translation -- a gloss rejection whose row does not show
    the English sentence is not diagnosable at all."""
    return RejectedCandidateRecord(
        topic_id=item.topic_id,
        type=item.type,
        difficulty=item.difficulty,
        prompt=item.prompt,
        proposed_answer=" / ".join(item.accepted_answers),
        layer_failed=None,
        error_type=outcome.error_type or "pedagogical_flaw",
        reason=outcome.reason,
        gloss_en=item.gloss_en,
    )


# Sentences per second, measured in this project's own container, for the two
# spaCy passes every run makes over the whole sentence pool: carrier
# validation (``carrier_validation.validate_carrier``, full parse) and tagging
# (``sentence_tagger.tag_sentence``, inside ``blank_sentences``). They are
# deliberately a LOWER bound on a developer machine -- the point is to say
# "this will take hours" before the hours start, not to predict a minute.
_MEASURED_VALIDATION_RATE = 78.0
_MEASURED_TAGGING_RATE = 92.0

# Above this many sentences the estimate is printed as a warning rather than a
# note. 100,000 is roughly where the two spaCy passes stop being a coffee
# break: about 40 minutes at the measured rates, against the 2 minutes a
# 6,000-line pilot takes.
_LARGE_RUN_SENTENCES = 100_000


def _print_scale_estimate(sentence_count: int) -> None:
    """Say how long the two silent spaCy passes will take, before they start.

    Neither ``carrier_validation.validate_carriers`` nor ``blank_sentences``
    reports progress, and both walk the whole pool. At the default 40,000
    lines per source that is a couple of minutes and nobody notices; over a
    whole corpus it is hours of a cursor not moving, which is exactly the
    kind of thing a run should not have to be interrupted to find out.
    """
    validation_minutes = sentence_count / _MEASURED_VALIDATION_RATE / 60
    tagging_minutes = sentence_count / _MEASURED_TAGGING_RATE / 60
    total = validation_minutes + tagging_minutes
    if sentence_count >= _LARGE_RUN_SENTENCES:
        print(
            f"\n  *** LARGE RUN: {sentence_count:,} sentences. The carrier-validation "
            f"and tagging passes each parse every one of them with spaCy and neither "
            f"prints anything until it finishes. At this project's own measured rates "
            f"({_MEASURED_VALIDATION_RATE:.0f} and {_MEASURED_TAGGING_RATE:.0f} "
            f"sentences/second) expect roughly {total:.0f} minutes of silence before "
            f"the next line appears, less on a faster machine. ***"
        )
    else:
        print(
            f"  Tagging and validating {sentence_count:,} sentences "
            f"(roughly {total:.1f} minutes at this project's measured rates; "
            f"neither pass prints progress)."
        )


def _fingerprint_for(args: argparse.Namespace) -> str:
    """The phase-A input fingerprint for this invocation."""
    return fingerprint_inputs(
        tatoeba_path=None if args.skip_tatoeba else args.tatoeba,
        leipzig_path=None if args.skip_leipzig else args.leipzig,
        limit_per_source=args.limit,
        per_topic_quota=args.per_topic_quota,
        seed=args.seed,
        max_items_per_lemma=args.max_items_per_lemma,
    )


def _pool_from_phase_a(
    args: argparse.Namespace, report: CorpusPilotReport, phase_a: PhaseAResult
) -> CandidatePool:
    """Package phase A's result for disk.

    Provenance is subset to the carriers the sampled items actually reference.
    The full map has one entry per corpus line (about 450,000 on a whole-corpus
    run) and phase B looks up a few thousand of them, so carrying all of it
    would be hundreds of megabytes written to save nothing.
    """
    referenced = {
        item.source_sentence_id
        for item in phase_a.bank_items
        if getattr(item, "source_sentence_id", None) is not None
    }
    provenance = {
        key: PooledProvenance(source=value.source, line_id=value.line_id, text=value.text)
        for key, value in phase_a.provenance_by_hash.items()
        if not referenced or key in referenced
    }
    return CandidatePool(
        inputs_fingerprint=_fingerprint_for(args),
        seed=args.seed,
        per_topic_quota=args.per_topic_quota,
        items=phase_a.bank_items,
        provenance=provenance,
        rejected=phase_a.rejected_records,
        report_prefix=report.phase_a_fields(),
    )


@dataclass
class PhaseAResult:
    """What the corpus half of the run hands to the model half.

    The five names that actually cross the seam, and no more. Everything
    else phase A computes is either already folded into ``report`` or is a
    working set phase B never looks at.
    """

    bank_items: list[BankItem]
    provenance_by_hash: dict[str, CorpusProvenance]
    rejected_records: list[RejectedCandidateRecord]
    topics_by_id: dict[str, Topic]


def _run_phase_a(args: argparse.Namespace, report: CorpusPilotReport) -> PhaseAResult | int:
    """Corpus to candidate pool: no network, deterministic on ``--seed``.

    Returns an exit code instead of a result where the run has nothing to
    do, so ``main`` can end cleanly without this function knowing how a
    pilot reports itself.
    """
    all_lines: list[tuple[str, CorpusLine]] = []
    if not args.skip_tatoeba:
        lines = _read_one_corpus(
            args.tatoeba, "tatoeba", "Tatoeba", args.limit, args.seed, SOURCE_TATOEBA
        )
        report.corpus_reads.append(
            CorpusReadStats(source="tatoeba", path=str(args.tatoeba), lines_read=len(lines))
        )
        all_lines.extend(("tatoeba", line) for line in lines)
    if not args.skip_leipzig:
        lines = _read_one_corpus(
            args.leipzig, "lines", "Leipzig", args.limit, args.seed, SOURCE_LEIPZIG
        )
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

    # Both of the next two stages parse every sentence with spaCy, once each,
    # and neither prints anything until it finishes. On a whole-corpus run
    # that is hours of apparent silence, so the estimate is printed BEFORE
    # the wait rather than discovered during it. The rate is measured, not
    # guessed: 78 sentences/second for carrier validation and 92 for tagging
    # in this project's own container, and a faster machine moves the lower
    # bound, never the shape.
    _print_scale_estimate(len(sentences))

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
        # This script reads ``skips_by_reason`` (the counts) and never
        # ``skip_details`` (one row per (sentence, topic) pair that produced
        # nothing, holding the whole sentence text). At the 6,000-line pilot
        # scale that list is 200,000 rows and nobody notices; over a whole
        # corpus it is about 15 million rows and 5 GB of resident memory that
        # this run would allocate, hold to the end, and never look at. The
        # counts are kept in full either way.
        collect_skip_details=False,
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

    # Everything phase B still needs, and nothing else. ``blanking_report`` is
    # folded into the rejection list here rather than carried across the
    # boundary: phase B only ever read two of its fields, and a pool file that
    # had to carry the whole blanking report would be carrying millions of skip
    # rows nothing reads.
    phase_a_rejections = (
        cefr_rejection_records
        + [_uniqueness_skip_to_record(s) for s in blanking_report.uniqueness_skips]
        + [
            _dropped_item_to_record(d)
            for d in blanking_report.dropped_details
            if d.reason == "cross_topic_duplicate"
        ]
    )
    return PhaseAResult(
        bank_items=bank_items,
        provenance_by_hash=provenance_by_hash,
        rejected_records=phase_a_rejections,
        topics_by_id=topics_by_id,
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
    parser.add_argument(
        "--translations",
        type=Path,
        default=DEFAULT_TRANSLATION_STORE_PATH,
        help=(
            "The German-to-English gloss store (TODO.md 2.1b), looked up by "
            "the carrier sentence's exact text. Anything this run has to "
            "translate is written back here, so the next run finds it free."
        ),
    )
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help=(
            "Look the translation store up but never call a translation "
            "provider. Items the store lacks keep gloss_en = None."
        ),
    )
    parser.add_argument(
        "--trust-stored-tatoeba",
        action="store_true",
        help=(
            "Use a stored gloss whatever its source, including Tatoeba's own "
            "human translations. This restores the behaviour from before "
            "2026-08-27 and exists only so an earlier run can be reproduced. "
            "By default a stored gloss whose source is 'tatoeba' is treated as "
            "ABSENT: the carrier is re-translated and the store record is "
            "overwritten with the machine translation. Why: a hand audit of all "
            "430 accepted items found 4 wrong glosses, and 3 of the 4 were "
            "Tatoeba's own translations (e.g. 'mir' glossed as 'you', a female "
            "'Freundin' glossed 'his own hair'). The Tatoeba records are NOT "
            "deleted either way; they still feed feature 5.3."
        ),
    )
    parser.add_argument(
        "--max-translation-characters",
        type=int,
        default=DEFAULT_MAX_CHARACTERS_PER_RUN,
        help=(
            "Runaway guard on this run's machine translation, in characters, "
            "stopping at a whole-batch boundary exactly as "
            "build_translations.py does. Arithmetic for one 475-item cycle "
            "under the default distrust of stored Tatoeba glosses: the last "
            "pilot's 392 items measure at a mean carrier length of 61.7 "
            "characters, so the worst case, a store that helps not at all, is "
            "475 x 61.7 = about 29,300 characters. The expected case is about "
            "300 carriers whose Tatoeba gloss gets replaced, about 18,500 "
            "characters, plus whatever the store has never held. The 60,000 "
            "default therefore still covers a full cycle with roughly 2x "
            "headroom."
        ),
    )
    parser.add_argument(
        "--verification-batch-size",
        type=int,
        default=DEFAULT_VERIFICATION_BATCH_SIZE,
        help=(
            "How many items ride in one model-verification prompt. TODO.md "
            "2.3 and 2.1c: the verifier agreed with itself only about 92%% of "
            "the time on the naturalness question across two cycles, and the "
            "first hypothesis is that items late in a large batch get less "
            "scrutiny. Run this against the default 20 with everything else "
            "held fixed to settle it."
        ),
    )
    parser.add_argument(
        "--verification-passes",
        type=int,
        default=DEFAULT_VERIFICATION_PASSES,
        help=(
            "How many times the model verification pass runs over the SAME "
            "items. An item is rejected if ANY pass rejects it (union of "
            "rejections, intersection of acceptances). Default 1, which is "
            "today's behaviour exactly. Why more than 1: the identical 475 "
            "candidates verified at batch size 20 and at batch size 5 gave "
            "444/31 and 438/37, but item by item 9 were accepted at 20 and "
            "rejected at 5 while 3 went the other way, and all 12 were read "
            "by hand and all 12 are genuinely bad items -- so neither run "
            "catches everything and the union catches all of them. COST, "
            "measured from cost_log (before the 2026-08-27 pricing fix, so "
            "up to 2x low for paid on-demand calls) against a $7.50/month "
            "ceiling: about $0.18 per pilot cycle at batch size 20 and $0.36 at batch "
            "size 5, and each extra pass adds roughly one more of whichever "
            "applies. Passes after the first bypass the local response cache "
            "on purpose (an identical prompt would otherwise replay pass 1's "
            "verdict for free and measure nothing)."
        ),
    )
    parser.add_argument(
        "--enforce-gloss-check",
        action="store_true",
        help=(
            "Let the gloss consistency check REJECT items, not just report "
            "them. Default is measure-only: the check always runs and its "
            "numbers are always printed, but nothing is dropped for it until "
            "a run has shown what enforcing would cost."
        ),
    )
    parser.add_argument(
        "--write-bank",
        type=Path,
        default=None,
        help=(
            "Insert every ACCEPTED item into this SQLite item bank (e.g. "
            "data/bank.db) after verification. Off by default; without it no "
            "database is opened or created and this script behaves exactly as "
            "it did before the flag existed. The write is idempotent on the "
            "item's content-addressed id, so running this twice over the same "
            "corpus skips instead of doubling the bank. Migrations run, so a "
            "bank.db created here comes up at the current schema version. The "
            "review and rejected JSONL files are written first and are never "
            "at risk from a bank failure, but a bank that was asked for and "
            "not written fails the run."
        ),
    )
    parser.add_argument(
        "--free-lane-only",
        action="store_true",
        help=(
            "Forbid the paid lane outright for this run: verification runs on "
            "the unbilled project or not at all. Requires GEMINI_FREE_API_KEY "
            "to be set explicitly (the run refuses to start otherwise, rather "
            "than falling back to GEMINI_API_KEY, which may be a billed key). "
            "Off by default; without it this script behaves exactly as it did "
            "before the flag existed, spilling onto the paid lane on demand "
            "once the free lane's daily quota is spent. With it, a spent free "
            "quota ends the run: verification reports every item as not-run, "
            "the bank write is refused, and the exit code is nonzero."
        ),
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help=(
            "Let overflow queue as a real Gemini Batch API job instead of "
            "falling through to paid on-demand calls, and do not wait for it: "
            "the job is recorded in .cache/pending_batch_jobs.json and the run "
            "exits. Collect it later with `python -m scripts.collect_batch_jobs`, "
            "then re-run --phase b, which finds the responses in the cache. "
            "This is the mode a scheduled pilot uses. Without it the pilot is "
            "on-demand only, exactly as before."
        ),
    )
    parser.add_argument(
        "--phase",
        choices=("a", "b", "both"),
        default="both",
        help=(
            "Which half of the run to do. 'a' is the corpus half: no network, "
            "deterministic on --seed, hours of spaCy, and it writes --pool-file "
            "and stops. 'b' reads that pool and does the translation, "
            "verification and bank write. 'both' is the default and is exactly "
            "the behaviour this script had before the split."
        ),
    )
    parser.add_argument(
        "--pool-file",
        type=str,
        default=str(DEFAULT_POOL_PATH),
        help="Where phase A writes, and phase B reads, the candidate pool.",
    )
    parser.add_argument("--review-file", type=str, default=str(DEFAULT_REVIEW_PATH))
    parser.add_argument("--rejected-file", type=str, default=str(DEFAULT_REJECTED_PATH))
    parser.add_argument("--report-file", type=str, default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args()

    load_env_file()
    try:
        llm_client = client_from_env(free_lane_only=args.free_lane_only, detached_batch=args.batch)
    except FreeLaneKeyMissingError as exc:
        # Before any corpus is read: hours of spaCy work would otherwise run
        # before the run discovers it cannot verify anything.
        print(f"\n  FAILING: {exc}")
        return 1
    ran_live = llm_client is not None

    report = CorpusPilotReport(
        seed=args.seed,
        per_topic_quota=args.per_topic_quota,
        max_items_per_lemma=args.max_items_per_lemma,
        limit_per_source=args.limit,
        verification_batch_size=args.verification_batch_size,
        verification_passes=args.verification_passes,
        ran_live=ran_live,
    )

    if not analysis_available():
        print(
            "spaCy's de_core_news_sm model is not installed; no items can be "
            "produced (degrading cleanly, not crashing). Install it "
            "(`python -m spacy download de_core_news_sm`) and re-run."
        )
        return 0

    phase_a: PhaseAResult
    if args.phase == "b":
        # Phase B alone: the expensive corpus work already happened and its
        # result is on disk. This is the path a scheduled job takes on every
        # wake-up, which is the whole reason the boundary exists.
        try:
            pool = CandidatePool.load(args.pool_file)
        except PoolFormatError as exc:
            print(f"\n  FAILING: {exc}")
            return 1
        expected = _fingerprint_for(args)
        if pool.inputs_fingerprint and pool.inputs_fingerprint != expected:
            # A warning, not a refusal: see candidate_pool's module
            # docstring on which flags are in the fingerprint and why.
            print(
                f"\n  WARNING: {args.pool_file} was built from different phase-A"
                f" inputs (pool {pool.inputs_fingerprint}, this run {expected})."
                f" Continuing, because a deliberate re-verification of an older"
                f" pool looks exactly like this."
            )
        print(f"\n  Loaded candidate pool: {pool.describe()}")
        for key, value in pool.report_prefix.items():
            if not hasattr(report, key):
                continue
            if key == "corpus_reads" and isinstance(value, list):
                report.corpus_reads = [CorpusReadStats(**row) for row in value]
            elif key == "topic_results" and isinstance(value, list):
                report.topic_results = [TopicSampleResult(**row) for row in value]
            else:
                setattr(report, key, value)
        phase_a = PhaseAResult(
            bank_items=list(pool.items),
            provenance_by_hash={
                key: CorpusProvenance(value.source, value.line_id, value.text)
                for key, value in pool.provenance.items()
            },
            rejected_records=list(pool.rejected),
            topics_by_id={t.id: t for t in load_taxonomy()},
        )
    else:
        phase_a_outcome = _run_phase_a(args, report)
        if isinstance(phase_a_outcome, int):
            return phase_a_outcome
        phase_a = phase_a_outcome
        if args.phase == "a":
            pool = _pool_from_phase_a(args, report, phase_a)
            pool.save(args.pool_file)
            report.write(Path(args.report_file))
            print(f"\n  Phase A complete. Wrote {args.pool_file}: {pool.describe()}")
            print(
                "  Nothing was translated, verified or banked: that is phase B."
                f" Run again with --phase b --pool-file {args.pool_file}."
            )
            return 0

    bank_items = phase_a.bank_items
    provenance_by_hash = phase_a.provenance_by_hash
    topics_by_id = phase_a.topics_by_id
    phase_a_rejections = phase_a.rejected_records

    # ---------------------------------------------------------------
    # TODO.md 2.1b: the English gloss, then the gloss consistency check.
    # Both sit here, between bank-item assembly and the model backstop:
    # the gloss is part of the finished item, and the check is free and
    # deterministic, so it runs before anything is spent on a model call.
    # ---------------------------------------------------------------
    translator: Translator | None
    translator_mode: TranslatorMode
    print(f"\n  Gloss policy: {stored_tatoeba_policy_sentence(args.trust_stored_tatoeba)}")
    if args.no_translate:
        translator, translator_mode = None, "none"
        print("\n  Gloss: --no-translate given; the store is read but nothing is translated.")
    else:
        translator, translator_mode = translator_from_env(llm_client)
        if translator_mode == "gemini_only":
            print(
                "\n  *** GLOSS: NO AZURE KEY CONFIGURED, translating Gemini-only. "
                "Quality is lower than the dedicated translation engine, and Gemini "
                "calls are not free once its own free lane closes. ***"
            )
        elif translator_mode == "none":
            print(
                "\n  *** GLOSS: NO TRANSLATOR CONFIGURED (no AZURE_TRANSLATOR_KEY and "
                "no Gemini key). Only the store can supply a gloss this run. ***"
            )

    bank_items, gloss_report = _populate_glosses(
        bank_items,
        provenance_by_hash,
        store_path=args.translations,
        translator=translator,
        translator_mode=translator_mode,
        max_characters=args.max_translation_characters,
        now=datetime.now(UTC),
        batch_size=_default_batch_size(translator_mode),
        trust_stored_tatoeba=args.trust_stored_tatoeba,
    )
    gloss_report.gloss_check_enforced = args.enforce_gloss_check
    report.gloss = gloss_report

    gloss_outcomes = _run_gloss_check(bank_items, topics_by_id)
    gloss_rejections = [o for o in gloss_outcomes if o.reason is not None]
    gloss_report.items_gloss_checked = sum(1 for o in gloss_outcomes if o.checked)
    gloss_report.rejected_by_gloss_check = len(gloss_rejections)
    gloss_report.rejected_by_gloss_check_by_topic = dict(
        Counter(o.topic_id for o in gloss_rejections)
    )
    gloss_report.gloss_unverified_dimensions = sum(o.unverified_dimensions for o in gloss_outcomes)

    gloss_rejection_records: list[RejectedCandidateRecord] = []
    if gloss_report.gloss_check_enforced and gloss_rejections:
        gloss_rejection_records = [
            _gloss_rejection_to_record(bank_items[o.index], o) for o in gloss_rejections
        ]
        rejected_indices = {o.index for o in gloss_rejections}
        bank_items = [item for i, item in enumerate(bank_items) if i not in rejected_indices]

    # TODO.md 2.3: one pass by default (byte-for-byte the previous behaviour,
    # cache on), N passes unioned when asked for. The per-pass transport guard
    # that used to sit here inline now lives in ``_one_verification_pass``, so
    # a later pass failing degrades that pass alone instead of erasing the
    # passes that already produced real verdicts.
    multi_pass = run_verification_passes(
        bank_items,
        llm_client,
        batch_size=args.verification_batch_size,
        passes=args.verification_passes,
    )
    verification_report = multi_pass.combined
    report.multi_pass = multi_pass
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
        phase_a_rejections
        + gloss_rejection_records
        + [_model_rejection_to_record(r) for r in verification_report.rejections]
    )

    review_path = Path(args.review_file)
    rejected_path = Path(args.rejected_file)
    batch_id = f"corpus_pilot_{hashlib.sha256(str(args.seed).encode()).hexdigest()[:8]}"
    _write_review_file(review_path, final_items, batch_id)
    _write_rejected_file(rejected_path, rejected_records, batch_id)
    report.review_file = str(review_path)
    report.rejected_file = str(rejected_path)

    # The corpus-to-browser chain's missing hop (module docstring's own
    # ``--write-bank`` section). Deliberately AFTER both JSONL writes: the
    # bank is additive, and the audit files this run exists to produce must
    # already be on disk before a database is touched.
    if args.write_bank is not None:
        if verification_report.not_run_count > 0:
            # ``final_items`` is "everything the model did not reject", which
            # includes items no pass could judge at all. That is the right
            # content for a review file -- the audit needs to see them -- and
            # exactly the wrong content for the bank the app ships from. This
            # script already treats a nonzero ``not_run_count`` as a failed
            # run; letting the bank write proceed anyway would put unverified
            # items in front of a learner on precisely the runs the failure
            # rule exists to catch.
            report.bank_write = BankWriteReport(
                requested=True,
                db_path=str(args.write_bank),
                items_offered=len(final_items),
                error=(
                    "refused: the model verification backstop did not run for "
                    f"{verification_report.not_run_count} item(s), so this run has "
                    "unverified items in it and none of them may reach the bank"
                ),
            )
        else:
            report.bank_write = write_accepted_to_bank(
                final_items, Path(args.write_bank), source_batch_id=batch_id
            )

    # TODO.md 2.1b: printed BEFORE the verification block and never folded
    # into it. The gloss check is a rejection cause now, and this task's own
    # brief is explicit that its numbers must be prominent rather than
    # buried -- some of these rejections are the machine translation really
    # being wrong, and some are the check misreading a correct but loose
    # translation, and nobody can tell which without seeing the shape.
    print("\n  English gloss (TODO.md 2.1b):")
    print(f"    Store:                 {gloss_report.store_path}")
    print(f"    Translator mode:       {gloss_report.translator_mode}")
    print(f"    Trust stored Tatoeba:  {gloss_report.trust_stored_tatoeba}")
    print(f"    Items:                 {gloss_report.items_total}")
    print(f"      from the store:      {gloss_report.gloss_from_store}")
    print(f"      re-translated (was Tatoeba's): {gloss_report.gloss_retranslated_tatoeba}")
    print(f"      newly translated:    {gloss_report.gloss_newly_translated}")
    print(f"      still without one:   {gloss_report.gloss_missing}")
    if gloss_report.gloss_missing_stale_tatoeba:
        print(
            "        of which a distrusted Tatoeba gloss this run could not "
            f"replace: {gloss_report.gloss_missing_stale_tatoeba}"
        )
    print(
        f"    Characters spent:      {gloss_report.characters_spent:,} of "
        f"{gloss_report.max_translation_characters:,} "
        f"(batch size {gloss_report.translation_batch_size})"
    )
    if gloss_report.skipped_for_budget:
        print(f"    Left for a later run:  {gloss_report.skipped_for_budget}")
    print(f"    Translation failures:  {gloss_report.translation_failures}")
    for example in gloss_report.translation_failure_examples:
        print(f"      - {example}")
    print(
        f"    What that means: {stored_tatoeba_policy_sentence(gloss_report.trust_stored_tatoeba)}"
    )

    print("\n  Gloss consistency check:")
    print(
        "    Mode:                 "
        + (
            "ENFORCING (--enforce-gloss-check; a contradicting gloss rejects the item)"
            if gloss_report.gloss_check_enforced
            else "MEASURED ONLY (the default; nothing was rejected for its gloss)"
        )
    )
    print(f"    Items with a gloss:   {gloss_report.items_gloss_checked}")
    print(f"    REJECTED BY GLOSS:    {gloss_report.rejected_by_gloss_check}")
    for topic_id, count in sorted(
        gloss_report.rejected_by_gloss_check_by_topic.items(), key=lambda kv: (-kv[1], kv[0])
    ):
        print(f"      - {topic_id}: {count}")
    print(f"    Unverified dims:      {gloss_report.gloss_unverified_dimensions}")
    if gloss_report.gloss_unverified_dimensions:
        print(
            "      (a dimension the gloss neither confirmed nor contradicted: "
            "not a pass, and never counted as one)"
        )

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

    # TODO.md 2.3. Printed in full, not just the totals: the totals are what
    # hid the finding in the batch-size experiment (444/31 versus 438/37 look
    # like a 6-item difference and are actually a 12-item disagreement), so
    # every per-pass number and the disagreement count are on the console as
    # well as in the report file.
    print(f"\n  Verification passes (TODO.md 2.3): {multi_pass.passes_requested}")
    print(f"    Passes that produced verdicts: {multi_pass.passes_completed}")
    for outcome in multi_pass.passes:
        cache_note = "cache on" if outcome.use_cache else "cache BYPASSED"
        print(
            f"    Pass {outcome.index} ({cache_note}): "
            f"verified {outcome.verified}, rejected {outcome.rejected}, "
            f"not run {outcome.not_run}"
        )
        print(f"      rejected only by this pass: {outcome.rejected_only_by_this_pass}")
        for reason, count in sorted(outcome.not_run_reasons.items(), key=lambda kv: -kv[1]):
            print(f"      - not run: {reason}: {count}")
        if outcome.index > 1 and not (outcome.verified or outcome.rejected):
            print(
                "      *** THIS PASS DID NOT RUN. The union below is over the "
                "passes that did, and is not the N-pass result asked for. ***"
            )
    print(f"    REJECTED BY ANY PASS:   {multi_pass.rejected_by_any_pass}")
    print(f"    PASS DISAGREEMENTS:     {multi_pass.pass_disagreements}")
    if multi_pass.passes_requested > 1:
        print(
            "      (items at least one pass rejected and at least one pass "
            "accepted: the direct measure of verifier instability on this corpus)"
        )

    bank_write = report.bank_write
    print("\n  Item bank (--write-bank):")
    if not bank_write.requested:
        print("    NOT REQUESTED: no --write-bank given, no database was opened or created.")
    elif bank_write.error is not None:
        print(f"    FAILED: {bank_write.db_path}")
        print(f"      {bank_write.error}")
        print("      The review, rejected and report files above are unaffected.")
    else:
        print(f"    Database:              {bank_write.db_path}")
        print(f"    Schema version:        {bank_write.schema_version}")
        print(f"    Offered:               {bank_write.items_offered}")
        print(f"    Inserted:              {bank_write.inserted}")
        print(f"    Already present:       {bank_write.skipped_already_present}")
        print(f"    Failed:                {bank_write.failed}")
        for reason in bank_write.failure_reasons:
            print(f"      - {reason}")
        print(f"    Items in bank now:     {bank_write.total_items_in_bank}")
        if bank_write.stale_gloss_rows:
            print(
                "    *** STALE GLOSSES: "
                f"{bank_write.stale_gloss_rows} item(s) already in the bank carry no "
                "gloss while this run has one for them. The id is content-addressed "
                "and does not cover gloss_en, so a duplicate skip cannot refresh it. "
                "See docs/building-the-bank.md, 'Topping up later'. ***"
            )
        if bank_write.schema_version != CURRENT_SCHEMA_VERSION:
            print(
                f"    *** SCHEMA: this bank is at version {bank_write.schema_version}, "
                f"not the current {CURRENT_SCHEMA_VERSION}. gloss_en arrived in v4; "
                "an older bank silently drops it. ***"
            )

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

    # Checked LAST, and only after every output file is on disk: a bank the
    # run was asked for and could not write is a failed run, but it is a
    # failed run whose review and report are still there to diagnose it with.
    if bank_write.requested and bank_write.error is not None:
        print()
        print(
            "  FAILING: --write-bank was given and the bank could not be "
            f"written ({bank_write.error}). Nothing else this run produced was "
            "lost; see the report file's bank_write block."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
