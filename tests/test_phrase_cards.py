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


def test_tatoeba_gloss_yields_an_unglossed_card_that_lands_in_wanted() -> None:
    """Cards come from the whole corpus; Tatoeba's English is never used."""
    occ = _occ("Er wartet auf den Bus.", "wartet", "auf")
    glosses = {occ.text: Gloss("He waits for the bus.", "tatoeba")}
    selection = select_cards([UNIT], {UNIT.unit_id: [occ]}, glosses, validate=lambda _: True)
    assert len(selection.cards) == 1
    assert selection.cards[0].gloss_en is None and selection.cards[0].gloss_source is None
    assert [w.text for w in selection.wanted] == [occ.text]
    assert selection.stats["glossed_cards"] == 0


def test_a_glossed_sentence_beats_an_unglossed_twin_of_the_same_form() -> None:
    a = _occ("Er wartet auf den Bus.", "wartet", "auf")
    b = _occ("Er wartet auf den Zug.", "wartet", "auf")
    glosses = {b.text: Gloss("He waits for the train.", "azure")}
    selection = select_cards([UNIT], {UNIT.unit_id: [a, b]}, glosses, validate=lambda _: True, k=1)
    assert selection.cards[0].sentence_de == b.text
    assert selection.wanted == []


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


def test_a_card_excluded_by_review_is_never_selected_again() -> None:
    occ = _occ("Er wartet auf den Bus.", "wartet", "auf")
    glosses = {occ.text: Gloss("He waits for the bus.", "azure")}
    selection = select_cards(
        [UNIT],
        {UNIT.unit_id: [occ]},
        glosses,
        validate=lambda _: True,
        excluded_card_ids=frozenset({card_id_for(UNIT.unit_id, occ.text)}),
    )
    assert selection.cards == []
    assert selection.stats["excluded_by_review"] == 1


def test_unsuitable_sentences_are_skipped_with_a_reason() -> None:
    from src.phrases.cards import unsuitable_reason

    leak = _occ("Er wartet auf den Bus, wartet und wartet.", "wartet", "auf")
    assert unsuitable_reason(leak) == "answer_leak"
    quote = _occ('Er wartet auf den "Bus.', "wartet", "auf")
    assert unsuitable_reason(quote) == "unbalanced_quotes"
    k1 = _occ("Er warte auf den Bus.", "warte", "auf", form_key="Fin|Pres|3|Sing|K1")
    assert unsuitable_reason(k1) == "konjunktiv_i"
    fragment = _occ("Weil er auf den Bus wartet.", "wartet", "auf")
    assert unsuitable_reason(fragment) == "subordinate_fragment"
    clause = _occ("Weil er auf den Bus wartet, kommt er später.", "wartet", "auf")
    assert unsuitable_reason(clause) is None
    fine = _occ("Er wartet auf den Bus.", "wartet", "auf")
    assert unsuitable_reason(fine) is None


def test_old_spelling_ocr_damage_and_dialogue_fragments_are_unsuitable() -> None:
    from src.phrases.cards import unsuitable_reason

    assert (
        unsuitable_reason(_occ("Er wartet auf den Bus, daß er kommt.", "wartet", "auf"))
        == "old_spelling"
    )
    assert (
        unsuitable_reason(_occ("Er wartet auf die Infor-mationen.", "wartet", "auf"))
        == "broken_hyphen"
    )
    assert (
        unsuitable_reason(_occ("Er wartet auf den Bus...", "wartet", "auf")) == "dialogue_fragment"
    )
    assert (
        unsuitable_reason(_occ("- Er wartet auf den Bus.", "wartet", "auf")) == "dialogue_fragment"
    )
    assert unsuitable_reason(_occ("Er wartet auf die AktivitÃ¤ten.", "wartet", "auf")) == "mojibake"
    assert unsuitable_reason(_occ("Er wartet auf die E-Mail.", "wartet", "auf")) is None


def test_a_tatoeba_sentence_beats_a_subtitle_cue_of_the_same_form() -> None:
    a = _occ("Er wartet auf den Bus.", "wartet", "auf")
    b = _occ("Er wartet auf dich.", "wartet", "auf").model_copy(
        update={"corpus_source": "opensubtitles_2018"}
    )
    selection = select_cards([UNIT], {UNIT.unit_id: [b, a]}, {}, validate=lambda _: True, k=1)
    assert selection.cards[0].sentence_de == a.text


def test_separable_card_with_a_reflexive_reading_is_unsuitable() -> None:
    from src.phrases.cards import unsuitable_reason

    def sep(text: str, verb: str, particle: str) -> Occurrence:
        v, p = text.index(verb), text.index(particle)
        return Occurrence(
            kind="separable_verb",
            unit_key="herausstellen",
            parts=["herausstellen"],
            token_indices=[1, 4],
            spans=[(v, v + len(verb)), (p, p + len(particle))],
            surfaces=[verb, particle],
            corpus_source="tatoeba",
            line_id=text,
            text=text,
            form_key="Fin|Pres|3|Sing|discontinuous",
        )

    assert unsuitable_reason(sep("Es stellt sich heraus, dass er lügt.", "stellt", "heraus")) == (
        "reflexive_reading"
    )
    assert unsuitable_reason(sep("Sie stellt die Vase heraus.", "stellt", "heraus")) is None


def test_url_stubs_and_sodass_fragments_are_unsuitable() -> None:
    from src.phrases.cards import unsuitable_reason

    assert unsuitable_reason(_occ("Er wartet auf den Bus, https://www.", "wartet", "auf")) == "url"
    assert (
        unsuitable_reason(_occ("Sodass er auf den Bus wartet.", "wartet", "auf"))
        == "subordinate_fragment"
    )


def test_complement_preposition_becomes_a_gap_when_unambiguous() -> None:
    from src.phrases.cards import complement_preposition, with_complement_gap

    unit = UNIT.model_copy(
        update={
            "kind": "noun_verb",
            "display_de": "Wert legen auf +Akk",
            "parts": ["Wert", "legen"],
        }
    )
    assert complement_preposition(unit) == "auf"
    assert complement_preposition(UNIT) is None  # verb_prep already gaps its preposition
    idiom = UNIT.model_copy(
        update={"kind": "idiom", "display_de": "ab und zu", "parts": ["ab und zu"]}
    )
    assert complement_preposition(idiom) is None  # its own last word, already a gap

    def occ(text: str, noun: str, verb: str) -> Occurrence:
        n, v = text.index(noun), text.index(verb)
        spans = sorted([(n, n + len(noun)), (v, v + len(verb))])
        return Occurrence(
            kind="noun_verb",
            unit_key="wert legen",
            parts=["Wert", "legen"],
            token_indices=[1, 2],
            spans=spans,
            surfaces=[text[a:b] for a, b in spans],
            corpus_source="tatoeba",
            line_id=text,
            text=text,
        )

    one = with_complement_gap(occ("Er legt großen Wert auf Pünktlichkeit.", "Wert", "legt"), "auf")
    assert one is not None
    assert one.surfaces == ["legt", "Wert", "auf"]
    assert one.text[one.spans[2][0] : one.spans[2][1]] == "auf"
    assert with_complement_gap(occ("Sie legen Wert darauf.", "Wert", "legen"), "auf") is None
    two = occ("Er legt Wert auf Ordnung auf dem Tisch.", "Wert", "legt")
    assert with_complement_gap(two, "auf") is None
    contracted = with_complement_gap(occ("Er legt Wert aufs Detail.", "Wert", "legt"), "auf")
    assert contracted is not None and contracted.surfaces[-1] == "aufs"


# -- single-word units, added 2026-09-20 ----------------------------------------

WORD_UNIT = PhraseUnit(
    unit_id="nn:frage",
    kind="noun",
    lemma_key="frage",
    parts=["frage"],
    display_de="die Frage",
    sentence_count=500,
    rank=1,
    source="mined",
    card_count=0,
)


def _word_occ(text: str, surface: str, form_key: str = "Acc|Sing") -> Occurrence:
    start = text.index(surface)
    return Occurrence(
        kind="noun",
        unit_key="frage",
        parts=["frage"],
        token_indices=[3],
        spans=[(start, start + len(surface))],
        surfaces=[surface],
        corpus_source="tatoeba",
        line_id=text,
        text=text,
        form_key=form_key,
    )


def test_a_single_word_unit_makes_a_one_gap_card_that_slices_back() -> None:
    occ = _word_occ("Er stellte eine Frage.", "Frage")
    glosses = {occ.text: Gloss("He asked a question.", "azure")}
    selection = select_cards(
        [WORD_UNIT], {WORD_UNIT.unit_id: [occ]}, glosses, validate=lambda _: True
    )
    assert len(selection.cards) == 1
    card = selection.cards[0]
    assert len(card.gaps) == 1
    assert card.answers == ["Frage"]
    assert card.sentence_de[card.gaps[0].start : card.gaps[0].end] == "Frage"
    assert card.kind == "noun"


def test_a_word_display_with_its_article_gets_no_complement_gap() -> None:
    """The citation form carries the article ("die Frage"); the complement
    scanner must not read a trailing word as a governing preposition."""
    from src.phrases.cards import complement_preposition

    assert complement_preposition(WORD_UNIT) is None
    ends_in_a_preposition = WORD_UNIT.model_copy(update={"display_de": "der Weg zu"})
    assert complement_preposition(ends_in_a_preposition) is None


def test_a_plain_verb_card_drawn_from_a_reflexive_sentence_is_unsuitable() -> None:
    """ "Wo es sich befindet" teaches "sich befinden"; a plain-verb card from
    it would leave "sich" outside the gap (review, 2026-09-21)."""
    from src.phrases.cards import unsuitable_reason

    def verb(text: str, surface: str) -> Occurrence:
        start = text.index(surface)
        return Occurrence(
            kind="verb",
            unit_key="befinden",
            parts=["befinden"],
            token_indices=[3],
            spans=[(start, start + len(surface))],
            surfaces=[surface],
            corpus_source="tatoeba",
            line_id=text,
            text=text,
            form_key="Fin|Pres|3|Sing",
        )

    assert unsuitable_reason(verb("Ich weiss, wo es sich befindet.", "befindet")) == (
        "reflexive_reading"
    )
    assert unsuitable_reason(verb("Er befindet sich im Haus.", "befindet")) == "reflexive_reading"
    assert unsuitable_reason(verb("Das Gericht befindet ihn für schuldig.", "befindet")) is None
