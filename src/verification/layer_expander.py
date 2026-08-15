"""Answer-set expander: cheap contraction expansion plus the model-backed
semantic/collocation check docs/audits/stage-00-quota.md's routing table
calls "Answer-Set Expansion (Stage 4)".

Two independent responsibilities live here, both about the same question --
"is the accepted-answers set for this item right?" -- from opposite ends:

- ``expand_answers`` is free and deterministic: known contractions and
  sentence-initial capitalisation, so an equally-correct variant of the
  proposed answer is not rejected as wrong at review or grading time.
- ``verify_semantic_validity`` is the one model-backed layer in the chain
  (``gemini-3.7-flash`` / ``MODEL_VERIFY``, per the routing table), reserved
  for exactly the defect class no regex or closed-class table can catch:
  wrong connectors, broken collocations, hallucinated tokens, and carrier
  sentences that are structurally fine but semantically incoherent. See
  docs/audits/stage-04-pilot-2026-08-14.md, whose items 34, 35, 39, 40, 41,
  43 and 44 are all in this class -- the audit's finding was that this class
  accounted for the largest share of defects reaching acceptance specifically
  because this layer existed but was never wired into
  ``src.verification.pipeline.VerificationPipeline``.
"""

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import MODEL_VERIFY, CandidateItem, Topic

if TYPE_CHECKING:
    from src.llm.client import GeminiLlmClient

# Surface forms whose Konjunktiv II reading is unambiguous -- no legitimate
# Indikativ/Präteritum reading exists for the same spelling (unlike
# "wollte"/"sollte", which are genuinely ambiguous between preterite
# indicative and Konjunktiv II for weak-conjugation modals, and are
# deliberately left out rather than guessed). Used to give a Konjunktiv II
# form a distinct identity from its own lemma's indicative forms: "würden"
# (unambiguously subjunctive/conditional) must not be treated as the same
# "form" as "werden" (indicative) for a topic testing Indikativ Futur I,
# even though both share the lemma "werden" in
# ``layer2_morphology.IRREGULAR_VERB_LEMMA``.
_KONJUNKTIV_II_UNAMBIGUOUS_FORMS: frozenset[str] = frozenset(
    {
        "wäre",
        "wärst",
        "wären",
        "wäret",
        "hätte",
        "hättest",
        "hätten",
        "hättet",
        "würde",
        "würdest",
        "würden",
        "würdet",
        "könnte",
        "könntest",
        "könnten",
        "könntet",
        "müsste",
        "müsstest",
        "müssten",
        "müsstet",
        "dürfte",
        "dürftest",
        "dürften",
        "dürftet",
    }
)


def _verb_form_key(answer: str) -> str | None:
    """The verb "form" ``check_ambiguity``/``filter_alternatives_by_target_form``
    compare candidates on: the lemma from ``IRREGULAR_VERB_LEMMA``, suffixed
    ``:Sub`` when the surface form is unambiguously Konjunktiv II. ``None``
    if the lemma cannot be resolved at all (an unrecognised or regular verb
    form), the signal this codebase's "skip rather than guess" philosophy
    treats as "no opinion", not as a mismatch.
    """
    from src.verification.layer2_morphology import IRREGULAR_VERB_LEMMA

    lower = answer.strip().lower()
    lemma = IRREGULAR_VERB_LEMMA.get(lower)
    if lemma is None:
        return None
    return f"{lemma}:Sub" if lower in _KONJUNKTIV_II_UNAMBIGUOUS_FORMS else lemma


class SemanticVerificationResult(BaseModel):
    """Verdict from the model-backed semantic/collocation check.

    ``valid=True`` with no ``additional_accepted_answers`` is also what a
    caller gets when the check could not run at all (no client configured,
    budget ceiling reached, unparseable response): a missing verdict must
    never reject an item that already passed every cheaper layer, so absence
    of signal degrades to "accept", never to "reject" -- the same fail-open
    posture ``ProductionGrader`` uses for its own model call.
    """

    model_config = ConfigDict(frozen=True)
    valid: bool = True
    reason: str | None = None
    additional_accepted_answers: list[str] = Field(default_factory=list)


class AnswerSetExpander:
    """Expands valid German grammatical variants and, when an LLM client is
    supplied, runs the model-backed semantic/collocation check."""

    CONTRACTION_MAP: dict[str, list[str]] = {
        "ans": ["an das"],
        "an das": ["ans"],
        "aufs": ["auf das"],
        "auf das": ["aufs"],
        "beim": ["bei dem"],
        "bei dem": ["beim"],
        "im": ["in dem"],
        "in dem": ["im"],
        "ins": ["in das"],
        "in das": ["ins"],
        "vom": ["von dem"],
        "von dem": ["vom"],
        "zum": ["zu dem"],
        "zu dem": ["zum"],
        "zur": ["zu der"],
        "zu der": ["zur"],
    }

    # Fallback for topics whose target feature has no morphological decoder
    # in this codebase (e.g. comparative/superlative degree) -- see
    # ``check_ambiguity``. Kept generous enough that it only fires on
    # genuinely wide answer sets, never on ordinary contraction-expansion
    # synonyms; topics with a real form decoder never reach this fallback.
    AMBIGUITY_COUNT_THRESHOLD: int = 5

    SYSTEM_PROMPT = (
        "Du bist ein erfahrener Deutschlehrer und prüfst Lückentext-Aufgaben "
        "gegen, nachdem sie bereits automatisch auf Struktur, Morphologie und "
        "Mehrdeutigkeit geprüft wurden. Deine einzige Aufgabe: beurteile, ob "
        "der VOLLSTÄNDIGE Satz (Lücke durch die vorgeschlagene Antwort "
        "ersetzt) für einen Muttersprachler semantisch kohärent, idiomatisch "
        "und inhaltlich sinnvoll ist -- und liste jede weitere Antwort, die "
        "ebenfalls grammatisch und inhaltlich korrekt wäre.\n\n"
        "Typische Fehler, auf die du achten musst: falsche Konnektoren "
        "(z.B. 'demnach' statt 'deshalb' oder 'folglich'), falsche "
        "Kollokationen (z.B. 'Kritik auf sich nehmen' statt 'auf sich "
        "ziehen'), erfundene oder unpassende Wörter, ungrammatische "
        "Konstruktionen, und inhaltlich widersprüchliche oder unlogische "
        "Sätze.\n\n"
        "Antworte ausschließlich im JSON-Format, ohne Markdown-Codeblock:\n"
        '{"valid": true, "reason": null, "additional_accepted_answers": []}\n'
        'Bei einem Fehler: {"valid": false, "reason": "kurze Begründung auf '
        'Deutsch", "additional_accepted_answers": []}'
    )

    @classmethod
    def expand_answers(cls, item: CandidateItem) -> list[str]:
        """Expand proposed answer into deduplicated accepted answers set."""
        accepted = [item.proposed_answer]
        raw_ans = item.proposed_answer.strip()

        # 1. Contraction expansion
        lower_ans = raw_ans.lower()
        if lower_ans in cls.CONTRACTION_MAP:
            for variant in cls.CONTRACTION_MAP[lower_ans]:
                if raw_ans[0].isupper():
                    expanded_v = variant.capitalize()
                else:
                    expanded_v = variant
                if expanded_v not in accepted:
                    accepted.append(expanded_v)

        # 2. Sentence initial position check
        if item.prompt.strip().startswith("___"):
            capitalized = raw_ans.capitalize()
            if capitalized not in accepted:
                accepted.append(capitalized)

        return accepted

    @classmethod
    def check_ambiguity(cls, accepted_answers: list[str], topic: Topic) -> str | None:
        """Return an ``ambiguity`` rejection reason if ``accepted_answers``
        spans more than one distinct form of ``topic``'s target feature, or
        ``None`` if the set is internally consistent (or the form cannot be
        derived at all, in which case there is nothing to check against).

        docs/audits/stage-04-pilot-2026-08-15.md fix 1 /
        02-content-pipeline.md: "Threshold on distinct forms, not on answer
        count." Ten answers are fine when they are ten lexemes carrying one
        form (``kasus_genitiv_formen``: des, eines, meines... all genitive);
        two answers are fatal when they are two forms (``futur_i``: 'werden'
        vs 'können', a future auxiliary vs a modal).

        Two identity signals, each gated on what the topic actually declares
        itself to be testing -- checking a signal the topic does not care
        about would reject good items. ``kasus_genitiv_formen`` legitimately
        spans both definite-article and ein-word genitive forms (it has no
        ``ArtType`` tag: Case is its only target feature), so the
        determiner-Definite check below must not fire for it, only for
        topics that actually declare ``ArtType``:

        - **Verb-category topics** (``morph_spec`` fixes Tense/Mood/Voice/
          Aspect, or ``syntax_tags['VerbType']`` is set): the target feature
          is the LEMMA (the specific auxiliary/modal) plus indicative-vs-
          unambiguously-Konjunktiv-II (``_verb_form_key``), so "würden" does
          not count as the same form as "werden" just because both are the
          lemma "werden".
        - **Determiner-category topics that declare ``syntax_tags['ArtType']``**
          (definite, indefinite, possessive, negative article topics): the
          target feature is the specific ArtType (Def/Ind/Neg/Poss), decoded
          via ``facets.determiner_art_type`` -- WHICH ein-word stem matched,
          not just whether one did, so a possessive topic accepting a bare
          indefinite ("Eine" is not a possessive) is caught, not just a
          definite-vs-everything-else split.
        - **Degree-based topics** (``morph_spec['Degree']`` set, e.g.
          comparative/superlative): no morphological decoder for comparison
          endings exists in this codebase, so this falls back to a plain
          count threshold (``AMBIGUITY_COUNT_THRESHOLD``) -- the audit's own
          sanctioned fallback for when "the form cannot be derived".
        - Every other topic shape has no cheap, reliable form signal in this
          codebase's closed-class tables, and is left unchecked here rather
          than guessed -- exactly this module's and facets.py's shared
          philosophy of skipping over guessing.
        """
        if len(accepted_answers) < 2:
            return None

        morph_spec = topic.morph_spec or {}
        syntax_tags = topic.syntax_tags or {}

        if any(k in morph_spec for k in ("Tense", "Mood", "Voice", "Aspect")) or syntax_tags.get(
            "VerbType"
        ):
            forms = {key for a in accepted_answers if (key := _verb_form_key(a)) is not None}
            if len(forms) > 1:
                return (
                    "Accepted answers span more than one verb form "
                    f"({', '.join(sorted(forms))}), so the item does not "
                    "consistently test its target tense/mood."
                )
            return None

        if syntax_tags.get("ArtType"):
            from src.taxonomy.facets import determiner_art_type

            forms = {determiner_art_type(a) for a in accepted_answers}
            forms.discard("Unk")
            if len(forms) > 1:
                return (
                    f"Accepted answers span more than one determiner type "
                    f"({', '.join(sorted(forms))}), so the item does not "
                    "consistently test one article type."
                )
            return None

        if morph_spec.get("Degree") and len(accepted_answers) > cls.AMBIGUITY_COUNT_THRESHOLD:
            return (
                f"{len(accepted_answers)} accepted answers exceed the fallback "
                f"ambiguity threshold ({cls.AMBIGUITY_COUNT_THRESHOLD}) for a "
                "topic whose degree/comparison form cannot be verified directly."
            )

        return None

    @classmethod
    def filter_alternatives_by_target_form(
        cls, alternatives: list[str], item: CandidateItem, topic: Topic
    ) -> list[str]:
        """Drop any semantic-layer-proposed alternative that does not carry
        ``item.proposed_answer``'s own target-feature form. An alternative
        that changes what is being tested is not an alternative correct
        answer, it is a different exercise (docs/audits/
        stage-04-pilot-2026-08-15.md fix 2 / 02-content-pipeline.md:
        "Expansion is constrained to the target form").

        Reuses exactly the identity signals ``check_ambiguity`` gates on,
        applied per-candidate against ``proposed_answer`` as the reference
        (it already passed layers 1-4, so it is a trustworthy anchor for
        "what form is this item actually testing") rather than mutual
        consistency across the whole set. Unresolvable candidates -- and an
        unresolvable reference -- are kept rather than dropped: this is a
        repair pass, not a gate, and ``check_ambiguity`` is the backstop
        that catches whatever slips through here.
        """
        if not alternatives:
            return []

        morph_spec = topic.morph_spec or {}
        syntax_tags = topic.syntax_tags or {}
        reference = item.proposed_answer

        if any(k in morph_spec for k in ("Tense", "Mood", "Voice", "Aspect")) or syntax_tags.get(
            "VerbType"
        ):
            ref_key = _verb_form_key(reference)
            if ref_key is None:
                return alternatives
            return [a for a in alternatives if _verb_form_key(a) in (ref_key, None)]

        if syntax_tags.get("ArtType"):
            from src.taxonomy.facets import determiner_art_type

            ref_form = determiner_art_type(reference)
            if ref_form == "Unk":
                return alternatives
            return [a for a in alternatives if determiner_art_type(a) in (ref_form, "Unk")]

        return alternatives

    @classmethod
    def _build_user_prompt(cls, item: CandidateItem) -> str:
        filled = item.prompt.replace("___", item.proposed_answer)
        return (
            f"Aufgabe (mit Lücke): {item.prompt}\n"
            f"Vorgeschlagene Antwort: {item.proposed_answer}\n"
            f"Vollständiger Satz: {filled}\n\n"
            "Ist dieser Satz semantisch kohärent, idiomatisch und inhaltlich korrekt?"
        )

    @staticmethod
    def _parse_semantic_response(response_text: str) -> SemanticVerificationResult:
        """Parse the model's JSON verdict.

        Strips a markdown code fence first (the transport sends no
        ``response_mime_type``, so Gemini routinely wraps JSON in one -- see
        ``src.generation.batch_client._parse_response`` for the same fix
        against the same live behaviour). Any parse failure or unexpected
        shape degrades to ``valid=True`` rather than rejecting on a defect in
        this layer's own plumbing, not the candidate's German.
        """
        text = response_text.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```")
            text = text.removesuffix("```").strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return SemanticVerificationResult()
        if not isinstance(payload, dict):
            return SemanticVerificationResult()

        valid = payload.get("valid", True)
        if not isinstance(valid, bool):
            valid = True

        reason = payload.get("reason")
        reason = reason if isinstance(reason, str) else None

        extra = payload.get("additional_accepted_answers")
        additional = [a for a in extra if isinstance(a, str)] if isinstance(extra, list) else []

        return SemanticVerificationResult(
            valid=valid, reason=reason, additional_accepted_answers=additional
        )

    @classmethod
    def verify_semantic_validity_many(
        cls, items: list[CandidateItem], llm_client: "GeminiLlmClient"
    ) -> list[SemanticVerificationResult]:
        """Run the model-backed semantic/collocation check for many candidates
        as one logical call, returning verdicts in the same order as ``items``.

        Every call goes through ``src.llm.client.GeminiLlmClient`` (CLAUDE.md
        rule 4) via ``generate_many``: cost accounting, the spend ceiling, and
        the two-lane routing all apply exactly as they do to generation, and
        on the free lane items are dispatched concurrently instead of one
        ``generate()`` call per item in a loop -- the previous per-item loop
        was, along with generation's own per-item loop, the dominant cost of
        a slow pilot run (each paid-lane item alone queued for 1-3 minutes).

        Two errors this layer explicitly degrades the WHOLE group on rather
        than propagates, both confirmed live against the real API, not
        hypothetical:

        - ``BudgetExceeded`` -- CLAUDE.md 9: "Callers handle it by degrading,
          never by retrying."
        - ``ServerUnavailableError`` -- transient 5xx overload that survives
          the transport's own bounded retries (``SERVER_ERROR_MAX_RETRIES``);
          observed live as a sustained-enough outage on ``gemini-3.7-flash``
          to exhaust them.

        Both are infrastructure trouble, not a verdict on any candidate's
        German, and this layer is a refinement on top of layers 1-4, not a
        required gate: either one skips the check for every item in the
        group rather than rejecting, or crashing the whole batch on,
        candidates that already passed every free/cheap check.
        """
        if not items:
            return []
        from src.llm.client import BudgetExceeded, ServerUnavailableError

        prompts = [f"{cls.SYSTEM_PROMPT}\n\n{cls._build_user_prompt(item)}" for item in items]
        try:
            response_texts = llm_client.generate_many(
                prompts, model=MODEL_VERIFY, purpose="answer_expansion"
            )
        except (BudgetExceeded, ServerUnavailableError):
            return [SemanticVerificationResult() for _ in items]
        return [cls._parse_semantic_response(text) for text in response_texts]

    @classmethod
    def verify_semantic_validity(
        cls, item: CandidateItem, llm_client: "GeminiLlmClient"
    ) -> SemanticVerificationResult:
        """Single-candidate case of ``verify_semantic_validity_many``, kept
        for callers verifying exactly one item on its own (e.g. ``verify_item``)."""
        return cls.verify_semantic_validity_many([item], llm_client)[0]
