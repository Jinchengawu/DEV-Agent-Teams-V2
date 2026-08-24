CREATE TABLE pipelines(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    active_revision INTEGER,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE pipeline_drafts(
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
    name TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    layout_json TEXT NOT NULL,
    input_schema_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    validation_status TEXT NOT NULL,
    validation_errors_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_pipeline_drafts_pipeline
ON pipeline_drafts(pipeline_id, updated_at DESC);

CREATE TABLE pipeline_revisions(
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
    revision INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    compiled_graph_json TEXT NOT NULL,
    binding_snapshot_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    published_by TEXT NOT NULL,
    published_at TEXT NOT NULL,
    PRIMARY KEY(pipeline_id, revision),
    UNIQUE(fingerprint)
);

CREATE INDEX idx_pipeline_revisions_published
ON pipeline_revisions(pipeline_id, published_at DESC);
