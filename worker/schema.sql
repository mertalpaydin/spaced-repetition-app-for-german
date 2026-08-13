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

CREATE INDEX IF NOT EXISTS idx_user_topic_updated ON user_topic_states(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_user_fsrs_due ON user_fsrs_cards(user_id, due_at);
