CREATE TABLE workspace_candidates_v2(
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    workcell_key TEXT NOT NULL,
    workspace_binding_id TEXT NOT NULL REFERENCES workspace_bindings(id),
    repository_uri TEXT NOT NULL,
    adapter_type TEXT NOT NULL CHECK(adapter_type IN ('managed-bare-git','external-git')),
    base_revision TEXT NOT NULL,
    candidate_revision TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL,
    candidate_branch TEXT NOT NULL,
    verification_sha256 TEXT NOT NULL,
    review_artifact_ids_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status='verified'),
    created_at TEXT NOT NULL,
    UNIQUE(delivery_id,workcell_key),
    UNIQUE(delivery_id,workspace_binding_id)
);

CREATE TABLE github_pr_receipts(
    candidate_id TEXT PRIMARY KEY REFERENCES workspace_candidates_v2(id),
    provider TEXT NOT NULL CHECK(provider='github'),
    pull_request_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    base_branch TEXT NOT NULL CHECK(base_branch='main'),
    head_branch TEXT NOT NULL,
    head_candidate_sha TEXT NOT NULL,
    state TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE release_bundles_v2(
    delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    pipeline_revision_id TEXT NOT NULL,
    release_contract_snapshot_json TEXT NOT NULL,
    candidate_ids_json TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status='verified'),
    verified_at TEXT NOT NULL
);

CREATE TABLE release_apply_attempts_v2(
    delivery_id TEXT PRIMARY KEY REFERENCES release_bundles_v2(delivery_id),
    project_id TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('applying','needs_attention','completed')),
    error_code TEXT,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE remote_apply_receipts(
    delivery_id TEXT NOT NULL REFERENCES release_apply_attempts_v2(delivery_id),
    ordinal INTEGER NOT NULL,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES workspace_candidates_v2(id),
    workcell_key TEXT NOT NULL,
    repository_uri TEXT NOT NULL,
    before_revision TEXT NOT NULL,
    candidate_revision TEXT NOT NULL,
    after_revision TEXT NOT NULL,
    recovered INTEGER NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY(delivery_id,ordinal)
);

CREATE TABLE release_manifests_v2(
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    delivery_id TEXT NOT NULL UNIQUE REFERENCES release_bundles_v2(delivery_id),
    pipeline_revision_id TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    repositories_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status='active'),
    activated_at TEXT NOT NULL
);

CREATE TABLE project_release_health_v2(
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    status TEXT NOT NULL CHECK(status IN ('healthy','release_drifted')),
    delivery_id TEXT,
    bundle_sha256 TEXT,
    error_code TEXT,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_candidates_v2_delivery
ON workspace_candidates_v2(delivery_id,workcell_key);
