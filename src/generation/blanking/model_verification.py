"""Model-backed verification pass over finished, already-accepted items --
the backstop this project's own diagnosed defect (docs/audits/
cycle-06-report.md class G) says a rule can never fully anticipate.

## Why this exists, in one carrier sentence

    Nach der Arbeit treue ich mich mit Lisa auf einen Kaffee in der Stadt.

``treue`` is not a German word; ``treffe`` was meant. ``de_core_news_sm``
nonetheless tags it a finite verb, ``Person=1|Number=Sing``, agreeing
perfectly with ``ich`` -- every structural check upstream of this module
(carrier validation's subject-verb agreement check included) passed it, and
it reached an accepted item. Every fix this project has made so far has been
a rule aimed at a defect an audit had already observed; a rule cannot catch
what nobody has anticipated yet. This module is the backstop: instead of one
more closed-class table, it asks a model to read the finished item the way a
learner or a textbook editor would, and to say plainly whether it is sound.

## What this module does, and does not, replace

Nothing upstream changes. Carrier validation, the selectors, paradigm
reconstruction and the uniqueness gate in ``pipeline.py`` all still run
first, exactly as before -- this module runs LAST, over the items that
already survived every one of those, and asks three questions per item that
no closed-class table or dependency-parse rule can answer on its own:

1. Is the finished sentence (gap filled with the stated answer) correct,
   natural German that a textbook could print?
2. Given only the prompt and the cue -- never the topic -- is the stated
   answer the ONLY correct filler for the gap?
3. Does every word in the finished sentence exist in German as a word a
   native speaker would actually use?

Question 2 is where most of the remaining value is. The rule-based
uniqueness gate in ``uniqueness.py`` already covers a handful of closed
classes it has a name for: free-choice modals, unanchored personal
pronouns, open-class plural nouns. It has no opinion on a tense that is free
where the surrounding discourse wants one particular tense, a lexeme that
fits exactly as well as the intended one, or an ambiguity the carrier's own
wording happens to create -- exactly the cases this model pass exists to
catch, because they are not a closed class at all, just contextual
solvability, which is what a competent reader (or model) judges directly
rather than what a table enumerates.

## Question 3: the carrier word that is not a word, docs/audits/cycle-08-report.md

    Ich wünsche mir, dass Stefan bald den Tennisschlüssel findet, den wir
    gestern im Park gesucht haben.

``Tennisschlüssel`` ("tennis key") is not a word anyone means; ``Tennis-
schläger`` (tennis racket) was intended. Both halves -- ``Tennis`` and
``Schlüssel`` -- are individually real German words, so the compound
splitter in ``selectors._cue_is_real_word`` and every dictionary check
upstream of this module pass it cleanly: neither operates on the
COMPLETED sentence's actual meaning, only on whether a string resolves to
known morphemes. The cycle-08 audit found this one bad carrier had already
reached three separate accepted items (``kasus_akkusativ_formen``,
``verb_praesens_regelm``, ``relativsatz_nom_akk``) before the model
verification pass existed to catch it.

A frequency-list gate was tried and rejected during that audit, deliberately
not adopted here: ``Tennisschlüssel`` is absent from the vendored 50k
frequency list, but so are ``Radweg``, ``Altkleidersammlung``,
``Einweihungsfest``, ``Aufräumaktion``, ``Mathehausaufgabe``,
``Lieblingsjacke``, ``Kulturzentrum`` and ``Ingwertee`` -- every one of them
a legitimate, freely-formed compound that appeared in an otherwise-accepted
item. A frequency floor cannot tell "uncommon but real" apart from
"nonexistent"; only a semantic judgment can, which is exactly what a model
call and nothing upstream of it can make. The live prompt (below) is
explicit with the model about this distinction -- it must not reject a word
merely for being uncommon, only for being wrong or nonexistent -- using
``Tennisschlüssel`` as the negative worked example and ``Radweg``/
``Altkleidersammlung``/``Einweihungsfest`` as positive ones that must pass.

The prompt never names or hints at the grammar topic (CLAUDE.md rule 2):
naming it would tell the model what "should" be tested and bias the
judgment toward accepting, exactly the failure mode rule 2 exists to
prevent everywhere else in this codebase. The model sees only the prompt
(with its gap), the cue if one exists, and the stated answer -- the same
three things, and nothing more, a learner actually facing the item would
have.

## Batching

Roughly ``DEFAULT_VERIFICATION_BATCH_SIZE`` (20) items ride in one prompt,
so a 300-item pilot costs about fifteen calls, not three hundred. The free
lane's real ceiling is 5 RPM (``GeminiLlmClient.FREE_LANE_RATE_LIMIT_PER_MINUTE``);
one call per item would take the better part of an hour for a run this
module is meant to finish in minutes. Batching is not an optimisation here,
it is what makes the pass usable at all on the free lane. Every batch is one
prompt inside ``GeminiLlmClient.generate_many`` (CLAUDE.md rule 4: every LLM
call goes through that one wrapper) so cost accounting, the spend ceiling,
and the two-lane routing apply exactly as they do to every other call in the
app.

## Degrading honestly

Two failure modes this module is deliberately built never to produce, both
named directly in the brief for this cycle:

1. **No API key configured.** ``verify_items`` with ``llm_client=None``
   performs no work and returns a report in which EVERY item's outcome is
   ``"not_run"``, with a reason that says plainly the check never executed.
   It never reports an item as ``"verified"`` just because nothing rejected
   it -- "nothing objected" and "a model actually read this and approved
   it" are different claims, and conflating them is the exact failure mode
   docs/audits/cycle-06-report.md and this cycle's brief both name as
   already having happened twice in this project's history.
2. **A malformed model response.** Unlike ``src.verification.layer_expander
   .AnswerSetExpander.verify_semantic_validity_many`` (a lower-stakes
   REFINEMENT layer on top of layers 1-4, whose own docstring says a
   missing verdict "must never reject an item that already passed every
   cheaper layer" and so fails open to ``valid=True``), this module
   deliberately does NOT fail open on a parse failure. This pass is
   described, in the brief that created it, as "the highest-value check in
   the system" and the dedicated backstop for a defect class nothing else
   catches; failing open here would silently reintroduce exactly the
   failure mode it exists to close -- an item nobody actually verified,
   reported as verified. A batch whose response cannot be parsed into
   exactly as many verdicts as items were sent, each with a boolean
   ``valid``, a boolean ``woerter_echt`` (the third question's own answer)
   and a resolvable ``index``, degrades every item in THAT batch to
   ``"not_run"`` (reason ``"malformed_model_response"``), never to
   ``"verified"``. Other, well-formed batches in the same run are
   unaffected -- ``_parse_batch_response`` operates one batch at a time.

``BudgetExceeded``, ``ServerUnavailableError``, ``PaidLaneForbiddenError``,
``BatchForbiddenError`` and ``MissingApiKeyError`` are caught around the
whole ``generate_many`` call (which either returns every batch's response
text or raises -- there is no partial result to salvage from a raised call)
and degrade the ENTIRE run to ``"not_run"`` for every item, each carrying a
reason naming which of the five occurred, for exactly the same "never
silently claim success" reason.

Every rejected item's model-given reason is kept (never discarded), so a
rejected item is diagnosable rather than a silent drop -- the same standard
CLAUDE.md 12's "report honestly" posture and ``pipeline.py``'s own four
skip/drop categories already hold this whole package to.

This script never queues a real Batch API job (``scripts/step6_blank_pilot.py``
has no ``--batch`` flag at all: real batch submission is unconditionally
forbidden for it), so the ``llm_client`` a caller hands to ``verify_items``
is expected to already be built with ``forbid_batch=True`` and
``forbid_paid_lane=False`` (``src.generation.blanking.sentence_source.
client_from_env`` already does this; the pilot script reuses that same
client and function for both generation and this verification pass, rather
than building a second one, so both share one cost log and one
rate-limiter state). This is a deliberate change from an earlier version of
this module, which expected ``forbid_paid_lane=True`` instead: that
forbade the paid lane outright, so once sentence generation had spent the
free lane's whole daily quota, this pass's own ``generate_many`` call
raised ``PaidLaneForbiddenError`` immediately, before a single verification
request went out, and every item degraded to ``"not_run"`` while the
script still exited 0 -- the exact failure ``scripts/step6_blank_pilot.py``
now refuses to let pass silently (see its own module docstring and
``main()``'s exit-code handling). ``forbid_batch=True`` keeps this pass
able to run, on the paid lane, synchronously, once the free lane closes.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.contracts import MODEL_VERIFY, BankItem
from src.llm.client import (
    BatchForbiddenError,
    BudgetExceeded,
    GeminiLlmClient,
    MissingApiKeyError,
    PaidLaneForbiddenError,
    ServerUnavailableError,
)

# ---------------------------------------------------------------------------
# The instruction, maintained in TWO languages, exactly the pattern
# ``sentence_source.py`` already established for its own live German
# instruction (see that module's own docstring for the full rationale, not
# repeated here): ``_INSTRUCTION_EN_REFERENCE_ONLY`` is documentation only,
# read by a human, never sent to the API and never imported into a live
# code path; ``_INSTRUCTION_DE_LIVE`` is the text ``build_batch_prompt``
# actually sends. German, because the items being judged are German and an
# English wrapper around German content was an earlier, separately-found
# quality regression in this project (see ``sentence_source.py``'s own
# docstring on the same point).
#
# As with ``sentence_source.py``: the two texts are independently phrased,
# idiomatic prose that say the same four things (judge each numbered task
# on its own, blind to which grammar it tests; ask both questions; the
# verdict format; the required JSON response shape) -- not a mechanical
# translation of each other, and no test can verify semantic equivalence
# between two natural-language texts in two different languages. What the
# tests below actually assert is only that both exist non-empty, that
# ``build_batch_prompt`` uses the German text and never the English one,
# and that the German text does not itself name a grammar topic.
# ---------------------------------------------------------------------------

_INSTRUCTION_EN_REFERENCE_ONLY = (
    "You are a German teacher reviewing finished fill-in-the-blank exercise "
    "items before learners ever see them. You will get several numbered "
    "tasks in a row; judge EACH one on its own, exactly as a German learner "
    "would see it: only the task with its gap, an optional cue in brackets, "
    "and the proposed answer. Never name or guess which grammar topic a "
    "task is testing -- that is not needed for your judgment and must play "
    "no part in it.\n\n"
    "For every task, answer three questions: (1) is the complete sentence, "
    "with the gap filled by the proposed answer, correct and natural "
    "German a textbook could print; (2) working out for yourself, from "
    "only the task and the cue, exactly as a learner facing just those two "
    "things would have to, is the proposed answer the ONLY correct filler "
    "for the gap, or would at least one other answer -- a different tense, "
    "an equally fitting word, a different reading the sentence allows just "
    "as well -- fit too. Only count an alternative a native speaker would "
    "actually use just as naturally; a technically-grammatical but stilted "
    "or dated option (e.g. the relative pronoun 'welcher' where 'der/die/"
    "das' is the plain modern choice) does not disqualify the proposed "
    "answer, but a genuinely equally idiomatic alternative does; (3) look "
    "at EVERY SINGLE WORD in the complete sentence: is it a word that "
    "actually exists in German and that a native speaker would really use? "
    "Freely-formed compounds are completely normal German and must NOT be "
    "rejected merely for being uncommon -- 'Radweg' (bike path), "
    "'Altkleidersammlung' (used-clothing collection) and 'Einweihungsfest' "
    "(housewarming party) are all perfectly good German and must pass this "
    "question even though none of them is a high-frequency word. Reject a "
    "word here only when its MEANING is wrong or it does not exist at all "
    "-- for example 'Tennisschlüssel' ('tennis key') is not a real word; "
    "'Tennisschläger' (tennis racket) was clearly meant, and this question "
    "must get a no for that sentence. A task is valid only if all three "
    "answers are yes; otherwise give a short, concrete reason naming which "
    "question failed and why. Respond with ONLY a JSON object of the given "
    "shape, one verdict per task carrying both the overall verdict and the "
    "third question's own answer, indexed to match the task numbers, and "
    "nothing else."
)

_INSTRUCTION_DE_LIVE = (
    "Du bist Deutschlehrer/in und prüfst fertige Lückentext-Aufgaben, bevor "
    "sie Lernenden gezeigt werden. Du bekommst mehrere nummerierte Aufgaben "
    "hintereinander; beurteile JEDE Aufgabe für sich, genau so, wie ein "
    "Deutschlernender sie sehen würde: nur die Aufgabe mit der Lücke, ein "
    "Hinweis in Klammern (falls vorhanden), und die vorgeschlagene "
    "Antwort. Nenne oder errate an keiner Stelle, welches Grammatikthema "
    "geprüft wird -- das spielt für deine Beurteilung keine Rolle und darf "
    "sie auch nicht beeinflussen.\n\n"
    "Beantworte zu jeder Aufgabe drei Fragen:\n"
    "1. Ist der VOLLSTÄNDIGE Satz (Lücke durch die vorgeschlagene Antwort "
    "ersetzt) korrektes, natürliches Deutsch, wie es in einem Lehrbuch "
    "stehen könnte?\n"
    "2. Überlege selbst, allein anhand von Aufgabe und Hinweis, welche "
    "Wörter in die Lücke passen würden -- genau so, wie ein Lernender es "
    "tun müsste, der nur diese beiden Angaben hat. Ist die vorgeschlagene "
    "Antwort dabei die EINZIGE richtig passende Lösung, oder gäbe es "
    "mindestens eine ebenso richtige, ebenso natürliche Alternative, zum "
    "Beispiel eine andere Zeitform, ein anderes ebenso passendes Wort, "
    "oder eine andere Bedeutung, die der Satz genauso gut zulässt? Zähle "
    "dabei nur eine Alternative, die ein/e Muttersprachler/in tatsächlich "
    "genauso natürlich verwenden würde. Eine Alternative, die zwar "
    "grammatisch zulässig, aber selten, gestelzt oder veraltet wirkt -- "
    "zum Beispiel das Relativpronomen 'welcher' anstelle von 'der/die/"
    "das', wo beide grammatisch möglich sind, 'welcher' im heutigen "
    "Sprachgebrauch aber ungewöhnlich ist --, zählt NICHT als zweite "
    "richtige Lösung; eine Alternative, die genauso alltäglich und "
    "natürlich ist wie die vorgeschlagene Antwort, zählt dagegen sehr "
    "wohl.\n"
    "3. Sieh dir JEDES EINZELNE WORT im vollständigen Satz an: Ist es ein "
    "Wort, das es im Deutschen wirklich gibt und das ein/e "
    "Muttersprachler/in tatsächlich so verwenden würde? Frei gebildete, "
    "aber sinnvolle Komposita sind im Deutschen völlig normal und dürfen "
    "NICHT allein deswegen abgelehnt werden, weil sie selten oder "
    "ungewöhnlich sind -- 'Radweg', 'Altkleidersammlung' und "
    "'Einweihungsfest' sind zum Beispiel völlig korrektes Deutsch und "
    "müssen diese Frage mit Ja beantwortet bekommen, obwohl keins davon "
    "zu den häufigsten Wörtern gehört. Lehne ein Wort hier nur ab, wenn "
    "seine BEDEUTUNG falsch ist oder es das Wort schlicht nicht gibt -- "
    "'Tennisschlüssel' zum Beispiel ist kein Wort, das es gibt (gemeint "
    "war offenbar 'Tennisschläger', der Schläger, mit dem man Tennis "
    "spielt), und für einen Satz mit diesem Wort muss diese Frage mit "
    "Nein beantwortet werden.\n\n"
    'Eine Aufgabe ist nur dann gültig ("valid": true), wenn ALLE DREI '
    'Fragen mit Ja beantwortet sind. Gib zusätzlich unter "woerter_echt" '
    "gesondert an, ob Frage 3 für sich allein mit Ja beantwortet wurde "
    '(unabhängig von "valid"). Bei "valid": false oder "woerter_echt": '
    'false nenne unter "reason" kurz und konkret auf Deutsch, welche der '
    "drei Fragen mit Nein beantwortet wurde und warum, so dass das "
    "Problem auch ohne erneutes Lesen der Aufgabe klar wird.\n\n"
    "Antworte ausschließlich mit einem JSON-Objekt dieser exakten Form, "
    "ohne Markdown-Codeblock:\n"
    '{"verdicts": [{"index": 1, "valid": true, "woerter_echt": true, '
    '"reason": null}, {"index": 2, "valid": false, "woerter_echt": true, '
    '"reason": "kurze Begründung auf Deutsch"}]}\n'
    "Die Liste muss genau so viele Einträge enthalten wie Aufgaben unten, "
    'jeweils mit "index" gleich der Aufgabennummer, in beliebiger '
    "Reihenfolge."
)

DEFAULT_VERIFICATION_BATCH_SIZE = 20

# Reason slugs for outcomes where no model verdict exists at all -- these are
# never per-item free text from the model (that is what a "rejected" verdict
# carries), they are this module's own, fixed, machine-stable strings so a
# caller can group/count on them without depending on free-text phrasing.
REASON_NO_CLIENT = "no_llm_client_configured"
REASON_MALFORMED_RESPONSE = "malformed_model_response"
REASON_BUDGET_EXCEEDED = "budget_exceeded"
REASON_SERVER_UNAVAILABLE = "server_unavailable"
REASON_PAID_LANE_FORBIDDEN = "paid_lane_forbidden"
REASON_BATCH_FORBIDDEN = "batch_forbidden"
REASON_MISSING_API_KEY = "missing_api_key"

# Maps the transport/budget exceptions ``verify_items`` degrades a whole run
# on to their reason slug -- checked in this order (a subclass relationship
# does not exist among these five, so order does not affect matching, only
# readability matches the order they are documented in above).
_DEGRADE_EXCEPTIONS: tuple[tuple[type[Exception], str], ...] = (
    (BudgetExceeded, REASON_BUDGET_EXCEEDED),
    (ServerUnavailableError, REASON_SERVER_UNAVAILABLE),
    (PaidLaneForbiddenError, REASON_PAID_LANE_FORBIDDEN),
    (BatchForbiddenError, REASON_BATCH_FORBIDDEN),
    (MissingApiKeyError, REASON_MISSING_API_KEY),
)
_DEGRADE_EXCEPTION_TYPES: tuple[type[Exception], ...] = tuple(
    exc_type for exc_type, _ in _DEGRADE_EXCEPTIONS
)


class ItemVerdict(BaseModel):
    """One item's outcome from this pass, aligned by position with the
    ``items`` list ``verify_items`` was called with.

    ``"not_run"`` is a first-class outcome, not an error swallowed into
    ``"verified"`` -- see the module docstring's "degrading honestly"
    section. ``reason`` is ``None`` only for ``"verified"``; every
    ``"rejected"`` or ``"not_run"`` verdict carries one, either the model's
    own German explanation (rejected) or one of this module's fixed reason
    slugs (not_run).
    """

    model_config = ConfigDict(frozen=True)
    outcome: Literal["verified", "rejected", "not_run"]
    reason: str | None = None


@dataclass(frozen=True)
class ModelRejection:
    """One item the model rejected, carrying enough to diagnose why without
    naming the topic (the model was never told it, so it cannot appear in
    ``reason``) -- mirrors ``pipeline.UniquenessSkip``'s shape and purpose:
    a solvability/quality judgment about an item that built and reconstructed
    cleanly, kept as its own record rather than folded into a generic skip
    bucket, so CLAUDE.md 12's report-honestly standard holds here too."""

    topic_id: str
    prompt: str
    accepted_answers: tuple[str, ...]
    reason: str


@dataclass
class VerificationReport:
    """Everything a caller (``scripts/step6_blank_pilot.py``, or a test)
    needs to report this pass's outcome honestly: three DISJOINT counts that
    must always sum to ``len(items)`` -- verified, rejected (by the model,
    with a reason), and not-run (the pass could not execute, with a reason)
    -- never collapsed into a single "how many are fine" number, per this
    cycle's own brief ("Report the counts separately")."""

    attempted: bool
    verdicts: list[ItemVerdict] = field(default_factory=list)
    rejections: list[ModelRejection] = field(default_factory=list)

    @property
    def verified_count(self) -> int:
        return sum(1 for v in self.verdicts if v.outcome == "verified")

    @property
    def rejected_count(self) -> int:
        return sum(1 for v in self.verdicts if v.outcome == "rejected")

    @property
    def not_run_count(self) -> int:
        return sum(1 for v in self.verdicts if v.outcome == "not_run")

    @property
    def rejected_reasons(self) -> Counter[str]:
        """Model-given rejection reasons, grouped by exact reason text (the
        brief's "reasons grouped"). Free text from the model will not merge
        two differently-phrased explanations of the same underlying defect
        into one bucket -- an inherent limit of grouping on free text rather
        than a closed reason-code table, the same honest limit ``pipeline.py``
        accepts for its own model-adjacent counters. ``rejections`` (the full
        per-item list, prompt and answer included) is what makes an
        individual rejection diagnosable beyond the grouped count."""
        return Counter(r.reason for r in self.rejections)

    @property
    def not_run_reasons(self) -> Counter[str]:
        """Why the pass could not run, grouped by this module's fixed reason
        slugs (never free text, unlike ``rejected_reasons`` -- see
        ``ItemVerdict``'s own docstring)."""
        return Counter(
            v.reason or "unknown_not_run_reason" for v in self.verdicts if v.outcome == "not_run"
        )


def _format_item_block(number: int, item: BankItem) -> str:
    """One numbered task block: the gap, the cue if any, and the stated
    answer -- and NOTHING else. Deliberately excludes every other
    ``BankItem`` field (``topic_id``, ``rule_hint``, ``facet``,
    ``confusion_group``, ``domain``, ``carrier_lemmas``): several of those
    (``rule_hint`` especially) describe the grammar being tested, and
    sending them would be exactly the topic leak CLAUDE.md rule 2 forbids,
    now aimed at a model prompt instead of a learner-facing one -- the
    model must judge the item the way a learner actually would, seeing only
    what a learner sees."""
    lines = [f"Aufgabe {number}:", f"Lücke: {item.prompt}"]
    lines.append(f"Hinweis: ({item.cue})" if item.cue else "Hinweis: (kein Hinweis)")
    lines.append(f"Vorgeschlagene Antwort: {' / '.join(item.accepted_answers)}")
    return "\n".join(lines)


def build_batch_prompt(batch: Sequence[BankItem]) -> str:
    """The full prompt for one batch: the live German instruction
    (``_INSTRUCTION_DE_LIVE``) followed by every item's numbered task block,
    numbered 1..len(batch) in the order given."""
    blocks = [_format_item_block(i + 1, item) for i, item in enumerate(batch)]
    return _INSTRUCTION_DE_LIVE + "\n\n" + "\n\n".join(blocks) + "\n"


def _chunk(items: Sequence[BankItem], size: int) -> list[list[BankItem]]:
    """Split ``items`` into groups of at most ``size``, preserving order.
    ``size <= 0`` is treated as "one chunk" (mirrors ``pilot._chunk_requests``'
    identical fallback), a tuning knob never a correctness gate."""
    if not items:
        return []
    if size <= 0:
        return [list(items)]
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _strip_code_fence(text: str) -> str:
    """Gemini routinely wraps JSON output in a markdown code fence even when
    asked for raw JSON (confirmed against the live endpoint elsewhere in
    this codebase -- see ``sentence_source._strip_code_fence`` and
    ``src.verification.layer_expander._parse_semantic_response``, the same
    fix against the same live behaviour, kept as its own small local copy
    here rather than importing a private helper across modules)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```")
        stripped = stripped.removesuffix("```").strip()
    return stripped


def _parse_batch_response(text: str, expected_count: int) -> list[tuple[bool, str | None]] | None:
    """Parse one batch's response into ``expected_count`` ``(valid, reason)``
    pairs, ordered to match the batch's own task numbering (1-indexed).

    Returns ``None`` -- "malformed", the whole batch degrades to
    ``"not_run"`` -- unless the response is a JSON object whose
    ``"verdicts"`` is a list of EXACTLY ``expected_count`` entries, each a
    dict with an integer ``"index"``, a boolean ``"valid"`` AND a boolean
    ``"woerter_echt"`` (the third question -- every word in the completed
    sentence is a real German word someone would actually use -- see the
    module docstring's ``Tennisschlüssel`` example), and whose indices are
    exactly ``{1, ..., expected_count}`` with no duplicate and no gap. This
    is deliberately strict (module docstring: this pass does not fail open
    on a parse defect) -- a response missing one task's verdict, missing
    the third question's own answer, or numbering two tasks the same
    index, is exactly the kind of silent-drop failure this module exists
    to never produce for the candidate items themselves, so it must not
    reproduce that failure mode in its own transport parsing either.

    ``woerter_echt`` is kept as its OWN required field rather than folded
    silently into ``valid`` (which the live prompt also asks the model to
    set to the AND of all three questions) for exactly this reason: a
    response that carries ``valid`` but omits ``woerter_echt`` entirely
    must be rejected as malformed, not silently trusted on the model's own
    aggregate bit alone -- the same "verify, don't just trust a single
    flag" posture ``_cue_equals_answer`` already applies one layer down in
    ``blanker.py``. The final per-item validity this function returns is
    the AND of both fields, so a model response that answers ``valid:
    true`` but ``woerter_echt: false`` (an internally inconsistent
    response, but not a malformed one) is still correctly treated as a
    rejection rather than trusted on the aggregate field alone."""
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    raw_verdicts = payload.get("verdicts")
    if not isinstance(raw_verdicts, list) or len(raw_verdicts) != expected_count:
        return None

    by_index: dict[int, tuple[bool, str | None]] = {}
    for entry in raw_verdicts:
        if not isinstance(entry, dict):
            return None
        index = entry.get("index")
        # bool is an int subclass in Python; explicitly excluded so a
        # stray {"index": true, ...} is never silently accepted as index 1.
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        valid = entry.get("valid")
        if not isinstance(valid, bool):
            return None
        words_real = entry.get("woerter_echt")
        if not isinstance(words_real, bool):
            return None
        reason = entry.get("reason")
        reason = reason if isinstance(reason, str) and reason.strip() else None
        if index in by_index:
            return None
        by_index[index] = (valid and words_real, reason)

    if set(by_index) != set(range(1, expected_count + 1)):
        return None
    return [by_index[i] for i in range(1, expected_count + 1)]


def verify_items(
    items: Sequence[BankItem],
    llm_client: GeminiLlmClient | None,
    *,
    batch_size: int = DEFAULT_VERIFICATION_BATCH_SIZE,
) -> VerificationReport:
    """Run the model verification pass over ``items``, batched
    ``batch_size`` at a time, and return a report whose three counts
    (verified/rejected/not_run) always sum to ``len(items)``.

    ``llm_client=None`` (module docstring's degrade case 1) performs no
    network activity at all and returns every item as ``"not_run"`` with
    ``REASON_NO_CLIENT``. A transport/budget failure that survives
    ``GeminiLlmClient``'s own bounded retries (module docstring's degrade
    case 2's exception list) degrades the ENTIRE run the same way, since a
    raised ``generate_many`` call leaves no partial per-batch result to
    salvage. A per-batch malformed response degrades only that batch's own
    items, leaving every other, well-formed batch's real verdicts intact.
    """
    if not items:
        return VerificationReport(attempted=llm_client is not None)

    if llm_client is None:
        not_run_verdicts = [ItemVerdict(outcome="not_run", reason=REASON_NO_CLIENT) for _ in items]
        return VerificationReport(attempted=False, verdicts=not_run_verdicts)

    batches = _chunk(items, batch_size)
    prompts = [build_batch_prompt(batch) for batch in batches]

    try:
        response_texts = llm_client.generate_many(
            prompts, model=MODEL_VERIFY, purpose="item_verification"
        )
    except _DEGRADE_EXCEPTION_TYPES as exc:
        degrade_reason = next(
            slug for exc_type, slug in _DEGRADE_EXCEPTIONS if isinstance(exc, exc_type)
        )
        degraded_verdicts = [ItemVerdict(outcome="not_run", reason=degrade_reason) for _ in items]
        return VerificationReport(attempted=True, verdicts=degraded_verdicts)

    verdicts: list[ItemVerdict] = []
    rejections: list[ModelRejection] = []
    for batch, response_text in zip(batches, response_texts, strict=True):
        parsed = _parse_batch_response(response_text, len(batch))
        if parsed is None:
            verdicts.extend(
                ItemVerdict(outcome="not_run", reason=REASON_MALFORMED_RESPONSE) for _ in batch
            )
            continue
        for item, (valid, model_reason) in zip(batch, parsed, strict=True):
            if valid:
                verdicts.append(ItemVerdict(outcome="verified"))
                continue
            final_reason = model_reason or "Kein Grund vom Modell angegeben."
            verdicts.append(ItemVerdict(outcome="rejected", reason=final_reason))
            rejections.append(
                ModelRejection(
                    topic_id=item.topic_id,
                    prompt=item.prompt,
                    accepted_answers=tuple(item.accepted_answers),
                    reason=final_reason,
                )
            )

    return VerificationReport(attempted=True, verdicts=verdicts, rejections=rejections)
