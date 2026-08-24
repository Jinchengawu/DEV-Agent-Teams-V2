CREATE TABLE projects(
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE project_workspaces(
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    workspace_id TEXT NOT NULL UNIQUE,
    seed_revision TEXT,
    repository_ref TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    provision_attempt INTEGER NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE project_delivery_leases(
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    delivery_id TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL
);

CREATE TABLE project_pipeline_bindings(
    project_id TEXT NOT NULL REFERENCES projects(id),
    pipeline_id TEXT NOT NULL,
    pipeline_revision INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    is_default INTEGER NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id,pipeline_id,pipeline_revision)
);

CREATE UNIQUE INDEX idx_project_default_pipeline
ON project_pipeline_bindings(project_id)
WHERE enabled=1 AND is_default=1;

CREATE TABLE project_deployment_access(
    project_id TEXT NOT NULL REFERENCES projects(id),
    deployment_id TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id,deployment_id)
);

CREATE TABLE project_knowledge_sources(
    project_id TEXT NOT NULL REFERENCES projects(id),
    binding_id TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id,binding_id,source_scope)
);

CREATE TABLE project_migration_audit(
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    original_json TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    migrated_at TEXT NOT NULL,
    PRIMARY KEY(aggregate_type,aggregate_id)
);

CREATE TABLE project_migration_reports(
    migration_id TEXT PRIMARY KEY,
    delivery_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    evidence_count INTEGER NOT NULL,
    source_index_sha256 TEXT NOT NULL,
    migrated_at TEXT NOT NULL
);

INSERT INTO projects(
    id,slug,name,description,lifecycle_status,version,created_by,created_at,updated_at
) VALUES(
    'legacy-default','legacy-default','默认项目（历史迁移）',
    '承接 V0.3.1 及更早版本的内置 backend-demo 交付历史。',
    'active',1,'system',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
);

INSERT INTO project_workspaces(
    project_id,workspace_id,seed_revision,repository_ref,status,provision_attempt,
    error_code,created_at,updated_at
) VALUES(
    'legacy-default','backend-demo',NULL,'legacy/backend-demo','ready',1,NULL,
    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
);

ALTER TABLE deliveries ADD COLUMN project_id TEXT REFERENCES projects(id);
INSERT INTO project_migration_audit(
    aggregate_type,aggregate_id,original_sha256,original_json,
    normalized_sha256,migrated_at
)
SELECT 'delivery',id,sha256(snapshot_json),snapshot_json,
sha256(json_set(snapshot_json,'$.project_id','legacy-default')),CURRENT_TIMESTAMP
FROM deliveries;
UPDATE deliveries SET project_id='legacy-default' WHERE project_id IS NULL;
UPDATE deliveries
SET snapshot_json=json_set(snapshot_json,'$.project_id','legacy-default')
WHERE json_extract(snapshot_json,'$.project_id') IS NULL;
CREATE INDEX idx_deliveries_project ON deliveries(project_id);

ALTER TABLE product_events ADD COLUMN project_id TEXT REFERENCES projects(id);
UPDATE product_events
SET project_id='legacy-default'
WHERE aggregate_type='delivery' AND project_id IS NULL;
CREATE INDEX idx_product_events_project
ON product_events(project_id,sequence);

ALTER TABLE evidence_records ADD COLUMN project_id TEXT REFERENCES projects(id);
UPDATE evidence_records SET project_id='legacy-default' WHERE project_id IS NULL;
CREATE INDEX idx_evidence_project
ON evidence_records(project_id,created_at);

INSERT INTO project_migration_reports(
    migration_id,delivery_count,event_count,evidence_count,source_index_sha256,migrated_at
)
SELECT '0020-project-governance',
    (SELECT COUNT(*) FROM deliveries),
    (SELECT COUNT(*) FROM product_events),
    (SELECT COUNT(*) FROM evidence_records),
    sha256(
        COALESCE((SELECT group_concat(value,'|') FROM (
            SELECT id || ':' || project_id AS value FROM deliveries ORDER BY id
        )),'')
        || '#'
        || COALESCE((SELECT group_concat(value,'|') FROM (
            SELECT event_id || ':' || COALESCE(project_id,'') AS value
            FROM product_events ORDER BY event_id
        )),'')
        || '#'
        || COALESCE((SELECT group_concat(value,'|') FROM (
            SELECT id || ':' || project_id AS value FROM evidence_records ORDER BY id
        )),'')
    ),
    CURRENT_TIMESTAMP;
