"""Generate-then-blank item construction (docs/audits/generation-track-plan.md).

The model is asked only for a plain, natural German sentence at a CEFR level,
given a theme -- it is never told a grammar topic, never asked for a gap, an
answer, or distractors. This package tags that sentence with spaCy, selects
the token(s) that are instances of a given grammar topic via a declarative
predicate, removes the selected token to form the gap, and computes the
accepted-answer set and distractors from closed morphological paradigms
already in ``src.taxonomy.facets``. No model claim about the answer is ever
involved.

Cycle 2 scope (docs/audits/generation-track-plan.md): article and adjective
declension only, the 15 topics enumerated in ``src.generation.blanking.
selectors.SELECTORS``. Not wired into ``src.verification.pipeline`` yet --
that is cycle 3.

Modules:

* ``sentence_tagger`` -- full-sentence spaCy tagging (POS + morphology per
  token), degrading to ``None``/empty everywhere spaCy is unavailable.
* ``paradigms`` -- reverse (cell -> surface form) lookups built from the
  existing closed-class paradigm tables in ``src.taxonomy.facets``, never
  duplicating the linguistic data those tables encode.
* ``selectors`` -- one declarative predicate per topic, built from two small
  parameterised factories (determiner topics, adjective-declension topics)
  plus one comparative/superlative predicate, rather than fifteen bespoke
  functions.
* ``blanker`` -- turns a selected token into a ``CandidateItem``: removes it,
  computes the accepted-answer set and distractors, or rejects the sentence
  for this topic if the paradigm cannot resolve it cleanly.
* ``pipeline`` -- orchestrates tag -> select -> blank across every topic and
  every sentence, and reports skips by reason.
* ``sentence_source`` -- the "generate" step: a live Gemini-backed sentence
  generator through ``src.llm.client`` and a deterministic offline mock for
  tests and no-API-key development.
"""
