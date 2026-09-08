"""The opt-in context stage: approval before any call, strict parsing,
deterministic rejection, partial results kept."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.phrases import contexts

UNIT = PhraseUnit(
    unit_id="cn:trotzdem",
    kind="connector",
    lemma_key="trotzdem",
    parts=["trotzdem"],
    display_de="trotzdem",
    sentence_count=1,
    rank=1,
    source="curated",
    card_count=1,
)
CARD = PhraseCard(
    card_id="abcdefabcdef",
    unit_id=UNIT.unit_id,
    kind="connector",
    sentence_de="Trotzdem kam sie.",
    gloss_en="Nevertheless she came.",
    gloss_source="azure",
    gaps=[GapSpan(start=0, end=8, answer="Trotzdem", token_index=0)],
    answers=["Trotzdem"],
    form_key="initial",
    corpus_source="tatoeba",
    corpus_line_id="1",
    needs_context=True,
)


class FakeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[dict[str, object]] = []

    def generate(self, prompt: str, model: str = "", purpose: str = "", **kwargs: object) -> str:
        self.calls.append({"prompt": prompt, "model": model, "purpose": purpose, **kwargs})
        if not self.replies:
            raise RuntimeError("quota")
        return self.replies.pop(0)


def test_refuses_before_any_call_without_approval() -> None:
    client = FakeClient(['{"context_de": "x", "context_en": "y"}'])
    with pytest.raises(contexts.ApprovalRequired):
        contexts.generate_contexts(
            [CARD],
            {UNIT.unit_id: UNIT},
            client=client,
            validate=lambda _: True,
            existing={},
            approved=False,
            max_calls=10,
        )
    assert client.calls == []


def test_accepted_reply_is_stored_with_cache_namespace_and_purpose() -> None:
    client = FakeClient(
        [
            "```json\n"
            '{"context_de": "Sie war sehr müde nach der Arbeit.", '
            '"context_en": "She was very tired after work."}\n```'
        ]
    )
    records = contexts.generate_contexts(
        [CARD],
        {UNIT.unit_id: UNIT},
        client=client,
        validate=lambda _: True,
        existing={},
        approved=True,
        max_calls=10,
        now=lambda: datetime(2026, 9, 8, tzinfo=UTC),
    )
    assert records[0].accepted and records[0].context_de == "Sie war sehr müde nach der Arbeit."
    assert client.calls[0]["purpose"] == "phrase_context"
    assert client.calls[0]["namespace"] == "phrase_context_v1"


def test_reply_containing_the_connector_is_rejected_with_reason() -> None:
    client = FakeClient(['{"context_de": "Trotzdem war sie müde gestern.", "context_en": "x"}'])
    records = contexts.generate_contexts(
        [CARD],
        {UNIT.unit_id: UNIT},
        client=client,
        validate=lambda _: True,
        existing={},
        approved=True,
        max_calls=10,
    )
    assert not records[0].accepted and records[0].reject_reason == "contains_connector"


def test_unparseable_reply_and_carrier_rejection_are_recorded() -> None:
    client = FakeClient(
        ["no json here", '{"context_de": "Sie war sehr müde nach der Arbeit.", "context_en": "y"}']
    )
    records = contexts.generate_contexts(
        [CARD, CARD.model_copy(update={"card_id": "bbbbbbbbbbbb"})],
        {UNIT.unit_id: UNIT},
        client=client,
        validate=lambda _: False,
        existing={},
        approved=True,
        max_calls=10,
    )
    assert [r.reject_reason for r in records] == ["unparseable", "carrier_rejected"]


def test_existing_records_are_not_re_asked() -> None:
    client = FakeClient([])
    existing = contexts.load_records(Path("does/not/exist"))
    existing[CARD.card_id] = contexts.ContextRecord(
        card_id=CARD.card_id,
        unit_id=UNIT.unit_id,
        sentence_de=CARD.sentence_de,
        accepted=False,
        reject_reason="length",
        model="m",
        prompt_version=1,
        generated_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    assert (
        contexts.generate_contexts(
            [CARD],
            {UNIT.unit_id: UNIT},
            client=client,
            validate=lambda _: True,
            existing=existing,
            approved=True,
            max_calls=10,
        )
        == []
    )


def test_records_round_trip_and_apply_to_cards(tmp_path: Path) -> None:
    record = contexts.ContextRecord(
        card_id=CARD.card_id,
        unit_id=UNIT.unit_id,
        sentence_de=CARD.sentence_de,
        context_de="Sie war müde.",
        context_en="She was tired.",
        accepted=True,
        model="m",
        prompt_version=1,
        generated_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    path = tmp_path / "contexts.jsonl"
    contexts.save_records([record], path)
    loaded = contexts.load_records(path)
    applied = contexts.apply_contexts([CARD], loaded)[0]
    assert applied.context_de == "Sie war müde." and applied.context_source == "gemini"
