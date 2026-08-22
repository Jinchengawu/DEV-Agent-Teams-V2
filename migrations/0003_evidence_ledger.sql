CREATE TABLE IF NOT EXISTS evidence_records(
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    producer_identity TEXT NOT NULL,
    content_sha256 TEXT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    verification_error TEXT,
    UNIQUE(delivery_id, kind, source_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_delivery
ON evidence_records(delivery_id, created_at);

CREATE TABLE IF NOT EXISTS evidence_verifications(
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL REFERENCES evidence_records(id),
    status TEXT NOT NULL,
    error TEXT,
    verified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_verification
ON evidence_verifications(evidence_id, verified_at);
