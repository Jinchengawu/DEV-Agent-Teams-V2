CREATE TABLE release_apply_attempts(
    delivery_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'applying','completed','compensating','compensated','needs_attention'
    )),
    snapshot_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE project_release_manifests(
    project_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL UNIQUE,
    bundle_sha256 TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    activated_at TEXT NOT NULL
);

CREATE INDEX idx_release_apply_attempts_project
ON release_apply_attempts(project_id,status,updated_at);
