from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class MigrationChecksumError(RuntimeError):
    pass


class MigrationRunner:
    def __init__(self, database: Path, migrations: Path) -> None:
        self.database = database
        self.migrations = migrations

    def migrate(self) -> tuple[int, ...]:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        applied_now: list[int] = []
        with self.connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations(
                version INTEGER PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL)"""
            )
            applied = dict(
                connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
            )
            for path in sorted(self.migrations.glob("[0-9][0-9][0-9][0-9]_*.sql")):
                version = int(path.name.split("_", 1)[0])
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                if version in applied:
                    if applied[version] != checksum:
                        raise MigrationChecksumError(
                            f"migration {version} checksum changed after application"
                        )
                    continue
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    for statement in _statements(sql):
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                        (version, checksum, datetime.now(UTC).isoformat()),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                applied_now.append(version)
        return tuple(applied_now)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.create_function("sha256", 1, _sql_sha256, deterministic=True)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


class LegacyDatabaseImporter:
    ACTIVE = {
        "queued",
        "planning",
        "awaiting_plan_decision",
        "awaiting_design_decision",
        "executing",
        "verifying",
        "awaiting_candidate_decision",
        "applying",
    }

    def __init__(self, runner: MigrationRunner, backup_dir: Path) -> None:
        self.runner = runner
        self.backup_dir = backup_dir

    def import_if_present(self, delivery_database: Path, control_database: Path) -> None:
        for source, kind in (
            (delivery_database, "deliveries"),
            (control_database, "control"),
        ):
            if source.exists() and source.resolve() != self.runner.database.resolve():
                self._import(source, kind)

    def _import(self, source: Path, kind: str) -> None:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.runner.connect() as target:
            existing = target.execute(
                "SELECT 1 FROM legacy_imports WHERE source_path=?", (str(source.resolve()),)
            ).fetchone()
            if existing:
                return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = self.backup_dir / f"{source.stem}-{digest[:12]}{source.suffix}"
        shutil.copy2(source, backup)
        with sqlite3.connect(source) as legacy, self.runner.connect() as target:
            target.execute("BEGIN IMMEDIATE")
            try:
                if kind == "deliveries":
                    self._import_deliveries(legacy, target, source)
                else:
                    self._import_control(legacy, target)
                target.execute(
                    """INSERT INTO legacy_imports(
                    source_path,source_sha256,backup_path,imported_at) VALUES(?,?,?,?)""",
                    (str(source.resolve()), digest, str(backup), datetime.now(UTC).isoformat()),
                )
                target.commit()
            except Exception:
                target.rollback()
                raise

    def _import_deliveries(
        self, legacy: sqlite3.Connection, target: sqlite3.Connection, source: Path
    ) -> None:
        if not _table_exists(legacy, "deliveries"):
            return
        for delivery_id, snapshot_json in legacy.execute("SELECT id,snapshot_json FROM deliveries"):
            snapshot = json.loads(snapshot_json)
            snapshot["project_id"] = "legacy-default"
            action = "copied"
            if snapshot.get("resolved_journey_sha256") == "0" * 64:
                snapshot["resolved_journey_sha256"] = None
            if _incomplete_active_snapshot(snapshot):
                snapshot["status"] = "failed"
                snapshot["error_code"] = "LEGACY_INCOMPLETE_EVIDENCE"
                snapshot["updated_at"] = datetime.now(UTC).isoformat()
                action = "failed-invalid-active"
            target.execute(
                """INSERT INTO legacy_snapshot_audit(
                source_database,aggregate_type,aggregate_id,original_sha256,original_json,
                migration_action,imported_at) VALUES(?,?,?,?,?,?,?)""",
                (
                    str(source.resolve()),
                    "delivery",
                    delivery_id,
                    hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                    snapshot_json,
                    action,
                    datetime.now(UTC).isoformat(),
                ),
            )
            target.execute(
                """INSERT OR IGNORE INTO deliveries(id,snapshot_json,project_id)
                VALUES(?,?,?)""",
                (
                    delivery_id,
                    json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    "legacy-default",
                ),
            )

    @staticmethod
    def _import_control(legacy: sqlite3.Connection, target: sqlite3.Connection) -> None:
        if _table_exists(legacy, "control_records"):
            target.executemany(
                "INSERT OR IGNORE INTO control_records(kind,id,snapshot_json) VALUES(?,?,?)",
                legacy.execute("SELECT kind,id,snapshot_json FROM control_records").fetchall(),
            )
        if _table_exists(legacy, "control_events"):
            target.executemany(
                """INSERT INTO control_events(event_type,aggregate_id,payload_json,created_at)
                VALUES(?,?,?,?)""",
                legacy.execute(
                    "SELECT event_type,aggregate_id,payload_json,created_at FROM control_events"
                ).fetchall(),
            )


def _statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer = f"{buffer}\n{line}".strip()
        if sqlite3.complete_statement(buffer):
            statements.append(buffer)
            buffer = ""
    if buffer:
        raise ValueError("migration contains an incomplete SQL statement")
    return tuple(statements)


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _sql_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _incomplete_active_snapshot(snapshot: dict[str, object]) -> bool:
    status = str(snapshot.get("status", ""))
    if status not in LegacyDatabaseImporter.ACTIVE:
        return False
    journey_hash = snapshot.get("resolved_journey_sha256")
    if not isinstance(journey_hash, str) or journey_hash == "0" * 64:
        return True
    if status == "awaiting_plan_decision":
        return not all(snapshot.get(name) for name in ("requirements", "task", "plan_gate"))
    if status == "awaiting_candidate_decision":
        return not all(
            snapshot.get(name) for name in ("candidate", "verification", "candidate_gate")
        )
    if status == "applying":
        return snapshot.get("candidate") is None
    return False
