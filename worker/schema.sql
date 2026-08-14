-- D1 Database Schema for Cloudflare Sync Engine
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_sync_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_topic_states (
    user_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    state TEXT NOT NULL,
    consecutive_passes INTEGER DEFAULT 0,
    distinct_facets_json TEXT DEFAULT '[]',
    acquired_via TEXT,
    last_review_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, topic_id)
);

CREATE TABLE IF NOT EXISTS user_fsrs_cards (
    user_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    state TEXT NOT NULL,
    due_at TIMESTAMP NOT NULL,
    stability REAL,
    difficulty REAL,
    step INTEGER DEFAULT 0,
    reps INTEGER DEFAULT 0,
    lapses INTEGER DEFAULT 0,
    last_review_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, card_id)
);

CREATE TABLE IF NOT EXISTS sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Mirrors src/bank/migrations.py's review_logs (id, item_id, topic_id,
-- user_answer, is_correct, hint_level, fsrs_rating TEXT, mode, facet,
-- response_ms, created_at), plus `user_id` because this table serves many
-- users. Keep the shared columns identical to the Python side; a divergence
-- here is the same class of defect as the JS/Python typo-grader drift.
--
-- The UNIQUE constraint on (user_id, item_id, created_at) is what makes
-- "duplicate review_log rows are idempotent" (04-application.md stage 9)
-- hold: /sync uses INSERT OR IGNORE against this key, so re-submitting the
-- same review event twice (e.g. after a retried request) inserts one row,
-- not two. `created_at` is populated from the client's own event timestamp
-- when supplied, not the server's insert time, so a genuine retry collides
-- with the original row instead of appearing to be a new event.
CREATE TABLE IF NOT EXISTS review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    user_answer TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    hint_level INTEGER NOT NULL,
    fsrs_rating TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'review',
    facet TEXT,
    response_ms INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, item_id, created_at)
);

CREATE INDEX IF NOT EXISTS idx_user_topic_updated ON user_topic_states(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_user_fsrs_due ON user_fsrs_cards(user_id, due_at);
CREATE INDEX IF NOT EXISTS idx_user_review_logs ON review_logs(user_id, created_at);
