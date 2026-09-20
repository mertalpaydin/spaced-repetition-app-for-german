"""Mining fixed expressions from surface n-grams: the measure, the filters."""

from collections import Counter

from src.phrases.mining.expressions import (
    Expression,
    drop_contained,
    min_split_pmi,
    select_expressions,
)

#: A small corpus: "auf jeden Fall" is held together at every seam, while
#: "in der Stadt" is a common frame followed by a common noun phrase.
SURFACES = Counter(
    {
        "auf": 5_000,
        "jeden": 400,
        "fall": 600,
        "in": 9_000,
        "der": 12_000,
        "stadt": 700,
        "soweit": 300,
        "ich": 8_000,
        "weiss": 900,
    }
)
NGRAMS = Counter(
    {
        "auf jeden": 380,
        "jeden fall": 390,
        "auf jeden fall": 370,
        "in der": 4_000,
        "der stadt": 500,
        "in der stadt": 450,
        "soweit ich": 280,
        "ich weiss": 600,
        "soweit ich weiss": 270,
    }
)
TOTAL = 100_000


def test_min_split_pmi_rewards_an_expression_and_not_a_common_frame() -> None:
    fixed = min_split_pmi("auf jeden fall", 370, NGRAMS, SURFACES, TOTAL)
    frame = min_split_pmi("in der stadt", 450, NGRAMS, SURFACES, TOTAL)
    assert fixed > frame
    # a single word has no seam
    assert min_split_pmi("fall", 600, NGRAMS, SURFACES, TOTAL) == 0.0
    # a half that was pruned out of the counts scores nothing rather than
    # dividing by zero
    assert min_split_pmi("unbekannt wort", 50, NGRAMS, SURFACES, TOTAL) == 0.0


def test_select_skips_all_function_word_frames_and_known_units() -> None:
    # the default score floor is calibrated against the real corpus; this
    # toy one needs its own
    common = {"sentences": TOTAL, "min_count": 100, "min_score": 4.0}
    picked = {e.text for e in select_expressions(NGRAMS, SURFACES, **common)}
    assert "auf jeden fall" in picked
    assert "in der stadt" not in picked  # a common frame plus a common phrase
    assert "in der" not in picked  # every token is a function word
    picked_known = {
        e.text
        for e in select_expressions(NGRAMS, SURFACES, known_keys=["auf jeden fall"], **common)
    }
    assert "auf jeden fall" not in picked_known


def test_select_honours_the_count_floor_and_the_score_floor() -> None:
    assert select_expressions(NGRAMS, SURFACES, sentences=TOTAL, min_count=10_000) == []
    assert select_expressions(NGRAMS, SURFACES, sentences=TOTAL, min_score=99.0) == []
    # "soweit ich weiss" is the one that clears the real default
    assert [e.text for e in select_expressions(NGRAMS, SURFACES, sentences=TOTAL)] == [
        "jeden fall",
        "soweit ich weiss",
    ]


def test_drop_contained_keeps_the_longer_expression() -> None:
    kept = drop_contained(
        [
            Expression("auf jeden fall", 370, 9.0),
            Expression("jeden fall", 390, 8.0),
            Expression("ein anderes ding", 100, 7.0),
        ]
    )
    assert [e.text for e in kept] == ["auf jeden fall", "ein anderes ding"]
