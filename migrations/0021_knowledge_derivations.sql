CREATE TABLE knowledge_derivations(
    document_id TEXT PRIMARY KEY REFERENCES wiki_documents(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    target_space_id TEXT NOT NULL REFERENCES wiki_spaces(id),
    source_kind TEXT NOT NULL CHECK(source_kind IN ('evidence','provider-snapshot')),
    source_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE(project_id,target_space_id,source_kind,source_id)
);

CREATE INDEX idx_knowledge_derivations_source
ON knowledge_derivations(project_id,source_kind,source_id);
