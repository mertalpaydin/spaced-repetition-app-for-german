"""Step 2: Generate, verify, and populate the SQLite Item Bank (data/bank.db).

Run this script directly in your IDE (Right click -> Run Python File, or hit F5).
"""

import json
from pathlib import Path

from src.bank.storage import SqliteItemBank
from src.contracts import BankItem


def main() -> None:
    print("\n=======================================================")
    print("  Step 2: Building and Verifying Item Bank")
    print("=======================================================\n")

    db_path = Path("data/bank.db")
    bank = SqliteItemBank(db_path)

    golden_sample = Path("data/fixtures/production/golden_bank_sample.json")
    if not golden_sample.exists():
        print(f"Error: {golden_sample} not found.")
        return

    with golden_sample.open("r", encoding="utf-8") as f:
        items_data = json.load(f)

    print(f"Ingesting and verifying {len(items_data)} items from golden bank sample...")
    for raw in items_data:
        item = BankItem.model_validate(raw)
        bank.insert_item(item, source_batch_id="bootstrap_sample")

    print("\nItem Bank Population Complete!")
    print(f"Database Path: {db_path.resolve()}")
    print(f"Total Items in Database: {bank.count_total()}\n")


if __name__ == "__main__":
    main()
