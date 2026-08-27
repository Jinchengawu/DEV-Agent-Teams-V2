CREATE TABLE knowledge_publications(
    id TEXT PRIMARY KEY,
    publication_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    delivery_id TEXT NOT NULL,
    pipeline_node_id TEXT NOT NULL,
    binding_site TEXT NOT NULL,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id),
    artifact_id TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    runtime_identity TEXT,
    required INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    target_space_id TEXT REFERENCES wiki_spaces(id),
    target_document_id TEXT REFERENCES wiki_documents(id),
    target_revision INTEGER,
    expected_document_version INTEGER,
    error_code TEXT,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT,
    CHECK(status IN ('pending','publishing','published','failed')),
    CHECK(required IN (0,1)),
    CHECK(attempt_count >= 0),
    CHECK(version >= 1)
);

CREATE INDEX idx_knowledge_publications_delivery_status
ON knowledge_publications(delivery_id,status,required,created_at,id);

CREATE INDEX idx_knowledge_publications_recovery
ON knowledge_publications(status,updated_at,id)
WHERE status IN ('pending','failed','publishing');
