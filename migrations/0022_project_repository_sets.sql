CREATE TABLE project_repositories(
    project_id TEXT NOT NULL REFERENCES projects(id),
    role TEXT NOT NULL CHECK(role IN ('backend','design','frontend','qa')),
    workspace_ref TEXT NOT NULL UNIQUE,
    repository_ref TEXT NOT NULL UNIQUE,
    seed_revision TEXT,
    status TEXT NOT NULL CHECK(status IN ('provisioning','ready','failed')),
    provision_attempt INTEGER NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id,role)
);

INSERT INTO project_repositories(
    project_id,role,workspace_ref,repository_ref,seed_revision,status,
    provision_attempt,error_code,created_at,updated_at
)
SELECT project_id,'backend',workspace_id,repository_ref,seed_revision,status,
       provision_attempt,error_code,created_at,updated_at
FROM project_workspaces;

CREATE INDEX idx_project_repositories_status
ON project_repositories(project_id,status,role);
