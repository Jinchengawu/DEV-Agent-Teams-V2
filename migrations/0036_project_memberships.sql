CREATE TABLE project_memberships(
    project_id TEXT NOT NULL REFERENCES projects(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id,user_id)
);

CREATE INDEX idx_project_memberships_user
ON project_memberships(user_id,project_id);

CREATE TABLE project_authorization_versions(
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE project_access_audit(
    id TEXT PRIMARY KEY,
    actor_user_id TEXT NOT NULL REFERENCES users(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    capability TEXT NOT NULL,
    resource TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_project_access_audit_project
ON project_access_audit(project_id,created_at,id);

INSERT INTO project_authorization_versions(project_id,version,updated_at)
SELECT id,1,CURRENT_TIMESTAMP FROM projects;

INSERT INTO project_memberships(
    project_id,user_id,role,version,created_at,updated_at
)
SELECT projects.id,projects.created_by,'owner',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
FROM projects
JOIN users ON users.id=projects.created_by
WHERE projects.id<>'legacy-default';
