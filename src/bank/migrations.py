"""SQLite database schema migrations for the item bank."""

import sqlite3
from pathlib import Path

MIGRATION_V1_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    type TEXT NOT NULL,
    difficulty INTEGER NOT NULL,
    cefr TEXT NOT NULL,
    prompt TEXT NOT NULL,
    cue TEXT,
    accepted_answers_json TEXT NOT NULL,
    rule_hint TEXT DEFAULT '',
    facet TEXT,
    confusion_group TEXT,
    block_id TEXT,
    block_position INTEGER,
    domain TEXT,
    source_sentence_id TEXT,
    source_batch_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS distractors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    text TEXT NOT NULL,
    implied_topic_id TEXT,
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS carrier_lemmas (
    item_id TEXT NOT NULL,
    lemma TEXT NOT NULL,
    PRIMARY KEY(item_id, lemma),
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS verification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    batch_id TEXT,
    passed INTEGER NOT NULL,
    layer_failed INTEGER,
    reason TEXT,
    error_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    total_items INTEGER NOT NULL,
    passed_items INTEGER NOT NULL,
    drop_rate REAL NOT NULL,
    cost_usd REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    user_answer TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    hint_level INTEGER NOT NULL,
    fsrs_rating INTEGER NOT NULL,
    response_ms INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_items_topic_id ON items(topic_id);
CREATE INDEX IF NOT EXISTS idx_items_cefr ON items(cefr);
CREATE INDEX IF NOT EXISTS idx_items_difficulty ON items(topic_id, difficulty);
CREATE INDEX IF NOT EXISTS idx_distractors_item_id ON distractors(item_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_topic_id ON review_logs(topic_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_created ON review_logs(created_at);
"""

# Version 2: `review_logs` gains `mode` and `facet`, and `fsrs_rating` is corrected
# from a lying `INTEGER` declaration to `TEXT` (it has only ever held FsrsRating
# string literals such as "good" or "again"). SQLite has no `ALTER COLUMN`, so the
# table is rebuilt: existing rows are carried over, cast to the true `fsrs_rating`
# type, and backfilled with `mode='review'` (the only mode the old code ever wrote)
# and `facet=NULL` (not recorded pre-migration, so it is honestly left unknown
# rather than guessed).
MIGRATION_V2_SQL = """
CREATE TABLE IF NOT EXISTS review_logs_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    user_answer TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    hint_level INTEGER NOT NULL,
    fsrs_rating TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'review',
    facet TEXT,
    response_ms INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO review_logs_v2 (
    id, item_id, topic_id, user_answer, is_correct, hint_level,
    fsrs_rating, mode, facet, response_ms, created_at
)
SELECT
    id, item_id, topic_id, user_answer, is_correct, hint_level,
    CAST(fsrs_rating AS TEXT), 'review', NULL, response_ms, created_at
FROM review_logs;

DROP TABLE review_logs;
ALTER TABLE review_logs_v2 RENAME TO review_logs;

CREATE INDEX IF NOT EXISTS idx_review_logs_topic_id ON review_logs(topic_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_created ON review_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_review_logs_mode ON review_logs(mode);
"""


# Version 3: `items` gains `tag_id` and `dimension`, the two ``BankItem``
# contract fields that previously had no column at all -- a round trip
# silently dropped them. `tag_id` is the general-purpose identifier the item
# is stocked and exported under (a topic id for grammar items, a lemma for
# vocabulary items); `dimension` records which. Both are plain `ALTER TABLE
# ADD COLUMN`, so existing rows are simply backfilled with NULL / the
# 'grammar' default -- no table rebuild needed, unlike v2.
MIGRATION_V3_SQL = """
ALTER TABLE items ADD COLUMN tag_id TEXT;
ALTER TABLE items ADD COLUMN dimension TEXT NOT NULL DEFAULT 'grammar';

CREATE INDEX IF NOT EXISTS idx_items_tag_id ON items(tag_id, difficulty);
"""


# Version 4: `items` gains `gloss_en`, the English translation of the whole
# carrier sentence. Exactly the same class of defect v3 fixed: the field is
# declared on ``BankItem``, filled by the pilot (scripts/step7_corpus_pilot.py
# ``_populate_glosses``), and then silently discarded by a round trip through
# this table because no column existed to hold it. TODO.md section 4 makes the
# gloss learner-facing on every exercise, so it has to survive into the export
# the PWA reads. Plain `ALTER TABLE ADD COLUMN`: every pre-existing row
# backfills to NULL, which is the honest value -- those items were banked
# before any translation ran, and ``gloss_en = None`` is what the client is
# built to degrade cleanly on.
MIGRATION_V4_SQL = """
ALTER TABLE items ADD COLUMN gloss_en TEXT;
"""


#: The version ``run_migrations`` brings a database up to. Named rather than
#: left implicit in the last ``if current_version < N`` branch so a caller can
#: assert "this database is current" without hardcoding the number, and so a
#: fifth migration that forgets to bump this fails a test instead of shipping.
CURRENT_SCHEMA_VERSION = 4


def schema_version(db_path: Path | str) -> int:
    """The migration version ``db_path`` is currently at.

    ``0`` for a database that has never been migrated (or has no
    ``schema_version`` table at all), so a caller can tell "not migrated" from
    "migrated to version 1" instead of both looking like a missing row.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version';")
        if cur.fetchone() is None:
            return 0
        cur.execute("SELECT MAX(version) FROM schema_version;")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def run_migrations(db_path: Path | str) -> None:
    """Apply all pending migrations to the specified SQLite database."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        with conn:
            conn.executescript(MIGRATION_V1_SQL)
            cur = conn.cursor()
            cur.execute("SELECT MAX(version) FROM schema_version;")
            row = cur.fetchone()
            current_version = row[0] if row and row[0] is not None else 0

            if current_version < 1:
                cur.execute("INSERT INTO schema_version (version) VALUES (1);")
                current_version = 1

            if current_version < 2:
                conn.executescript(MIGRATION_V2_SQL)
                cur.execute("INSERT INTO schema_version (version) VALUES (2);")
                current_version = 2

            if current_version < 3:
                conn.executescript(MIGRATION_V3_SQL)
                cur.execute("INSERT INTO schema_version (version) VALUES (3);")
                current_version = 3

            if current_version < 4:
                conn.executescript(MIGRATION_V4_SQL)
                cur.execute("INSERT INTO schema_version (version) VALUES (4);")
                current_version = 4
    finally:
        conn.close()
