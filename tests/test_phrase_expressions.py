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


# -- counting over the raw corpus ----------------------------------------------


def test_count_ngrams_stops_at_punctuation_and_outside_the_vocabulary() -> None:
    from scripts.count_ngrams import count_ngrams

    vocabulary = frozenset({"auf", "jeden", "fall", "ich", "komme", "und", "gehe"})
    counts = count_ngrams(
        ["Auf jeden Fall, ich komme.", "Auf jeden Fall!", "Ich komme und gehe."],
        vocabulary,
        log=None,
    )
    assert counts.ngrams["auf jeden fall"] == 2
    assert counts.surfaces["auf"] == 2
    # the comma ends the run, so no n-gram spans it
    assert "fall ich" not in counts.ngrams
    # a word outside the vocabulary ends the run as punctuation does
    assert (
        "jeden fall" not in count_ngrams(["auf jeden Xylophon fall"], vocabulary, log=None).ngrams
    )


def test_count_ngrams_counts_the_everyday_corpora_separately() -> None:
    """The register gate needs to know how much of a count came from speech
    rather than from the news wire."""
    from scripts.count_ngrams import count_ngrams

    vocabulary = frozenset({"auf", "jeden", "fall", "laut", "angaben"})
    counts = count_ngrams(
        [
            ("tatoeba", "Auf jeden Fall."),
            ("opensubtitles_2018", "Auf jeden Fall!"),
            ("leipzig_news", "Auf jeden Fall."),
            ("leipzig_news", "Laut Angaben."),
        ],
        vocabulary,
        log=None,
    )
    assert counts.ngrams["auf jeden fall"] == 3
    assert counts.everyday["auf jeden fall"] == 2
    assert counts.everyday["laut angaben"] == 0


def test_count_ngrams_folds_the_eszett_like_the_vocabulary_does() -> None:
    """The frequency list is normalised (ss for the eszett) so the text must
    be too, or every word spelled with an eszett ends the run."""
    from scripts.count_ngrams import count_ngrams

    vocabulary = frozenset({"soweit", "ich", "weiss", "zu", "fuss"})
    counts = count_ngrams(["Soweit ich weiß.", "Soweit ich weiß!"], vocabulary, log=None)
    assert counts.ngrams["soweit ich weiss"] == 2
    assert counts.surfaces["weiss"] == 2


# -- the gates -----------------------------------------------------------------


def test_extend_to_longest_grows_a_span_the_corpus_never_leaves_bare() -> None:
    """ "erster Linie" scores well and "in erster Linie" does not, because "in"
    is too common for the seam to look surprising. The counts settle it: the
    preposition is there 99% of the time (owner, 2026-09-21)."""
    from src.phrases.mining.expressions import extend_to_longest

    ngrams = Counter({"erster linie": 766, "in erster linie": 763, "linie stehen": 40})
    grown = extend_to_longest([Expression(text="erster linie", count=766, score=9.0)], ngrams)
    assert [e.text for e in grown] == ["in erster linie"]
    assert grown[0].count == 763


def test_extend_to_longest_leaves_a_span_with_several_frames_alone() -> None:
    """ "Krankenhaus gebracht" follows "ins", "in ein" and more, so no single
    extension speaks for it."""
    from src.phrases.mining.expressions import extend_to_longest

    ngrams = Counter({"krankenhaus gebracht": 1088, "ins krankenhaus gebracht": 503})
    grown = extend_to_longest(
        [Expression(text="krankenhaus gebracht", count=1088, score=7.0)], ngrams
    )
    assert [e.text for e in grown] == ["krankenhaus gebracht"]


def test_everyday_share_separates_speech_from_the_news_wire() -> None:
    from src.phrases.mining.expressions import everyday_share

    ngrams = Counter({"auf jeden fall": 100, "angaben zufolge": 100})
    everyday = Counter({"auf jeden fall": 80})
    assert everyday_share("auf jeden fall", ngrams, everyday) == 0.8
    assert everyday_share("angaben zufolge", ngrams, everyday) == 0.0
    assert everyday_share("nie gezaehlt", ngrams, everyday) == 0.0


def test_choose_expressions_drops_names_and_news_boilerplate() -> None:
    """The two gates no measure stands in for: a proper name scores highest
    of all on PMI, and news boilerplate is real German a learner does not
    need first."""
    from src.phrases.mining.expressions import choose_expressions

    surfaces = Counter({"auf": 5000, "jeden": 400, "fall": 600, "buenos": 90, "aires": 95})
    ngrams = Counter(
        {"auf jeden": 380, "jeden fall": 390, "auf jeden fall": 370, "buenos aires": 86}
    )
    everyday = Counter({"auf jeden fall": 300, "buenos aires": 80})
    dictionary = frozenset({"auf", "jeden", "fall"})
    chosen = choose_expressions(
        ngrams,
        surfaces,
        everyday,
        sentences=100_000,
        dictionary=dictionary,
        min_everyday_share=0.05,
    )
    assert [e.text for e in chosen] == ["auf jeden fall"]
    denied = choose_expressions(
        ngrams,
        surfaces,
        everyday,
        sentences=100_000,
        dictionary=dictionary,
        deny=["auf jeden fall"],
    )
    assert denied == []


def test_a_two_word_candidate_needs_two_content_words() -> None:
    """At a low score floor the pairs are otherwise conjugation frames.
    Three words and up keep the one-content-word rule, or "zum ersten Mal"
    would go with them."""
    from src.phrases.mining.expressions import _carries_vocabulary

    assert _carries_vocabulary(["jeden", "tag"])
    assert not _carries_vocabulary(["habt", "ihr"])
    assert not _carries_vocabulary(["mein", "vater"])
    assert _carries_vocabulary(["zum", "ersten", "mal"])
    assert _carries_vocabulary(["soweit", "ich", "weiss"])
    assert not _carries_vocabulary(["in", "der", "das"])
