CREATE TABLE workcell_result_validations(
    id TEXT PRIMARY KEY,
    workcell_run_id TEXT NOT NULL UNIQUE REFERENCES workcell_runs(id),
    status TEXT NOT NULL CHECK(status IN ('passed','failed')),
    artifact_references_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
