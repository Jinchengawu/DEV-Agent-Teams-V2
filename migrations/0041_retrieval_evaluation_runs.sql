ALTER TABLE knowledge_index_revisions ADD COLUMN evaluation_report_uri TEXT;
ALTER TABLE knowledge_index_revisions ADD COLUMN evaluation_report_sha256 TEXT;

CREATE TABLE retrieval_evaluation_runs(
    id TEXT PRIMARY KEY,
    evaluation_policy_revision_id TEXT NOT NULL
        REFERENCES retrieval_evaluation_policy_revisions(id),
    index_revision_id TEXT NOT NULL REFERENCES knowledge_index_revisions(id),
    dataset_manifest_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('passed','failed')),
    metrics_json TEXT NOT NULL,
    report_uri TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    run_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE(evaluation_policy_revision_id,index_revision_id)
);
