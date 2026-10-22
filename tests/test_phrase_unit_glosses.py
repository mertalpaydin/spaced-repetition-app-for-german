"""The opt-in unit-gloss stage: approval before any call, rank order and
batching, strict parsing, shape checks, curated glosses untouched."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.phrases import unit_glosses


def unit(unit_id: str, display: str, rank: int, **kw: Any) -> PhraseUnit:
    kind = kw.pop("kind", "separable_verb")
    return PhraseUnit(
        unit_id=unit_id,
        kind=kind,
        lemma_key=display,
        parts=display.split(),
        display_de=display,
        sentence_count=1,
        rank=rank,
        source="mined",
        card_count=1,
        **kw,
    )


A = unit("sv:aussehen", "aussehen", 1)
B = unit("vp:warten_auf", "warten auf", 2, kind="verb_prep", case="Akk")
C = unit("cn:trotzdem", "trotzdem", 3, kind="connector", gloss_en="nevertheless")
T = unit("sv:trivial", "trivial", 4, trivial=True)
CARD = PhraseCard(
    card_id="abcdefabcdef",
    unit_id=A.unit_id,
    kind="separable_verb",
    sentence_de="Du siehst gut aus.",
    gloss_en="You look good.",
    gloss_source="azure",
    gaps=[GapSpan(start=3, end=9, answer="siehst", token_index=1)],
    answers=["siehst"],
    form_key="Fin",
    corpus_source="tatoeba",
    corpus_line_id="1",
)


class FakeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    def generate_many(self, prompts: list[str], **kwargs: Any) -> list[str]:
        self.calls.append({"prompts": prompts, **kwargs})
        return self.replies[: len(prompts)]


def test_units_needing_gloss_skips_curated_and_trivial_and_orders_by_rank() -> None:
    assert [u.unit_id for u in unit_glosses.units_needing_gloss([T, C, B, A])] == [
        "sv:aussehen",
        "vp:warten_auf",
    ]


def test_refuses_before_any_call_without_approval() -> None:
    client = FakeClient([])
    with pytest.raises(unit_glosses.ApprovalRequired):
        unit_glosses.generate_unit_glosses(
            [A, B], [CARD], client=client, existing={}, approved=False, max_calls=10
        )
    assert client.calls == []


def test_batches_by_rank_and_keeps_up_to_three_glosses_in_order() -> None:
    client = FakeClient(
        [
            '```json\n[{"unit_id": "sv:aussehen", "glosses": ["to look", "to appear", '
            '"to seem", "to look like"]}, '
            '{"unit_id": "vp:warten_auf", "glosses": ["to wait for"]}]\n```'
        ]
    )
    records = unit_glosses.generate_unit_glosses(
        [B, A, C, T],
        [CARD],
        client=client,
        existing={},
        approved=True,
        max_calls=10,
        now=lambda: datetime(2026, 9, 12, tzinfo=UTC),
    )
    assert [r.unit_id for r in records] == ["sv:aussehen", "vp:warten_auf"]
    assert records[0].glosses == ["to look", "to appear", "to seem"]
    assert records[1].accepted and records[1].glosses == ["to wait for"]
    call = client.calls[0]
    assert call["purpose"] == "unit_gloss" and call["cache_namespace"] == "unit_gloss_v1"
    prompt = call["prompts"][0]
    assert "sv:aussehen | aussehen (separable verb)" in prompt
    assert 'example: "Du siehst gut aus."' in prompt
    assert "vp:warten_auf | warten auf (verb + preposition, + Akk)" in prompt
    assert prompt.index("sv:aussehen") < prompt.index("vp:warten_auf")


def test_batch_size_and_max_calls_bound_the_run() -> None:
    client = FakeClient(['[{"unit_id": "sv:aussehen", "glosses": ["to look"]}]'] * 5)
    records = unit_glosses.generate_unit_glosses(
        [A, B],
        [],
        client=client,
        existing={},
        approved=True,
        max_calls=1,
        batch_size=1,
    )
    assert len(client.calls[0]["prompts"]) == 1
    assert [r.unit_id for r in records] == ["sv:aussehen"]


def test_rejections_are_recorded_with_reason() -> None:
    client = FakeClient(
        [
            '[{"unit_id": "sv:aussehen", "glosses": ["aussehen"]}, '
            '{"unit_id": "vp:warten_auf", "glosses": []}]',
        ]
    )
    records = unit_glosses.generate_unit_glosses(
        [A, B], [], client=client, existing={}, approved=True, max_calls=10
    )
    assert [(r.accepted, r.reject_reason) for r in records] == [
        (False, "german_echoed"),
        (False, "empty"),
    ]
    unparseable = unit_glosses.generate_unit_glosses(
        [A], [], client=FakeClient(["no json here"]), existing={}, approved=True, max_calls=1
    )
    assert unparseable[0].reject_reason == "unparseable"
    missing = unit_glosses.generate_unit_glosses(
        [A, B],
        [],
        client=FakeClient(['[{"unit_id": "sv:aussehen", "glosses": ["to look"]}]']),
        existing={},
        approved=True,
        max_calls=1,
    )
    assert missing[1].reject_reason == "missing"


def test_one_malformed_object_does_not_sink_the_batch() -> None:
    raw = (
        '[{"unit_id": "sv:aussehen", "glosses": ["to look"]},\n'
        '{"vp:warten_auf", "glosses": ["to wait for"]}]'
    )
    assert unit_glosses.parse_response(raw) == {"sv:aussehen": ["to look"]}
    records = unit_glosses.generate_unit_glosses(
        [A, B], [], client=FakeClient([raw]), existing={}, approved=True, max_calls=1
    )
    assert [(r.accepted, r.reject_reason) for r in records] == [(True, None), (False, "missing")]


def test_existing_records_are_not_re_asked_and_apply_keeps_curated(tmp_path: Path) -> None:
    client = FakeClient(['[{"unit_id": "vp:warten_auf", "glosses": ["to wait for"]}]'])
    existing = {
        "sv:aussehen": unit_glosses.UnitGlossRecord(
            unit_id="sv:aussehen",
            display_de="aussehen",
            glosses=["to look", "to appear"],
            accepted=True,
            model="m",
            prompt_version=1,
            generated_at=datetime(2026, 9, 12, tzinfo=UTC),
        )
    }
    records = unit_glosses.generate_unit_glosses(
        [A, B], [], client=client, existing=existing, approved=True, max_calls=10
    )
    assert [r.unit_id for r in records] == ["vp:warten_auf"]
    path = tmp_path / "unit_glosses.jsonl"
    unit_glosses.save_records([*existing.values(), *records], path)
    loaded = unit_glosses.load_records(path)
    applied = unit_glosses.apply_unit_glosses([A, B, C], loaded)
    assert [u.gloss_en for u in applied] == ["to look / to appear", "to wait for", "nevertheless"]


def test_german_leak_flags_a_quoted_phrase_and_spares_cognates() -> None:
    gehen_um = unit("vp:gehen_um", "gehen um", 1, kind="verb_prep", case="Akk")
    assert unit_glosses.german_leak("to be about (es geht um)", gehen_um) == "es um"
    assert unit_glosses.german_leak("to be about / to concern", gehen_um) is None
    handeln = unit("rv:sich_handeln_um", "sich handeln um", 2, kind="reflexive_verb")
    assert unit_glosses.german_leak("to be a matter of (es handelt sich um)", handeln)
    # cognates are the translation, not a leak
    krieg = unit("an:total_krieg", "der totale Krieg", 3, kind="adj_noun")
    assert unit_glosses.german_leak("total war", krieg) is None
    assert (
        unit_glosses.german_leak("bitter taste", unit("an:bitter", "bitterer Geschmack", 4)) is None
    )
    assert (
        unit_glosses.german_leak(
            "a private conversation", unit("an:priv", "eine private Unterhaltung", 5)
        )
        is None
    )
    # the display quoted whole, without a function word
    entscheidung = unit("nv:entscheidung_treffen", "eine Entscheidung treffen", 6, kind="noun_verb")
    assert unit_glosses.german_leak("to decide, eine Entscheidung treffen", entscheidung)


def test_a_gloss_that_quotes_the_german_is_rejected() -> None:
    gehen_um = unit("vp:gehen_um", "gehen um", 1, kind="verb_prep", case="Akk")
    assert unit_glosses.reject_reason(gehen_um, ["to be about (es geht um)"]) == "german_leak"
    assert unit_glosses.reject_reason(gehen_um, ["to be about", "to concern"]) is None


def test_a_cognate_gloss_is_not_an_echo() -> None:
    """Found 2026-10-07: eleven nouns in the top 500 had no "Gesucht:" line
    because the only correct English was rejected as german_echoed. The echo
    check exists to catch a model that handed the German back, but for a
    cognate the right answer and a refusal look identical, and CLAUDE.md
    already says a cognate is not a leak."""
    from src.phrases.unit_glosses import reject_reason

    problem = PhraseUnit(
        unit_id="nn:problem",
        kind="noun",
        lemma_key="problem",
        parts=["problem"],
        display_de="das Problem",
        sentence_count=100,
        rank=105,
        source="mined",
        card_count=6,
    )
    assert reject_reason(problem, ["problem"]) is None
    assert reject_reason(problem, ["problem", "issue"]) is None

    # The list is meaning, not spelling. "das Land" is a country and "der
    # Fall" is a case, so an echo there is a wrong gloss and stays rejected.
    land = problem.model_copy(
        update={
            "unit_id": "nn:land",
            "lemma_key": "land",
            "parts": ["land"],
            "display_de": "das Land",
        }
    )
    assert reject_reason(land, ["land"]) == "german_echoed"
    assert reject_reason(land, ["country"]) is None

    # And a single-part unit that is not a cognate is still an echo: a model
    # handing back "aussehen" has given up, not translated.
    aussehen = problem.model_copy(
        update={
            "unit_id": "sv:aussehen",
            "kind": "separable_verb",
            "lemma_key": "aussehen",
            "parts": ["aussehen"],
            "display_de": "aussehen",
        }
    )
    assert reject_reason(aussehen, ["aussehen"]) == "german_echoed"

    # A multi-part unit handing back one of its own parts is still an echo:
    # it tells the learner the half they could already see.
    warten = PhraseUnit(
        unit_id="vp:warten_auf",
        kind="verb_prep",
        lemma_key="warten auf",
        parts=["warten", "auf"],
        display_de="warten auf",
        sentence_count=100,
        rank=1,
        source="mined",
        card_count=6,
    )
    assert reject_reason(warten, ["warten"]) == "german_echoed"
    assert reject_reason(warten, ["warten auf"]) == "german_echoed"
    assert reject_reason(warten, ["wait for"]) is None

    # And a single word glossed with a German PHRASE is not a cognate either.
    assert reject_reason(problem, ["das Problem"]) == "german_echoed"


# -- every verb gloss starts with "to", added 2026-10-22 ------------------------


def test_normalize_verb_gloss_gives_every_verb_its_to() -> None:
    """The prompt asked for "to" from the start and the model often left it off.
    The owner: "all verb phrase translations should start with 'to'. there is
    one verb I cannot remember and the translation misleads me every time"."""
    n = unit_glosses.normalize_verb_gloss
    assert n("enjoy") == "to enjoy"
    assert n("take part in") == "to take part in"
    assert n("to wait for") == "to wait for"
    assert n("To wait for") == "To wait for"
    # "must" has no infinitive, so a modal lead becomes "to have to"
    assert n("must go") == "to have to go"
    assert n("has to go") == "to have to go"
    assert n("needs to happen") == "to have to happen"
    # and a negation keeps "to" first
    assert n("not to lift a finger") == "to not lift a finger"


def test_normalized_glosses_touch_only_verb_kinds_and_drop_duplicates() -> None:
    verb = unit("jv:muss_gehen", "muss gehen", 1, kind="adj_verb")
    noun = unit("nn:hand", "Hand", 2, kind="noun")
    assert unit_glosses.normalized_glosses(verb, ["must go", "has to go"]) == ["to have to go"]
    assert unit_glosses.normalized_glosses(noun, ["hand"]) == ["hand"]


def test_every_verb_gloss_in_the_committed_deck_starts_with_to() -> None:
    """Pinned against what is actually deployed, so the rule cannot drift."""
    import json

    deck = json.loads(Path("web/data/deck/units.json").read_text(encoding="utf-8"))
    offenders = []
    for u in deck["units"]:
        if u["kind"] not in unit_glosses.VERB_KINDS or u.get("trivial") or not u.get("gloss_en"):
            continue
        if not all(a.strip().lower().startswith("to ") for a in u["gloss_en"].split("/")):
            offenders.append(f"{u['unit_id']}: {u['gloss_en']}")
    assert not offenders, f"{len(offenders)} verb gloss(es) without 'to': {offenders[:10]}"
