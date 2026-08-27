CREATE TABLE evaluation_runs(
    id TEXT PRIMARY KEY,
    suite_json TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    baseline_json TEXT NOT NULL,
    mode TEXT NOT NULL,
    profile TEXT NOT NULL,
    seed INTEGER NOT NULL,
    concurrency_json TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    max_cost_usd REAL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    evidence_identity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_evaluation_runs_created
ON evaluation_runs(created_at DESC,id);

CREATE TABLE evaluation_case_results(
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
    case_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    category TEXT NOT NULL,
    difficulty INTEGER,
    status TEXT NOT NULL,
    candidate_score REAL,
    baseline_score REAL,
    metrics_json TEXT NOT NULL,
    judgment_json TEXT,
    artifact_sha256 TEXT NOT NULL,
    trace_sha256 TEXT,
    failure_code TEXT,
    evidence_identity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id,case_id)
);

CREATE INDEX idx_evaluation_cases_run
ON evaluation_case_results(run_id,dimension,category);

CREATE TABLE evaluation_reports(
    run_id TEXT PRIMARY KEY REFERENCES evaluation_runs(id),
    report_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE evaluation_human_reviews(
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
    case_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    notes_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id,case_id,reviewer_id)
);

CREATE INDEX idx_evaluation_reviews_run
ON evaluation_human_reviews(run_id,case_id);

CREATE TABLE evaluation_calibrations(
    id TEXT PRIMARY KEY,
    suite_sha256 TEXT NOT NULL,
    subject_fingerprint TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    metric_medians_json TEXT NOT NULL,
    metric_mad_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(suite_sha256,subject_fingerprint)
);
