from __future__ import annotations

import sqlite3
from pathlib import Path

from ...delivery import ReleaseManifest
from .domain import ReleaseApplyAttempt


class SQLiteReleaseRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def get_attempt(self, delivery_id: str) -> ReleaseApplyAttempt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM release_apply_attempts WHERE delivery_id=?",
                (delivery_id,),
            ).fetchone()
        return None if row is None else ReleaseApplyAttempt.model_validate_json(row[0])

    def put_attempt(self, attempt: ReleaseApplyAttempt, *, expected_version: int | None) -> None:
        with self._connect() as connection:
            if expected_version is None:
                try:
                    connection.execute(
                        """INSERT INTO release_apply_attempts(
                        delivery_id,project_id,bundle_sha256,status,snapshot_json,version,
                        created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                        _attempt_values(attempt),
                    )
                except sqlite3.IntegrityError as error:
                    raise RuntimeError("release attempt already exists") from error
                return
            cursor = connection.execute(
                """UPDATE release_apply_attempts SET status=?,snapshot_json=?,version=?,
                updated_at=? WHERE delivery_id=? AND version=?""",
                (
                    attempt.status,
                    attempt.model_dump_json(),
                    attempt.version,
                    attempt.updated_at.isoformat(),
                    attempt.delivery_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("release attempt version conflict")

    def activate_manifest(self, manifest: ReleaseManifest) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT delivery_id,manifest_sha256 FROM project_release_manifests "
                "WHERE project_id=?",
                (manifest.project_id,),
            ).fetchone()
            if existing is not None and existing[0] == manifest.delivery_id:
                if existing[1] != manifest.manifest_sha256:
                    raise RuntimeError("release manifest integrity conflict")
                connection.commit()
                return
            connection.execute(
                """INSERT INTO project_release_manifests(
                project_id,delivery_id,bundle_sha256,manifest_sha256,snapshot_json,activated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
                delivery_id=excluded.delivery_id,bundle_sha256=excluded.bundle_sha256,
                manifest_sha256=excluded.manifest_sha256,
                snapshot_json=excluded.snapshot_json,activated_at=excluded.activated_at""",
                (
                    manifest.project_id,
                    manifest.delivery_id,
                    manifest.bundle_sha256,
                    manifest.manifest_sha256,
                    manifest.model_dump_json(),
                    manifest.activated_at.isoformat(),
                ),
            )
            connection.commit()

    def get_manifest(self, project_id: str) -> ReleaseManifest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM project_release_manifests WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return None if row is None else ReleaseManifest.model_validate_json(row[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


def _attempt_values(attempt: ReleaseApplyAttempt) -> tuple[object, ...]:
    return (
        attempt.delivery_id,
        attempt.project_id,
        attempt.bundle_sha256,
        attempt.status,
        attempt.model_dump_json(),
        attempt.version,
        attempt.created_at.isoformat(),
        attempt.updated_at.isoformat(),
    )
