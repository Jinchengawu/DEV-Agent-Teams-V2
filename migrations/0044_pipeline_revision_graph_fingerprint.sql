DROP INDEX idx_pipeline_revisions_published;

ALTER TABLE pipeline_revisions
RENAME TO pipeline_revisions_with_unique_fingerprint;

CREATE TABLE pipeline_revisions(
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
    revision INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    compiled_graph_json TEXT NOT NULL,
    binding_snapshot_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    published_by TEXT NOT NULL,
    published_at TEXT NOT NULL,
    binding_model TEXT NOT NULL DEFAULT 'legacy-v0',
    resolved_provider_bindings_json TEXT NOT NULL DEFAULT '{}',
    workcell_stage_map_json TEXT NOT NULL DEFAULT '{}',
    release_contract_snapshot_json TEXT NOT NULL DEFAULT '[]',
    knowledge_context_bindings_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(pipeline_id, revision)
);

INSERT INTO pipeline_revisions(
    pipeline_id,
    revision,
    definition_json,
    compiled_graph_json,
    binding_snapshot_json,
    fingerprint,
    published_by,
    published_at,
    binding_model,
    resolved_provider_bindings_json,
    workcell_stage_map_json,
    release_contract_snapshot_json,
    knowledge_context_bindings_json
)
SELECT
    pipeline_id,
    revision,
    definition_json,
    compiled_graph_json,
    binding_snapshot_json,
    fingerprint,
    published_by,
    published_at,
    binding_model,
    resolved_provider_bindings_json,
    workcell_stage_map_json,
    release_contract_snapshot_json,
    knowledge_context_bindings_json
FROM pipeline_revisions_with_unique_fingerprint;

DROP TABLE pipeline_revisions_with_unique_fingerprint;

CREATE INDEX idx_pipeline_revisions_published
ON pipeline_revisions(pipeline_id, published_at DESC);
