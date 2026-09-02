CREATE TABLE knowledge_connections(
    id TEXT PRIMARY KEY,
    provider_kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    access_model TEXT NOT NULL,
    app_id_ref TEXT NOT NULL,
    app_secret_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    authorization_version INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_diagnosed_at TEXT,
    last_error_code TEXT,
    UNIQUE(provider_kind,app_id_ref)
);

CREATE TABLE knowledge_provider_bindings_v2(
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES knowledge_connections(id),
    display_name TEXT NOT NULL,
    external_space_id TEXT NOT NULL,
    root_node_token TEXT,
    status TEXT NOT NULL,
    authorization_version INTEGER NOT NULL,
    version INTEGER NOT NULL,
    replaces_binding_id TEXT REFERENCES knowledge_provider_bindings(id),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_permission_probe_at TEXT,
    last_error_code TEXT,
    UNIQUE(connection_id,external_space_id,root_node_token)
);

CREATE TABLE project_knowledge_source_approvals_v2(
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    enabled INTEGER NOT NULL,
    rag_enabled INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id,binding_id)
);

CREATE TABLE knowledge_provider_binding_nodes_v2(
    binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    external_id TEXT NOT NULL,
    external_space_id TEXT NOT NULL,
    parent_external_id TEXT,
    source_id TEXT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    provider_revision TEXT,
    updated_at TEXT,
    PRIMARY KEY(binding_id,external_id)
);

CREATE INDEX knowledge_provider_binding_nodes_source_idx
    ON knowledge_provider_binding_nodes_v2(binding_id,source_id);

CREATE TABLE knowledge_binding_migration_receipts(
    id TEXT PRIMARY KEY,
    migration_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    legacy_binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings(id),
    replacement_binding_id TEXT NOT NULL REFERENCES knowledge_provider_bindings_v2(id),
    approval_id TEXT NOT NULL REFERENCES project_knowledge_source_approvals_v2(id),
    activated_at TEXT NOT NULL
);
