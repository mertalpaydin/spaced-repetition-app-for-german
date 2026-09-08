"""Card selection: the trust rule, form diversity, the cap, wanted carriers,
and the gap-slicing invariant."""

from hypothesis import given
from hypothesis import strategies as st
from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.phrases.cards import Gloss, card_id_for, select_cards, with_card_counts
from src.phrases.occurrences import Occurrence

UNIT = PhraseUnit(
    unit_id="vp:warten_auf",
    kind="verb_prep",
    lemma_key="warten auf",
    parts=["warten", "auf"],
    display_de="warten auf",
    sentence_count=3,
    rank=1,
    source="mined",
    card_count=0,
)


def _occ(text: str, verb: str, prep: str, form_key: str = "Fin|Pres|3|Sing") -> Occurrence:
    v, p = text.index(verb), text.index(prep)
    return Occurrence(
        kind="verb_prep",
        unit_key="warten auf",
        parts=["warten", "auf"],
        token_indices=[1, 2],
        spans=sorted([(v, v + len(verb)), (p, p + len(prep))]),
        surfaces=[verb, prep] if v < p else [prep, verb],
        corpus_source="tatoeba",
        line_id=text,
        text=text,
        form_key=form_key,
    )


def test_tatoeba_gloss_never_yields_a_card_but_lands_in_wanted() -> None:
    occ = _occ("Er wartet auf den Bus.", "wartet", "auf")
    glosses = {occ.text: Gloss("He waits for the bus.", "tatoeba")}
    selection = select_cards([UNIT], {UNIT.unit_id: [occ]}, glosses, validate=lambda _: True)
    assert selection.cards == []
    assert [w.text for w in selection.wanted] == [occ.text]


def test_azure_gloss_yields_a_card_whose_gaps_slice_back() -> None:
    occ = _occ("Er wartet auf den Bus.", "wartet", "auf")
    glosses = {occ.text: Gloss("He waits for the bus.", "azure")}
    selection = select_cards([UNIT], {UNIT.unit_id: [occ]}, glosses, validate=lambda _: True)
    card = selection.cards[0]
    assert card.answers == ["wartet", "auf"]
    assert card.card_id == card_id_for(UNIT.unit_id, occ.text)
    assert card.gloss_source == "azure"


def test_distinct_surface_forms_are_covered_before_repeats() -> None:
    a = _occ("Er wartet auf den Bus.", "wartet", "auf", "Fin|Pres|3|Sing")
    b = _occ("Er wartete auf den Zug.", "wartete", "auf", "Fin|Past|3|Sing")
    c = _occ("Sie wartet auf dich.", "wartet", "auf", "Fin|Pres|3|Sing")
    glosses = {o.text: Gloss("x " + o.text, "azure") for o in (a, b, c)}
    selection = select_cards(
        [UNIT], {UNIT.unit_id: [a, c, b]}, glosses, validate=lambda _: True, k=2
    )
    assert {card.form_key for card in selection.cards} == {"Fin|Pres|3|Sing", "Fin|Past|3|Sing"}


def test_rejected_carriers_are_skipped_and_counted() -> None:
    occ = _occ("Er wartet auf den Bus.", "wartet", "auf")
    glosses = {occ.text: Gloss("He waits for the bus.", "azure")}
    selection = select_cards([UNIT], {UNIT.unit_id: [occ]}, glosses, validate=lambda _: False)
    assert selection.cards == []
    assert selection.stats["carrier_rejected"] == 1


def test_trivial_units_get_the_smaller_cap() -> None:
    trivial = UNIT.model_copy(update={"trivial": True})
    occs = [_occ(f"Er wartet auf den Bus {i}.", "wartet", "auf") for i in range(5)]
    glosses = {o.text: Gloss("x " + o.text, "azure") for o in occs}
    selection = select_cards(
        [trivial], {UNIT.unit_id: occs}, glosses, validate=lambda _: True, k=6, k_trivial=2
    )
    assert len(selection.cards) == 2


def test_with_card_counts_updates_units() -> None:
    occ = _occ("Er wartet auf den Bus.", "wartet", "auf")
    glosses = {occ.text: Gloss("He waits for the bus.", "azure")}
    selection = select_cards([UNIT], {UNIT.unit_id: [occ]}, glosses, validate=lambda _: True)
    assert with_card_counts([UNIT], selection.cards)[0].card_count == 1


@given(
    words=st.lists(st.text(alphabet="abcdefghij", min_size=1, max_size=6), min_size=2, max_size=6)
)
def test_card_validator_accepts_gaps_that_slice_back_and_rejects_drift(words: list[str]) -> None:
    sentence = " ".join(words)
    offset = 0
    gaps: list[GapSpan] = []
    for index, word in enumerate(words[:2]):
        gaps.append(GapSpan(start=offset, end=offset + len(word), answer=word, token_index=index))
        offset += len(word) + 1
    card = PhraseCard(
        card_id="0123456789ab",
        unit_id="vp:x",
        kind="verb_prep",
        sentence_de=sentence,
        gloss_en="gloss",
        gloss_source="azure",
        gaps=gaps,
        answers=[g.answer for g in gaps],
        form_key="",
        corpus_source="tatoeba",
        corpus_line_id="1",
    )
    assert [card.sentence_de[g.start : g.end] for g in card.gaps] == card.answers
    try:
        card.model_copy(update={"answers": [a + "z" for a in card.answers]}).model_validate(
            card.model_copy(update={"answers": [a + "z" for a in card.answers]}).model_dump()
        )
    except ValueError:
        return
    raise AssertionError("drifted answers must not validate")
