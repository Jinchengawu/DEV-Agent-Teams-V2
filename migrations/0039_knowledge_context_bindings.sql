ALTER TABLE pipeline_drafts
ADD COLUMN knowledge_context_bindings_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE pipeline_revisions
ADD COLUMN knowledge_context_bindings_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE knowledge_context_preparation_runs(
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    knowledge_binding_hash TEXT NOT NULL,
    input_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    next_attempt_at TEXT,
    authorization_stamp_json TEXT,
    authorization_epoch_hash TEXT,
    final_snapshot_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(delivery_id,input_sha256,knowledge_binding_hash)
);

CREATE INDEX knowledge_context_preparation_status_idx
ON knowledge_context_preparation_runs(status,next_attempt_at,lease_expires_at);

CREATE TABLE knowledge_context_stage_results(
    preparation_run_id TEXT NOT NULL REFERENCES knowledge_context_preparation_runs(id),
    stage_path TEXT NOT NULL,
    query_sha256 TEXT NOT NULL,
    retrieval_policy_revision_id TEXT NOT NULL,
    artifact_uri TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    artifact_media_type TEXT NOT NULL,
    artifact_size_bytes INTEGER NOT NULL,
    citation_ids_json TEXT NOT NULL,
    authorization_epoch_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(preparation_run_id,stage_path)
);
