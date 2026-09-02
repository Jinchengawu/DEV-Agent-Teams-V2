CREATE TABLE knowledge_provider_snapshots_v2(
    id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    source_id TEXT NOT NULL,
    provider_revision TEXT NOT NULL,
    content_type TEXT NOT NULL,
    artifact_uri TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    artifact_media_type TEXT NOT NULL,
    artifact_size_bytes INTEGER NOT NULL,
    normalized_text_sha256 TEXT NOT NULL,
    source_url TEXT,
    fetched_by_product_user_id TEXT NOT NULL REFERENCES users(id),
    fetched_at TEXT NOT NULL,
    UNIQUE(binding_id,source_id,provider_revision)
);

CREATE TABLE knowledge_provider_source_heads_v2(
    binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    source_id TEXT NOT NULL,
    provider_revision TEXT,
    snapshot_id TEXT REFERENCES knowledge_provider_snapshots_v2(id),
    status TEXT NOT NULL,
    permission_probe_at TEXT NOT NULL,
    authorization_version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(binding_id,source_id)
);

CREATE TABLE knowledge_sync_jobs(
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    source_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    retry_at TEXT,
    provider_revision TEXT,
    snapshot_id TEXT REFERENCES knowledge_provider_snapshots_v2(id),
    snapshot_sha256 TEXT,
    error_code TEXT,
    requested_by TEXT NOT NULL REFERENCES users(id),
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(project_id,idempotency_key)
);

CREATE INDEX knowledge_sync_jobs_status_retry_idx
    ON knowledge_sync_jobs(status,retry_at,lease_expires_at);

CREATE INDEX knowledge_provider_snapshots_source_idx
    ON knowledge_provider_snapshots_v2(binding_id,source_id,fetched_at);

CREATE TABLE knowledge_index_profile_revisions(
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    config_sha256 TEXT NOT NULL UNIQUE,
    published_by TEXT NOT NULL REFERENCES users(id),
    published_at TEXT NOT NULL
);

CREATE TABLE embedding_qualification_snapshots(
    id TEXT PRIMARY KEY,
    provider_kind TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_digest TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    adapter_revision TEXT NOT NULL,
    tokenizer_contract TEXT NOT NULL,
    vector_normalization TEXT NOT NULL,
    distance_metric TEXT NOT NULL,
    sqlite_vec_version TEXT NOT NULL,
    qualification_sha256 TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    qualified_at TEXT NOT NULL
);

CREATE TABLE retrieval_policy_revisions(
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    index_profile_revision_id TEXT NOT NULL
        REFERENCES knowledge_index_profile_revisions(id),
    config_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL UNIQUE,
    published_by TEXT NOT NULL REFERENCES users(id),
    published_at TEXT NOT NULL
);

CREATE TABLE retrieval_evaluation_policy_revisions(
    id TEXT PRIMARY KEY,
    retrieval_policy_revision_id TEXT NOT NULL REFERENCES retrieval_policy_revisions(id),
    index_profile_revision_id TEXT NOT NULL
        REFERENCES knowledge_index_profile_revisions(id),
    dataset_manifest_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL UNIQUE,
    published_by TEXT NOT NULL REFERENCES users(id),
    published_at TEXT NOT NULL
);

CREATE TABLE knowledge_index_revisions(
    id TEXT PRIMARY KEY,
    provider_binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    index_profile_revision_id TEXT NOT NULL
        REFERENCES knowledge_index_profile_revisions(id),
    embedding_qualification_id TEXT REFERENCES embedding_qualification_snapshots(id),
    input_manifest_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    storage_uri TEXT,
    storage_sha256 TEXT,
    chunk_count INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    qualified_at TEXT,
    activated_at TEXT,
    error_code TEXT,
    UNIQUE(provider_binding_id,index_profile_revision_id,input_manifest_sha256)
);

CREATE TABLE knowledge_index_active_pointers(
    provider_binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    index_profile_revision_id TEXT NOT NULL
        REFERENCES knowledge_index_profile_revisions(id),
    index_revision_id TEXT NOT NULL REFERENCES knowledge_index_revisions(id),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(provider_binding_id,index_profile_revision_id)
);

CREATE TABLE knowledge_retrieval_runs(
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    provider_binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    index_revision_id TEXT NOT NULL REFERENCES knowledge_index_revisions(id),
    retrieval_policy_revision_id TEXT NOT NULL REFERENCES retrieval_policy_revisions(id),
    query_sha256 TEXT NOT NULL,
    allowed_source_set_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    receipt_uri TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    hit_count INTEGER NOT NULL,
    empty_reason TEXT,
    requested_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
