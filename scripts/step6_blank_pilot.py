"""Step 6: generate-then-blank pilot for article and adjective declension.

docs/audits/generation-track-plan.md's cycle-2 architecture: ask the model
only for plain, natural German sentences (no gap, no answer, no grammar
topic), then tag, select, and blank them in code. This script runs that
whole flow end to end and prints a report -- sentences requested, sentences
tagged, items produced per topic, and skips by reason -- for the 15 article
and adjective declension topics this cycle covers
(``src.generation.blanking.selectors.SELECTORS``).

Run this from a terminal:

    .venv/bin/python -m scripts.step6_blank_pilot --sentences 60

With no API key configured it runs entirely offline against the
deterministic mock sentence pool (never a live call by accident); with
GEMINI_FREE_API_KEY/GEMINI_PAID_API_KEY/GEMINI_API_KEY set it generates for
real through ``src.llm.client.GeminiLlmClient``. Not wired into the bank or
the verification chain yet -- that is cycle 3.
"""

from __future__ import annotations

import argparse
import sys

from src.contracts import CEFR
from src.generation.blanking import sentence_tagger
from src.generation.blanking.pipeline import TOPIC_IDS, BlankingReport, blank_sentences
from src.generation.blanking.sentence_source import build_sentence_generator, client_from_env
from src.llm.env import load_env_file

_VALID_CEFR: tuple[CEFR, ...] = ("A1", "A2", "B1", "B2")


def _print_report(report: BlankingReport, requested: int, ran_live: bool) -> None:
    print("==========================================================")
    print("  Step 6: generate-then-blank pilot (article/adjective declension)")
    print("==========================================================")
    live_note = "yes" if ran_live else "no (no API key configured; ran the offline mock pool)"
    print(f"  Live model calls:      {live_note}")
    print(f"  Sentences requested:   {requested}")
    print(f"  Sentences tagged:      {report.sentences_tagged}")
    print(f"  Items produced:        {report.total_items}")
    print("  Items by topic:")
    for topic_id in TOPIC_IDS:
        print(f"    - {topic_id}: {report.items_by_topic.get(topic_id, 0)}")
    print("  Skips by reason:")
    if not report.skips_by_reason:
        print("    (none)")
    for reason, count in sorted(report.skips_by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 6 generate-then-blank pilot.")
    parser.add_argument(
        "--sentences", type=int, default=60, help="Number of sentences to request (default 60)."
    )
    parser.add_argument(
        "--cefr", type=str, default="A2", choices=list(_VALID_CEFR), help="CEFR level to request."
    )
    parser.add_argument(
        "--theme",
        type=str,
        default="Alltag",
        help="Theme handed to the model (never a grammar topic).",
    )
    args = parser.parse_args()

    # The two lane keys live in a gitignored .env per docs/01-foundation.md;
    # nothing else in the process reads that file.
    load_env_file()

    llm_client = client_from_env()
    ran_live = llm_client is not None
    generator = build_sentence_generator(llm_client)

    sentences = generator.generate(args.cefr, args.theme, args.sentences)
    if not sentences:
        print("No sentences were generated (empty or unparseable model output); nothing to do.")
        return 1

    if not sentence_tagger.analysis_available():
        print(
            "spaCy's de_core_news_sm model is not installed; no items can be "
            "produced (degrading cleanly, not crashing). Install it "
            "(`python -m spacy download de_core_news_sm`) and re-run."
        )
        print(f"  Sentences requested:   {len(sentences)}")
        print(f"  Topics in scope:       {len(TOPIC_IDS)}")
        return 0

    report = blank_sentences(sentences)
    _print_report(report, requested=len(sentences), ran_live=ran_live)
    return 0


if __name__ == "__main__":
    sys.exit(main())
