CREATE TABLE agent_deployments(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    profile_revision INTEGER NOT NULL,
    profile_sha256 TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    instance_version INTEGER NOT NULL,
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    provider_revision TEXT NOT NULL,
    provider_fingerprint TEXT NOT NULL,
    isolation_mode TEXT NOT NULL,
    policy_snapshot_json TEXT NOT NULL,
    qualification_status TEXT NOT NULL,
    qualification_errors_json TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(profile_id,profile_revision)
        REFERENCES agent_profile_revisions(profile_id,revision)
);

CREATE INDEX idx_agent_deployments_profile
ON agent_deployments(profile_id,profile_revision);

CREATE INDEX idx_agent_deployments_instance
ON agent_deployments(instance_id,enabled,qualification_status);
