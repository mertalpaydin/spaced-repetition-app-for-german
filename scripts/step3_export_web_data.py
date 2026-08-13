"""Step 3: Export the SQLite Item Bank to static JSON assets for the Web PWA.

Run this script directly in your IDE (Right click -> Run Python File, or hit F5).
"""

from pathlib import Path

from src.bank.exporter import BankExporter
from src.bank.storage import SqliteItemBank


def main() -> None:
    print("\n=======================================================")
    print("  Step 3: Exporting Bank to Web Frontend (web/data/)")
    print("=======================================================\n")

    db_path = Path("data/bank.db")
    if not db_path.exists():
        print(f"Warning: {db_path} does not exist yet. Initializing item bank first...")
        from scripts.step2_build_item_bank import main as build_bank

        build_bank()

    bank = SqliteItemBank(db_path)
    output_dir = Path("web/data")

    print(f"Exporting to: {output_dir.resolve()} ...")
    manifest = BankExporter.export_to_directory(bank, output_dir)

    print("\nExport Succeeded!")
    print(f"Total Items Exported: {manifest['total_items']}")
    print(f"Topics Covered: {manifest['topic_count']}")
    print(f"Manifest Path: {(output_dir / 'manifest.json').resolve()}\n")


if __name__ == "__main__":
    main()
