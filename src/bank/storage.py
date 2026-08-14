"""SQLite item bank storage implementation."""

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.bank.migrations import run_migrations
from src.contracts import (
    BankExport,
    BankItem,
    Difficulty,
    Distractor,
    InsertReport,
    ReviewMode,
    VerificationResult,
)


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

    def insert_item(self, item: BankItem, source_batch_id: str | None = None) -> bool:
        """Insert a verified BankItem into the database.

        Idempotent on ``item.id``: re-inserting an item whose id already
        exists is a silent no-op rather than an ``IntegrityError``, matching
        ``carrier_lemmas``' existing ``INSERT OR IGNORE`` behaviour instead of
        the previous plain ``INSERT`` that raised on any re-ingest. Returns
        ``True`` if a new row was written, ``False`` if ``id`` was already
        present (nothing was changed).
        """
        conn = self._get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR IGNORE INTO items (
                        id, topic_id, tag_id, dimension, type, difficulty, cefr, prompt, cue,
                        accepted_answers_json, rule_hint, facet, confusion_group,
                        block_id, block_position, domain, source_sentence_id, source_batch_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id,
                        item.topic_id,
                        item.tag_id,
                        item.dimension,
                        item.type,
                        item.difficulty,
                        item.cefr,
                        item.prompt,
                        item.cue,
                        json.dumps(item.accepted_answers, ensure_ascii=False),
                        item.rule_hint,
                        item.facet,
                        item.confusion_group,
                        item.block_id,
                        item.block_position,
                        item.domain,
                        item.source_sentence_id,
                        source_batch_id,
                    ),
                )
                if cur.rowcount == 0:
                    # id already existed: a duplicate re-ingest, not an error.
                    # Do not touch distractors/carrier_lemmas for an existing
                    # row -- re-running the same insert must not accumulate
                    # duplicate child rows.
                    return False

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
                return True
        finally:
            conn.close()

    @staticmethod
    def _validate_for_insert(item: BankItem) -> list[str]:
        """Bank-level integrity checks applied at insert time.

        ``accepted_answers`` non-emptiness/deduplication is already enforced
        by ``BankItem`` itself (a malformed item cannot even be constructed).
        This is the "last line of defence" named in 02-content-pipeline.md:
        a cloze prompt must never contain one of its own accepted answers as
        a standalone word outside the ``___`` gap, which would let the
        learner read the answer straight off the question. Only checked when
        a gap marker is present, and only as a whole-word (not substring)
        match, so short answers that happen to occur as a sub-string of an
        unrelated word (e.g. accepted answer "a" inside prompt word "Satz")
        do not false-positive.
        """
        issues: list[str] = []
        if "___" not in item.prompt:
            return issues
        prompt_without_gap = item.prompt.replace("___", " ")
        for answer in item.accepted_answers:
            stripped = answer.strip()
            if not stripped:
                continue
            pattern = r"(?<!\w)" + re.escape(stripped) + r"(?!\w)"
            if re.search(pattern, prompt_without_gap, flags=re.IGNORECASE):
                issues.append(f"prompt contains accepted answer '{stripped}' outside the gap")
        return issues

    def insert(self, items: list[BankItem], source_batch_id: str | None = None) -> InsertReport:
        """Batch-insert items, idempotently on ``id``, returning an outcome report."""
        inserted = 0
        duplicates = 0
        rejected = 0
        reasons: list[str] = []
        for item in items:
            issues = self._validate_for_insert(item)
            if issues:
                rejected += 1
                reasons.extend(f"{item.id}: {issue}" for issue in issues)
                continue
            if self.insert_item(item, source_batch_id=source_batch_id):
                inserted += 1
            else:
                duplicates += 1
        return InsertReport(
            inserted=inserted,
            duplicates=duplicates,
            rejected=rejected,
            rejection_reasons=reasons,
        )

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

    def stock(self, tag_id: str, difficulty: Difficulty) -> int:
        """Count UNSEEN items for ``tag_id`` at ``difficulty``.

        "Unseen" means no row in ``review_logs`` references the item's id --
        the learner has never attempted it. ``tag_id`` is matched against the
        item's own ``tag_id`` column, falling back to ``topic_id`` for rows
        ingested before ``tag_id`` existed (or never given one), so legacy
        grammar items keep counting toward their topic's stock.
        """
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) FROM items i
                WHERE COALESCE(i.tag_id, i.topic_id) = ?
                  AND i.difficulty = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM review_logs r WHERE r.item_id = i.id
                  )
                """,
                (tag_id, difficulty),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def export_full(self) -> BankExport:
        """Export every item currently in the bank."""
        items = self.get_all_items()
        return self._build_export(items)

    def export_delta(self, since_id: str) -> BankExport:
        """Export only items inserted strictly after ``since_id``.

        Insertion order is tracked via SQLite's implicit ``rowid`` (the
        ``items`` table has a ``TEXT`` primary key, not an ``INTEGER``, so
        ``rowid`` remains a separate monotonically increasing insertion
        sequence). An empty or unknown ``since_id`` is treated as "client has
        nothing yet", returning every item -- but a client that passes the id
        of its most recently seen item gets exactly what was inserted after
        it, regardless of insert order randomness.
        """
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            since_rowid = 0
            if since_id:
                cur.execute("SELECT rowid FROM items WHERE id = ?", (since_id,))
                found = cur.fetchone()
                if found is not None:
                    since_rowid = int(found[0])
            cur.execute(
                "SELECT * FROM items WHERE rowid > ? ORDER BY rowid",
                (since_rowid,),
            )
            rows = cur.fetchall()
            items = [self._row_to_bank_item(conn, r) for r in rows]
        finally:
            conn.close()
        return self._build_export(items)

    @staticmethod
    def _build_export(items: list[BankItem]) -> BankExport:
        topic_count = len({it.tag_id or it.topic_id for it in items})
        return BankExport(
            generated_at=datetime.now(UTC).isoformat(),
            total_items=len(items),
            topic_count=topic_count,
            items=items,
        )

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
        cur.execute(
            "SELECT text, implied_topic_id FROM distractors WHERE item_id = ? ORDER BY id",
            (item_id,),
        )
        distractors = [
            Distractor(text=d_row["text"], implied_topic_id=d_row["implied_topic_id"])
            for d_row in cur.fetchall()
        ]

        # Load carrier lemmas
        cur.execute(
            "SELECT lemma FROM carrier_lemmas WHERE item_id = ? ORDER BY rowid",
            (item_id,),
        )
        carrier_lemmas = [l_row["lemma"] for l_row in cur.fetchall()]

        keys = row.keys()
        return BankItem(
            id=item_id,
            topic_id=row["topic_id"],
            tag_id=row["tag_id"] if "tag_id" in keys else None,
            dimension=row["dimension"] if "dimension" in keys and row["dimension"] else "grammar",
            type=row["type"],
            difficulty=row["difficulty"],
            cefr=row["cefr"],
            prompt=row["prompt"],
            cue=row["cue"],
            accepted_answers=json.loads(row["accepted_answers_json"]),
            distractors=distractors,
            # Preserve NULL as None rather than coercing to "" -- the two are
            # distinct values on the BankItem contract and a round trip must
            # not collapse them (test_export_round_trip_is_lossless).
            rule_hint=row["rule_hint"],
            facet=row["facet"] if "facet" in keys else None,
            confusion_group=row["confusion_group"] if "confusion_group" in keys else None,
            block_id=row["block_id"] if "block_id" in keys else None,
            block_position=row["block_position"] if "block_position" in keys else None,
            domain=row["domain"] if "domain" in keys else None,
            source_sentence_id=row["source_sentence_id"] if "source_sentence_id" in keys else None,
            carrier_lemmas=carrier_lemmas,
        )

    def append_review_log(
        self,
        item_id: str,
        topic_id: str,
        user_answer: str,
        is_correct: bool,
        hint_level: int,
        fsrs_rating: str,
        mode: ReviewMode = "review",
        facet: str | None = None,
        response_ms: int = 0,
    ) -> None:
        """Append an immutable review log entry to SQLite storage.

        ``mode`` distinguishes measurement rows (``review``, ``recalibration``) that
        feed FSRS and topic-state recomputation from practice rows (``duel``,
        ``challenge``) that never do. ``facet`` records the item's morphological
        facet at the time of the attempt so the log remains the source of truth
        for facet accuracy even after a topic later splits.
        """
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO review_logs (
                        item_id, topic_id, user_answer, is_correct,
                        hint_level, fsrs_rating, mode, facet, response_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        topic_id,
                        user_answer,
                        1 if is_correct else 0,
                        hint_level,
                        fsrs_rating,
                        mode,
                        facet,
                        response_ms,
                    ),
                )
        finally:
            conn.close()

    def get_review_logs(
        self, topic_id: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Retrieve recent review logs optionally filtered by topic_id."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            if topic_id:
                cur.execute(
                    "SELECT * FROM review_logs WHERE topic_id = ? ORDER BY created_at DESC LIMIT ?",
                    (topic_id, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM review_logs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
