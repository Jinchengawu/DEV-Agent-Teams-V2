INSERT INTO team_templates(
    id,name,description,latest_revision,version,created_by,created_at,updated_at
) VALUES(
    'software-delivery-team','四仓软件交付团队','v0.5 内置四 Workcell 组织拓扑。',
    1,1,'system',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
);

INSERT INTO team_template_revisions(
    template_id,revision,name,description,workcells_json,topology_json,sha256,
    published_by,published_at
) VALUES(
    'software-delivery-team',1,'四仓软件交付团队','v0.5 内置四 Workcell 组织拓扑。',
    '[{"delegate_purposes":["workspace_write","artifact","review"],"delegation_policy":{"max_children":3,"max_concurrency":2,"max_depth":1,"max_writers":1,"wall_clock_budget_seconds":900},"name":"Design","primary_workspace":{"kind":"git_repository_v1"},"responsibility":"设计契约与可验证设计工件","workcell_key":"design"},{"delegate_purposes":["workspace_write","artifact","review"],"delegation_policy":{"max_children":3,"max_concurrency":2,"max_depth":1,"max_writers":1,"wall_clock_budget_seconds":900},"name":"Frontend","primary_workspace":{"kind":"git_repository_v1"},"responsibility":"前端实现与 UX 边界验证","workcell_key":"frontend"},{"delegate_purposes":["workspace_write","artifact","review"],"delegation_policy":{"max_children":3,"max_concurrency":2,"max_depth":1,"max_writers":1,"wall_clock_budget_seconds":900},"name":"Backend","primary_workspace":{"kind":"git_repository_v1"},"responsibility":"后端实现与安全边界验证","workcell_key":"backend"},{"delegate_purposes":["workspace_write","artifact","review"],"delegation_policy":{"max_children":3,"max_concurrency":2,"max_depth":1,"max_writers":1,"wall_clock_budget_seconds":900},"name":"QA","primary_workspace":{"kind":"git_repository_v1"},"responsibility":"测试设计、自动化、审查与追踪","workcell_key":"qa"}]',
    '{"links":[{"label":"artifact","source_workcell_key":"design","target_workcell_key":"frontend"},{"label":"artifact","source_workcell_key":"design","target_workcell_key":"backend"},{"label":"artifact","source_workcell_key":"frontend","target_workcell_key":"qa"},{"label":"artifact","source_workcell_key":"backend","target_workcell_key":"qa"}],"nodes":[{"workcell_key":"design","x":0,"y":120},{"workcell_key":"frontend","x":240,"y":120},{"workcell_key":"backend","x":480,"y":120},{"workcell_key":"qa","x":720,"y":120}]}',
    '7c8c12d7ae8332e0636a7e9b3d0ace53440f6cbe7da9cf86d80ff507de4ad116',
    'system',CURRENT_TIMESTAMP
);

CREATE TABLE project_team_bindings(
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    template_id TEXT NOT NULL,
    template_revision INTEGER NOT NULL,
    template_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('provisioning','active','legacy_projected')),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(template_id,template_revision)
        REFERENCES team_template_revisions(template_id,revision)
);

CREATE TABLE workspace_bindings(
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL CHECK(kind='git_repository_v1'),
    adapter_type TEXT NOT NULL CHECK(adapter_type IN ('managed-bare-git','external-git')),
    repository_uri TEXT NOT NULL,
    credential_reference TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending','ready','failed')),
    verification_sha256 TEXT,
    verification_json TEXT NOT NULL,
    error_code TEXT,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id,repository_uri)
);

CREATE TABLE project_workcell_bindings(
    project_id TEXT NOT NULL REFERENCES projects(id),
    workcell_key TEXT NOT NULL,
    workspace_binding_id TEXT NOT NULL UNIQUE REFERENCES workspace_bindings(id),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id,workcell_key)
);

CREATE INDEX idx_workspace_bindings_project_status
ON workspace_bindings(project_id,status,id);

INSERT INTO project_team_bindings(
    project_id,template_id,template_revision,template_sha256,status,version,updated_at
)
SELECT id,'software-delivery-team',1,
       '7c8c12d7ae8332e0636a7e9b3d0ace53440f6cbe7da9cf86d80ff507de4ad116',
       'legacy_projected',1,CURRENT_TIMESTAMP
FROM projects;

INSERT INTO workspace_bindings(
    id,project_id,kind,adapter_type,repository_uri,credential_reference,status,
    verification_sha256,verification_json,error_code,version,created_at,updated_at
)
SELECT 'legacy-' || project_id || '-' || role,project_id,'git_repository_v1',
       'managed-bare-git',repository_ref,NULL,
       CASE WHEN status='ready' THEN 'ready' ELSE 'failed' END,
       CASE WHEN status='ready' AND seed_revision IS NOT NULL
            THEN sha256(repository_ref || ':' || seed_revision) ELSE NULL END,
       json_object('legacy_projection',1,'repository_ref',repository_ref,
                   'seed_revision',seed_revision),
       error_code,1,created_at,updated_at
FROM project_repositories;

INSERT INTO project_workcell_bindings(
    project_id,workcell_key,workspace_binding_id,version,updated_at
)
SELECT project_id,role,'legacy-' || project_id || '-' || role,1,updated_at
FROM project_repositories;
