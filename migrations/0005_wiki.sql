CREATE TABLE IF NOT EXISTS wiki_spaces(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_documents(
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL REFERENCES wiki_spaces(id),
    parent_id TEXT REFERENCES wiki_documents(id),
    title TEXT NOT NULL,
    current_revision INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_revisions(
    document_id TEXT NOT NULL REFERENCES wiki_documents(id),
    revision INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    search_text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY(document_id, revision)
);

CREATE TABLE IF NOT EXISTS wiki_comments(
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES wiki_documents(id),
    parent_id TEXT REFERENCES wiki_comments(id),
    body TEXT NOT NULL,
    author_id TEXT REFERENCES users(id),
    resolved INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_permissions(
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    access TEXT NOT NULL,
    PRIMARY KEY(resource_kind, resource_id, user_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
    document_id UNINDEXED,
    space_id UNINDEXED,
    title,
    content
);

