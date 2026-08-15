"""Unit and golden tests for the 5-layer verification chain and kill gate."""

import json
from collections import defaultdict
from pathlib import Path

import pytest
from src.contracts import BankItem, CandidateItem, Distractor, Topic
from src.generation.spec import TopicSpec, load_spec
from src.lexicon.vocabulary import VocabularyStore
from src.taxonomy.loader import load_taxonomy
from src.verification.layer1_syntax import Layer1SyntaxValidator
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

        res = pipeline.verify_item(item, spec=sample_spec, existing_bank_items=existing_bank_items)

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


def _build_batch(n_clean: int, n_bad: int) -> list[CandidateItem]:
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
    )
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
    return [clean_item] * n_clean + [bad_item] * n_bad


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
    """Layer 1 rejects items with != 3 distractors."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1


def test_layer1_rejects_vocabulary_ceiling_violations(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """Layer 1 rejects items containing vocabulary beyond CEFR ceiling."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Die verfassungswidrige Demonstrationsverbotsverordnung liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1
    assert res.error_type == "vocabulary_ceiling_violation"


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
    cue ('(Katze)') leaking into the visible sentence, instead of the
    dedicated ``cue`` field, must be rejected -- this item was accepted by
    every layer, including the new model-backed layer 5, in the pilot re-run
    that motivated this fix."""
    item = CandidateItem(
        topic_id="artikel_unbestimmt_kein_nom",
        type="cloze_free",
        difficulty=2,
        prompt="Weil das Haus so alt ist, wohnt dort ___ (Katze) drin.",
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
        topic_id="verb_praesens_regelm",
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
            topic_id="verb_praesens_regelm",
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
    pipeline_with_vocab = VerificationPipeline(vocab_store=vocab_store)
    res = pipeline_with_vocab.verify_item(
        _item(prompt="Die verfassungswidrige Verordnung liegt auf ___ Tisch."), spec=sample_spec
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
    item is repaired (accepted, without the bad alternative), not rejected."""
    item = CandidateItem(
        topic_id="artikel_bestimmt_nom",
        type="cloze_free",
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


def test_layer5_ambiguity_check_is_a_backstop_when_fix2_cannot_filter(
    artikel_possessiv_topic: Topic,
) -> None:
    """fix 2 only filters per-candidate against ``proposed_answer``'s OWN
    resolved form; when that reference itself does not resolve (here:
    'Dieser', a demonstrative this taxonomy has no closed-class table for --
    but a legitimate Nominative possessive-slot answer per
    ``CASE_FORM_FALLBACK``, so it clears layer 2), fix 2 keeps every
    alternative unfiltered. fix 1's mutual-consistency check over the final
    set is what still catches two alternatives that disagree with EACH
    OTHER in that case."""
    item = CandidateItem(
        topic_id="artikel_possessiv_nom",
        type="cloze_free",
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

    assert not res.passed
    assert res.layer_failed == 5
    assert res.error_type == "ambiguity"


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
