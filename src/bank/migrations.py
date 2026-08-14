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


def run_migrations(db_path: Path | str) -> None:
    """Apply all pending migrations to the specified SQLite database."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        with conn:
            conn.executescript(MIGRATION_V1_SQL)
            # Record version 1
            cur = conn.cursor()
            cur.execute("SELECT MAX(version) FROM schema_version;")
            row = cur.fetchone()
            current_version = row[0] if row and row[0] is not None else 0

            if current_version < 1:
                cur.execute("INSERT INTO schema_version (version) VALUES (1);")
    finally:
        conn.close()
