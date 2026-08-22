CREATE TABLE IF NOT EXISTS app_settings(
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    snapshot_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_order(
    work_item_id TEXT PRIMARY KEY,
    column_id TEXT NOT NULL,
    rank TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

