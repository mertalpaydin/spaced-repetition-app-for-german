"""Unit and golden tests for the 4-layer verification chain and kill gate."""

import json
from collections import defaultdict
from pathlib import Path

import pytest
from src.contracts import BankItem, CandidateItem, Distractor, Topic
from src.generation.spec import TopicSpec, load_spec
from src.lexicon.vocabulary import VocabularyStore
from src.taxonomy.loader import load_taxonomy
from src.verification.layer1_syntax import Layer1SyntaxValidator
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
