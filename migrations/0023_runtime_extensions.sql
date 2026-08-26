CREATE TABLE runtime_extensions(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('skill','plugin','mcp')),
    extension_version TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    revision_sha256 TEXT NOT NULL,
    requested_permissions_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('installed','qualified','failed','disabled')),
    qualification_sha256 TEXT,
    qualification_errors_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_runtime_extensions_status
ON runtime_extensions(status,kind,id);
