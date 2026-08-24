CREATE TABLE agent_profiles(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    latest_revision INTEGER,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE agent_profile_drafts(
    profile_id TEXT PRIMARY KEY REFERENCES agent_profiles(id),
    spec_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    validation_status TEXT NOT NULL,
    validation_errors_json TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE agent_profile_revisions(
    profile_id TEXT NOT NULL REFERENCES agent_profiles(id),
    revision INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    published_by TEXT NOT NULL,
    published_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, revision),
    UNIQUE(sha256)
);

CREATE INDEX idx_agent_profile_revisions_published
ON agent_profile_revisions(profile_id, published_at DESC);
