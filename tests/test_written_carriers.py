"""Sentences written for a word the corpus never uses.

The gates here are the whole justification for the stage: a written sentence
is only allowed to become a card if it clears everything a corpus sentence
clears. No test touches the network; the client is a fake.
"""

import json

import pytest
from src.contracts import MODEL_GENERATE
from src.phrases.written_carriers import (
    ApprovalRequired,
    WrittenSentence,
    build_prompt,
    parse_response,
    reject_reason,
    sentences_of,
    write_sentences,
)

GOOD = WrittenSentence(
    german="Das Hähnchen im Ofen ist endlich fertig.",
    english="The chicken in the oven is finally ready.",
    surface="Hähnchen",
)


class FakeClient:
    """Records the prompts and answers with whatever it was given."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.prompts: list[str] = []
        self.namespaces: list[str] = []

    def generate(self, prompt: str, *, model: str, purpose: str, namespace: str) -> str:
        self.prompts.append(prompt)
        self.namespaces.append(namespace)
        return self.answers[len(self.prompts) - 1]


def _answer(rows: list[dict[str, str]]) -> str:
    return json.dumps({"cards": rows})


def test_the_prompt_names_the_word_and_forbids_german_in_the_english() -> None:
    prompt = build_prompt("hähnchen", "noun", "das Hähnchen", count=3)
    assert "3 short German sentences" in prompt
    assert 'das Hähnchen" (noun)' in prompt
    # Rule 2: the English is shown before the answer, so it must not quote
    # the German it translates.
    assert 'must NOT contain "hähnchen"' in prompt
    assert "surface form" in prompt


def test_parse_response_reads_the_json_and_survives_a_code_fence() -> None:
    rows = [{"de": "A", "en": "B", "surface": "C"}]
    assert parse_response(_answer(rows)) == [WrittenSentence("A", "B", "C")]
    fenced = "```json\n" + _answer(rows) + "\n```"
    assert parse_response(fenced) == [WrittenSentence("A", "B", "C")]
    assert parse_response("not json at all") is None
    assert parse_response('{"something": 1}') is None


def test_a_sentence_whose_surface_is_absent_is_refused() -> None:
    """Without this the gap would not line up and ``answers`` would not slice
    back out of ``sentence_de`` (rule 6)."""
    bad = WrittenSentence("Der Hund schläft tief.", "The dog sleeps deeply.", "Hähnchen")
    assert reject_reason(bad, "hähnchen", lambda _t: True) == "surface_not_in_sentence"


def test_an_english_gloss_that_quotes_the_german_is_refused() -> None:
    """Rule 2: that prints the answer on the card."""
    leaky = WrittenSentence(GOOD.german, "The Hähnchen is finally ready.", "Hähnchen")
    assert reject_reason(leaky, "hähnchen", lambda _t: True) == "english_quotes_the_german"


def test_a_sentence_the_carrier_validator_rejects_is_refused() -> None:
    """A written sentence passes the same 21 rules a corpus line does."""
    assert reject_reason(GOOD, "hähnchen", lambda _t: False) == "carrier_rejected"
    assert reject_reason(GOOD, "hähnchen", lambda _t: True) is None


def test_an_implausible_gloss_is_refused() -> None:
    stubby = WrittenSentence(GOOD.german, "ok", "Hähnchen")
    assert reject_reason(stubby, "hähnchen", lambda _t: True) == "gloss_implausible"


def test_incomplete_rows_are_refused_rather_than_patched() -> None:
    for sentence in (
        WrittenSentence("", GOOD.english, "Hähnchen"),
        WrittenSentence(GOOD.german, "", "Hähnchen"),
        WrittenSentence(GOOD.german, GOOD.english, ""),
    ):
        assert reject_reason(sentence, "hähnchen", lambda _t: True) == "incomplete"


def test_nothing_is_called_before_the_owner_approves() -> None:
    client = FakeClient(["never used"])
    with pytest.raises(ApprovalRequired, match="--approved-by-owner"):
        write_sentences(
            [("hähnchen", "noun", "das Hähnchen")],
            client=client,
            validate=lambda _t: True,
            approved=False,
            max_calls=5,
        )
    assert client.prompts == [], "a refusal must cost nothing"


def test_one_call_per_word_and_the_gates_are_applied() -> None:
    answer = _answer(
        [
            {"de": GOOD.german, "en": GOOD.english, "surface": "Hähnchen"},
            {"de": "Der Hund schläft tief.", "en": "The dog sleeps.", "surface": "Hähnchen"},
            {"de": GOOD.german, "en": GOOD.english, "surface": "Hähnchen"},
        ]
    )
    client = FakeClient([answer])
    results = write_sentences(
        [("hähnchen", "noun", "das Hähnchen")],
        client=client,
        validate=lambda _t: True,
        approved=True,
        max_calls=5,
    )
    assert len(client.prompts) == 1
    assert client.namespaces == ["written_carrier_v1"]
    (result,) = results
    # The duplicate is dropped, the absent surface is rejected, one is kept.
    assert [s.german for s in result.accepted] == [GOOD.german]
    assert [reason for _, reason in result.rejected] == ["surface_not_in_sentence"]
    assert result.model == MODEL_GENERATE
    assert sentences_of(results) == {GOOD.german: GOOD.english}


def test_max_calls_bounds_the_run() -> None:
    client = FakeClient([_answer([]), _answer([])])
    results = write_sentences(
        [("a", "noun", "a"), ("b", "noun", "b"), ("c", "noun", "c")],
        client=client,
        validate=lambda _t: True,
        approved=True,
        max_calls=2,
    )
    assert len(results) == 2 and len(client.prompts) == 2


def test_an_unparseable_answer_yields_a_result_with_nothing_in_it() -> None:
    """The stage must report the word as unwritten rather than crash."""
    client = FakeClient(["the model rambled"])
    (result,) = write_sentences(
        [("hähnchen", "noun", "das Hähnchen")],
        client=client,
        validate=lambda _t: True,
        approved=True,
        max_calls=1,
    )
    assert result.accepted == [] and result.rejected == []
