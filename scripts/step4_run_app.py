"""Step 4: Launch the interactive Terminal Learning Trainer or open the Web PWA.

Run this script directly in your IDE (Right click -> Run Python File, or hit F5).
"""

from pathlib import Path
import webbrowser

from src.bank.storage import SqliteItemBank
from src.cli.app import cmd_stats, cmd_topics
from src.cli.session import InteractiveSession
from src.engine.fsrs import FSRSEngine
from src.engine.scheduler import LearningScheduler
from src.engine.topic_state import TopicStateManager
from src.taxonomy.loader import load_taxonomy


def main() -> None:
    print("\n=======================================================")
    print("  DeutschMaster - German Grammar Learning Trainer")
    print("=======================================================\n")

    db_path = Path("data/bank.db")
    if not db_path.exists():
        print("Initializing Item Bank from sample...")
        from scripts.step2_build_item_bank import main as build_bank

        build_bank()

    bank = SqliteItemBank(db_path)
    topics = load_taxonomy()
    topic_manager = TopicStateManager(topics)
    fsrs_engine = FSRSEngine()
    scheduler = LearningScheduler()

    print("Choose an option:")
    print("  [1] Show Learning & Item Bank Statistics")
    print("  [2] Show Topic DAG Tree Status")
    print("  [3] Open Web PWA Interface in Browser (Recommended)")
    print("  [4] Quick Practice Round in Terminal")
    print("  [0] Exit\n")

    choice = input("Enter choice [1-4] (default 3): ").strip() or "3"

    if choice == "1":
        cmd_stats(bank)
    elif choice == "2":
        cmd_topics(topic_manager)
    elif choice == "3":
        web_index = Path("web/index.html").resolve().as_uri()
        print(f"\nOpening Web Interface in default browser:\n{web_index}")
        webbrowser.open(web_index)
    elif choice == "4":
        plan = scheduler.plan_next_round(
            bank=bank,
            topic_manager=topic_manager,
            fsrs_records={},
        )
        session = InteractiveSession(
            round_plan=plan,
            topic_manager=topic_manager,
            fsrs_engine=fsrs_engine,
            fsrs_records={},
        )

        print(f"\nStarting Round ({len(plan.items)} items):\n")
        for idx, item in enumerate(plan.items, start=1):
            print(f"[{idx}/{len(plan.items)}] Thema: {item.topic_id} ({item.cefr})")
            print(f"Satz: {item.prompt}")
            if item.cue:
                print(f"Hinweis: ({item.cue})")
            ans = input("Deine Antwort: ").strip()
            att = session.process_item_attempt(item, user_answer=ans, hint_level=0)
            if att.is_correct:
                print("-> Richtig! 🎉\n")
            else:
                print(f"-> Falsch. Richtig ist: {', '.join(item.accepted_answers)}\n")

        summary = session.get_summary()
        print("=== Runden-Ergebnis ===")
        print(f"Genauigkeit: {summary.accuracy:.0%}")
        print(f"Richtig: {summary.correct_items}/{summary.total_items}\n")


if __name__ == "__main__":
    main()
