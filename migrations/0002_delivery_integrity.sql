CREATE TABLE IF NOT EXISTS legacy_snapshot_audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_database TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    original_json TEXT NOT NULL,
    migration_action TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_imports(
    source_path TEXT PRIMARY KEY,
    source_sha256 TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

