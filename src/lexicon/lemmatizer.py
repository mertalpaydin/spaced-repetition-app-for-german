"""Rule-based German morphological reducer.

``VocabularyStore`` (see ``vocabulary.py``) holds surface forms scraped from
CEFR wordlist PDFs, not lemmas. An inflected form that never happened to be
printed in a wordlist PDF (e.g. ``Regens``, the genitive of ``Regen``) reads
as an unknown word and trips a false vocabulary-ceiling violation even though
its lemma is well within level.

spaCy is not available in this environment (declared as a dependency but
installed nowhere), so this module implements a small, deliberately
conservative rule-based reducer instead of a statistical lemmatiser:

* verb inflection (weak present/preterite endings, ``ge-...-t`` and
  ``ge-...-en`` participles, a documented irregular-stem table for common
  strong verbs)
* noun inflection (genitive ``-s``/``-es``, plural ``-e``/``-en``/``-er``/
  ``-n``/``-s``, umlaut plurals)
* adjective / participle declension endings (``-e``, ``-er``, ``-es``,
  ``-en``, ``-em``) and attributive Partizip I (``spielend`` -> ``spielen``)
* separable verbs, resolved using the *sentence* the token came from: a verb
  stem plus a known separable prefix appearing elsewhere in the same
  sentence lemmatises to the combined verb (``rufe`` + ``an`` -> ``anrufen``),
  not to the bare stem verb.

The reducer never claims a single "correct" lemma. Instead it produces an
ordered list of *candidate* lemmas -- the surface form first, then
progressively reduced forms -- so callers can look each one up against a
vocabulary and stop at the first candidate that resolves acceptably. This
keeps the reducer conservative: an over-eager single-answer lemmatiser risks
silently matching an unrelated real word, which is worse than leaving a
token unresolved.
"""

import unicodedata

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

#: Character-level normalisation table so that the eszett spelling and its
#: Swiss/keyboard-limited "ss" substitute always produce the same lookup key,
#: and so umlauts survive Unicode composition differences (NFC).
_ESZETT_MAP = str.maketrans({"ß": "ss"})


def normalise(word: str) -> str:
    """Return a canonical, idempotent lookup key for a German word.

    - Unicode-normalises (NFC) so precomposed and decomposed umlauts match.
    - Casefolds (broader than ``.lower()`` for German).
    - Maps ``ß`` to ``ss`` so ``normalise("Straße") == normalise("Strasse")``.

    Idempotent: ``normalise(normalise(w)) == normalise(w)`` for any ``w``.
    """
    text = unicodedata.normalize("NFC", word.strip())
    text = text.casefold()
    text = text.translate(_ESZETT_MAP)
    return unicodedata.normalize("NFC", text)


#: Reverse-umlaut table used to recover the un-umlauted stem of umlaut
#: plurals/comparatives, e.g. "Strom" -> "Strömen" (o -> ö), "Buch" -> "Bücher"
#: (u -> ü). Documented, deliberately small: only the three umlaut vowels.
_UMLAUT_REVERSE = {"ä": "a", "ö": "o", "ü": "u"}


def _deumlaut(stem: str) -> str:
    """Replace umlaut vowels with their base vowel (best-effort, may no-op)."""
    return "".join(_UMLAUT_REVERSE.get(ch, ch) for ch in stem)


# ---------------------------------------------------------------------------
# Irregular strong-verb table
# ---------------------------------------------------------------------------

#: Small, documented table of common strong/irregular verb forms (imperative,
#: preterite, and irregular participles that a regular ``ge-...-t``/``-en``
#: strip cannot recover) mapped to their infinitive. Not exhaustive -- only
#: the handful of high-frequency verbs that show up in A1-B2 material.
IRREGULAR_LEMMAS: dict[str, str] = {
    # sprechen (to speak)
    "sprich": "sprechen",
    "spricht": "sprechen",
    "sprach": "sprechen",
    "sprachen": "sprechen",
    # geben (to give)
    "gib": "geben",
    "gibt": "geben",
    "gab": "geben",
    "gaben": "geben",
    # nehmen (to take)
    "nimm": "nehmen",
    "nimmt": "nehmen",
    "nahm": "nehmen",
    "nahmen": "nehmen",
    # lesen (to read)
    "lies": "lesen",
    "liest": "lesen",
    "las": "lesen",
    "lasen": "lesen",
    # sehen (to see)
    "sieh": "sehen",
    "sah": "sehen",
    "sahen": "sehen",
    # helfen (to help)
    "hilf": "helfen",
    "hilft": "helfen",
    "half": "helfen",
    "halfen": "helfen",
    # bleiben (to stay)
    "blieb": "bleiben",
    "blieben": "bleiben",
    # fahren (to drive/travel)
    "fuhr": "fahren",
    "fuhren": "fahren",
    # kommen (to come)
    "kam": "kommen",
    "kamen": "kommen",
    # schließen (to close) / beschließen (to decide, resolve)
    "schloss": "schließen",
    "geschlossen": "schließen",
    "beschloss": "beschließen",
    "beschlossen": "beschließen",
    # brechen (to break)
    "brach": "brechen",
    "gebrochen": "brechen",
    # essen (to eat)
    "ass": "essen",
    "gegessen": "essen",
    # trinken (to drink)
    "trank": "trinken",
    "getrunken": "trinken",
    # finden (to find)
    "fand": "finden",
    "gefunden": "finden",
    # schreiben (to write)
    "schrieb": "schreiben",
    "geschrieben": "schreiben",
    # stehen (to stand)
    "stand": "stehen",
    "gestanden": "stehen",
}

# ---------------------------------------------------------------------------
# Separable verb prefixes
# ---------------------------------------------------------------------------

#: Common separable-verb prefixes. Deliberately restricted to unambiguous,
#: high-frequency separable prefixes (excludes "durch", "um", "unter",
#: "über", which are sometimes separable and sometimes not, to avoid
#: misreading an unrelated preposition elsewhere in the sentence as a
#: separable prefix).
SEPARABLE_PREFIXES: frozenset[str] = frozenset(
    {
        "ab",
        "an",
        "auf",
        "aus",
        "bei",
        "ein",
        "fest",
        "her",
        "hin",
        "los",
        "mit",
        "nach",
        "vor",
        "weg",
        "weiter",
        "zu",
        "zurueck",
        "zurück",
        "zusammen",
    }
)

# ---------------------------------------------------------------------------
# Suffix tables (documented, ordered longest-first for readability only --
# every suffix is tried regardless of order since candidates are collected
# into a set).
# ---------------------------------------------------------------------------

#: Weak-verb preterite endings: legte, legtest, legten, legtet.
_PRETERITE_SUFFIXES = ("test", "tet", "ten", "te")

#: Weak-verb present-tense endings and adjective/participle declension
#: endings share several suffixes (-e, -en, -em, -er, -es, -et, -est, -st,
#: -t), so both are handled by one stripping pass.
_ENDING_SUFFIXES = ("est", "et", "em", "en", "er", "es", "st", "e", "t", "n", "s")

_MIN_STEM_LEN = 3


def _strip_suffixes(root: str) -> set[str]:
    """Strip every plausible inflectional suffix from ``root``, conservatively.

    A stem is only kept if at least ``_MIN_STEM_LEN`` characters remain, to
    avoid manufacturing bogus two-letter roots that might coincidentally
    collide with an unrelated real word.
    """
    stems: set[str] = set()
    for suf in (*_PRETERITE_SUFFIXES, *_ENDING_SUFFIXES):
        if root.endswith(suf) and len(root) - len(suf) >= _MIN_STEM_LEN:
            stems.add(root[: -len(suf)])
    return stems


def _participle_roots(key: str) -> set[str]:
    """Strip a ``ge-`` participle prefix, returning the bare core(s).

    ``gemacht`` -> ``{"macht", "mach"}`` (weak, -t participle)
    ``gefahren`` -> ``{"fahren"}`` (strong, participle already looks like an
    infinitive once the ``ge-`` prefix is removed)
    """
    roots: set[str] = set()
    if key.startswith("ge") and len(key) > 2 + _MIN_STEM_LEN:
        core = key[2:]
        roots.add(core)
        if core.endswith("et") and len(core) - 2 >= _MIN_STEM_LEN:
            roots.add(core[:-2])
        elif core.endswith("t") and len(core) - 1 >= _MIN_STEM_LEN:
            roots.add(core[:-1])
    return roots


def lemma_candidates(surface: str, context_tokens: list[str] | None = None) -> list[str]:
    """Return ordered candidate lemmas for ``surface``, surface form first.

    ``context_tokens``, when given, should be the other tokens of the
    sentence the word appears in (normalised or not, either is fine). It is
    used only to reconstruct separable verbs: if a reduced candidate looks
    like an infinitive (ends in ``-en``/``-eln``/``-ern``) and one of the
    other sentence tokens is a known separable prefix, the combined verb
    (``prefix + infinitive``) is added as a preferred candidate, per
    "Ich rufe dich morgen an" -> ``anrufen``, not ``rufen``.
    """
    key = normalise(surface)
    if len(key) < 2:
        return [key]

    roots = {key} | _participle_roots(key)
    stems: set[str] = set(roots)
    for root in roots:
        stems |= _strip_suffixes(root)
        # attributive Partizip I: "spielend" (already a stripped stem) -> "spielen"
        if root.endswith("d") and len(root) - 1 >= _MIN_STEM_LEN:
            stems.add(root[:-1])

    # Partizip I recovery also applies to freshly-stripped stems, e.g.
    # "spielende" -[strip -e]-> "spielend" -[strip trailing d]-> "spielen".
    for stem in list(stems):
        if stem.endswith("d") and len(stem) - 1 >= _MIN_STEM_LEN:
            stems.add(stem[:-1])

    candidates: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    add(key)

    ordered_stems = [key, *sorted(stems, key=len, reverse=True)]
    for stem in ordered_stems:
        add(stem)
        if not stem.endswith("en") and len(stem) >= _MIN_STEM_LEN:
            add(stem + "en")
        irregular = IRREGULAR_LEMMAS.get(stem)
        if irregular:
            add(irregular)

        deumlauted = _deumlaut(stem)
        if deumlauted != stem:
            add(deumlauted)
            if not deumlauted.endswith("en") and len(deumlauted) >= _MIN_STEM_LEN:
                add(deumlauted + "en")
            irregular_deumlauted = IRREGULAR_LEMMAS.get(deumlauted)
            if irregular_deumlauted:
                add(irregular_deumlauted)

    if key in IRREGULAR_LEMMAS:
        add(IRREGULAR_LEMMAS[key])

    if context_tokens:
        context_keys = {normalise(t) for t in context_tokens}
        prefixes_present = context_keys & SEPARABLE_PREFIXES
        if prefixes_present:
            infinitive_like = [c for c in candidates if c.endswith("en") and len(c) >= 4]
            separable_candidates = [
                prefix + infinitive for prefix in prefixes_present for infinitive in infinitive_like
            ]
            # Separable-verb readings are the most specific interpretation
            # available (they use extra sentence evidence beyond the token
            # itself), so they are tried before the bare stem.
            for sep in separable_candidates:
                if sep not in candidates:
                    candidates.insert(1, sep)

    return candidates
