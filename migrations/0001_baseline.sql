CREATE TABLE IF NOT EXISTS deliveries(
    id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS control_records(
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY(kind, id)
);

CREATE TABLE IF NOT EXISTS control_events(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    document_id UNINDEXED,
    title,
    content,
    artifact_type,
    source_id
);

CREATE TABLE IF NOT EXISTS product_events(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_product_events_aggregate
ON product_events(aggregate_type, aggregate_id, sequence);

