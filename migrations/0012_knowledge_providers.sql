CREATE TABLE knowledge_provider_bindings(
    id TEXT PRIMARY KEY,
    provider_kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    external_space_id TEXT NOT NULL,
    credential_ref TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider_kind, external_space_id)
);

CREATE TABLE knowledge_provider_snapshots(
    id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings(id),
    source_id TEXT NOT NULL,
    provider_revision TEXT NOT NULL,
    content_type TEXT NOT NULL,
    normalized_content_json TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    source_url TEXT,
    fetched_by_product_user_id TEXT NOT NULL REFERENCES users(id),
    fetched_by_provider_user_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(binding_id, source_id, provider_revision)
);

CREATE INDEX idx_knowledge_provider_snapshot_source
ON knowledge_provider_snapshots(binding_id, source_id, fetched_at DESC);

CREATE VIRTUAL TABLE knowledge_provider_fts USING fts5(
    snapshot_id UNINDEXED,
    binding_id UNINDEXED,
    source_id UNINDEXED,
    content
);

CREATE TABLE knowledge_provider_sync_runs(
    id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings(id),
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_revision TEXT,
    snapshot_id TEXT REFERENCES knowledge_provider_snapshots(id),
    snapshot_sha256 TEXT,
    error_code TEXT,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX idx_knowledge_provider_sync_binding
ON knowledge_provider_sync_runs(binding_id, started_at DESC, id);
