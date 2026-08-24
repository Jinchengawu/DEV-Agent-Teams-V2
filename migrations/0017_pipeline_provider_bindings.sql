ALTER TABLE pipeline_drafts
ADD COLUMN agent_assignments_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE pipeline_revisions
ADD COLUMN binding_model TEXT NOT NULL DEFAULT 'legacy-v0';

ALTER TABLE pipeline_revisions
ADD COLUMN resolved_provider_bindings_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE agent_runs(
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    pipeline_revision_id TEXT NOT NULL,
    binding_site TEXT NOT NULL,
    resolved_binding_hash TEXT NOT NULL,
    deployment_snapshot_json TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    runtime_identity TEXT,
    status TEXT NOT NULL,
    artifact_envelopes_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(delivery_id,attempt_id)
);

CREATE INDEX idx_agent_runs_delivery
ON agent_runs(delivery_id,created_at);
