"""Pre-verification repair of recoverable candidate defects.

docs/audits/stage-04-recovery-plan.md fix A. Of the 84 rejections in
``batch_51fc18e48f7b``, 30 were thrown away because the model emitted a sloppy
*distractor list* alongside a perfectly correct German sentence: 26 for
"proposed answer is present among distractors" and 4 for "duplicate
distractors found".

Distractors are presentation metadata for the multiple-choice rendering. They
are not part of the sentence being tested, and they are not part of what the
learner must produce. Rejecting a correct carrier sentence because its
distractor list needs tidying discards the expensive part of the item to
punish the cheap part.

This module runs before layer 1 and normalises what can be normalised, so the
verification layers only ever judge defects that are actually about the item.
Repair is deliberately conservative: it only ever *removes* distractors, never
invents them, so it cannot manufacture a plausible-looking wrong answer that
no model proposed.
"""

from src.contracts import CandidateItem, Distractor

# A cloze item still offers a real choice with two distractors (a three-way
# selection). Below that the multiple-choice rendering is degenerate, so the
# item is worth rejecting rather than repairing.
MIN_DISTRACTORS_AFTER_REPAIR = 2


def repair_distractors(item: CandidateItem) -> tuple[CandidateItem, list[str]]:
    """Drop answer-colliding and duplicate distractors.

    Returns the repaired item and a list of human-readable notes describing
    what was changed, so the pilot report can show how much repair is
    happening rather than hiding it. An item needing repair on every run is a
    prompt problem worth knowing about even though it is no longer a
    rejection.
    """
    notes: list[str] = []
    answer = item.proposed_answer.strip().lower()

    kept: list[Distractor] = []
    seen: set[str] = set()
    for d in item.distractors:
        text = d.text.strip()
        key = text.lower()
        if not text:
            notes.append("dropped an empty distractor")
            continue
        if key == answer:
            notes.append(f"dropped distractor {text!r}: identical to the accepted answer")
            continue
        if key in seen:
            notes.append(f"dropped distractor {text!r}: duplicate")
            continue
        seen.add(key)
        kept.append(d)

    if not notes:
        return item, []

    return item.model_copy(update={"distractors": kept}), notes


def repair_candidate(item: CandidateItem) -> tuple[CandidateItem, list[str]]:
    """Run every repair pass over a candidate. Currently distractors only."""
    return repair_distractors(item)
