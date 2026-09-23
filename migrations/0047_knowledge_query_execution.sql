-- 内部查询 sidecar；不修改历史 Index、Qualification 或 Preparation。
CREATE TABLE knowledge_query_admissions(
    preparation_run_id TEXT PRIMARY KEY,
    admission_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE knowledge_query_qualifications(
    qualification_sha256 TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
CREATE TABLE knowledge_query_plans(
    execution_key TEXT PRIMARY KEY,
    identity_json TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    unit_count INTEGER NOT NULL CHECK(unit_count > 0),
    fence INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at TEXT,
    lease_started_at TEXT,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    artifact_sha256 TEXT,
    final_context_sha256 TEXT
);
CREATE TABLE knowledge_query_unit_receipts(
    execution_key TEXT NOT NULL REFERENCES knowledge_query_plans(execution_key),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    result_sha256 TEXT NOT NULL,
    PRIMARY KEY(execution_key, ordinal)
);
CREATE UNIQUE INDEX knowledge_query_stage_identity ON knowledge_query_plans(
    json_extract(identity_json,'$.preparation_run_id'),
    json_extract(identity_json,'$.stage_path'),
    json_extract(identity_json,'$.binding_id')
);
CREATE TABLE knowledge_query_safe_errors(
    execution_key TEXT NOT NULL REFERENCES knowledge_query_plans(execution_key),
    correlation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(execution_key, correlation_id)
);
