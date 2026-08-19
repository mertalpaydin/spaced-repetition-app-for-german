"""Unit and golden tests for the 5-layer verification chain and kill gate."""

import json
from collections import defaultdict
from pathlib import Path

import pytest
from src.contracts import BankItem, CandidateItem, Distractor, Topic
from src.generation.blanking import sentence_tagger
from src.generation.spec import TopicSpec, build_spec_for_topic, load_spec
from src.lexicon.vocabulary import VocabularyStore
from src.taxonomy.loader import load_taxonomy
from src.verification.layer1_syntax import Layer1SyntaxValidator
from src.verification.layer3_solver import Layer3AdversarialSolver
from src.verification.layer_expander import AnswerSetExpander
from src.verification.pipeline import VerificationPipeline

# docs/02-content-pipeline.md stage 4: "for each rejection reason, assert the
# chain catches >= 90% of the adversarial items carrying that defect."
ADVERSARIAL_RECALL_THRESHOLD_PER_REASON = 0.90

# docs/02-content-pipeline.md stage 4 DoD: "false-positive rate below 10%".
KNOWN_GOOD_FALSE_POSITIVE_THRESHOLD = 0.10


@pytest.fixture
def adversarial_suite_path(data_fixtures_dir: Path) -> Path:
    p = data_fixtures_dir / "verification" / "adversarial.jsonl"
    if p.exists():
        return p
    return data_fixtures_dir / "verification" / "adversarial_suite.jsonl"


@pytest.fixture
def vocab_store(data_fixtures_dir: Path) -> VocabularyStore:
    vocab_path = data_fixtures_dir / "corpus" / "vocab_levels.json"
    if vocab_path.exists():
        return VocabularyStore.load(vocab_path)
    return VocabularyStore({"buch": "A1", "tisch": "A1", "liegen": "A1"})


@pytest.fixture
def pipeline(vocab_store: VocabularyStore) -> VerificationPipeline:
    return VerificationPipeline(vocab_store=vocab_store)


@pytest.fixture
def sample_spec(repo_root: Path) -> TopicSpec:
    return load_spec(repo_root / "data" / "specs" / "dativ_nach_praeposition.yaml")


@pytest.fixture
def sample_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "dativ_nach_praeposition")


def _load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _as_candidate(data: dict) -> CandidateItem:
    """Build a CandidateItem from a raw adversarial.jsonl row, keeping the
    fixture's own bookkeeping fields (id, expected_*, reason, cefr) out of the
    model (CandidateItem allows extras, but these have their own meaning)."""
    data = dict(data)
    for key in ("expected_layer_failed", "expected_error_type", "reason", "cefr"):
        data.pop(key, None)
    return CandidateItem.model_validate(data)


@pytest.mark.golden
def test_adversarial_suite_catches_all_known_defects(
    pipeline: VerificationPipeline,
    adversarial_suite_path: Path,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """Every item in the adversarial fixture must be rejected, and the per-reason
    recall breakdown (docs/02-content-pipeline.md stage 4,
    `test_adversarial_recall_per_reason`) must show >= 90% recall for every
    single rejection reason: a 100% aggregate can hide 0% recall on one reason,
    which is exactly the failure mode this test exists to catch.

    Duplicate-labelled items are checked against a bank built from the *other*
    items sharing the "duplicate" label (each is a deliberate near-duplicate of
    its sibling), since the dedup layer has nothing to compare against
    otherwise and would never actually run.
    """
    assert adversarial_suite_path.exists()

    rows = _load_jsonl(adversarial_suite_path)
    assert len(rows) >= 60, f"Expected at least 60 adversarial items, found {len(rows)}"

    # Build a static "already exists in the bank" pool from every item labelled
    # duplicate, so a duplicate pair actually has something to collide with.
    duplicate_bank: list[tuple[int, BankItem]] = []
    for idx, row in enumerate(rows):
        if row.get("expected_error_type") == "duplicate":
            duplicate_bank.append(
                (
                    idx,
                    BankItem(
                        id=row["id"],
                        topic_id=row["topic_id"],
                        type=row["type"],
                        difficulty=row["difficulty"],
                        cefr=row["cefr"],
                        prompt=row["prompt"],
                        accepted_answers=[row["proposed_answer"]],
                    ),
                )
            )

    recall_hits: dict[str, int] = defaultdict(int)
    recall_totals: dict[str, int] = defaultdict(int)
    layer_mismatches: list[str] = []

    for idx, row in enumerate(rows):
        expected_layer = row.get("expected_layer_failed")
        expected_type = row.get("expected_error_type")
        item = _as_candidate(row)

        existing_bank_items = None
        if expected_type == "duplicate":
            existing_bank_items = [b for j, b in duplicate_bank if j != idx]

        # A vocabulary-ceiling check (layer1_syntax) needs the SPEC of the
        # row's own topic, not an unrelated fixed one: ``sample_spec`` is
        # ``dativ_nach_praeposition``'s (ceiling A2), which tolerates B1
        # vocabulary one band above it, so every A1 row this fixture ships
        # was checked against the wrong ceiling entirely. This went
        # unnoticed while ``artikel_bestimmt_nom``'s own eligible_types was
        # ``[paragraph_cloze]`` only, because adv_036's ``cloze_free`` type
        # got caught by that unrelated layer-1 check first and never
        # actually reached the vocabulary-ceiling check this fixture item
        # is meant to exercise -- widening eligible_types for the three
        # artikel_*_nom topics (data/taxonomy.yaml, this task) unmasked it.
        # ``build_spec_for_topic`` derives a spec from the row's own topic
        # (ceiling = that topic's own CEFR, matching every row's own "cefr"
        # field), falling back to ``sample_spec`` only for the handful of
        # legacy topic ids in this fixture that predate the current
        # taxonomy and have no topic to derive one from.
        row_topic = pipeline.topics_map.get(row["topic_id"])
        row_spec = build_spec_for_topic(row_topic) if row_topic is not None else sample_spec

        res = pipeline.verify_item(item, spec=row_spec, existing_bank_items=existing_bank_items)

        recall_totals[expected_type] += 1
        if not res.passed:
            recall_hits[expected_type] += 1
            assert res.layer_failed is not None
            assert res.error_type is not None
            if expected_layer is not None and res.layer_failed != expected_layer:
                layer_mismatches.append(
                    f"{row['id']}: expected_layer_failed={expected_layer}, "
                    f"actual layer_failed={res.layer_failed} ({res.reason})"
                )
        else:
            layer_mismatches.append(
                f"{row['id']}: expected defect {expected_type!r} at layer "
                f"{expected_layer}, item was ACCEPTED"
            )

    # Per-reason recall, per docs/02-content-pipeline.md: "Report per-reason,
    # not aggregate: 95% aggregate can hide 40% recall on topic_leak."
    below_threshold = []
    for reason, total in recall_totals.items():
        recall = recall_hits[reason] / total
        if recall < ADVERSARIAL_RECALL_THRESHOLD_PER_REASON:
            below_threshold.append(f"{reason}: {recall:.0%} ({recall_hits[reason]}/{total})")

    assert not below_threshold, (
        "Per-reason recall below the "
        f"{ADVERSARIAL_RECALL_THRESHOLD_PER_REASON:.0%} threshold for: "
        + "; ".join(below_threshold)
        + "\nDetail:\n"
        + "\n".join(layer_mismatches)
    )


@pytest.mark.golden
def test_known_good_suite_passes_all_verification_layers(
    pipeline: VerificationPipeline,
    data_fixtures_dir: Path,
    repo_root: Path,
) -> None:
    """False-positive rate on known-good items must stay at or below 10%
    (docs/02-content-pipeline.md stage 4 DoD).

    Each item is measured against its OWN topic's spec sheet
    (``data/specs/<topic_id>.yaml``), not a single shared spec: applying one
    topic's spec (e.g. A2 ``dativ_nach_praeposition``) to all 60 items,
    including B1/B2 items, is not a valid measurement in either direction --
    it is a test-design bug, not a signal about the chain. A missing spec
    sheet for an item's topic_id is a data-integrity defect in the fixture
    itself and is reported explicitly (and fails this test) rather than
    silently falling back to no spec, which would silently skip the
    vocabulary-ceiling check for that item instead of measuring it.
    """
    known_good_path = data_fixtures_dir / "verification" / "known_good.jsonl"
    assert known_good_path.exists()

    rows = _load_jsonl(known_good_path)
    assert len(rows) >= 60, f"Expected at least 60 known good items, found {len(rows)}"

    specs_dir = repo_root / "data" / "specs"
    missing_specs: list[str] = []
    false_positives: list[str] = []
    for data in rows:
        spec_path = specs_dir / f"{data['topic_id']}.yaml"
        if not spec_path.exists():
            missing_specs.append(f"{data['id']}: no spec sheet for topic_id={data['topic_id']!r}")
            continue
        own_spec = load_spec(spec_path)

        item = CandidateItem(
            topic_id=data["topic_id"],
            type=data["type"],
            difficulty=data["difficulty"],
            prompt=data["prompt"],
            proposed_answer=data["accepted_answers"][0],
            distractors=[Distractor(text=d["text"]) for d in data.get("distractors", [])],
            cue=data.get("cue"),
        )
        res = pipeline.verify_item(item, spec=own_spec)
        if not res.passed:
            false_positives.append(f"{data['id']}: {res.reason} (prompt: {item.prompt!r})")

    assert not missing_specs, (
        "Known-good fixture items whose topic_id has no matching spec sheet "
        "(data integrity defect in the fixture, not a chain measurement):\n"
        + "\n".join(missing_specs)
    )

    false_positive_rate = len(false_positives) / len(rows)
    assert false_positive_rate <= KNOWN_GOOD_FALSE_POSITIVE_THRESHOLD, (
        f"False-positive rate {false_positive_rate:.1%} exceeds the "
        f"{KNOWN_GOOD_FALSE_POSITIVE_THRESHOLD:.0%} threshold "
        f"({len(false_positives)}/{len(rows)} known-good items rejected):\n"
        + "\n".join(false_positives)
    )


def test_clean_items_pass_all_four_layers(
    pipeline: VerificationPipeline,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """Verify that a compliant candidate item successfully passes all verification layers."""
    clean_item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[
            Distractor(text="den", implied_topic_id="kasus_akkusativ_formen"),
            Distractor(text="des", implied_topic_id="kasus_genitiv_formen"),
            Distractor(text="das", implied_topic_id="artikel_bestimmt_nom"),
        ],
        carrier_lemmas=["Buch", "liegen", "Tisch"],
    )

    res = pipeline.verify_item(clean_item, spec=sample_spec, topic=sample_topic)
    assert res.passed
    assert res.layer_failed is None
    assert res.error_type is None


# Task 4 (cycle-4 report): batch-internal near-duplicate detection means a
# batch of otherwise-identical "clean" candidates can no longer all survive
# together -- the second one onward would (correctly) be flagged as a
# duplicate of the first, well before the kill-gate/rejection-rate math these
# tests actually exercise ever runs. Every A1 adverb below is a real
# A1-band entry (data/fixtures/corpus/vocab_levels.json), well under the
# A2 vocabulary ceiling dativ_nach_praeposition's own spec sheet declares,
# so distinctness costs nothing these tests were relying on: none of them
# assert anything about the clean items' shared TEXT, only about pass/fail
# counts and kill-gate arithmetic.
_CLEAN_BATCH_ADVERBS: tuple[str, ...] = (
    "heute",
    "morgen",
    "jetzt",
    "dort",
    "hier",
    "gleich",
    "sofort",
    "schon",
    "noch",
    "dann",
    "oft",
    "immer",
    "manchmal",
    "still",
    "wieder",
)


def _clean_batch_item(index: int) -> CandidateItem:
    """One structurally-clean candidate, textually distinct from every other
    ``index`` (see ``_CLEAN_BATCH_ADVERBS`` above) so a batch of several
    doesn't trip the batch-internal near-duplicate check on its own."""
    adverb = _CLEAN_BATCH_ADVERBS[index % len(_CLEAN_BATCH_ADVERBS)]
    return CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt=f"Das Buch liegt {adverb} auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[
            Distractor(text="den", implied_topic_id="kasus_akkusativ_formen"),
            Distractor(text="des", implied_topic_id="kasus_genitiv_formen"),
            Distractor(text="das", implied_topic_id="artikel_bestimmt_nom"),
        ],
    )


def _build_batch(n_clean: int, n_bad: int) -> list[CandidateItem]:
    assert n_clean <= len(_CLEAN_BATCH_ADVERBS), (
        "add more adverbs to _CLEAN_BATCH_ADVERBS to keep every clean item distinct"
    )
    clean_items = [_clean_batch_item(i) for i in range(n_clean)]
    bad_item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf dem Tisch.",  # missing gap
        proposed_answer="dem",
        distractors=[
            Distractor(text="den"),
            Distractor(text="des"),
            Distractor(text="das"),
        ],
    )
    return clean_items + [bad_item] * n_bad


def test_verify_batch_reports_rejection_rate_separately_from_kill_gate(
    pipeline: VerificationPipeline,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """A chain that correctly REJECTS malformed candidates must never trip its
    own kill gate on that basis. `failed_count` is the number of candidates the
    chain rejected (i.e. the candidate rejection rate, exposed as
    `rejection_rate`), not the post-verifier error rate the kill gate is
    defined over (docs/00-index.md:32, docs/02-content-pipeline.md:337-341).

    This replaces a test that previously asserted the inverted, buggy
    semantics: that correctly catching 2 of 10 defective candidates counted as
    the chain "failing". Per CLAUDE.md rule 7 the old test is rewritten, not
    deleted, to assert the corrected behaviour.
    """
    batch = _build_batch(n_clean=8, n_bad=2)
    report = pipeline.verify_batch(batch, spec=sample_spec, topic=sample_topic)

    assert report.total_candidates == 10
    assert report.passed_count == 8
    assert report.failed_count == 2
    # This is the candidate rejection rate: useful operational/yield
    # information, but not itself a quality signal.
    assert report.rejection_rate == 0.20

    # No audit labels were supplied, so the post-verifier error rate is
    # genuinely unmeasured. It must never be inferred from the rejection rate.
    assert report.post_verifier_error_rate is None
    assert report.kill_gate_tripped in (None, False)
    assert report.kill_gate_tripped is not True
    assert report.gate_status == "unmeasured"


def test_kill_gate_unmeasured_without_audit_labels(
    pipeline: VerificationPipeline,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """A missing measurement must never silently read as a pass."""
    batch = _build_batch(n_clean=10, n_bad=0)
    report = pipeline.verify_batch(batch, spec=sample_spec, topic=sample_topic)

    assert report.post_verifier_error_rate is None
    assert report.kill_gate_tripped in (None, False)
    assert report.kill_gate_tripped is not True
    assert report.gate_status == "unmeasured"


def test_kill_gate_trips_on_high_post_verifier_error_rate_from_audit_labels(
    pipeline: VerificationPipeline,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """The kill gate is driven by audited defects among ACCEPTED items, never
    by the rejection rate."""
    batch = _build_batch(n_clean=10, n_bad=0)  # all 10 accepted by the chain
    # An independent audit finds 2 of the 10 accepted items are actually
    # defective: 20% post-verifier error rate, above the 15% threshold.
    audit_labels = {str(i): (i < 2) for i in range(10)}

    report = pipeline.verify_batch(
        batch, spec=sample_spec, topic=sample_topic, audit_labels=audit_labels
    )

    assert report.passed_count == 10
    assert report.audited_accepted_count == 10
    assert report.post_verifier_error_rate == 0.20
    assert report.kill_gate_tripped is True
    assert report.gate_status == "tripped"


def test_kill_gate_does_not_trip_on_low_post_verifier_error_rate_from_audit_labels(
    pipeline: VerificationPipeline,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """Below the threshold, the gate must record a pass, not silence."""
    batch = _build_batch(n_clean=10, n_bad=0)
    audit_labels = {str(i): False for i in range(10)}  # audit finds zero defects

    report = pipeline.verify_batch(
        batch, spec=sample_spec, topic=sample_topic, audit_labels=audit_labels
    )

    assert report.post_verifier_error_rate == 0.0
    assert report.kill_gate_tripped is False
    assert report.gate_status == "passed"


def test_post_verifier_error_rate_ignores_rejected_and_unlabelled_items(
    pipeline: VerificationPipeline,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """The rate is computed over the labelled ACCEPTED subset only: rejected
    candidates and accepted-but-unaudited items must not dilute it."""
    batch = _build_batch(n_clean=8, n_bad=2)  # indices 0-7 accepted, 8-9 rejected
    audit_labels = {
        "0": True,  # accepted + audited defective
        "1": False,  # accepted + audited clean
        # indices 2-7: accepted but not audited -> excluded from the rate
        "8": True,  # rejected candidate; must be ignored even if labelled
        "9": True,
    }

    report = pipeline.verify_batch(
        batch, spec=sample_spec, topic=sample_topic, audit_labels=audit_labels
    )

    assert report.audited_accepted_count == 2
    assert report.post_verifier_error_rate == 0.5
    assert report.gate_status == "tripped"


def test_layer1_rejects_missing_gap(pipeline: VerificationPipeline, sample_spec: TopicSpec) -> None:
    """Layer 1 rejects items without a gap placeholder."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf dem Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1


def test_layer1_rejects_wrong_distractor_count(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """Layer 1 rejects items with fewer than MIN_DISTRACTORS_AFTER_REPAIR
    distractors. docs/audits/stage-04-recovery-plan.md fix A lowered the
    floor from 3 to 2 (a two-way choice is still a real multiple choice),
    so this must supply only 1 to still exercise the rejection."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1


def test_layer1_rejects_vocabulary_ceiling_violations(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """Layer 1 rejects items containing vocabulary beyond CEFR ceiling.

    docs/audits/stage-04-recovery-plan.md fix C: absence from the wordlist no
    longer means "too hard" (an incomplete list can only prove a word easy,
    never that it's hard), so this must use a word the list actually contains
    ABOVE the ceiling -- "Ablauf" is a real B2 entry -- not an invented
    compound the list simply doesn't have.
    """
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Der Ablauf liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1
    assert res.error_type == "vocabulary_ceiling_violation"


def test_layer1_permits_two_content_words_one_band_above_ceiling(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """docs/audits/stage-04-a2-pilot-audit.md, 'On the vocabulary ceiling':
    zero tolerance was stricter than any graded reader and was the largest
    single A2 pilot rejection category. Two B1 content words in an A2-ceiling
    item ("Tatsache", "Sturm", both real B1 wordlist entries) are exactly one
    band over and must be forgiven, not rejected -- and the forgiveness must
    be visible on the result rather than silent.
    """
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Die Tatsache und der Sturm liegen auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert res.passed
    assert res.error_type != "vocabulary_ceiling_violation"

    validator = Layer1SyntaxValidator(vocab_store=pipeline.vocab_store)
    passed, reason, error_type = validator.validate(item, spec=sample_spec)
    assert passed
    assert error_type is None
    assert reason is not None
    assert "Tatsache" in reason
    assert "Sturm" in reason


def test_layer1_rejects_three_content_words_one_band_above_ceiling(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """A third one-band-over content word ("Vokabeln", real B1 entry)
    exhausts the two-word budget, so the item is rejected exactly as
    zero-tolerance would have rejected it before."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Die Tatsache, der Sturm und die Vokabeln liegen auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1
    assert res.error_type == "vocabulary_ceiling_violation"
    assert res.reason is not None
    assert "Tatsache" in res.reason
    assert "Sturm" in res.reason
    assert "Vokabeln" in res.reason


def test_layer1_length_fallback_permits_up_to_thirty_tokens_without_a_spec() -> None:
    """docs/audits/stage-04-a2-pilot-audit.md: the A2 prompt-length limit of
    18 tokens (set per-topic in data/specs/*.yaml, not owned by this module)
    rejected four otherwise-good items at 20 to 25 tokens. This module owns
    only the fallback used when no ``TopicSpec`` is supplied, raised from 35
    to 30; a 24-token prompt with no spec must still pass Layer 1's length
    check under that raised default.
    """
    validator = Layer1SyntaxValidator(vocab_store=None)
    twenty_four_tokens = " ".join(f"wort{i}" for i in range(22)) + " ___ Ende."
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt=twenty_four_tokens,
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    passed, reason, error_type = validator.validate(item)
    assert passed, f"Unexpected Layer 1 rejection: {reason}"
    assert error_type is None


def _plain_item(prompt: str) -> CandidateItem:
    """A structurally-clean cloze item carrying the given prompt, isolating
    Layer 1's topic-leak check from every other Layer 1 rule (gap presence,
    distractor count/uniqueness, length, register, vocabulary ceiling)."""
    return CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt=prompt,
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Er hat sein Deutsch in den letzten Monaten deutlich ___ verbessert.",
        "Das war jedenfalls nicht meine Schuld, ___ sagte er.",
        "Er hatte gestern einen Unfall auf ___ Autobahn.",
        "Ich lese jeden Morgen ___ Zeitung.",
        "Für den kaputten Toaster gab es ___ Ersatz.",
        "Bitte fülle ___ Formular vollständig aus.",
    ],
)
def test_layer1_does_not_reject_ordinary_words_containing_grammar_substrings(
    prompt: str,
) -> None:
    """Regression test for the Layer 1 topic-leak substring-match defect.

    ``Layer1SyntaxValidator`` delegates its topic-leak check to
    ``PromptBuilder.check_for_topic_leaks``. That function used to do a raw
    ``term in text`` substring scan, which rejected ordinary German
    sentences purely because a word like "verbessert" or "jedenfalls"
    happens to contain a blocklist term ("verb", "fall") as a substring.
    None of these prompts names a grammar topic, so none of them may be
    rejected as a topic leak. If this regresses, the assertion on
    ``error_type`` below will start firing.
    """
    validator = Layer1SyntaxValidator(vocab_store=None)
    passed, reason, error_type = validator.validate(_plain_item(prompt))
    assert error_type != "topic_leak", f"False-positive topic leak for {prompt!r}: {reason}"
    assert passed, f"Unexpected Layer 1 rejection for {prompt!r}: {reason}"


def test_layer1_still_rejects_compound_words_that_leak_the_topic() -> None:
    """A leak hidden inside a German compound (both halves grammar
    metalanguage) must still be rejected, not just the bare whole-word form."""
    validator = Layer1SyntaxValidator(vocab_store=None)
    passed, reason, error_type = validator.validate(
        _plain_item("Die ___ Dativform ist hier unregelmäßig.")
    )
    assert not passed
    assert error_type == "topic_leak"


def test_layer1_rejects_parenthetical_cue_leaked_into_prompt(
    pipeline: VerificationPipeline,
) -> None:
    """docs/audits/stage-04-pilot-2026-08-14.md item 7: a bracketed authoring
    cue leaking into the visible sentence, instead of the dedicated ``cue``
    field, must be rejected -- this item was accepted by every layer,
    including the new model-backed layer 5, in the pilot re-run that
    motivated this fix.

    The original single-word cue from that audit ('(Katze)') is no longer a
    usable fixture for this: docs/audits/stage-04-recovery-plan.md fix D
    later reversed the unconditional parenthetical ban (a single-word cue
    that is not the answer itself is legitimate -- ``layer1_syntax``'s own
    "2b" comment), so '(Katze)' alone never violates either of the two rules
    that remain, and this test's old fixture only ever failed at layer 1
    because ``artikel_unbestimmt_kein_nom``'s ``eligible_types`` used to be
    ``[paragraph_cloze]`` alone -- an unrelated, coincidental mask, removed
    now that this task widened it to include ``cloze_free`` (data/
    taxonomy.yaml). The fixture below exercises the check this test is
    actually named for -- rule 2, a multi-word aside, which IS still
    rejected -- instead of relying on that removed coincidence."""
    item = CandidateItem(
        topic_id="artikel_unbestimmt_kein_nom",
        type="cloze_free",
        difficulty=2,
        prompt="Weil das Haus so alt ist, wohnt dort ___ (eine Katze) drin.",
        proposed_answer="eine",
        distractors=[Distractor(text="die"), Distractor(text="der"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item)
    assert not res.passed
    assert res.layer_failed == 1
    assert res.error_type == "structural_malformation"


def test_layer2_morphosyntactic_validation(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """Layer 2 catches lowercase sentence starts and missing terminal punctuation."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="das Buch liegt auf ___ Tisch",  # lowercase start + no terminal punct
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 2


def test_layer3_detects_ambiguous_completions(pipeline: VerificationPipeline) -> None:
    """Layer 3 catches under-constrained prompts admitting open alternative completions."""
    item = CandidateItem(
        topic_id="konjunktionen_position_0",
        type="cloze_free",
        difficulty=1,
        prompt="Ich trinke ___ Kaffee.",
        proposed_answer="oft",
        distractors=[Distractor(text="gern"), Distractor(text="heute"), Distractor(text="viel")],
    )
    res = pipeline.verify_item(item)
    assert not res.passed
    assert res.layer_failed == 3
    assert res.error_type == "ambiguity"


# ----------------------------------------------------------------------
# Cycle 3: a validated gloss relieves layer3's "open lexical slot"
# rejection, but ONLY for the tense/person ambiguity it genuinely
# constrains, never for a free lexeme choice. docs/audits/
# generation-track-plan.md: this was the largest single rejection bucket
# in the prior pilot (26 items).
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def konjunktiv_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "konjunktiv_ii_irreal_gegenwart")


def _konjunktiv_item(distractor_texts: list[str], gloss_en: str | None) -> CandidateItem:
    """A cloze_free (uncued) Konjunktiv II item whose carrier has no other
    agreement anchor before the gap ("sofort", an adverb, not a subject
    pronoun or preposition) and no auxiliary/modal elsewhere in the prompt,
    so it reaches layer3's bottom "open lexical slot" check rather than
    being exempted or caught by an earlier signal."""
    return CandidateItem(
        topic_id="konjunktiv_ii_irreal_gegenwart",
        type="cloze_free",
        difficulty=2,
        prompt="Meine Schwester sagte, dass sie sofort ___.",
        proposed_answer="käme",
        distractors=[Distractor(text=t) for t in distractor_texts],
        gloss_en=gloss_en,
    )


def test_layer3_open_lexical_slot_rejects_without_a_gloss(konjunktiv_topic: Topic) -> None:
    """Baseline: no gloss at all, the rejection fires exactly as before."""
    solver = Layer3AdversarialSolver()
    item = _konjunktiv_item(["kommt"], gloss_en=None)
    passed, reason, error_type = solver.validate(item, topic=konjunktiv_topic)
    assert not passed
    assert error_type == "ambiguity"
    assert "kommt" in (reason or "")


@pytest.mark.skipif(
    not sentence_tagger.analysis_available(),
    reason="requires the German lemma tagger (de_core_news_sm) to confirm same-verb identity",
)
def test_layer3_open_lexical_slot_suppressed_when_gloss_validates_and_distractor_is_same_verb(
    konjunktiv_topic: Topic,
) -> None:
    """ "kommt" is a different TENSE/MOOD form of the same verb ("kommen") as
    the answer "käme" -- ``_same_lexeme``'s prefix heuristic does not
    recognise that (no ablaut table exists for a non-auxiliary verb), but a
    gloss that mechanically validates against the answer's own Konjunktiv II
    features, confirmed via the German lemma tagger to be the same verb,
    resolves exactly this ambiguity."""
    solver = Layer3AdversarialSolver()
    item = _konjunktiv_item(["kommt"], gloss_en="My sister said that she would come immediately.")
    passed, reason, error_type = solver.validate(item, topic=konjunktiv_topic)
    assert passed, f"Unexpected rejection: {reason}"
    assert error_type is None


def test_layer3_open_lexical_slot_still_rejects_a_genuinely_different_lexeme_distractor(
    konjunktiv_topic: Topic,
) -> None:
    """ "ginge" (from "gehen") is a genuinely different VERB from the answer
    "käme" (from "kommen"), not a tense/person form of the same one. A gloss
    can confirm tense/person, which English marks; it never resolves which
    verb was meant (this module does not align gloss words to the German
    answer), so the rejection must still fire even with the same validating
    gloss that relieved the same-verb case above."""
    solver = Layer3AdversarialSolver()
    item = _konjunktiv_item(["ginge"], gloss_en="My sister said that she would come immediately.")
    passed, reason, error_type = solver.validate(item, topic=konjunktiv_topic)
    assert not passed
    assert error_type == "ambiguity"
    assert "ginge" in (reason or "")


def test_layer3_open_lexical_slot_still_rejects_when_gloss_contradicts_the_answer(
    konjunktiv_topic: Topic,
) -> None:
    """A gloss that fails its OWN mechanical consistency check (here: no
    conditional marker at all, contradicting Konjunktiv II) must never be
    trusted to resolve anything -- the rejection fires exactly as if no
    gloss were present."""
    solver = Layer3AdversarialSolver()
    item = _konjunktiv_item(["kommt"], gloss_en="My sister said that she comes immediately.")
    passed, reason, error_type = solver.validate(item, topic=konjunktiv_topic)
    assert not passed
    assert error_type == "ambiguity"


def test_layer3_open_lexical_slot_still_rejects_for_a_non_tense_selecting_topic(
    sample_topic: Topic,
) -> None:
    """A topic whose ``morph_spec`` fixes neither Tense nor Person (here,
    the Dativ-preposition fixture topic) is not tense/person-selecting at
    all -- per ``gloss_required``'s own topic-level rule -- so a gloss must
    never suppress this rejection for it, however well the gloss happens to
    read."""
    solver = Layer3AdversarialSolver()
    item = CandidateItem(
        topic_id=sample_topic.id,
        type="cloze_free",
        difficulty=1,
        prompt="Mein Kollege meinte, dass er lieber ___.",
        proposed_answer="bliebe",
        distractors=[Distractor(text="geht")],
        gloss_en="My colleague said that he would rather stay.",
    )
    passed, reason, error_type = solver.validate(item, topic=sample_topic)
    assert not passed
    assert error_type == "ambiguity"


def test_every_error_taxonomy_category_is_reachable_from_a_real_layer_output(
    pipeline: VerificationPipeline,
    vocab_store: VocabularyStore,
    sample_spec: TopicSpec,
) -> None:
    """Every ``ErrorTaxonomy`` category must be produced by at least one real
    verification-layer output, not just be a value the classifier happens to
    know how to spell.

    This is a direct regression test for the "ALSO" defect: feeding
    ``ErrorClassifier.classify`` the literal reason strings the layers
    actually emitted used to yield ``pedagogical_flaw`` for 15 of 20 of them,
    with ``topic_leak``, ``structural_malformation``, and
    ``morphosyntactic_error`` reachable from no branch at all (shadowed by
    earlier keyword matches, e.g. "Topic leak: ..." hit the "topic" branch
    and returned ``pedagogical_flaw``, and "Distractor 'dem' matches proposed
    answer." was shadowed by an earlier "distractor" branch before it could
    reach the ``ambiguity`` branch). Each layer now attaches its own
    structured code, so this test drives every layer directly instead of
    reverse-engineering reason strings.
    """
    taxonomy = load_taxonomy()
    kasus_dativ_formen = next(t for t in taxonomy if t.id == "kasus_dativ_formen")

    def _item(**overrides: object) -> CandidateItem:
        base = {
            "topic_id": "dativ_nach_praeposition",
            "type": "cloze_free",
            "difficulty": 1,
            "prompt": "Das Buch liegt auf ___ Tisch.",
            "proposed_answer": "dem",
            "distractors": [
                Distractor(text="den"),
                Distractor(text="des"),
                Distractor(text="das"),
            ],
        }
        base.update(overrides)
        return CandidateItem(**base)

    reached: dict[str, str | None] = {}

    # structural_malformation: layer 1, missing gap placeholder.
    res = pipeline.verify_item(_item(prompt="Das Buch liegt auf dem Tisch."), spec=sample_spec)
    reached["structural_malformation"] = res.error_type

    # topic_leak: layer 1, forbidden grammatical terminology in the prompt.
    res = pipeline.verify_item(
        _item(prompt="Setze den Dativ ein: Das Buch liegt auf ___ Tisch."), spec=sample_spec
    )
    reached["topic_leak"] = res.error_type

    # morphosyntactic_error: layer 2, Wechselpräposition direction mismatch.
    res = pipeline.verify_item(_item(prompt="Er stellt das Buch auf ___ Tisch."), spec=sample_spec)
    reached["morphosyntactic_error"] = res.error_type

    # morphological_defect: layer 2, answer not a recognised case form at all,
    # with no following noun to agree with (so no second element is
    # involved -- the defect is in the answer's own morphology). "auch" is a
    # closed-class function word (always treated as within-ceiling, see
    # ``VocabularyStore.FUNCTION_WORDS``), so this exercises layer 2 without
    # tripping the layer 1 vocabulary-ceiling check first.
    res = pipeline.verify_item(
        _item(
            topic_id="kasus_dativ_formen",
            prompt="Ich helfe ___ gern.",
            proposed_answer="auch",
            distractors=[Distractor(text="ja"), Distractor(text="so"), Distractor(text="wie")],
        ),
        spec=sample_spec,
        topic=kasus_dativ_formen,
    )
    reached["morphological_defect"] = res.error_type

    # register_mismatch: layer 1, colloquial/slang register.
    res = pipeline.verify_item(
        _item(prompt="Das ist ja voll krass, dass das Buch auf ___ Tisch liegt."),
        spec=sample_spec,
    )
    reached["register_mismatch"] = res.error_type

    # ambiguity: layer 3, under-constrained gap with no governing anchor.
    res = pipeline.verify_item(
        _item(
            topic_id="konjunktionen_position_0",
            prompt="Ich trinke ___ Kaffee.",
            proposed_answer="oft",
            distractors=[
                Distractor(text="gern"),
                Distractor(text="heute"),
                Distractor(text="viel"),
            ],
        )
    )
    reached["ambiguity"] = res.error_type

    # duplicate: layer 4, identical prompt already in the bank.
    existing = [
        BankItem(
            id="existing-1",
            topic_id="dativ_nach_praeposition",
            type="cloze_free",
            difficulty=1,
            cefr="A2",
            prompt="Das Buch liegt auf ___ Tisch.",
            accepted_answers=["dem"],
        )
    ]
    res = pipeline.verify_item(_item(), spec=sample_spec, existing_bank_items=existing)
    reached["duplicate"] = res.error_type

    # vocabulary_ceiling_violation: layer 1, above-ceiling vocabulary.
    # "Ablauf" is a real B2 entry in the wordlist; fix C means an absent
    # (merely unlisted) word no longer counts as a violation, so this must
    # use a word the list actually contains above the A2 ceiling.
    pipeline_with_vocab = VerificationPipeline(vocab_store=vocab_store)
    res = pipeline_with_vocab.verify_item(
        _item(prompt="Der Ablauf liegt auf ___ Tisch."), spec=sample_spec
    )
    reached["vocabulary_ceiling_violation"] = res.error_type

    # pedagogical_flaw: layer 1, prompt exceeds the spec's token-length limit.
    long_prompt = (
        "Das kleine, freundliche und sehr aufmerksame Buch, das gestern Abend spät "
        "gekauft wurde, liegt heute Morgen ganz still und ruhig auf ___ Tisch."
    )
    res = pipeline.verify_item(_item(prompt=long_prompt), spec=sample_spec)
    reached["pedagogical_flaw"] = res.error_type

    missing = [category for category, got in reached.items() if got != category]
    assert not missing, (
        f"ErrorTaxonomy categories not reachable from a real layer output: {missing}"
    )


# ----------------------------------------------------------------------
# Layer 5: semantic / answer-set expansion (model-backed, optional).
#
# docs/audits/stage-04-pilot-2026-08-14.md: "layer_expander.py ... was never
# wired into pipeline.py ... this is the missing layer, and it is the one
# that addresses the largest defect class." These tests exercise the wiring
# with a fake LLM client (no network), never the real Gemini transport.
# ----------------------------------------------------------------------


class _FakeSemanticLlmClient:
    """Records every prompt and returns a canned response for each, standing
    in for ``GeminiLlmClient`` at the seam ``AnswerSetExpander
    .verify_semantic_validity_many`` calls
    (``.generate_many(prompts, model=..., purpose=...)``)."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, object]] = []

    def generate_many(self, prompts: list[str], model: str, purpose: str) -> list[str]:
        for prompt in prompts:
            self.calls.append({"prompt": prompt, "model": model, "purpose": purpose})
        return [self.response_text for _ in prompts]


def _semantic_item() -> CandidateItem:
    return CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )


def test_layer5_disabled_when_no_llm_client_configured(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """``VerificationPipeline()`` with no ``llm_client`` (every offline/golden
    test's default) must accept exactly as before layer 5 existed -- the
    model-backed layer is optional, never a hard dependency."""
    res = pipeline.verify_item(_semantic_item(), spec=sample_spec)
    assert res.passed
    assert res.accepted


def test_layer5_rejects_on_semantic_invalidity(sample_spec: TopicSpec) -> None:
    """A candidate that passes layers 1-4 but that the model judges
    semantically incoherent (wrong connector / collocation / hallucinated
    content) is rejected at layer 5 with ``pedagogical_flaw``."""
    fake_llm = _FakeSemanticLlmClient(
        '{"valid": false, "reason": "Falsche Kollokation.", "additional_accepted_answers": []}'
    )
    pipeline = VerificationPipeline(llm_client=fake_llm)  # type: ignore[arg-type]

    res = pipeline.verify_item(_semantic_item(), spec=sample_spec)

    assert not res.passed
    assert res.layer_failed == 5
    assert res.error_type == "pedagogical_flaw"
    assert res.reason == "Falsche Kollokation."
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["purpose"] == "answer_expansion"


def test_layer5_accepts_and_merges_additional_answers(sample_spec: TopicSpec) -> None:
    """A candidate the model judges valid is accepted, with its
    ``accepted_answers`` set to the contraction-expanded proposed answer plus
    whatever additional variants the model reports -- the answer-set
    expansion half of this layer's name, not just the semantic gate."""
    fake_llm = _FakeSemanticLlmClient(
        '{"valid": true, "reason": null, "additional_accepted_answers": ["diesem"]}'
    )
    pipeline = VerificationPipeline(llm_client=fake_llm)  # type: ignore[arg-type]

    res = pipeline.verify_item(_semantic_item(), spec=sample_spec)

    assert res.passed
    assert res.accepted
    assert "dem" in res.accepted_answers
    assert "diesem" in res.accepted_answers


def test_layer5_strips_markdown_fence_before_parsing(sample_spec: TopicSpec) -> None:
    """Gemini routinely wraps JSON responses in a ```json fence (no
    ``response_mime_type`` is set on the request, matching the same live
    behaviour ``src.generation.batch_client._parse_response`` works around);
    layer 5 must not treat that fence as a parse failure."""
    fake_llm = _FakeSemanticLlmClient(
        '```json\n{"valid": true, "reason": null, "additional_accepted_answers": []}\n```'
    )
    pipeline = VerificationPipeline(llm_client=fake_llm)  # type: ignore[arg-type]

    res = pipeline.verify_item(_semantic_item(), spec=sample_spec)
    assert res.passed


def test_layer5_degrades_to_accept_on_budget_exceeded(sample_spec: TopicSpec) -> None:
    """CLAUDE.md 9: 'Callers handle [BudgetExceeded] by degrading, never by
    retrying.' Layer 5 is a refinement on top of layers 1-4, not a required
    gate, so a closed budget skips the check rather than rejecting -- or
    crashing the whole batch on -- a candidate that already passed every
    free/cheap layer."""
    from src.llm.client import BudgetExceeded

    class _BudgetExceededLlmClient:
        def generate_many(self, prompts: list[str], model: str, purpose: str) -> list[str]:
            raise BudgetExceeded("Monthly spend ceiling reached.")

    pipeline = VerificationPipeline(llm_client=_BudgetExceededLlmClient())  # type: ignore[arg-type]

    res = pipeline.verify_item(_semantic_item(), spec=sample_spec)
    assert res.passed
    assert res.accepted


def test_layer5_degrades_to_accept_on_server_unavailable(sample_spec: TopicSpec) -> None:
    """A sustained 5xx overload that survives the transport's own bounded
    retries (confirmed live: gemini-3.7-flash returning 503 UNAVAILABLE
    through all of SERVER_ERROR_MAX_RETRIES) must degrade the same way
    BudgetExceeded does -- infrastructure trouble is not a verdict on the
    candidate's German, and must never crash the whole pilot batch."""
    from src.llm.client import ServerUnavailableError

    class _OverloadedLlmClient:
        def generate_many(self, prompts: list[str], model: str, purpose: str) -> list[str]:
            raise ServerUnavailableError("503 UNAVAILABLE")

    pipeline = VerificationPipeline(llm_client=_OverloadedLlmClient())  # type: ignore[arg-type]

    res = pipeline.verify_item(_semantic_item(), spec=sample_spec)
    assert res.passed
    assert res.accepted


def test_layer5_runs_after_dedup_never_spending_a_call_on_a_duplicate(
    sample_spec: TopicSpec,
) -> None:
    """Layer 5 is an LLM call; layer 4 (dedup) is free. A duplicate must be
    rejected at layer 4 without ever reaching the model, so the pilot never
    spends budget checking a candidate that was going to be dropped anyway."""
    fake_llm = _FakeSemanticLlmClient('{"valid": true}')
    pipeline = VerificationPipeline(llm_client=fake_llm)  # type: ignore[arg-type]

    item = _semantic_item()
    existing = [
        BankItem(
            id="existing-1",
            topic_id=item.topic_id,
            type=item.type,
            difficulty=item.difficulty,
            cefr="A2",
            prompt=item.prompt,
            accepted_answers=[item.proposed_answer],
        )
    ]

    res = pipeline.verify_item(item, spec=sample_spec, existing_bank_items=existing)

    assert not res.passed
    assert res.layer_failed == 4
    assert fake_llm.calls == []


# ----------------------------------------------------------------------
# Layer 2 extension: strong/weak/mixed adjective declension gender
# agreement, driven by facets.attributive_adjective_gender_candidates.
#
# docs/audits/stage-04-pilot-2026-08-14.md items 14 and 15: a masculine and a
# feminine noun each given a neuter- or masculine-only strong-declension
# ending, in adjektivdeklination_nullartikel -- a topic layer 2 skipped
# entirely because its morph_spec is deliberately {} (zero article has no UD
# FEATS equivalent).
# ----------------------------------------------------------------------


@pytest.fixture
def nullartikel_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "adjektivdeklination_nullartikel")


@pytest.fixture
def bestimmt_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "adjektivdeklination_bestimmt")


def _adj_item(prompt: str, answer: str, topic_id: str) -> CandidateItem:
    return CandidateItem(
        topic_id=topic_id,
        type="cloze_free",
        difficulty=1,
        prompt=prompt,
        proposed_answer=answer,
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )


def test_layer2_catches_strong_declension_gender_mismatch_masculine_noun(
    pipeline: VerificationPipeline, nullartikel_topic: Topic
) -> None:
    """Pilot audit item 14: 'gutes' (neuter-only strong ending) given for
    masculine 'Kaffee'. morph_spec is {} for this topic, so this can only be
    caught by the syntax_tags-driven declension check, not the morph_spec
    Case gate every other layer 2 check runs under."""
    item = _adj_item(
        "Obwohl sie oft ___ Kaffee trinkt, schläft sie nachts gut.", "gutes", nullartikel_topic.id
    )
    res = pipeline.verify_item(item, topic=nullartikel_topic)
    assert not res.passed
    assert res.layer_failed == 2
    assert res.error_type == "morphosyntactic_error"


def test_layer2_catches_strong_declension_gender_mismatch_feminine_noun(
    pipeline: VerificationPipeline, nullartikel_topic: Topic
) -> None:
    """Pilot audit item 15: 'heißen' (masc/neut-only strong ending) given for
    feminine 'Suppe'."""
    item = _adj_item(
        "Wenn wir ___ Suppe essen, wärmen wir uns schnell auf.", "heißen", nullartikel_topic.id
    )
    res = pipeline.verify_item(item, topic=nullartikel_topic)
    assert not res.passed
    assert res.layer_failed == 2
    assert res.error_type == "morphosyntactic_error"


def test_layer2_accepts_correct_strong_declension(
    pipeline: VerificationPipeline, nullartikel_topic: Topic
) -> None:
    """Every gold example in data/specs/adjektivdeklination_nullartikel.yaml
    must still pass -- the extension must not reject correct German."""
    for prompt, answer in [
        ("___ Kaffee schmeckt sehr gut.", "Heißer"),
        ("Weil sie ___ Wasser trinkt, fühlt sie sich fit.", "kaltes"),
        ("Wenn wir ___ Suppe essen, wärmen wir uns schnell auf.", "heiße"),
    ]:
        item = _adj_item(prompt, answer, nullartikel_topic.id)
        res = pipeline.verify_item(item, topic=nullartikel_topic)
        assert res.passed, f"{answer!r} incorrectly rejected: {res.reason}"


def test_layer2_catches_impossible_ending_for_weak_declension(
    pipeline: VerificationPipeline, bestimmt_topic: Topic
) -> None:
    """'-es' never occurs in the weak declension (only the definite article
    itself carries that signal) -- a defect the paradigm-membership check
    catches regardless of noun gender."""
    item = _adj_item(
        "Weil er Hunger hatte, aß er die ___ Suppe sofort auf.", "heißes", bestimmt_topic.id
    )
    res = pipeline.verify_item(item, topic=bestimmt_topic)
    assert not res.passed
    assert res.layer_failed == 2
    assert res.error_type == "morphosyntactic_error"


def test_layer2_accepts_correct_weak_declension(
    pipeline: VerificationPipeline, bestimmt_topic: Topic
) -> None:
    """Every gold example in data/specs/adjektivdeklination_bestimmt.yaml
    must still pass."""
    for prompt, answer in [
        ("Der ___ Mann liest die Zeitung.", "alte"),
        ("Weil er Hunger hatte, aß er die ___ Suppe sofort auf.", "heiße"),
    ]:
        item = _adj_item(prompt, answer, bestimmt_topic.id)
        res = pipeline.verify_item(item, topic=bestimmt_topic)
        assert res.passed, f"{answer!r} incorrectly rejected: {res.reason}"


# ----------------------------------------------------------------------
# Second pilot audit fixes 1-4 (docs/audits/stage-04-pilot-2026-08-15.md).
#
# fix 1: ambiguity threshold on distinct forms, not answer count.
# fix 2: expansion constrained to the topic's target form.
# fix 3: distractor check re-run against the final expanded accepted set.
# fix 4: expander alternatives validated as real, correctly-agreeing words.
#
# All four live in AnswerSetExpander / VerificationPipeline._finalize_layer5.
# ----------------------------------------------------------------------


@pytest.fixture
def artikel_bestimmt_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "artikel_bestimmt_nom")


@pytest.fixture
def futur_i_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "futur_i")


@pytest.fixture
def adjektiv_komparativ_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "adjektiv_komparativ_superlativ")


@pytest.fixture
def artikel_possessiv_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "artikel_possessiv_nom")


@pytest.fixture
def kasus_genitiv_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "kasus_genitiv_formen")


@pytest.fixture
def artikel_unbestimmt_kein_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "artikel_unbestimmt_kein_nom")


def test_check_ambiguity_rejects_mixed_article_types(artikel_bestimmt_topic: Topic) -> None:
    """docs/audits/.../item 1: an artikel_bestimmt_nom item accepting
    'Der, Ein, Mein, Dein, Sein, Ihr, Unser, Euer, Dieser, Kein' does not
    test the definite article at all -- it accepts any determiner."""
    answers = ["Der", "Ein", "Mein", "Dein", "Sein", "Ihr", "Unser", "Euer", "Dieser", "Kein"]
    reason = AnswerSetExpander.check_ambiguity(answers, artikel_bestimmt_topic)
    assert reason is not None
    assert "determiner type" in reason.lower()


def test_check_ambiguity_rejects_auxiliary_vs_modal(futur_i_topic: Topic) -> None:
    """docs/audits/.../item 13: 'werden' (future auxiliary) and 'können'
    (modal) are two different forms, not two lexemes of one form."""
    answers = ["werden", "können"]
    reason = AnswerSetExpander.check_ambiguity(answers, futur_i_topic)
    assert reason is not None
    assert "verb form" in reason.lower()


def test_check_ambiguity_distinguishes_indicative_from_konjunktiv(futur_i_topic: Topic) -> None:
    """'würden' shares the lemma 'werden' with the indicative Futur I forms
    but is Konjunktiv II / conditional, a different form for a topic
    testing Indikativ Futur I."""
    reason = AnswerSetExpander.check_ambiguity(["werden", "würden"], futur_i_topic)
    assert reason is not None


def test_check_ambiguity_accepts_one_form_many_lexemes(kasus_genitiv_topic: Topic) -> None:
    """docs/audits/.../item 15: ten genitive determiners of different
    subtypes (definite, ein-word, possessive) are all ONE tested form
    (Case=Gen) -- kasus_genitiv_formen has no ArtType tag, so the
    definite/indefinite distinction is not its target feature."""
    answers = [
        "des",
        "eines",
        "meines",
        "deines",
        "seines",
        "ihres",
        "unseres",
        "eures",
        "dieses",
        "jenes",
    ]
    assert AnswerSetExpander.check_ambiguity(answers, kasus_genitiv_topic) is None


def test_check_ambiguity_distinguishes_possessive_from_bare_indefinite(
    artikel_possessiv_topic: Topic,
) -> None:
    """Regression for a live pilot re-run finding: 'ein/kein' and every
    possessive (mein/dein/sein/ihr/unser/euer) all share the SAME ein-word
    ending paradigm, so the coarser Def/Ind check alone cannot tell a bare
    indefinite article apart from a possessive. An artikel_possessiv_nom
    item accepting 'Eine' (genuinely indefinite, not possessive) alongside
    'Meine'/'Deine' does not consistently test the possessive article."""
    answers = ["Deine", "Meine", "Seine", "Ihre", "Unsere", "Eure", "Eine"]
    reason = AnswerSetExpander.check_ambiguity(answers, artikel_possessiv_topic)
    assert reason is not None
    assert "determiner type" in reason.lower()


def test_check_ambiguity_accepts_possessives_across_persons(
    artikel_possessiv_topic: Topic,
) -> None:
    """Every possessive person (mein/dein/sein/ihr/unser/euer) is ONE
    determiner type (Poss); a set spanning only these must not be flagged."""
    answers = ["dein", "mein", "sein", "ihr", "unser", "euer"]
    assert AnswerSetExpander.check_ambiguity(answers, artikel_possessiv_topic) is None


def test_check_ambiguity_falls_back_to_count_for_degree_topics(
    adjektiv_komparativ_topic: Topic,
) -> None:
    """docs/audits/.../item 10: no comparative/superlative decoder exists,
    so a wide answer set for a Degree topic falls back to the audit's own
    sanctioned count threshold."""
    answers = ["langsameren", "nächsten", "langsamen", "normalen", "öffentlichen", "ersten"]
    reason = AnswerSetExpander.check_ambiguity(answers, adjektiv_komparativ_topic)
    assert reason is not None
    assert "threshold" in reason.lower()


def test_check_ambiguity_ignores_topics_with_no_identity_signal(
    pipeline: VerificationPipeline,
) -> None:
    """A topic with no ArtType, no verb-tense marker and no Degree has no
    cheap form signal in this codebase's tables, and must not be guessed."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "dativ_nach_praeposition")
    # A deliberately large, heterogeneous set: if this were mistakenly
    # treated as checkable, it would be flagged.
    answers = ["dem", "einem", "meinem", "deinem", "seinem", "ihrem", "unserem", "eurem"]
    assert AnswerSetExpander.check_ambiguity(answers, topic) is None


def test_determiner_art_type_recognizes_demonstratives() -> None:
    """Regression: 'dieser'/'jener' and their declined forms had no
    closed-class table at all, so ``determiner_art_type`` returned "Unk" for
    every demonstrative -- and "Unk" reads to every consumer of this
    function as "no opinion, do not block". Demonstratives now get their own
    "Dem" bucket, distinct from Def/Ind/Neg/Poss."""
    from src.taxonomy.facets import determiner_art_type

    for form in ("dieser", "diese", "dieses", "diesen", "diesem"):
        assert determiner_art_type(form) == "Dem", form
    for form in ("jener", "jene", "jenes", "jenen", "jenem"):
        assert determiner_art_type(form) == "Dem", form
    # Case-insensitive, matching every other lookup in this module.
    assert determiner_art_type("Diese") == "Dem"
    # Still unrelated to the other buckets.
    assert determiner_art_type("der") == "Def"
    assert determiner_art_type("mein") == "Poss"


def test_check_ambiguity_rejects_definite_vs_demonstrative(artikel_bestimmt_topic: Topic) -> None:
    """Real pilot defect: 'Wir sehen eine Katze. ___ Katze schläft.'
    accepted both 'Die' (Def) and 'Diese' (Dem) -- a demonstrative is not an
    interchangeable alternative to the definite article, it asserts
    something the definite article does not (contrastive/deictic
    selection)."""
    reason = AnswerSetExpander.check_ambiguity(["Die", "Diese"], artikel_bestimmt_topic)
    assert reason is not None
    assert "determiner type" in reason.lower()


def test_check_ambiguity_rejects_negative_vs_demonstrative(
    artikel_unbestimmt_kein_topic: Topic,
) -> None:
    """Real pilot defect: 'Da draußen im Regen steht ___ Mann, der...'
    accepted both 'kein' (Neg) and 'dieser' (Dem) -- opposite meanings
    ("no man" vs. "this man"), both passing unflagged before demonstratives
    had their own determiner type."""
    reason = AnswerSetExpander.check_ambiguity(["kein", "dieser"], artikel_unbestimmt_kein_topic)
    assert reason is not None
    assert "determiner type" in reason.lower()


def test_check_ambiguity_rejects_indefinite_vs_demonstrative(
    artikel_unbestimmt_kein_topic: Topic,
) -> None:
    """Real pilot defect: 'Der Tisch ist neu. Dort steht ___ Lampe.'
    accepted both 'eine' (Ind) and 'diese' (Dem)."""
    reason = AnswerSetExpander.check_ambiguity(["eine", "diese"], artikel_unbestimmt_kein_topic)
    assert reason is not None
    assert "determiner type" in reason.lower()


def test_filter_alternatives_by_target_form_drops_wrong_lemma(futur_i_topic: Topic) -> None:
    """fix 2: an alternative carrying a different verb lemma than the
    proposed answer is dropped before it can ever reach accepted_answers."""
    item = CandidateItem(
        topic_id="futur_i",
        type="cloze_free",
        difficulty=2,
        prompt="Nächstes Jahr ___ ich nach Spanien reisen.",
        proposed_answer="werde",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    alternatives = ["wollen", "möchten", "können", "würden"]
    filtered = AnswerSetExpander.filter_alternatives_by_target_form(
        alternatives, item, futur_i_topic
    )
    assert filtered == []


def test_filter_alternatives_by_target_form_keeps_matching_form(
    artikel_bestimmt_topic: Topic,
) -> None:
    """A same-Definite-value alternative (another definite-article form) is
    kept; expansion is a repair pass, not a blanket filter."""
    item = CandidateItem(
        topic_id="artikel_bestimmt_nom",
        type="cloze_free",
        difficulty=1,
        prompt="___ Hund bellt laut.",
        proposed_answer="Der",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    filtered = AnswerSetExpander.filter_alternatives_by_target_form(
        ["Ein"], item, artikel_bestimmt_topic
    )
    assert filtered == [], "'Ein' (indefinite) must be dropped for a definite-article topic"


def test_layer5_repairs_item_when_fix2_drops_the_mismatched_alternative(
    artikel_bestimmt_topic: Topic,
) -> None:
    """End-to-end: fix 2 drops an indefinite alternative proposed for a
    definite-article topic before it ever reaches accepted_answers, so the
    item is repaired (accepted, without the bad alternative), not rejected.

    ``type`` is ``paragraph_cloze``, not ``error_correction``: fix
    (5-fix-set) "error_correction has no gap, by design" withdrew
    ``error_correction`` from every topic's ``eligible_types`` in
    ``data/taxonomy.yaml`` (it was never actually implemented -- the
    generator does not produce it correctly and nothing verifies it), so
    ``artikel_bestimmt_nom``'s own eligible types are now just
    ``paragraph_cloze``, which this topic's ``requires_context: true``
    already called for."""
    item = CandidateItem(
        topic_id="artikel_bestimmt_nom",
        type="paragraph_cloze",
        difficulty=1,
        prompt="___ Hund bellt laut im Hof.",
        proposed_answer="Der",
        distractors=[Distractor(text="Den"), Distractor(text="Dem"), Distractor(text="Des")],
    )
    fake_llm = _FakeSemanticLlmClient(
        '{"valid": true, "reason": null, "additional_accepted_answers": ["Ein"]}'
    )
    pipeline = VerificationPipeline(
        llm_client=fake_llm,  # type: ignore[arg-type]
        topics=[artikel_bestimmt_topic],
    )

    res = pipeline.verify_item(item, topic=artikel_bestimmt_topic)

    assert res.passed
    assert "Ein" not in res.accepted_answers


def test_layer5_fix2_now_resolves_demonstrative_references_directly(
    artikel_possessiv_topic: Topic,
) -> None:
    """Superseded regression: this test used to be named
    ``..._is_a_backstop_when_fix2_cannot_filter`` and demonstrated that fix
    2 (``filter_alternatives_by_target_form``) could NOT resolve 'Dieser'
    (no closed-class table had a demonstrative entry, so
    ``determiner_art_type`` returned "Unk" and fix 2 passed every
    alternative through unfiltered), leaving fix 1's mutual-consistency
    check (``check_ambiguity``) as the only thing that still caught 'Der'
    and 'Mein' disagreeing with each other.

    The largest defect class found by a later audit was exactly this hole:
    a demonstrative reads as compatible with every other determiner type
    because it has no type of its own. Now that ``determiner_art_type``
    recognises demonstratives as their own ``"Dem"`` bucket, fix 2 resolves
    'Dieser' directly and drops 'Der' (Def) and 'Mein' (Poss) itself before
    the mutual-consistency backstop is ever needed -- the item is repaired
    down to its own reference answer, not rejected. Kept as a positive
    regression (rather than deleted) precisely because it used to encode the
    now-fixed permissive behaviour: CLAUDE.md 7 requires this be explained,
    not silently dropped.
    """
    item = CandidateItem(
        topic_id="artikel_possessiv_nom",
        type="paragraph_cloze",
        difficulty=1,
        prompt="___ Hund bellt laut im Hof.",
        proposed_answer="Dieser",
        distractors=[Distractor(text="Den"), Distractor(text="Dem"), Distractor(text="Des")],
    )
    fake_llm = _FakeSemanticLlmClient(
        '{"valid": true, "reason": null, "additional_accepted_answers": ["Der", "Mein"]}'
    )
    pipeline = VerificationPipeline(
        llm_client=fake_llm,  # type: ignore[arg-type]
        topics=[artikel_possessiv_topic],
    )

    res = pipeline.verify_item(item, topic=artikel_possessiv_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["Dieser"]


def test_layer5_drops_hallucinated_alternative_not_real_word(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """docs/audits/.../item 20: the expander invented 'paratstünden', not a
    German word. fix 4 must drop it (repair), not accept it."""
    fake_llm = _FakeSemanticLlmClient(
        '{"valid": true, "reason": null, "additional_accepted_answers": ["paratstunden"]}'
    )
    pipeline_with_llm = VerificationPipeline(llm_client=fake_llm)  # type: ignore[arg-type]

    res = pipeline_with_llm.verify_item(_semantic_item(), spec=sample_spec)

    assert res.passed
    assert "paratstunden" not in res.accepted_answers


def test_layer5_rejects_on_post_expansion_distractor_collision() -> None:
    """fix 3: an alternative the semantic layer proposes that happens to
    equal one of the item's OWN distractors must reject the item (it was
    under-constrained to begin with), not silently ship a distractor that
    is also a correct answer (docs/audits/.../items 21, 26, 27, 29)."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="einem")],
    )
    fake_llm = _FakeSemanticLlmClient(
        '{"valid": true, "reason": null, "additional_accepted_answers": ["einem"]}'
    )
    pipeline = VerificationPipeline(llm_client=fake_llm)  # type: ignore[arg-type]

    res = pipeline.verify_item(item)

    assert not res.passed
    assert res.layer_failed == 5
    assert res.error_type == "structural_malformation"


# ----------------------------------------------------------------------
# docs/audits/stage-04-a2-pilot-audit.md's four fixes:
#   1. filter the WHOLE accepted set by target form, not only the extras
#   2. cue consistency (accepted answers must be forms of the item's cue)
#   3. Unk-facet honesty (reject as under-constrained, don't silently accept)
#   4. relativsatz_dativ's missing PronType: Rel (tested in test_taxonomy.py)
#
# 11 of the audit's 17 defective items shared the fix-1 root cause; the six
# concrete real-world defects the audit named are reproduced below as
# end-to-end regression fixtures through the real pipeline (real taxonomy
# topics, a fake layer-5 LLM client standing in for the model, exactly the
# additional_accepted_answers the audit found the chain had accepted).
# ----------------------------------------------------------------------


@pytest.fixture
def nebensatz_indirekte_frage_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "nebensatz_indirekte_frage")


@pytest.fixture
def nebensatz_wenn_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "nebensatz_wenn")


@pytest.fixture
def verben_reflexiv_akk_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "verben_reflexiv_akk")


def _extras_llm(extras: list[str]) -> _FakeSemanticLlmClient:
    return _FakeSemanticLlmClient(
        json.dumps({"valid": True, "reason": None, "additional_accepted_answers": extras})
    )


def test_a2_audit_item_37_drops_second_tense_for_cued_verb(
    nebensatz_indirekte_frage_topic: Topic,
) -> None:
    """'Weißt du, wo er ___ (wohnen)?' accepted BOTH 'wohnt' and 'wohnte' --
    nothing in the sentence forces past tense, and the topic's own
    morph_spec is deliberately empty (it tests verb-final word order, not
    tense), so only the widened, cue-independent verb-form check (fix 1)
    catches this."""
    item = CandidateItem(
        topic_id="nebensatz_indirekte_frage",
        type="cloze_cued",
        difficulty=2,
        prompt="Weißt du, wo er ___?",
        proposed_answer="wohnt",
        distractors=[
            Distractor(text="wohnst"),
            Distractor(text="wohnen"),
            Distractor(text="wohnst"),
        ],
        cue="wohnen",
    )
    pipeline = VerificationPipeline(llm_client=_extras_llm(["wohnte"]))  # type: ignore[arg-type]

    res = pipeline.verify_item(item, topic=nebensatz_indirekte_frage_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["wohnt"]


def test_a2_audit_item_38_drops_future_and_past_alternatives(
    nebensatz_indirekte_frage_topic: Topic,
) -> None:
    """'Sie möchte wissen, wann das Konzert ___ (beginnen).' accepted
    'beginnt', 'beginnen wird', and 'begann' -- three tenses for one cue."""
    item = CandidateItem(
        topic_id="nebensatz_indirekte_frage",
        type="cloze_cued",
        difficulty=2,
        prompt="Sie möchte wissen, wann das Konzert ___.",
        proposed_answer="beginnt",
        distractors=[
            Distractor(text="endet"),
            Distractor(text="dauert"),
            Distractor(text="startet"),
        ],
        cue="beginnen",
    )
    pipeline = VerificationPipeline(  # type: ignore[arg-type]
        llm_client=_extras_llm(["beginnen wird", "begann"])
    )

    res = pipeline.verify_item(item, topic=nebensatz_indirekte_frage_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["beginnt"]


def test_a2_audit_item_41_drops_wrong_lexeme_and_konjunktiv_ii(
    nebensatz_wenn_topic: Topic,
) -> None:
    """'Wenn du Zeit ___, helfen wir dir.' accepted 'hast' (indicative,
    correct), 'findest' (a different lexeme entirely), and 'hättest'
    (Konjunktiv II, which needs a subjunctive main clause this one is not).
    No cue on this item -- the widened verb check must fire from the
    reference answer's own tagged POS alone, not from a cue."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_free",
        difficulty=2,
        prompt="Wenn du Zeit ___, helfen wir dir.",
        proposed_answer="hast",
        distractors=[Distractor(text="habe"), Distractor(text="habt"), Distractor(text="haben")],
    )
    pipeline = VerificationPipeline(llm_client=_extras_llm(["findest", "hättest"]))  # type: ignore[arg-type]

    res = pipeline.verify_item(item, topic=nebensatz_wenn_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["hast"]


def test_a2_audit_item_42_cue_consistency_drops_every_wrong_lexeme(
    nebensatz_wenn_topic: Topic,
) -> None:
    """'Wenn er Hunger hat, ___ er sich eine Suppe.' cue 'kochen', accepted
    'kocht' plus 'macht', 'bestellt', 'holt', 'kauft', 'gönnt' -- a cued
    item whose answer set ignores its own cue is self-contradictory (fix
    2); every wrong-lexeme extra must be dropped."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_cued",
        difficulty=2,
        prompt="Wenn er Hunger hat, ___ er sich eine Suppe.",
        proposed_answer="kocht",
        distractors=[Distractor(text="isst"), Distractor(text="trinkt"), Distractor(text="backt")],
        cue="kochen",
    )
    pipeline = VerificationPipeline(  # type: ignore[arg-type]
        llm_client=_extras_llm(["macht", "bestellt", "holt", "kauft", "gönnt"])
    )

    res = pipeline.verify_item(item, topic=nebensatz_wenn_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["kocht"]


def test_a2_audit_item_50_drops_non_reflexive_noun_phrases(
    verben_reflexiv_akk_topic: Topic,
) -> None:
    """'Ich wasche ___ jeden Morgen mit kaltem Wasser.' accepted 'mich' plus
    'mein Gesicht' and 'meine Haare' -- only 'mich' is reflexive, the rest
    are ordinary direct objects that stop the item testing its own topic."""
    item = CandidateItem(
        topic_id="verben_reflexiv_akk",
        type="cloze_free",
        difficulty=1,
        prompt="Ich wasche ___ jeden Morgen mit kaltem Wasser.",
        proposed_answer="mich",
        distractors=[Distractor(text="dich"), Distractor(text="euch"), Distractor(text="sich")],
    )
    pipeline = VerificationPipeline(  # type: ignore[arg-type]
        llm_client=_extras_llm(["mein Gesicht", "meine Haare"])
    )

    res = pipeline.verify_item(item, topic=verben_reflexiv_akk_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["mich"]


def test_a2_audit_item_9_drops_adverbs_and_article_keeps_adjectives(
    nullartikel_topic: Topic,
) -> None:
    """'Weil er ___ Milch trinkt, bleibt er gesund.' accepted 'frische' plus
    'immer', 'gerne' (adverbs -- do not decline at all) and 'keine' (a
    negative article, not an adjective). 'warme'/'kalte' are genuine
    same-form adjective alternatives and must survive (ten answers are fine
    when they carry one form)."""
    item = CandidateItem(
        topic_id="adjektivdeklination_nullartikel",
        type="cloze_free",
        difficulty=2,
        prompt="Weil er ___ Milch trinkt, bleibt er gesund.",
        proposed_answer="frische",
        distractors=[
            Distractor(text="frischer"),
            Distractor(text="frisches"),
            Distractor(text="frischen"),
        ],
    )
    pipeline = VerificationPipeline(  # type: ignore[arg-type]
        llm_client=_extras_llm(["immer", "gerne", "keine", "warme", "kalte"])
    )

    res = pipeline.verify_item(item, topic=nullartikel_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert set(res.accepted_answers) == {"frische", "warme", "kalte"}
    assert "immer" not in res.accepted_answers
    assert "gerne" not in res.accepted_answers
    assert "keine" not in res.accepted_answers


# ----------------------------------------------------------------------
# Fix 2 (cue consistency) and fix 3 (Unk-facet honesty), unit-tested
# directly against AnswerSetExpander.
# ----------------------------------------------------------------------


def test_filter_by_cue_consistency_noop_without_cue() -> None:
    """No cue at all is a no-op: the answers pass through unchanged."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_free",
        difficulty=1,
        prompt="Wenn du Zeit ___, helfen wir dir.",
        proposed_answer="hast",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    answers, reason = AnswerSetExpander.filter_by_cue_consistency(["hast", "findest"], item)
    assert reason is None
    assert answers == ["hast", "findest"]


def test_filter_by_cue_consistency_drops_wrong_lexeme() -> None:
    """A cued item's accepted set is filtered to forms of the cue's own
    lemma; a same-tense, different-lexeme alternative is dropped."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_cued",
        difficulty=2,
        prompt="Wenn er Hunger hat, ___ er sich eine Suppe.",
        proposed_answer="kocht",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
        cue="kochen",
    )
    answers, reason = AnswerSetExpander.filter_by_cue_consistency(
        ["kocht", "macht", "bestellt"], item
    )
    assert reason is None
    assert answers == ["kocht"]


def test_filter_by_cue_consistency_rejects_outright_when_primary_contradicts_cue() -> None:
    """If the PRIMARY answer itself is not a form of the item's own cue,
    the item is self-contradictory and must be rejected outright, not
    merely repaired down to an empty set."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_cued",
        difficulty=2,
        prompt="Wenn er Hunger hat, ___ er sich eine Suppe.",
        proposed_answer="macht",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
        cue="kochen",
    )
    answers, reason = AnswerSetExpander.filter_by_cue_consistency(["macht"], item)
    assert answers == []
    assert reason is not None
    assert "self-contradictory" in reason


def test_filter_by_cue_consistency_noop_with_empty_string_cue() -> None:
    """Regression: the model routinely emits ``cue: ""`` for an item with no
    real cue, rather than omitting the field. A prior fix only special-cased
    ``cue=None`` and left the empty string falling through to the "cue is
    set" branch, so ``lemma_candidates("")`` never matched anything and
    every such item was rejected outright as self-contradictory (36
    rejections in one pilot) even though it never had a cue to begin with.
    Whitespace-only must be treated the same way."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_free",
        difficulty=1,
        prompt="Wenn du Zeit hast, helfen wir ___.",
        proposed_answer="das",
        distractors=[Distractor(text="dir"), Distractor(text="dich"), Distractor(text="dem")],
        cue="",
    )
    answers, reason = AnswerSetExpander.filter_by_cue_consistency(["das"], item)
    assert reason is None
    assert answers == ["das"]

    whitespace_item = item.model_copy(update={"cue": "   "})
    answers, reason = AnswerSetExpander.filter_by_cue_consistency(["das"], whitespace_item)
    assert reason is None
    assert answers == ["das"]


# ----------------------------------------------------------------------
# Fix 5 (cue degree consistency), unit-tested directly against
# AnswerSetExpander, plus one end-to-end pipeline test.
# ----------------------------------------------------------------------


def _degree_cued_item(proposed_answer: str) -> CandidateItem:
    return CandidateItem(
        topic_id="adjektiv_komparativ_superlativ",
        type="cloze_cued",
        difficulty=2,
        prompt="Das ist das ___ (groß) Fenster im ganzen Haus.",
        proposed_answer=proposed_answer,
        distractors=[Distractor(text="kleine"), Distractor(text="alte"), Distractor(text="neue")],
        cue="groß",
    )


def test_filter_by_cue_degree_noop_without_cue() -> None:
    """No cue at all is a no-op: the answers pass through unchanged."""
    item = _degree_cued_item("große").model_copy(update={"cue": None})
    answers, reason = AnswerSetExpander.filter_by_cue_degree(["große", "größte"], item)
    assert reason is None
    assert answers == ["große", "größte"]


def test_filter_by_cue_degree_drops_superlative_when_cue_is_positive() -> None:
    """Real pilot defect: 'das ___ (groß) Fenster' cue 'groß' (positive)
    accepted both 'große' (Pos, correct) and 'größte' (Sup, wrong) -- the
    cue names the citation form the learner inflects, not a licence to
    switch the degree of comparison."""
    item = _degree_cued_item("große")
    answers, reason = AnswerSetExpander.filter_by_cue_degree(["große", "größte"], item)
    assert reason is None
    assert answers == ["große"]


def test_filter_by_cue_degree_rejects_outright_when_primary_is_wrong_degree() -> None:
    """If the PRIMARY answer itself is the wrong degree for its own cue, the
    item is self-contradictory and must be rejected outright."""
    item = _degree_cued_item("größte")
    answers, reason = AnswerSetExpander.filter_by_cue_degree(["größte"], item)
    assert answers == []
    assert reason is not None
    assert "self-contradictory" in reason


def test_filter_by_cue_degree_noop_for_a_non_degree_cue() -> None:
    """A verb cue (e.g. 'kochen') has no Degree at all -- the tagger simply
    never marks Degree on a verb, so this must fail open rather than reject
    on a dimension the cue was never claiming in the first place."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_cued",
        difficulty=2,
        prompt="Wenn er Hunger hat, ___ er sich eine Suppe.",
        proposed_answer="kocht",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
        cue="kochen",
    )
    answers, reason = AnswerSetExpander.filter_by_cue_degree(["kocht"], item)
    assert reason is None
    assert answers == ["kocht"]


def test_layer5_fix5_drops_superlative_alternative_for_a_positive_cue(
    adjektiv_komparativ_topic: Topic,
) -> None:
    """End-to-end: the semantic layer proposes 'größte' as an additional
    accepted answer for a cue-'groß' item; fix 5 drops it before it ever
    reaches ``accepted_answers``, repairing the item rather than rejecting
    it."""
    item = _degree_cued_item("große")
    pipeline = VerificationPipeline(llm_client=_extras_llm(["größte"]))  # type: ignore[arg-type]

    res = pipeline.verify_item(item, topic=adjektiv_komparativ_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["große"]


# ----------------------------------------------------------------------
# Fix 4 (possessive person agreement), unit-tested directly against
# AnswerSetExpander, plus one end-to-end pipeline test.
# ----------------------------------------------------------------------


def _possessive_item(prompt: str, proposed_answer: str) -> CandidateItem:
    return CandidateItem(
        topic_id="artikel_possessiv_nom",
        type="paragraph_cloze",
        difficulty=1,
        prompt=prompt,
        proposed_answer=proposed_answer,
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )


def test_check_possessive_person_agreement_noop_for_non_possessive_topic(
    artikel_bestimmt_topic: Topic,
) -> None:
    """A no-op for any topic that is not a possessive-article topic."""
    item = _possessive_item("Anna kocht gern, weil ___ Küche sehr groß ist.", "die").model_copy(
        update={"topic_id": "artikel_bestimmt_nom"}
    )
    answers, reason = AnswerSetExpander.check_possessive_person_agreement(
        ["die"], item, artikel_bestimmt_topic
    )
    assert reason is None
    assert answers == ["die"]


def test_check_possessive_person_agreement_drops_wrong_persons(
    artikel_possessiv_topic: Topic,
) -> None:
    """Real pilot defect: 'Anna kocht gern, weil ___ Küche sehr groß ist.'
    accepted 'ihre' (Anna, 3rd singular -- correct), 'diese' (a
    demonstrative, caught separately by fix 3), 'unsere' (1st plural) and
    'meine' (1st singular). Beyond the demonstrative, three different
    persons were accepted with nothing in the sentence choosing between
    them; only 'ihre' agrees with the established possessor 'Anna'."""
    item = _possessive_item("Anna kocht gern, weil ___ Küche sehr groß ist.", "ihre")
    answers, reason = AnswerSetExpander.check_possessive_person_agreement(
        ["ihre", "unsere", "meine"], item, artikel_possessiv_topic
    )
    assert reason is None
    assert answers == ["ihre"]


def test_check_possessive_person_agreement_rejects_outright_when_primary_disagrees(
    artikel_possessiv_topic: Topic,
) -> None:
    """If the PRIMARY answer itself does not agree with the established
    possessor, the item is self-contradictory and must be rejected
    outright, not merely repaired down to an empty set."""
    item = _possessive_item("Anna kocht gern, weil ___ Küche sehr groß ist.", "unsere")
    answers, reason = AnswerSetExpander.check_possessive_person_agreement(
        ["unsere"], item, artikel_possessiv_topic
    )
    assert answers == []
    assert reason is not None
    assert "self-contradictory" in reason


def test_check_possessive_person_agreement_rejects_when_no_possessor_established(
    artikel_possessiv_topic: Topic,
) -> None:
    """A bare, contextless carrier with no pronoun or proper noun anywhere
    in the sentence establishes no possessor at all; a possessive-topic item
    that cannot be resolved this way is under-constrained, not silently
    accepted on whichever person the generator happened to propose."""
    item = _possessive_item("___ Buch liegt auf dem Tisch.", "mein")
    answers, reason = AnswerSetExpander.check_possessive_person_agreement(
        ["mein", "dein"], item, artikel_possessiv_topic
    )
    assert answers == []
    assert reason is not None
    assert "under-constrained" in reason


def test_check_possessive_person_agreement_noop_when_no_possessive_answer_survives(
    artikel_possessiv_topic: Topic,
) -> None:
    """If an earlier stage already narrowed the accepted set down to
    non-possessive-form answers only (e.g. a demonstrative fix 3 alone
    would resolve), there is nothing left for THIS check to validate a
    possessor against, even with no possessor established in the carrier --
    it must not manufacture a rejection out of an empty carrier a different
    check already made moot."""
    item = _possessive_item("___ Hund bellt laut im Hof.", "Dieser")
    answers, reason = AnswerSetExpander.check_possessive_person_agreement(
        ["Dieser"], item, artikel_possessiv_topic
    )
    assert reason is None
    assert answers == ["Dieser"]


def test_layer5_fix4_drops_wrong_person_possessives(artikel_possessiv_topic: Topic) -> None:
    """End-to-end: the semantic layer proposes 'unsere' and 'meine' as
    additional accepted answers for a possessive item whose carrier
    establishes 'Anna' (3rd singular) as the possessor; fix 4 drops both
    before they ever reach ``accepted_answers``."""
    item = _possessive_item("Anna kocht gern, weil ___ Küche sehr groß ist.", "ihre")
    pipeline = VerificationPipeline(  # type: ignore[arg-type]
        llm_client=_extras_llm(["unsere", "meine"])
    )

    res = pipeline.verify_item(item, topic=artikel_possessiv_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["ihre"]


def test_layer5_fix3_and_fix4_together_reproduce_the_full_audit_example(
    artikel_possessiv_topic: Topic,
) -> None:
    """The literal combined audit example: 'Anna kocht gern, weil ___ Küche
    sehr groß ist.' accepted 'ihre', 'diese', 'unsere' and 'meine' all at
    once. Fix 3 (Dem determiner type) drops 'diese' via fix 2's
    target-form filter; fix 4 (possessive person agreement) drops 'unsere'
    and 'meine'. Only the correct 'ihre' survives -- fixes 3 and 4 acting
    together, exactly as a real pilot run would exercise them, not each in
    isolation."""
    item = _possessive_item("Anna kocht gern, weil ___ Küche sehr groß ist.", "ihre")
    pipeline = VerificationPipeline(  # type: ignore[arg-type]
        llm_client=_extras_llm(["diese", "unsere", "meine"])
    )

    res = pipeline.verify_item(item, topic=artikel_possessiv_topic)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["ihre"]


def test_check_facet_derivability_rejects_when_facet_is_all_unk(
    monkeypatch: pytest.MonkeyPatch, artikel_bestimmt_topic: Topic
) -> None:
    """docs/audits/stage-04-a2-pilot-audit.md rule 2: an unresolvable facet
    with more than one accepted answer is under-constrained, not silently
    accepted. Wired via a monkeypatched ``derive_facet`` so this test
    exercises the wiring itself, independent of whether any real sentence
    in this codebase's tables currently produces an all-Unk facet (the
    tagger rewrite this cycle made that genuinely rare)."""
    monkeypatch.setattr("src.taxonomy.facets.facet_space", lambda topic: ("Gender", "Number"))
    monkeypatch.setattr(
        "src.taxonomy.facets.derive_facet", lambda item, topic: "Gender=Unk|Number=Unk"
    )
    item = CandidateItem(
        topic_id="artikel_bestimmt_nom",
        type="cloze_free",
        difficulty=1,
        prompt="___ Hund bellt laut.",
        proposed_answer="Der",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    reason = AnswerSetExpander.check_facet_derivability(
        ["Der", "Ein"], item, artikel_bestimmt_topic
    )
    assert reason is not None
    assert "could not be derived" in reason


def test_check_facet_derivability_guards_against_mass_rejection_without_tagger(
    monkeypatch: pytest.MonkeyPatch, artikel_bestimmt_topic: Topic
) -> None:
    """When the spaCy model is unavailable (``analysis_available() is
    False``), this rule must be a no-op rather than a mass-rejection
    engine, even for a facet that WOULD resolve to all-Unk -- several of
    facets.py's closed-class categories defer entire dimensions to the
    tagger with no fallback of their own."""
    monkeypatch.setattr("src.taxonomy.tagger.analysis_available", lambda: False)
    monkeypatch.setattr(
        "src.taxonomy.facets.derive_facet", lambda item, topic: "Gender=Unk|Number=Unk"
    )
    item = CandidateItem(
        topic_id="artikel_bestimmt_nom",
        type="cloze_free",
        difficulty=1,
        prompt="___ Hund bellt laut.",
        proposed_answer="Der",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    reason = AnswerSetExpander.check_facet_derivability(
        ["Der", "Ein"], item, artikel_bestimmt_topic
    )
    assert reason is None


def test_check_facet_derivability_ignores_topics_with_no_facet_space(
    nebensatz_wenn_topic: Topic,
) -> None:
    """A topic with an empty facet space (nebensatz_wenn: structural, tests
    word order, not any morphological feature) has NO facet to derive by
    design -- this must never be treated as "unresolvable" and rejected."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_free",
        difficulty=1,
        prompt="Wenn du Zeit ___, helfen wir dir.",
        proposed_answer="hast",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    reason = AnswerSetExpander.check_facet_derivability(
        ["hast", "habt"], item, nebensatz_wenn_topic
    )
    assert reason is None


def test_check_facet_derivability_ignores_single_answer_sets(
    artikel_bestimmt_topic: Topic,
) -> None:
    """Fewer than two accepted answers means there is nothing to be
    ambiguous about, regardless of facet resolvability."""
    item = CandidateItem(
        topic_id="artikel_bestimmt_nom",
        type="cloze_free",
        difficulty=1,
        prompt="___ Hund bellt laut.",
        proposed_answer="Der",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    reason = AnswerSetExpander.check_facet_derivability(["Der"], item, artikel_bestimmt_topic)
    assert reason is None


def test_check_ambiguity_widens_verb_check_via_tagger_when_item_given(
    nebensatz_wenn_topic: Topic,
) -> None:
    """nebensatz_wenn declares no Tense/Mood/Voice/Aspect at all (it tests
    verb-final word order, not tense), so the pre-fix gate never entered
    the verb branch for it. With ``item`` supplied, the reference answer's
    own tagged POS is enough to widen the check."""
    item = CandidateItem(
        topic_id="nebensatz_wenn",
        type="cloze_free",
        difficulty=1,
        prompt="Wenn du Zeit ___, helfen wir dir.",
        proposed_answer="hast",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    reason = AnswerSetExpander.check_ambiguity(["hast", "findest"], nebensatz_wenn_topic, item=item)
    assert reason is not None
    assert "verb form" in reason.lower()


def test_check_ambiguity_without_item_keeps_old_narrow_behaviour(
    nebensatz_wenn_topic: Topic,
) -> None:
    """Every direct caller that predates the widening (every other
    ``check_ambiguity`` test in this file) must see EXACTLY the old
    behaviour: with no ``item``, a topic with no declared Tense/Mood/Voice/
    Aspect and no VerbType tag has no identity signal, full stop."""
    reason = AnswerSetExpander.check_ambiguity(["hast", "findest"], nebensatz_wenn_topic)
    assert reason is None


def test_filter_alternatives_by_target_form_pos_fallback_drops_wrong_word_class(
    nullartikel_topic: Topic,
) -> None:
    """docs/audits/stage-04-a2-pilot-audit.md item 9: an adverb ('immer')
    and a negative article ('keine') are not adjective forms of 'frische'
    at all -- dropped by the general part-of-speech fallback, independent
    of the Verb/ArtType branches above it (this topic's own ArtType tag is
    the "Zero" sentinel, which must NOT be treated as a real determiner
    identity signal)."""
    item = CandidateItem(
        topic_id="adjektivdeklination_nullartikel",
        type="cloze_free",
        difficulty=2,
        prompt="Weil er ___ Milch trinkt, bleibt er gesund.",
        proposed_answer="frische",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )
    filtered = AnswerSetExpander.filter_alternatives_by_target_form(
        ["immer", "gerne", "keine", "warme"], item, nullartikel_topic
    )
    assert filtered == ["warme"]


def test_layer1_rejects_item_type_not_in_topic_eligible_types(
    pipeline: VerificationPipeline,
) -> None:
    """docs/audits/stage-04-pilot-2026-08-15.md fix 5: an item's type must
    be in its topic's eligible_types. This is the backstop for that rule --
    generation is instructed to pick only from eligible_types, but this
    check is what actually enforces it regardless of whether generation
    complied."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "konjunktiv_i_indirekte_rede")
    assert "cloze_free" not in topic.eligible_types, (
        "fixture assumption: this topic must not allow cloze_free"
    )
    item = CandidateItem(
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        prompt="Er sagte, er ___ krank.",
        proposed_answer="sei",
        distractors=[Distractor(text="ist"), Distractor(text="war"), Distractor(text="wäre")],
    )

    res = pipeline.verify_item(item, topic=topic)

    assert not res.passed
    assert res.layer_failed == 1
    assert res.error_type == "structural_malformation"
    assert "eligible_types" in (res.reason or "")


def test_layer1_accepts_item_type_that_is_in_topic_eligible_types(
    pipeline: VerificationPipeline, sample_spec: TopicSpec, sample_topic: Topic
) -> None:
    """The enforcement must not reject a type the topic genuinely allows."""
    assert "cloze_free" in sample_topic.eligible_types
    item = CandidateItem(
        topic_id=sample_topic.id,
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec, topic=sample_topic)
    assert res.passed, f"unexpected rejection: {res.reason}"


# ----------------------------------------------------------------------
# Cycle-2 report, tasks 1-5.
# ----------------------------------------------------------------------
#
# Task 1: the blanking pipeline's computed answers must survive the chain.
#
# ``src/generation/blanking/`` removes a token from an already-tagged
# sentence, so its answer is OBSERVED and its accepted set is COMPUTED from
# a closed paradigm, not proposed and heuristically widened. A candidate
# opts into this by carrying the extra field ``computed_accepted_answers``
# (a list of strings) -- ``CandidateItem`` is ``frozen``/``extra="allow"``
# and not owned by this module, so this is a documented convention rather
# than a first-class field; see ``AnswerSetExpander
# .get_computed_accepted_answers``'s own docstring.
# ----------------------------------------------------------------------


def test_computed_accepted_answers_survive_with_exactly_one_member_no_llm_client() -> None:
    """A blanked item's computed set of exactly one answer must come out
    the other side with EXACTLY that one answer -- not widened by the
    cheap contraction-expansion heuristic (``AnswerSetExpander
    .expand_answers`` would turn 'im' into ['im', 'in dem']) and not
    replaced by anything else, even with no LLM client configured (the
    common case: most verification runs are offline)."""
    pipeline = VerificationPipeline(vocab_store=VocabularyStore({}))
    item = CandidateItem(
        topic_id="does_not_matter_for_this_test",
        type="cloze_free",
        difficulty=1,
        prompt="Er wohnt ___ Zentrum der Stadt.",
        proposed_answer="im",
        distractors=[Distractor(text="x"), Distractor(text="y")],
        computed_accepted_answers=["im"],
    )

    res = pipeline.verify_item(item, topic=None, spec=None)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["im"]


def test_computed_accepted_answers_survive_in_verify_batch_no_llm_client() -> None:
    """The same guarantee holds through ``verify_batch``'s (not just
    ``verify_item``'s) no-llm-client survivor path."""
    pipeline = VerificationPipeline(vocab_store=VocabularyStore({}))
    item = CandidateItem(
        topic_id="does_not_matter_for_this_test",
        type="cloze_free",
        difficulty=1,
        prompt="Er wohnt ___ Zentrum der Stadt.",
        proposed_answer="im",
        distractors=[Distractor(text="x"), Distractor(text="y")],
        computed_accepted_answers=["im"],
    )

    report = pipeline.verify_batch([item])

    assert report.passed_count == 1
    assert report.results[0].accepted_answers == ["im"]


def test_computed_accepted_answers_ignore_model_proposed_additions() -> None:
    """Even with a real (faked) LLM client configured and returning
    ``additional_accepted_answers``, a computed set is FINAL: no
    model-proposed addition is merged in."""
    fake_llm = _FakeSemanticLlmClient(
        '{"valid": true, "reason": null, "additional_accepted_answers": ["dem"]}'
    )
    pipeline = VerificationPipeline(
        vocab_store=VocabularyStore({}),
        llm_client=fake_llm,  # type: ignore[arg-type]
    )
    item = CandidateItem(
        topic_id="does_not_matter_for_this_test",
        type="cloze_free",
        difficulty=1,
        prompt="Er wohnt ___ Zentrum der Stadt.",
        proposed_answer="im",
        distractors=[Distractor(text="x"), Distractor(text="y")],
        computed_accepted_answers=["im"],
    )

    res = pipeline.verify_item(item, topic=None, spec=None)

    assert res.passed, f"unexpected rejection: {res.reason}"
    assert res.accepted_answers == ["im"]


def test_computed_accepted_answers_absent_falls_back_to_ordinary_chain(
    pipeline: VerificationPipeline, sample_spec: TopicSpec, sample_topic: Topic
) -> None:
    """A candidate that never opted into ``computed_accepted_answers`` is
    completely unaffected -- this is exactly ``test_layer5_disabled_when_no
    _llm_client_configured``'s existing, unchanged behaviour (no LLM client
    configured means layer 5 is a bare no-op, per that test's own
    docstring), asserted again here as the control case for the two
    computed-set tests above it."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec, topic=sample_topic)
    assert res.passed


def test_computed_accepted_answers_still_run_ambiguity_and_distractor_checks() -> None:
    """A computed set is trusted as CORRECT, not as immune to the other
    checks the task calls out: ambiguity and distractor collision must
    still run. Here the computed set's second member collides with the
    item's own distractor, so it is rejected as under-constrained rather
    than shipped. ``type="cloze_cued"`` (rather than ``cloze_free``) keeps
    layer 3's own under-constrained-prompt heuristic (a check for a
    plausible governing preposition/verb/determiner, unrelated to this
    test) from firing first on the deliberately minimal 'y' filler
    distractor, so this test isolates layer 5's collision check."""
    pipeline = VerificationPipeline(vocab_store=VocabularyStore({}))
    item = CandidateItem(
        topic_id="does_not_matter_for_this_test",
        type="cloze_cued",
        difficulty=1,
        prompt="Der Film ___ sehr spannend.",
        proposed_answer="ist",
        distractors=[Distractor(text="war"), Distractor(text="y")],
        computed_accepted_answers=["ist", "war"],
    )

    res = pipeline.verify_item(item, topic=None, spec=None)

    assert not res.passed
    assert res.layer_failed == 5
    assert res.error_type == "structural_malformation"
    assert "war" in (res.reason or "")


# ----------------------------------------------------------------------
# Task 2: determiner-type spanning, derived from the answers themselves,
# not gated on the topic declaring ``ArtType``.
# ----------------------------------------------------------------------


def test_check_ambiguity_rejects_determiner_type_span_with_no_declared_identity_signal() -> None:
    """'kaufen wir ___ Apfel auf dem Markt' accepted einen (Ind), den
    (Def), diesen (Dem) AND keinen (Neg) -- four different determiner
    types -- and the check that exists specifically to catch this never
    fired because it is gated on the topic declaring ``ArtType``, which a
    topic with no morph_spec/syntax_tags identity signal at all never
    does. The determiner type must be derived from the ANSWERS themselves
    for a topic that has no other reason (like testing Case) to accept
    several types."""
    topic = Topic(
        id="test_no_declared_identity_signal",
        name_de="Test",
        cefr="A2",
        description="No morph_spec/syntax_tags identity signal declared.",
    )
    answers = ["einen", "den", "diesen", "keinen"]

    reason = AnswerSetExpander.check_ambiguity(answers, topic)

    assert reason is not None
    assert "determiner type" in reason.lower()


def test_check_ambiguity_accepts_determiner_type_span_when_topic_tests_case() -> None:
    """The legitimate exception: 'Ich helfe ___ Kind' accepts eight
    determiners (dem, einem, keinem, meinem, deinem, seinem, ihrem,
    unserem) spanning Def/Ind/Neg/Poss, all Dat Neut Sing. For a
    ``kasus_*_formen`` topic the tested feature is CASE, not article type,
    so this must NOT be rejected even though the new Task 2 check now
    looks at topics without a declared ``ArtType`` too."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "kasus_dativ_formen")
    assert (topic.syntax_tags or {}).get("ArtType") is None, (
        "fixture assumption: kasus_dativ_formen must not declare ArtType"
    )
    answers = ["dem", "einem", "keinem", "meinem", "deinem", "seinem", "ihrem", "unserem"]

    assert AnswerSetExpander.check_ambiguity(answers, topic) is None


# ----------------------------------------------------------------------
# Task 3: auxiliary and copula forms need explicit Tense/Mood/Person/Number
# comparison via the tagger, not the coarse lemma-only facet.
# ----------------------------------------------------------------------


def test_check_ambiguity_rejects_imperative_2sg_vs_2pl_of_sein() -> None:
    """'___ (sein) bitte ruhig!' accepted 'Sei' (2sg) and 'Seid' (2pl) --
    both resolve to the bare lemma 'sein', which is not enough: they
    address a different NUMBER of people."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "imperativ")
    item = CandidateItem(
        topic_id="imperativ",
        type="cloze_cued",
        difficulty=1,
        prompt="___ bitte ruhig!",
        proposed_answer="Sei",
        cue="sein",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )

    reason = AnswerSetExpander.check_ambiguity(["Sei", "Seid"], topic, item=item)

    assert reason is not None
    assert "verb form" in reason.lower()


def test_check_ambiguity_rejects_perfekt_vs_plusquamperfekt_sein() -> None:
    """'Gestern ___ wir nach Berlin gefahren.' accepted 'sind' (Perfekt,
    Tense=Pres auxiliary) and 'waren' (Plusquamperfekt, Tense=Past
    auxiliary) -- two different tenses sharing the bare lemma 'sein'."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "perfekt_sein")
    item = CandidateItem(
        topic_id="perfekt_sein",
        type="cloze_free",
        difficulty=1,
        prompt="Gestern ___ wir nach Berlin gefahren.",
        proposed_answer="sind",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )

    reason = AnswerSetExpander.check_ambiguity(["sind", "waren"], topic, item=item)

    assert reason is not None
    assert "verb form" in reason.lower()


def test_check_ambiguity_rejects_present_vs_past_copula() -> None:
    """'Das Fenster ___ geöffnet.' accepted 'ist' (present) and 'war'
    (past) -- two different tenses sharing the bare lemma 'sein'."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "zustandspassiv_zeiten")
    item = CandidateItem(
        topic_id="zustandspassiv_zeiten",
        type="cloze_free",
        difficulty=1,
        prompt="Das Fenster ___ geöffnet.",
        proposed_answer="ist",
        distractors=[Distractor(text="x"), Distractor(text="y"), Distractor(text="z")],
    )

    reason = AnswerSetExpander.check_ambiguity(["ist", "war"], topic, item=item)

    assert reason is not None
    assert "verb form" in reason.lower()


# ----------------------------------------------------------------------
# Task 4: near-duplicate detection on the carrier (the phrase immediately
# surrounding the gap), not just whole-prompt exact/Jaccard matching.
# ----------------------------------------------------------------------


def test_verify_item_rejects_near_duplicate_carrier_with_different_tail() -> None:
    """'Ungeachtet ___ schlechten Wetters gingen die Kinder im Park
    spielen.' and '...unternahmen die Wanderer eine lange Tour.' share the
    entire fixed carrier phrase around the gap and then diverge completely
    -- whole-prompt Jaccard similarity on this pair is ~0.33, nowhere near
    ``ItemDeduplicator``'s 0.85 threshold, so exact-match/whole-sentence
    dedup misses it. The carrier-anchored check must catch it."""
    pipeline = VerificationPipeline(vocab_store=VocabularyStore({}))
    existing = BankItem(
        id="existing_1",
        topic_id="praepositionen_genitiv",
        type="cloze_free",
        difficulty=2,
        cefr="B1",
        prompt="Ungeachtet ___ schlechten Wetters gingen die Kinder im Park spielen.",
        accepted_answers=["des"],
    )
    candidate = CandidateItem(
        topic_id="praepositionen_genitiv",
        type="cloze_free",
        difficulty=2,
        prompt="Ungeachtet ___ schlechten Wetters unternahmen die Wanderer eine lange Tour.",
        proposed_answer="des",
        distractors=[Distractor(text="x"), Distractor(text="y")],
    )

    res = pipeline.verify_item(candidate, topic=None, spec=None, existing_bank_items=[existing])

    assert not res.passed
    assert res.layer_failed == 4
    assert res.error_type == "duplicate"
    assert "carrier" in (res.reason or "").lower()


def test_verify_item_accepts_items_that_merely_share_a_common_opening(
    pipeline: VerificationPipeline, sample_spec: TopicSpec, sample_topic: Topic
) -> None:
    """Two genuinely distinct items must not be rejected just because they
    open with the same two words ("Das Buch") when the GAP itself sits
    somewhere else entirely -- the carrier-anchored check is anchored on
    the gap's own neighbourhood specifically to avoid this false positive.
    ``candidate`` here is the exact fixture
    ``test_clean_items_pass_all_four_layers`` already proves passes every
    layer on its own, so a rejection can only come from the near-duplicate
    check this test exists to exonerate."""
    existing = BankItem(
        id="existing_2",
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=2,
        cefr="A2",
        prompt="Das Buch war so spannend, dass ich es in einer Nacht ___ gelesen habe.",
        accepted_answers=["ganz"],
    )
    candidate = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )

    res = pipeline.verify_item(
        candidate, spec=sample_spec, topic=sample_topic, existing_bank_items=[existing]
    )

    assert res.passed, f"unexpectedly rejected as a near-duplicate: {res.reason}"


# ----------------------------------------------------------------------
# Task 5: a pronoun answer set must not span pronoun classes (personal,
# demonstrative, interrogative).
# ----------------------------------------------------------------------


def test_check_pronoun_class_consistency_rejects_mixed_classes() -> None:
    """'___ ist mein guter Freund.' accepted 'Er' (personal), 'Das'/
    'Dieser'/'Der' (demonstrative) AND 'Wer' (interrogative) -- 'Wer'
    turns the declarative statement into a question, not an alternative
    correct answer."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "pronomen_personal_nom")
    answers = ["Er", "Das", "Dieser", "Der", "Wer"]

    reason = AnswerSetExpander.check_pronoun_class_consistency(answers, topic)

    assert reason is not None
    assert "pronoun class" in reason.lower()


def test_check_pronoun_class_consistency_accepts_one_class() -> None:
    """A set spanning only personal-pronoun forms (three different
    persons, but ONE class) must not be flagged."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "pronomen_personal_nom")

    assert AnswerSetExpander.check_pronoun_class_consistency(["er", "sie", "es"], topic) is None


def test_check_pronoun_class_consistency_ignores_non_pronoun_topics() -> None:
    """The check is scoped to pronoun topics (``syntax_tags['Pos'] ==
    'Pron'`` or ``morph_spec['PronType']`` set): 'der'/'die'/'das' are
    lexically identical to demonstrative-pronoun forms but are legitimately
    DEFINITE ARTICLES for an article-choice topic, which must not be
    misclassified as a pronoun-class span."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "artikel_bestimmt_nom")
    assert (topic.syntax_tags or {}).get("Pos") != "Pron"

    answers = ["der", "die", "das"]

    assert AnswerSetExpander.check_pronoun_class_consistency(answers, topic) is None


def test_check_pronoun_class_consistency_ignores_relative_pronoun_topics() -> None:
    """'der'/'die'/'das'/'den'/'dem' are the RELATIVE pronoun paradigm for
    a ``PronType: Rel`` topic -- the topic's own single legitimate target
    form, not a mix of personal/demonstrative/interrogative pronouns."""
    taxonomy = load_taxonomy()
    topic = next(t for t in taxonomy if t.id == "relativsatz_nom_akk")
    assert (topic.morph_spec or {}).get("PronType") == "Rel"

    answers = ["der", "die", "das", "den"]

    assert AnswerSetExpander.check_pronoun_class_consistency(answers, topic) is None
