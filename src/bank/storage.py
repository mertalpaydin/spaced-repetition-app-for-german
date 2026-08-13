"""SQLite item bank storage implementation."""

import json
import sqlite3
from pathlib import Path

from src.bank.migrations import run_migrations
from src.contracts import BankItem, Difficulty, Distractor, VerificationResult


class SqliteItemBank:
    """Persistent SQLite repository for verified grammar items and batch audit records."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        run_migrations(self.db_path)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def insert_item(self, item: BankItem, source_batch_id: str | None = None) -> None:
        """Insert a verified BankItem into the database."""
        conn = self._get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO items (
                        id, topic_id, type, difficulty, cefr, prompt, cue,
                        accepted_answers_json, rule_hint, source_batch_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id,
                        item.topic_id,
                        item.type,
                        item.difficulty,
                        item.cefr,
                        item.prompt,
                        item.cue,
                        json.dumps(item.accepted_answers, ensure_ascii=False),
                        item.rule_hint,
                        source_batch_id,
                    ),
                )

                # Insert distractors
                for d in item.distractors:
                    d_text = d.text if isinstance(d, Distractor) else str(d)
                    d_topic = d.implied_topic_id if isinstance(d, Distractor) else None
                    cur.execute(
                        """
                        INSERT INTO distractors (item_id, text, implied_topic_id)
                        VALUES (?, ?, ?)
                        """,
                        (item.id, d_text, d_topic),
                    )

                # Insert carrier lemmas
                for lemma in item.carrier_lemmas:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO carrier_lemmas (item_id, lemma)
                        VALUES (?, ?)
                        """,
                        (item.id, lemma),
                    )
        finally:
            conn.close()

    def get_item(self, item_id: str) -> BankItem | None:
        """Retrieve a BankItem by its unique ID."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM items WHERE id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_bank_item(conn, row)
        finally:
            conn.close()

    def query_by_topic(self, topic_id: str, max_count: int | None = None) -> list[BankItem]:
        """Retrieve items for a specific topic ID."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            query = "SELECT * FROM items WHERE topic_id = ? ORDER BY difficulty ASC"
            if max_count is not None:
                query += f" LIMIT {int(max_count)}"
            cur.execute(query, (topic_id,))
            rows = cur.fetchall()
            return [self._row_to_bank_item(conn, r) for r in rows]
        finally:
            conn.close()

    def query_by_difficulty(self, topic_id: str, difficulty: Difficulty) -> list[BankItem]:
        """Retrieve items for a specific topic ID and difficulty tier."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM items WHERE topic_id = ? AND difficulty = ?",
                (topic_id, difficulty),
            )
            rows = cur.fetchall()
            return [self._row_to_bank_item(conn, r) for r in rows]
        finally:
            conn.close()

    def get_all_items(self) -> list[BankItem]:
        """Retrieve all bank items."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM items ORDER BY topic_id, difficulty")
            rows = cur.fetchall()
            return [self._row_to_bank_item(conn, r) for r in rows]
        finally:
            conn.close()

    def count_by_topic(self, topic_id: str) -> int:
        """Count items available for a topic."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM items WHERE topic_id = ?", (topic_id,))
            row = cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def count_total(self) -> int:
        """Count total items in the bank."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM items")
            row = cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def insert_batch_record(
        self,
        batch_id: str,
        model: str,
        total_items: int,
        passed_items: int,
        drop_rate: float,
        cost_usd: float,
    ) -> None:
        """Log a batch run in the database."""
        conn = self._get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO batches (
                        batch_id, model, total_items, passed_items, drop_rate, cost_usd
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (batch_id, model, total_items, passed_items, drop_rate, cost_usd),
                )
        finally:
            conn.close()

    def insert_verification_log(
        self,
        result: VerificationResult,
        item_id: str,
        batch_id: str | None = None,
    ) -> None:
        """Log verification outcome for a candidate item."""
        conn = self._get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO verification_log (
                        item_id, batch_id, passed, layer_failed, reason, error_type
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        batch_id,
                        1 if result.passed else 0,
                        result.layer_failed,
                        result.reason,
                        result.error_type,
                    ),
                )
        finally:
            conn.close()

    def _row_to_bank_item(self, conn: sqlite3.Connection, row: sqlite3.Row) -> BankItem:
        item_id = str(row["id"])
        cur = conn.cursor()

        # Load distractors
        cur.execute("SELECT text, implied_topic_id FROM distractors WHERE item_id = ?", (item_id,))
        distractors = [
            Distractor(text=d_row["text"], implied_topic_id=d_row["implied_topic_id"])
            for d_row in cur.fetchall()
        ]

        # Load carrier lemmas
        cur.execute("SELECT lemma FROM carrier_lemmas WHERE item_id = ?", (item_id,))
        carrier_lemmas = [l_row["lemma"] for l_row in cur.fetchall()]

        return BankItem(
            id=item_id,
            topic_id=row["topic_id"],
            type=row["type"],
            difficulty=row["difficulty"],
            cefr=row["cefr"],
            prompt=row["prompt"],
            cue=row["cue"],
            accepted_answers=json.loads(row["accepted_answers_json"]),
            distractors=distractors,
            rule_hint=row["rule_hint"] or "",
            carrier_lemmas=carrier_lemmas,
        )
