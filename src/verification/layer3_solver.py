"""Layer 3: Adversarial solver and ambiguity detection.

The ambiguity check replaces a single hardcoded regex (which matched exactly
one fixture sentence shape) with a general test for under-constrained gaps.
The plan's three suggested signals are all implemented, each gated on a
structural property of the sentence, never on its literal text:

1. No governing preposition, subject, or determiner-agreement anchor
   constrains the gap's *form* at all (open lexical choice: which verb, which
   adjective, which near-synonym preposition, which modal of similar
   meaning).
2. More than one of the item's own distractors would also be grammatical in
   the slot: approximated by lemma/paradigm equality (a distractor that is
   just a different *inflection* of the same lexeme is not a competing
   answer; a distractor that is a genuinely different lexeme, and is not
   excluded by whatever local agreement anchor exists, is).
3. The slot is sentence-initial with no agreement anchor (the subject itself
   is the gap, and more than one candidate subject agrees with the given
   verb).

No spaCy dependency: this module only uses small, general German word lists.
"""

import re

from src.contracts import CandidateItem, ErrorTaxonomy, Topic
from src.verification.layer2_morphology import (
    DEFINITE_FORMS,
    IRREGULAR_VERB_LEMMA,
    SUBJECT_ENDING,
    verb_ending,
)

# The longest legitimate German verbal form is periphrastic and three words:
# "gewesen sein wird", "gegangen sein könnte". Anything longer is a clause,
# not a form, and is a real defect.
MAX_DISTRACTOR_WORDS = 3

# Prepositions that force one specific case: the closed class of a small
# handful of forms (der/die/das/den/dem/des/... or the ein-paradigm) is the
# *only* thing that can fill the gap, so a gap immediately governed by one of
# these is never an open lexical choice.
CASE_PREPOSITIONS: frozenset[str] = frozenset(
    {
        "auf",
        "an",
        "in",
        "über",
        "unter",
        "vor",
        "hinter",
        "neben",
        "zwischen",
        "durch",
        "für",
        "um",
        "gegen",
        "ohne",
        "mit",
        "nach",
        "bei",
        "aus",
        "von",
        "seit",
        "zu",
        "wegen",
        "während",
        "trotz",
        "statt",
        "außer",
        "entlang",
    }
)

# Fixed two-part connectors: the first half, appearing anywhere in the
# prompt, uniquely forces the second half regardless of what other
# "same-shape" conjunctions might otherwise seem to fit in the gap.
CORRELATIVE_TRIGGERS: frozenset[str] = frozenset({"sowohl", "weder", "entweder", "je", "zwar"})

# Determiner paradigms, mapped to a shared lemma key so that e.g. "der/die/das"
# (one paradigm: the definite article) are not treated as three competing
# answers, while "der" vs "ein" (two different paradigms: definite vs
# indefinite) are recognised as genuinely different determiners.
_DETERMINER_LEMMA: dict[str, str] = {}  # noqa: RUF012
for _stem, _endings in {
    "der": ("der", "die", "das", "den", "dem", "des"),
    "ein": ("ein", "eine", "einen", "einem", "einer", "eines"),
    "kein": ("kein", "keine", "keinen", "keinem", "keiner", "keines"),
    "dieser": ("dieser", "diese", "dieses", "diesen", "diesem"),
    "jener": ("jener", "jene", "jenes", "jenen", "jenem"),
    "mein": ("mein", "meine", "meinen", "meinem", "meiner", "meines"),
    "dein": ("dein", "deine", "deinen", "deinem", "deiner", "deines"),
    "sein_poss": ("sein", "seine", "seinen", "seinem", "seiner", "seines"),
    "ihr_poss": ("ihr", "ihre", "ihren", "ihrem", "ihrer", "ihres"),
}.items():
    for _form in _endings:
        _DETERMINER_LEMMA.setdefault(_form, _stem)

_SUBJECT_PRONOUNS: frozenset[str] = frozenset({"ich", "du", "er", "es", "wir", "ihr", "sie"})

# The reflexive/personal object pronoun paradigm ("mich", "dich", "sich",
# "uns", "euch", ...): grammatical-person variants of the same closed-class
# category, not competing lexical choices, so they must not be flagged as
# heterogeneous distractors the way genuinely different content words are.
_PRONOUN_LEMMA: dict[str, str] = dict.fromkeys(
    ("mich", "dich", "sich", "uns", "euch", "ihn", "ihm", "ihr", "ihnen", "mir", "dir"),
    "obj_pron",
)  # noqa: RUF012

# Only "sie"/"Sie" as a *candidate subject* (signal 3: the gap itself is the
# subject) can be resolved by ending alone: every reading of "sie" (3sg
# "she", 3pl "they", formal "Sie") that is even possible takes the "-en"
# ending EXCEPT the singular "she" reading, which needs "-t" -- so treating
# "sie" as ending-compatible with "-en" only slightly under-counts, and never
# over-counts, competing subject candidates. This is deliberately NOT merged
# into ``SUBJECT_ENDING``: when "sie"/"Sie" is the item's OWN already-fixed
# subject (the agreement-governed verb-slot branch below), which reading is
# meant is unknowable from the text, so no expected ending can be asserted
# there without risking a false rejection.
_SIGNAL3_SUBJECT_ENDING: dict[str, str] = {**SUBJECT_ENDING, "sie": "en"}


def _lemma_key(word: str) -> str | None:
    """Return a shared paradigm key for irregular verbs / determiners, else ``None``."""
    w = word.lower()
    if w in IRREGULAR_VERB_LEMMA:
        return "v:" + IRREGULAR_VERB_LEMMA[w]
    if w in _DETERMINER_LEMMA:
        return "d:" + _DETERMINER_LEMMA[w]
    if w in _PRONOUN_LEMMA:
        return "p:" + _PRONOUN_LEMMA[w]
    return None


def _same_lexeme(a: str, b: str) -> bool:
    """Is ``b`` plausibly just a different inflection of the same word as ``a``?

    Irregular verbs and determiners are resolved through the tables above.
    Everything else (regular verbs, adjectives, adverbs, nouns) falls back to
    a shared-prefix heuristic: German inflection is suffixing, so paradigm
    mates share a long stem prefix (``wohn-t``/``wohn-en``), while unrelated
    lexemes typically diverge within the first two or three letters
    (``spiel-t`` vs ``lies-t``).
    """
    a_l, b_l = a.lower(), b.lower()
    if a_l == b_l:
        return True
    key_a, key_b = _lemma_key(a_l), _lemma_key(b_l)
    if key_a is not None or key_b is not None:
        return key_a == key_b
    shorter = min(len(a_l), len(b_l))
    if shorter == 0:
        return False
    common = 0
    for x, y in zip(a_l, b_l, strict=False):
        if x != y:
            break
        common += 1
    return common >= 3 or common >= shorter - 1


def _word_before(prompt: str, gap_pos: int) -> str | None:
    before = prompt[:gap_pos].rstrip()
    match = re.search(r"([A-Za-zÄÖÜäöüß]+)\s*$", before)
    return match.group(1) if match else None


def _word_after(prompt: str, gap_end: int) -> str | None:
    after = prompt[gap_end:].lstrip()
    match = re.match(r"[A-Za-zÄÖÜäöüß]+", after)
    return match.group(0) if match else None


class Layer3AdversarialSolver:
    """Simulates an adversarial solver to identify ambiguous prompts and distractors."""

    def validate(
        self, item: CandidateItem, topic: Topic | None = None
    ) -> tuple[bool, str | None, ErrorTaxonomy | None]:
        """Verify prompt disambiguation and assert no distractor provides an alternate solution."""
        ans_lower = item.proposed_answer.strip().lower()

        for d in item.distractors:
            if d.text.strip().lower() == ans_lower:
                return False, f"Distractor '{d.text}' matches proposed answer.", "ambiguity"

        # docs/audits/stage-04-recovery-plan.md fix B. This character class
        # used to omit the space, so every multi-word distractor read as
        # "non-German": "hat gekauft", "haben geholfen", "bist gefahren",
        # "gewesen wäre", "am größten", "interessiert an". All are correct
        # German, and 10 items in batch_51fc18e48f7b died on it.
        #
        # Multi-word distractors are not an edge case. Perfekt, Plusquam-
        # perfekt, Futur, the whole Konjunktiv II system, analytic
        # superlatives and the verb-preposition topics all need a periphrastic
        # form as their contrast. Banning the space bans the B1/B2 half of the
        # taxonomy from having meaningful wrong answers. A word-count bound
        # replaces it, since runaway distractors were the real underlying
        # worry and length is what actually measures that.
        for d in item.distractors:
            text = d.text.strip()
            if not re.match(r"^[A-ZÄÖÜa-zäöüß\-'\s]+$", text):
                return (
                    False,
                    f"Distractor '{d.text}' contains invalid non-German characters.",
                    "structural_malformation",
                )
            if len(text.split()) > MAX_DISTRACTOR_WORDS:
                return (
                    False,
                    f"Distractor '{d.text}' is {len(text.split())} words, over the "
                    f"{MAX_DISTRACTOR_WORDS}-word limit for a single verbal form.",
                    "structural_malformation",
                )

        reason = self._check_under_constrained(item, topic)
        if reason:
            return False, reason, "ambiguity"

        return True, None, None

    def _check_under_constrained(self, item: CandidateItem, topic: Topic | None) -> str | None:
        if item.cue or item.type != "cloze_free":
            return None
        if "___" not in item.prompt:
            return None

        gap_pos = item.prompt.index("___")
        left = _word_before(item.prompt, gap_pos)
        right = _word_after(item.prompt, gap_pos + 3)

        # Signal 3: sentence-initial gap with no agreement anchor -> the gap
        # IS the subject; ambiguous if more than one candidate subject agrees
        # with the given (already-fixed) verb ending.
        if left is None and right:
            right_ending = verb_ending(right)
            if right_ending is not None:
                candidates = [item.proposed_answer, *[d.text for d in item.distractors]]
                agreeing = [
                    c
                    for c in candidates
                    if c.lower() in _SIGNAL3_SUBJECT_ENDING
                    and _SIGNAL3_SUBJECT_ENDING[c.lower()] == right_ending
                ]
                if len(agreeing) >= 2:
                    return (
                        f"Sentence-initial gap has no agreement anchor: subjects "
                        f"{agreeing} all agree with '{right}'."
                    )
            return None

        if left is None:
            return None

        left_lower = left.lower()

        # A case-governing preposition immediately before the gap uniquely
        # forces a case; the closed article/pronoun paradigm for that case is
        # the only thing that can fill it. Well-constrained by construction.
        if left_lower in CASE_PREPOSITIONS:
            return None

        # Determiner-choice slot: a definite article correctly agrees in case
        # and gender, but a distractor from a *different* determiner paradigm
        # (indefinite/demonstrative/possessive) would be equally grammatical
        # there -- a definiteness/deixis choice, not a grammar error.
        if (
            topic
            and topic.syntax_tags.get("ArtType") == "Def"
            and topic.morph_spec
            and topic.morph_spec.get("Case") in DEFINITE_FORMS
            and right
        ):
            answer_key = _lemma_key(item.proposed_answer)
            other_paradigm = [
                d
                for d in item.distractors
                if _lemma_key(d.text) is not None
                and _lemma_key(d.text) != answer_key
                and _lemma_key(d.text) != "d:der"
            ]
            if other_paradigm:
                return (
                    "Gap admits more than one determiner paradigm (definite vs. "
                    f"indefinite/demonstrative/possessive): {[d.text for d in other_paradigm]} "
                    "are all grammatical here."
                )

        # Subject-pronoun-anchored verb slot: the subject fixes only the verb
        # *ending*, not which verb, so this only conclusively resolves the
        # slot when the proposed answer has a recognisable regular ending
        # that matches. An irregular-verb answer (no reliable ending, e.g.
        # "kann") or an ambiguous subject ("sie"/"Sie") give no agreement
        # signal at all and fall through to lexeme heterogeneity below.
        if left_lower in _SUBJECT_PRONOUNS:
            expected = SUBJECT_ENDING.get(left_lower)
            if expected is not None:
                ans_ending = verb_ending(item.proposed_answer)
                if ans_ending is not None:
                    if ans_ending == expected:
                        compatible = [
                            d.text for d in item.distractors if verb_ending(d.text) == expected
                        ]
                        if compatible:
                            return (
                                f"Subject '{left}' fixes only the verb ending, not which "
                                f"verb: distractor(s) {compatible} are also grammatical here."
                            )
                    return None

        # Fixed noun/adjective/verb + preposition collocations ("Interesse
        # AN", "fähig ZU", "warten AUF"): idiomatically fixed government, not
        # a semantically open choice among near-synonym prepositions, even
        # though a heuristic with no collocation dictionary cannot tell that
        # apart from genuinely open preposition choice by shape alone.
        # Verifying the *specific* preposition is correct for the governing
        # word is exactly what a real dictionary lookup (out of scope here)
        # would do; this only avoids guessing wrong in the meantime.
        if topic and topic.confusion_group == "praepositionen_kasus_fest":
            return None

        # Fixed two-part connectors ("sowohl ... als auch", "weder ... noch",
        # ...): the first half, present anywhere in the prompt, uniquely
        # forces the second half, regardless of how many other
        # same-shape conjunctions might otherwise seem to fit.
        prompt_tokens = re.findall(r"[A-Za-zÄÖÜäöüß]+", item.prompt)
        prompt_tokens_lower = {t.lower() for t in prompt_tokens}
        if prompt_tokens_lower & CORRELATIVE_TRIGGERS:
            return None

        # Verb-final / periphrastic governed slot: an auxiliary or modal verb
        # elsewhere in the sentence already occupies the clause's one
        # finite-verb slot (German is V2), so the gap is a participle,
        # infinitive, or aux-selection slot whose correctness is a
        # construction fact, not an open lexical preference. Also applies
        # whenever the topic itself is explicitly about compound-tense
        # auxiliary or voice selection (``morph_spec`` has ``Voice``,
        # ``Aspect == "Perf"``, or ``Tense == "PastPerf"``): choosing the
        # right auxiliary there is Layer 2's job, not an under-informed
        # lexical-heterogeneity guess here.
        #
        # "sein" is deliberately excluded from the scan itself: unlike
        # haben/werden/lassen/the modals, a bare copula "ist"/"war" does not
        # reliably signal a governed participle/infinitive slot -- it just as
        # often introduces a genuinely open predicate adjective ("Das Wetter
        # ist ___." -- kalt/warm/gut/schön are all open choices even though
        # "ist" is right there).
        if (
            topic
            and topic.morph_spec
            and (
                topic.morph_spec.get("Voice")
                or topic.morph_spec.get("Aspect") == "Perf"
                or topic.morph_spec.get("Tense") == "PastPerf"
            )
        ):
            return None
        if any(
            t.lower() in IRREGULAR_VERB_LEMMA and IRREGULAR_VERB_LEMMA[t.lower()] != "sein"
            for t in prompt_tokens
        ):
            return None

        # Open lexical slot: no case, determiner, correlative, periphrastic,
        # or resolvable subject anchor at all. Ambiguous if the answer and at
        # least one distractor are genuinely different lexemes (not just
        # different inflections), since nothing here rules the distractor
        # out.
        heterogeneous = [
            d.text for d in item.distractors if not _same_lexeme(item.proposed_answer, d.text)
        ]
        if heterogeneous:
            return (
                "Prompt provides no governing preposition, verb, or determiner "
                f"that narrows the gap to one lexeme: {heterogeneous} would also "
                "be grammatical here."
            )
        return None
