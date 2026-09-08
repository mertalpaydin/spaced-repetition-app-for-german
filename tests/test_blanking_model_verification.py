"""Tests for src/generation/blanking/model_verification.py.

Unit tests never touch the network (CLAUDE.md 7): every test here either
calls the pure prompt/parsing helpers directly or feeds ``verify_items`` a
fake ``GeminiLlmClient``-shaped object whose ``generate_many`` is a plain
Python function, never a real client.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from src.contracts import MODEL_VERIFY, BankItem
from src.generation.blanking.model_verification import (
    _INSTRUCTION_DE_LIVE,
    _INSTRUCTION_EN_REFERENCE_ONLY,
    REASON_BATCH_FORBIDDEN,
    REASON_BUDGET_EXCEEDED,
    REASON_MALFORMED_RESPONSE,
    REASON_MISSING_API_KEY,
    REASON_NO_CLIENT,
    REASON_PAID_LANE_FORBIDDEN,
    REASON_SERVER_UNAVAILABLE,
    _chunk,
    _degrade_detail,
    _format_item_block,
    _parse_batch_response,
    build_batch_prompt,
    cache_coverage,
    describe_not_run_cause,
    verify_items,
)
from src.llm.cache import LlmCache
from src.llm.client import (
    BatchForbiddenError,
    BudgetExceeded,
    MissingApiKeyError,
    PaidLaneForbiddenError,
    ServerUnavailableError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bank_item(
    *,
    topic_id: str = "verb_praesens_regelm",
    prompt: str = "Ich ___ jeden Morgen Kaffee.",
    answer: str = "trinke",
    cue: str | None = None,
    rule_hint: str | None = "Präsens, regelmäßig",
    gloss_en: str | None = None,
) -> BankItem:
    return BankItem(
        id=f"blank_{topic_id}_{answer}",
        topic_id=topic_id,
        tag_id=topic_id,
        type="cloze_free" if cue is None else "cloze_cued",
        difficulty=1,
        cefr="A2",
        prompt=prompt,
        accepted_answers=[answer],
        cue=cue,
        rule_hint=rule_hint,
        gloss_en=gloss_en,
    )


class _FakeVerifyLlmClient:
    """Records every ``generate_many`` call and returns a canned response per
    call, standing in for ``GeminiLlmClient`` at the seam ``verify_items``
    calls (``.generate_many(prompts, model=..., purpose=..., use_cache=...)``).

    ``use_cache`` is recorded, not merely tolerated: it is the one argument
    that decides whether a repeated verification pass asks the model again or
    silently replays a cached verdict (see this module's own "Repeated
    passes" docstring section and ``scripts/step7_corpus_pilot.py``'s
    ``run_verification_passes``), so a fake that swallowed it would let that
    whole feature regress to a no-op without a test noticing."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None) -> None:
        self._responses = responses
        self._error = error
        self.calls: list[dict[str, object]] = []

    def generate_many(
        self,
        prompts: list[str],
        model: str,
        purpose: str,
        use_cache: bool = True,
        cache_namespace: str | None = None,
    ) -> list[str]:
        self.calls.append(
            {"prompts": list(prompts), "model": model, "purpose": purpose, "use_cache": use_cache}
        )
        if self._error is not None:
            raise self._error
        assert self._responses is not None
        assert len(self._responses) == len(prompts)
        return list(self._responses)


def _verdict_response(
    verdicts: list[tuple[bool, str | None]],
    *,
    woerter_echt: bool | list[bool] = True,
    hinweis_korrekt: bool | list[bool] = True,
) -> str:
    """Build a canned ``generate_many`` response JSON. ``woerter_echt`` (the
    third question's own answer) and ``hinweis_korrekt`` (the fourth
    question's own answer, TODO 3.1) each default to ``True`` for every
    entry so existing callers that only care about the ``(valid, reason)``
    pair do not need to know about either field at all; pass a per-entry
    list to exercise either question's own effect on the combined verdict."""
    words_real_per_entry = (
        woerter_echt if isinstance(woerter_echt, list) else [woerter_echt] * len(verdicts)
    )
    cue_correct_per_entry = (
        hinweis_korrekt if isinstance(hinweis_korrekt, list) else [hinweis_korrekt] * len(verdicts)
    )
    assert len(words_real_per_entry) == len(verdicts)
    assert len(cue_correct_per_entry) == len(verdicts)
    return json.dumps(
        {
            "verdicts": [
                {
                    "index": i + 1,
                    "valid": valid,
                    "woerter_echt": we,
                    "hinweis_korrekt": hk,
                    "reason": reason,
                }
                for i, ((valid, reason), we, hk) in enumerate(
                    zip(verdicts, words_real_per_entry, cue_correct_per_entry, strict=True)
                )
            ]
        }
    )


# ---------------------------------------------------------------------------
# The two-language instruction, and topic-leak avoidance.
# ---------------------------------------------------------------------------

_FORBIDDEN_GRAMMAR_WORDS = (
    "kasus",
    "artikel",
    "dativ",
    "akkusativ",
    "nominativ",
    "genitiv",
    "deklination",
    "perfekt",
    "präteritum",
    "plusquamperfekt",
    "futur",
    "konjunktiv",
)


def _contains_forbidden_word(text: str) -> str | None:
    lowered = text.lower()
    for forbidden in _FORBIDDEN_GRAMMAR_WORDS:
        if re.search(rf"\b{re.escape(forbidden)}\b", lowered):
            return forbidden
    return None


def test_instruction_texts_are_nonempty_and_distinct() -> None:
    assert _INSTRUCTION_DE_LIVE.strip()
    assert _INSTRUCTION_EN_REFERENCE_ONLY.strip()
    assert _INSTRUCTION_DE_LIVE != _INSTRUCTION_EN_REFERENCE_ONLY


def test_instructions_never_name_a_grammar_topic() -> None:
    assert _contains_forbidden_word(_INSTRUCTION_DE_LIVE) is None
    assert _contains_forbidden_word(_INSTRUCTION_EN_REFERENCE_ONLY) is None


def test_build_batch_prompt_uses_the_german_instruction_not_the_english_one() -> None:
    prompt = build_batch_prompt([_bank_item()])
    assert _INSTRUCTION_DE_LIVE in prompt
    assert _INSTRUCTION_EN_REFERENCE_ONLY not in prompt


def test_instruction_de_live_asks_four_questions_and_carries_woerter_echt() -> None:
    """docs/audits/cycle-08-report.md: the third question -- every word in
    the completed sentence must be a real German word -- and TODO 3.1's own
    fourth question -- is the cue the answer's correct citation form -- are
    both added to the SAME batched call, not a new one; the response schema
    example must carry both ``woerter_echt`` and ``hinweis_korrekt`` so
    ``_parse_batch_response`` can require them."""
    assert "vier Fragen" in _INSTRUCTION_DE_LIVE
    assert "3." in _INSTRUCTION_DE_LIVE
    assert "4." in _INSTRUCTION_DE_LIVE
    assert '"woerter_echt"' in _INSTRUCTION_DE_LIVE
    assert '"hinweis_korrekt"' in _INSTRUCTION_DE_LIVE


def test_instruction_de_live_asks_about_the_cue() -> None:
    """TODO 3.1: none of the first three questions ever mentions the
    parenthesised hint at all -- the fourth question must, explicitly, and
    must be skippable ("automatisch mit Ja") for an item with no cue."""
    assert "Hinweis" in _INSTRUCTION_DE_LIVE
    assert "Zitierform" in _INSTRUCTION_DE_LIVE
    assert "kein Hinweis" in _INSTRUCTION_DE_LIVE


def test_instruction_de_live_explains_the_invariant_determiner_citation_form() -> None:
    """TODO 2.1-2.3's redesign (read before writing this question, per this
    task's own brief): a determiner cue is always its own word-series'
    invariant form, never agreed to the head noun's own grammar, and it may
    legitimately equal the answer -- the prompt must say all of this (using
    concrete word forms, not grammar terminology CLAUDE.md rule 2 forbids
    handing the model at all -- see ``test_instructions_never_name_a_
    grammar_topic``) or the model will reject a whole class of now-correct
    items."""
    for family_word in ("der", "ein", "kein", "mein"):
        assert family_word in _INSTRUCTION_DE_LIVE
    assert "immer richtig" in _INSTRUCTION_DE_LIVE


def test_instruction_de_live_gives_a_worked_cue_equals_answer_example() -> None:
    """TODO 2.2's owner-approved exemption, given to the model as a concrete
    worked example so it does not flag the whole class: 'Fahrrad' is neuter
    and the accusative neuter of 'ein' happens to also be 'ein', so a cue
    identical to the answer is correct here, not a leak."""
    assert "Fahrrad" in _INSTRUCTION_DE_LIVE
    assert "identisch" in _INSTRUCTION_DE_LIVE


def test_instruction_de_live_still_rejects_the_wrong_determiner_family() -> None:
    """What question 4 must still catch post-redesign: a determiner cue
    naming the wrong invariant word-series entirely (docs/audits/
    cycle-09-report.md's own 'die'/'das' misses, now framed as a
    wrong-series error rather than a wrong-gender one, since the cue no
    longer varies by gender at all)."""
    assert "'die'" in _INSTRUCTION_DE_LIVE
    assert "ANDEREN" in _INSTRUCTION_DE_LIVE


def test_instruction_de_live_gives_tennisschluessel_as_the_negative_example() -> None:
    """The exact defect from the last pilot: both halves of
    ``Tennisschlüssel`` are real German words, so a compound splitter or a
    plain dictionary check cannot reject it -- only a semantic judgment
    can, which is exactly why this is the model's own worked example."""
    assert "Tennisschlüssel" in _INSTRUCTION_DE_LIVE
    assert "Tennisschläger" in _INSTRUCTION_DE_LIVE


def test_instruction_de_live_gives_uncommon_compounds_as_positive_examples() -> None:
    """The prompt must tell the model NOT to reject a word merely for
    being uncommon -- these three are real, freely-formed compounds that
    were wrongly at risk of the same treatment as ``Tennisschlüssel``."""
    for compound in ("Radweg", "Altkleidersammlung", "Einweihungsfest"):
        assert compound in _INSTRUCTION_DE_LIVE


def test_instruction_de_live_tells_the_model_stilted_alternatives_do_not_count() -> None:
    """The self-consistency fix for question 2: a rare/stilted alternative
    (the model's own worked example, ``welcher`` as a relative pronoun)
    must not by itself disqualify the proposed answer, while a genuinely
    equally idiomatic alternative still must."""
    assert "welcher" in _INSTRUCTION_DE_LIVE
    assert "gestelzt" in _INSTRUCTION_DE_LIVE


def test_build_batch_prompt_never_leaks_the_topic_or_rule_hint() -> None:
    """The model must never see ``topic_id`` or ``rule_hint`` -- CLAUDE.md
    rule 2, applied to a model prompt instead of a learner-facing one."""
    item = _bank_item(
        topic_id="kasus_dativ_formen",
        rule_hint="Dativ nach 'auf' nur bei Ortsangabe im statischen Sinn",
    )
    prompt = build_batch_prompt([item])
    assert "kasus_dativ_formen" not in prompt
    assert item.rule_hint is not None
    assert item.rule_hint not in prompt
    assert _contains_forbidden_word(prompt.replace(item.prompt, "")) is None


# ---------------------------------------------------------------------------
# _format_item_block
# ---------------------------------------------------------------------------


def test_format_item_block_includes_cue_when_present() -> None:
    item = _bank_item(prompt="___ er noch arbeiten?", answer="muss", cue="müssen")
    block = _format_item_block(1, item)
    assert "Aufgabe 1:" in block
    assert "Lücke: ___ er noch arbeiten?" in block
    assert "Hinweis: (müssen)" in block
    assert "Vorgeschlagene Antwort: muss" in block


def test_format_item_block_marks_absent_cue_explicitly() -> None:
    item = _bank_item(cue=None)
    block = _format_item_block(3, item)
    assert "Aufgabe 3:" in block
    assert "Hinweis: (kein Hinweis)" in block


def test_format_item_block_joins_multiple_accepted_answers() -> None:
    item = _bank_item().model_copy(update={"accepted_answers": ["dem", "einem"]})
    block = _format_item_block(1, item)
    assert "Vorgeschlagene Antwort: dem / einem" in block


def test_build_batch_prompt_numbers_items_sequentially() -> None:
    items = [_bank_item(prompt=f"Satz {i} ___.", answer=f"antwort{i}") for i in range(3)]
    prompt = build_batch_prompt(items)
    assert "Aufgabe 1:" in prompt
    assert "Aufgabe 2:" in prompt
    assert "Aufgabe 3:" in prompt
    assert prompt.index("Aufgabe 1:") < prompt.index("Aufgabe 2:") < prompt.index("Aufgabe 3:")


# ---------------------------------------------------------------------------
# _chunk
# ---------------------------------------------------------------------------


def test_chunk_splits_into_groups_of_given_size() -> None:
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(45)]
    chunks = _chunk(items, 20)
    assert [len(c) for c in chunks] == [20, 20, 5]
    assert sum(chunks, []) == items


def test_chunk_of_empty_list_is_empty() -> None:
    assert _chunk([], 20) == []


def test_chunk_nonpositive_size_is_one_chunk() -> None:
    items = [_bank_item()]
    assert _chunk(items, 0) == [items]
    assert _chunk(items, -5) == [items]


# ---------------------------------------------------------------------------
# _parse_batch_response
# ---------------------------------------------------------------------------


def test_parse_batch_response_happy_path() -> None:
    text = _verdict_response([(True, None), (False, "Grund")])
    parsed = _parse_batch_response(text, 2)
    assert parsed == [(True, None), (False, "Grund")]


def test_parse_batch_response_strips_markdown_code_fence() -> None:
    inner = _verdict_response([(True, None)])
    fenced = f"```json\n{inner}\n```"
    assert _parse_batch_response(fenced, 1) == [(True, None)]


def test_parse_batch_response_reorders_by_index() -> None:
    text = json.dumps(
        {
            "verdicts": [
                {
                    "index": 2,
                    "valid": False,
                    "woerter_echt": True,
                    "hinweis_korrekt": True,
                    "reason": "zweitens",
                },
                {
                    "index": 1,
                    "valid": True,
                    "woerter_echt": True,
                    "hinweis_korrekt": True,
                    "reason": None,
                },
            ]
        }
    )
    assert _parse_batch_response(text, 2) == [(True, None), (False, "zweitens")]


def test_parse_batch_response_blank_reason_string_becomes_none() -> None:
    text = json.dumps(
        {
            "verdicts": [
                {
                    "index": 1,
                    "valid": True,
                    "woerter_echt": True,
                    "hinweis_korrekt": True,
                    "reason": "   ",
                }
            ]
        }
    )
    assert _parse_batch_response(text, 1) == [(True, None)]


# ---------------------------------------------------------------------------
# _parse_batch_response: the third question (``woerter_echt``) -- docs/
# audits/cycle-08-report.md's ``Tennisschlüssel`` defect.
# ---------------------------------------------------------------------------


def test_parse_batch_response_woerter_echt_false_overrides_valid_true() -> None:
    """A response can be internally inconsistent -- ``valid: true`` but
    ``woerter_echt: false`` -- and the combined verdict must still reject:
    the parser never trusts the model's own aggregate ``valid`` bit alone
    once a specific sub-answer contradicts it."""
    text = json.dumps(
        {
            "verdicts": [
                {
                    "index": 1,
                    "valid": True,
                    "woerter_echt": False,
                    "hinweis_korrekt": True,
                    "reason": "'Tennisschlüssel' ist kein Wort.",
                }
            ]
        }
    )
    assert _parse_batch_response(text, 1) == [(False, "'Tennisschlüssel' ist kein Wort.")]


def test_parse_batch_response_woerter_echt_true_and_valid_true_is_valid() -> None:
    text = _verdict_response([(True, None)], woerter_echt=True)
    assert _parse_batch_response(text, 1) == [(True, None)]


# ---------------------------------------------------------------------------
# _parse_batch_response: the fourth question (``hinweis_korrekt``) -- TODO
# 3.1, whether the cue is the answer's correct citation form.
# ---------------------------------------------------------------------------


def test_parse_batch_response_hinweis_korrekt_false_overrides_valid_true() -> None:
    """Same internal-consistency guard as ``woerter_echt``, for the fourth
    question: ``valid: true`` but ``hinweis_korrekt: false`` must still
    reject, never trusted on the aggregate bit alone."""
    text = json.dumps(
        {
            "verdicts": [
                {
                    "index": 1,
                    "valid": True,
                    "woerter_echt": True,
                    "hinweis_korrekt": False,
                    "reason": "Der Hinweis 'die' passt nicht zur Wortfamilie 'der'.",
                }
            ]
        }
    )
    assert _parse_batch_response(text, 1) == [
        (False, "Der Hinweis 'die' passt nicht zur Wortfamilie 'der'.")
    ]


def test_parse_batch_response_hinweis_korrekt_true_and_valid_true_is_valid() -> None:
    text = _verdict_response([(True, None)], hinweis_korrekt=True)
    assert _parse_batch_response(text, 1) == [(True, None)]


def test_parse_batch_response_missing_hinweis_korrekt_is_malformed() -> None:
    """A response missing the fourth question's own field entirely
    degrades the whole batch to ``not_run`` -- never silently trusted as if
    the cue had been judged correct, mirroring ``woerter_echt``'s own
    missing-field test."""
    text = json.dumps(
        {"verdicts": [{"index": 1, "valid": True, "woerter_echt": True, "reason": None}]}
    )
    assert _parse_batch_response(text, 1) is None


def test_parse_batch_response_non_bool_hinweis_korrekt_is_malformed() -> None:
    text = json.dumps(
        {"verdicts": [{"index": 1, "valid": True, "woerter_echt": True, "hinweis_korrekt": "ja"}]}
    )
    assert _parse_batch_response(text, 1) is None


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        "[]",
        json.dumps({"no_verdicts_key": []}),
        json.dumps(
            {"verdicts": [{"index": 1, "valid": True, "woerter_echt": True}]}
        ),  # count mismatch (expects 2)
        json.dumps(
            {
                "verdicts": [
                    {"index": 1, "valid": True, "woerter_echt": True},
                    {"index": 1, "valid": False, "woerter_echt": True},
                ]
            }
        ),  # duplicate index
        json.dumps(
            {
                "verdicts": [
                    {"index": 1, "valid": True, "woerter_echt": True},
                    {"index": 3, "valid": False, "woerter_echt": True},
                ]
            }
        ),  # gap: no index 2
        json.dumps(
            {
                "verdicts": [
                    {"index": True, "valid": True, "woerter_echt": True},
                    {"index": 2, "valid": False, "woerter_echt": True},
                ]
            }
        ),  # bool used as index
        json.dumps(
            {
                "verdicts": [
                    {"index": 1, "valid": "yes", "woerter_echt": True},
                    {"index": 2, "valid": False, "woerter_echt": True},
                ]
            }
        ),  # non-bool valid
        json.dumps(
            {
                "verdicts": [
                    {"index": 1, "valid": True},  # missing woerter_echt entirely
                    {"index": 2, "valid": False, "woerter_echt": True},
                ]
            }
        ),
        json.dumps(
            {
                "verdicts": [
                    {"index": 1, "valid": True, "woerter_echt": "yes"},  # non-bool woerter_echt
                    {"index": 2, "valid": False, "woerter_echt": True},
                ]
            }
        ),
        json.dumps(
            {"verdicts": ["not a dict", {"index": 2, "valid": False, "woerter_echt": True}]}
        ),
        json.dumps(["not", "a", "dict", "at", "top", "level"]),
    ],
)
def test_parse_batch_response_malformed_returns_none(text: str) -> None:
    assert _parse_batch_response(text, 2) is None


# ---------------------------------------------------------------------------
# verify_items: no client / empty input
# ---------------------------------------------------------------------------


def test_verify_items_empty_list_no_client() -> None:
    report = verify_items([], None)
    assert report.attempted is False
    assert report.verdicts == []
    assert report.verified_count == report.rejected_count == report.not_run_count == 0


def test_verify_items_empty_list_with_client_makes_no_calls() -> None:
    fake = _FakeVerifyLlmClient(responses=[])
    report = verify_items([], fake)  # type: ignore[arg-type]
    assert report.verdicts == []
    assert fake.calls == []


def test_verify_items_no_client_marks_everything_not_run_never_verified() -> None:
    """The core degrade-honestly requirement: no API key configured must
    never be reported as verification having happened."""
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(5)]
    report = verify_items(items, None)

    assert report.attempted is False
    assert len(report.verdicts) == 5
    assert report.verified_count == 0
    assert report.rejected_count == 0
    assert report.not_run_count == 5
    assert all(v.outcome == "not_run" for v in report.verdicts)
    assert all(v.reason == REASON_NO_CLIENT for v in report.verdicts)
    assert report.not_run_reasons == {REASON_NO_CLIENT: 5}


# ---------------------------------------------------------------------------
# verify_items: batching
# ---------------------------------------------------------------------------


def test_verify_items_batches_at_roughly_twenty_per_call() -> None:
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(45)]
    # One canned "all valid" response per expected batch (20, 20, 5).
    responses = [
        _verdict_response([(True, None)] * 20),
        _verdict_response([(True, None)] * 20),
        _verdict_response([(True, None)] * 5),
    ]
    fake = _FakeVerifyLlmClient(responses=responses)

    report = verify_items(items, fake, batch_size=20)  # type: ignore[arg-type]

    assert len(fake.calls) == 1  # one generate_many call carrying all 3 prompts
    call = fake.calls[0]
    prompts = call["prompts"]
    assert isinstance(prompts, list)
    assert len(prompts) == 3
    assert call["model"] == "gemini-3.7-flash"
    assert call["purpose"] == "item_verification"
    assert report.verified_count == 45
    assert report.not_run_count == 0


# ---------------------------------------------------------------------------
# verify_items: the cache switch (TODO.md 2.3)
#
# ``src/llm/cache.py`` is content-addressed on a hash of the full request, so
# a second verification pass over identical items builds a byte-identical
# prompt and would be answered from the cache: same verdict, zero cost, a
# ``lane="cache"`` cost_log row, and a repeated-pass feature that looks like
# it worked while measuring nothing at all. These two tests pin the switch
# that prevents it at the ``verify_items`` seam; the step7 tests pin that the
# repeated-pass runner actually flips it per pass.
# ---------------------------------------------------------------------------


def test_verify_items_uses_the_cache_by_default() -> None:
    """The default must stay ``True``: a rerun after a crash must not re-buy
    what already landed, which is what CLAUDE.md section 9 asks the local
    cache for in the first place."""
    items = [_bank_item()]
    fake = _FakeVerifyLlmClient(responses=[_verdict_response([(True, None)])])

    verify_items(items, fake)  # type: ignore[arg-type]

    assert fake.calls[0]["use_cache"] is True


def test_verify_items_bypasses_the_cache_when_asked_to() -> None:
    """``use_cache=False`` must reach ``generate_many``, not be swallowed by
    the default argument on the way. Without this the whole
    ``--verification-passes`` feature is one default away from silently
    replaying pass 1's verdict on every later pass."""
    items = [_bank_item()]
    fake = _FakeVerifyLlmClient(responses=[_verdict_response([(True, None)])])

    verify_items(items, fake, use_cache=False)  # type: ignore[arg-type]

    assert fake.calls[0]["use_cache"] is False


def test_verify_items_verified_and_rejected_mix() -> None:
    items = [
        _bank_item(prompt="Ich ___ Kaffee.", answer="trinke"),
        _bank_item(prompt="___ er noch arbeiten?", answer="muss", cue="müssen"),
    ]
    fake = _FakeVerifyLlmClient(
        responses=[_verdict_response([(True, None), (False, "Auch 'sollte' passt hier.")])]
    )

    report = verify_items(items, fake, batch_size=20)  # type: ignore[arg-type]

    assert len(report.verdicts) == 2
    assert report.verdicts[0].outcome == "verified"
    assert report.verdicts[0].reason is None
    assert report.verdicts[1].outcome == "rejected"
    assert report.verdicts[1].reason == "Auch 'sollte' passt hier."

    assert report.verified_count == 1
    assert report.rejected_count == 1
    assert report.not_run_count == 0

    assert len(report.rejections) == 1
    rejection = report.rejections[0]
    assert rejection.prompt == "___ er noch arbeiten?"
    assert rejection.accepted_answers == ("muss",)
    assert rejection.reason == "Auch 'sollte' passt hier."
    assert report.rejected_reasons == {"Auch 'sollte' passt hier.": 1}


def test_verify_items_missing_reason_on_rejection_gets_a_placeholder() -> None:
    items = [_bank_item()]
    fake = _FakeVerifyLlmClient(responses=[_verdict_response([(False, None)])])
    report = verify_items(items, fake)  # type: ignore[arg-type]
    assert report.verdicts[0].outcome == "rejected"
    assert report.verdicts[0].reason
    assert report.rejections[0].reason == report.verdicts[0].reason


def test_verify_items_malformed_batch_response_is_not_run_not_verified() -> None:
    """A malformed response must degrade to ``not_run``, never silently to
    ``verified`` -- unlike the older, lower-stakes semantic-expansion layer
    this module deliberately does not mirror that fail-open behaviour."""
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(25)]
    responses = [
        "this is not valid json",
        _verdict_response([(True, None)] * 5),
    ]
    fake = _FakeVerifyLlmClient(responses=responses)

    report = verify_items(items, fake, batch_size=20)  # type: ignore[arg-type]

    assert report.attempted is True
    assert report.not_run_count == 20
    assert report.verified_count == 5
    assert all(v.outcome == "not_run" for v in report.verdicts[:20])
    assert all(v.reason == REASON_MALFORMED_RESPONSE for v in report.verdicts[:20])
    assert all(v.outcome == "verified" for v in report.verdicts[20:])
    assert report.not_run_reasons == {REASON_MALFORMED_RESPONSE: 20}


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (BudgetExceeded("over ceiling"), REASON_BUDGET_EXCEEDED),
        (ServerUnavailableError("503"), REASON_SERVER_UNAVAILABLE),
        (PaidLaneForbiddenError("paid forbidden"), REASON_PAID_LANE_FORBIDDEN),
        (BatchForbiddenError("batch forbidden"), REASON_BATCH_FORBIDDEN),
        (MissingApiKeyError("no key"), REASON_MISSING_API_KEY),
    ],
)
def test_verify_items_degrades_whole_run_on_transport_errors(
    error: Exception, expected_reason: str
) -> None:
    """Every one of these is infrastructure trouble, not a verdict on any
    item's German -- CLAUDE.md 9: 'Callers handle it by degrading, never by
    retrying.' The whole run must degrade to not_run, never partially to
    verified."""
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(3)]
    fake = _FakeVerifyLlmClient(error=error)

    report = verify_items(items, fake)  # type: ignore[arg-type]

    assert report.attempted is True
    assert report.verified_count == 0
    assert report.rejected_count == 0
    assert report.not_run_count == 3
    assert report.not_run_reasons == {expected_reason: 3}


def test_verification_report_counts_always_sum_to_item_count() -> None:
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(7)]
    fake = _FakeVerifyLlmClient(
        responses=[_verdict_response([(True, None)] * 5 + [(False, "x")] * 2)]
    )
    report = verify_items(items, fake, batch_size=20)  # type: ignore[arg-type]
    total = report.verified_count + report.rejected_count + report.not_run_count
    assert total == len(items) == len(report.verdicts)


# ---------------------------------------------------------------------------
# The English gloss reaches the verifier (TODO.md 5.1, "then, and only then,
# loosen the verifier").
#
# Measured need, from the owner's first gloss-enabled pilot: 44 of the 105
# model rejections, 42%, said the answer was not unique because another TENSE
# would fit the sentence equally well. Examples, verbatim from that run:
#
#   "Neben 'Könnten' ist 'Können' ... eine ebenso richtige Loesung."
#   "Ohne zeitlichen Kontext ist neben dem Praesens 'kann' auch das
#    Praeteritum 'konnte' eine ebenso richtige Loesung."
#
# The learner sees the English translation on every exercise, so in each of
# those cases the tense was never actually open. The verifier was judging a
# harder task than the one being shipped, purely because the prompt did not
# carry the field.
# ---------------------------------------------------------------------------


def test_format_item_block_includes_the_english_gloss_when_present() -> None:
    prompt = build_batch_prompt([_bank_item(gloss_en="I drink coffee every morning.")])
    assert "Englische Übersetzung: I drink coffee every morning." in prompt


def test_format_item_block_says_so_explicitly_when_there_is_no_gloss() -> None:
    """States the absence rather than omitting the line. A missing line
    would leave the model to guess whether a translation existed and was
    withheld, and the instruction tells it to judge uniqueness without one
    in exactly this case."""
    prompt = build_batch_prompt([_bank_item(gloss_en=None)])
    assert "Englische Übersetzung: (keine)" in prompt


def test_format_item_block_treats_a_whitespace_only_gloss_as_absent() -> None:
    prompt = build_batch_prompt([_bank_item(gloss_en="   ")])
    assert "Englische Übersetzung: (keine)" in prompt


def test_build_batch_prompt_keeps_each_items_gloss_with_its_own_task() -> None:
    """A batch is judged item by item, so a gloss landing on the wrong task
    would attach correct-looking English to the wrong German and quietly
    corrupt every uniqueness judgment after it."""
    prompt = build_batch_prompt(
        [
            _bank_item(answer="trinke", gloss_en="I drink coffee every morning."),
            _bank_item(
                answer="gewinnen",
                prompt="Ich denke, dass Tom ___ wird.",
                gloss_en="I believe Tom is going to take first place.",
            ),
        ]
    )
    first = prompt.index("Aufgabe 1:")
    second = prompt.index("Aufgabe 2:")
    assert first < prompt.index("I drink coffee every morning.") < second
    assert second < prompt.index("I believe Tom is going to take first place.")


def test_instruction_de_live_bounds_what_the_translation_may_settle() -> None:
    """The relaxation has to be bounded or it manufactures the defect class
    cycles 11 and 12 closed. A translation settles tense, person, number and
    definiteness; it says nothing about German case, gender, adjective
    endings, reflexives or preposition government, and the instruction must
    say so in as many words rather than leaving the model to infer it."""
    assert "Englische Übersetzung" in _INSTRUCTION_DE_LIVE
    for bounded in ("Zeitform", "Person", "Zahl", "Bestimmtheit"):
        assert bounded in _INSTRUCTION_DE_LIVE
    for excluded in ("Fall", "Genus", "Adjektivendungen", "Reflexivpronomen", "Präposition"):
        assert excluded in _INSTRUCTION_DE_LIVE


def test_instruction_de_live_still_names_no_grammar_topic_after_the_gloss_change() -> None:
    """Rule 2 does not relax because a new paragraph was useful. The first
    draft of that paragraph explained the example by saying the translation
    shows the 'Futur', which this guard caught; it now describes the timing
    without naming the tense."""
    assert _contains_forbidden_word(_INSTRUCTION_DE_LIVE) is None


# ---------------------------------------------------------------------------
# not_run_detail: the degrading exception's own message reaches the report
# ---------------------------------------------------------------------------


_RPD_MESSAGE = (
    "The free lane is closed: the free-tier DAILY allowance (RPD) is exhausted, "
    "and the paid batch lane is forbidden in this run. Re-running does not help "
    "until the quota resets. The free lane reopens at 2026-08-29 09:00 CEST "
    "local (2026-08-29T07:00:00+00:00), the next Pacific midnight."
)


def test_verify_items_keeps_the_degrading_exceptions_own_message() -> None:
    """The reason slug says which of five degrade paths was taken; only the
    exception's own message says what to do about it. A ``--free-lane-only``
    run refused by the daily quota carries
    ``GeminiLlmClient._paid_lane_forbidden_message``, which names RPD and the
    reset time in local time, and dropping it left every reporting script
    guessing at a cause the code had already determined."""
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(3)]
    fake = _FakeVerifyLlmClient(error=PaidLaneForbiddenError(_RPD_MESSAGE))

    report = verify_items(items, fake)  # type: ignore[arg-type]

    assert report.not_run_count == 3
    assert report.not_run_reasons == {REASON_PAID_LANE_FORBIDDEN: 3}
    assert report.not_run_detail is not None
    assert "PaidLaneForbiddenError" in report.not_run_detail
    assert "DAILY allowance (RPD)" in report.not_run_detail
    assert "2026-08-29 09:00 CEST local" in report.not_run_detail


def test_verify_items_reports_no_detail_when_a_batch_response_is_malformed() -> None:
    """A malformed response is not an exception, so there is no message to
    quote. ``None`` must read as 'no detail available', never as an invented
    one."""
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(3)]
    fake = _FakeVerifyLlmClient(responses=["not json at all"])

    report = verify_items(items, fake, batch_size=20)  # type: ignore[arg-type]

    assert report.not_run_reasons == {REASON_MALFORMED_RESPONSE: 3}
    assert report.not_run_detail is None


def test_verify_items_reports_no_detail_on_a_fully_judged_run() -> None:
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(3)]
    fake = _FakeVerifyLlmClient(responses=[_verdict_response([(True, None)] * 3)])

    report = verify_items(items, fake, batch_size=20)  # type: ignore[arg-type]

    assert report.not_run_count == 0
    assert report.not_run_detail is None


def test_degrade_detail_falls_back_to_the_type_name_for_a_silent_exception() -> None:
    """An exception raised with no message must not produce a dangling
    'ClassName:' line in an operator's terminal."""
    assert _degrade_detail(BudgetExceeded()) == "BudgetExceeded"
    assert _degrade_detail(BudgetExceeded("over ceiling")) == "BudgetExceeded: over ceiling"


# ---------------------------------------------------------------------------
# describe_not_run_cause
# ---------------------------------------------------------------------------


def test_describe_not_run_cause_names_the_slug_and_the_exception_message() -> None:
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(3)]
    report = verify_items(  # type: ignore[arg-type]
        items, _FakeVerifyLlmClient(error=PaidLaneForbiddenError(_RPD_MESSAGE))
    )

    lines = describe_not_run_cause([report])

    assert lines[0] == f"cause: {REASON_PAID_LANE_FORBIDDEN} (3 item(s))"
    assert lines[1].startswith("detail: PaidLaneForbiddenError: ")
    assert "DAILY allowance (RPD)" in lines[1]


def test_describe_not_run_cause_merges_two_reports_and_deduplicates_one_detail() -> None:
    """The eval scripts run two fixtures through two separate calls, and one
    quota refusal degrades both. The operator must see one cause line with the
    combined count and one detail, not the same paragraph twice."""
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(3)]
    error = PaidLaneForbiddenError(_RPD_MESSAGE)
    reports = [
        verify_items(items, _FakeVerifyLlmClient(error=error)),  # type: ignore[arg-type]
        verify_items(items[:2], _FakeVerifyLlmClient(error=error)),  # type: ignore[arg-type]
    ]

    lines = describe_not_run_cause(reports)

    assert lines == [
        f"cause: {REASON_PAID_LANE_FORBIDDEN} (5 item(s))",
        f"detail: PaidLaneForbiddenError: {_RPD_MESSAGE}",
    ]


def test_describe_not_run_cause_is_empty_when_every_item_was_judged() -> None:
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(3)]
    report = verify_items(  # type: ignore[arg-type]
        items,
        _FakeVerifyLlmClient(responses=[_verdict_response([(True, None)] * 3)]),
        batch_size=20,
    )

    assert describe_not_run_cause([report]) == []


# ---------------------------------------------------------------------------
# cache_coverage: progress across days, read from the cache
# ---------------------------------------------------------------------------


class _CachedClient:
    """The only part of ``GeminiLlmClient`` ``cache_coverage`` touches."""

    def __init__(self, cache: LlmCache) -> None:
        self.cache = cache


def _seed_cache(cache: LlmCache, items: list[BankItem], *, batch_size: int, batches: int) -> None:
    """Write the response for the first ``batches`` batches, exactly as
    ``generate_many`` would after those batches landed."""
    for batch in _chunk(items, batch_size)[:batches]:
        cache.set(
            model=MODEL_VERIFY,
            prompt=build_batch_prompt(batch),
            response=_verdict_response([(True, None)] * len(batch)),
        )


def test_cache_coverage_counts_the_items_of_every_cached_batch(tmp_path: Path) -> None:
    """The unit of a cached request is a batch, so a cached batch contributes
    all of its items and a missing one contributes none."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(13)]
    _seed_cache(cache, items, batch_size=5, batches=2)

    coverage = cache_coverage(items, _CachedClient(cache), batch_size=5)  # type: ignore[arg-type]

    assert coverage.total_items == 13
    assert coverage.cached_items == 10
    assert coverage.missing_items == 3
    assert coverage.complete is False


def test_cache_coverage_is_complete_once_every_batch_has_landed(tmp_path: Path) -> None:
    cache = LlmCache(cache_dir=tmp_path / "llm")
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(13)]
    _seed_cache(cache, items, batch_size=5, batches=3)

    coverage = cache_coverage(items, _CachedClient(cache), batch_size=5)  # type: ignore[arg-type]

    assert coverage.cached_items == 13
    assert coverage.complete is True


def test_cache_coverage_resets_when_the_batch_size_changes(tmp_path: Path) -> None:
    """The cache is content-addressed on the full request, and a different
    batch size builds different prompts. Callers reporting this number to an
    operator have to say so, or a day's progress looks like it evaporated."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(13)]
    _seed_cache(cache, items, batch_size=5, batches=3)

    coverage = cache_coverage(items, _CachedClient(cache), batch_size=4)  # type: ignore[arg-type]

    assert coverage.cached_items == 0


def test_cache_coverage_reports_zero_rather_than_raising_without_a_cache() -> None:
    """An unknown coverage must read as 'nothing banked yet'. A progress
    report is the last place that should crash."""
    items = [_bank_item(prompt=f"S{i} ___.", answer=str(i)) for i in range(3)]

    assert cache_coverage(items, None).cached_items == 0
    assert cache_coverage(items, None).total_items == 3
    assert cache_coverage([], None).total_items == 0
    assert cache_coverage([], None).complete is False
