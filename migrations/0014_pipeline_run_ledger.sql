CREATE TABLE pipeline_runs(
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL UNIQUE,
    pipeline_id TEXT NOT NULL,
    pipeline_revision INTEGER NOT NULL,
    graph_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_pipeline_runs_revision
ON pipeline_runs(pipeline_id, pipeline_revision, created_at DESC);

CREATE TABLE pipeline_run_events(
    id TEXT PRIMARY KEY,
    pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(id),
    event_type TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX idx_pipeline_run_events_run
ON pipeline_run_events(pipeline_run_id, occurred_at, id);
