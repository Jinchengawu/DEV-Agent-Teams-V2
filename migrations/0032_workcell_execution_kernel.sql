CREATE TABLE workcell_runs(
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
    pipeline_run_id TEXT NOT NULL,
    stage_attempt_id TEXT NOT NULL,
    stage_path TEXT NOT NULL,
    loop_iteration INTEGER NOT NULL CHECK(loop_iteration >= 1),
    workcell_key TEXT NOT NULL,
    workcell_snapshot_json TEXT NOT NULL,
    workcell_snapshot_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'planning','delegating','verifying','reviewing','synthesizing',
        'succeeded','failed','cancelled','timed_out','interrupted'
    )),
    main_agent_run_id TEXT,
    version INTEGER NOT NULL,
    deadline_at TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(delivery_id,stage_attempt_id,loop_iteration)
);

ALTER TABLE agent_runs ADD COLUMN workcell_run_id TEXT REFERENCES workcell_runs(id);
ALTER TABLE agent_runs ADD COLUMN parent_agent_run_id TEXT REFERENCES agent_runs(id);
ALTER TABLE agent_runs ADD COLUMN root_agent_run_id TEXT REFERENCES agent_runs(id);
ALTER TABLE agent_runs ADD COLUMN depth INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN run_role TEXT NOT NULL DEFAULT 'main';
ALTER TABLE agent_runs ADD COLUMN delegate_purpose TEXT;
ALTER TABLE agent_runs ADD COLUMN workspace_access TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE agent_runs ADD COLUMN slot_key TEXT;

UPDATE agent_runs SET root_agent_run_id=id WHERE root_agent_run_id IS NULL;

CREATE TABLE agent_attempts(
    id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id),
    phase TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    provider_binding_hash TEXT NOT NULL,
    runtime_identity TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    result_artifact_sha256 TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(agent_run_id,ordinal)
);

INSERT INTO agent_attempts(
    id,agent_run_id,phase,ordinal,provider_binding_hash,runtime_identity,status,
    error_code,result_artifact_sha256,started_at,finished_at
)
SELECT attempt_id,id,'legacy',1,resolved_binding_hash,runtime_identity,status,
       NULL,NULL,created_at,
       CASE WHEN status='running' THEN NULL ELSE updated_at END
FROM agent_runs;

CREATE TABLE delegation_plans(
    id TEXT PRIMARY KEY,
    workcell_run_id TEXT NOT NULL UNIQUE REFERENCES workcell_runs(id),
    main_agent_run_id TEXT NOT NULL REFERENCES agent_runs(id),
    assignments_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE workcell_candidate_verifications(
    id TEXT PRIMARY KEY,
    workcell_run_id TEXT NOT NULL UNIQUE REFERENCES workcell_runs(id),
    writer_agent_run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(id),
    candidate_sha TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('passed','failed')),
    report_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE review_artifacts(
    id TEXT PRIMARY KEY,
    workcell_run_id TEXT NOT NULL REFERENCES workcell_runs(id),
    reviewer_agent_run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(id),
    candidate_sha TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL,
    reviewer_binding_hash TEXT NOT NULL,
    blocking_findings_json TEXT NOT NULL,
    artifact_reference_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE workcell_results(
    id TEXT PRIMARY KEY,
    workcell_run_id TEXT NOT NULL UNIQUE REFERENCES workcell_runs(id),
    candidate_sha TEXT,
    diff_sha256 TEXT,
    verification_sha256 TEXT NOT NULL,
    review_artifact_ids_json TEXT NOT NULL,
    output_artifact_references_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_workcell_runs_delivery
ON workcell_runs(delivery_id,created_at,id);

CREATE INDEX idx_agent_runs_workcell_parent
ON agent_runs(workcell_run_id,parent_agent_run_id,created_at,id);

CREATE INDEX idx_agent_attempts_run
ON agent_attempts(agent_run_id,ordinal);
