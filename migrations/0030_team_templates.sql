CREATE TABLE team_templates(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    latest_revision INTEGER,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE team_template_drafts(
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES team_templates(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    workcells_json TEXT NOT NULL,
    topology_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    validation_status TEXT NOT NULL,
    validation_errors_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_team_template_drafts_template
ON team_template_drafts(template_id,updated_at DESC);

CREATE TABLE team_template_revisions(
    template_id TEXT NOT NULL REFERENCES team_templates(id),
    revision INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    workcells_json TEXT NOT NULL,
    topology_json TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    published_by TEXT NOT NULL,
    published_at TEXT NOT NULL,
    PRIMARY KEY(template_id,revision)
);

CREATE INDEX idx_team_template_revisions_published
ON team_template_revisions(template_id,published_at DESC);
