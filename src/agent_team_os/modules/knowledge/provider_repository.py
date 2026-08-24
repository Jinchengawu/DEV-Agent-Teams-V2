from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from ...shared.events import ProductEvent
from .provider_domain import (
    ProviderBinding,
    ProviderSnapshotRecord,
    ProviderSyncRun,
    ProviderSyncStatus,
)

JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class SQLiteProviderKnowledgeRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create_binding(self, binding: ProviderBinding) -> ProviderBinding | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO knowledge_provider_bindings(
                    id,provider_kind,display_name,external_space_id,credential_ref,enabled,
                    version,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        binding.id,
                        binding.provider_kind.value,
                        binding.display_name,
                        binding.external_space_id,
                        binding.credential_ref,
                        int(binding.enabled),
                        binding.version,
                        binding.created_by,
                        binding.created_at.isoformat(),
                        binding.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return None
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.source-linked",
                    aggregate_type="knowledge-provider-binding",
                    aggregate_id=binding.id,
                    aggregate_version=binding.version,
                    payload={
                        "provider_kind": binding.provider_kind.value,
                        "external_space_id": binding.external_space_id,
                    },
                    occurred_at=binding.updated_at,
                ),
            )
            connection.commit()
        return binding

    def get_binding(self, binding_id: str) -> ProviderBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_provider_bindings WHERE id=?", (binding_id,)
            ).fetchone()
        return None if row is None else self._binding(row)

    def list_bindings(self) -> tuple[ProviderBinding, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_provider_bindings ORDER BY display_name,id"
            ).fetchall()
        return tuple(self._binding(row) for row in rows)

    def begin_sync(self, run: ProviderSyncRun) -> None:
        if run.status != ProviderSyncStatus.RUNNING or run.started_at is None:
            raise ValueError("Only a started Provider Sync Run may be persisted")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO knowledge_provider_sync_runs(
                id,binding_id,source_id,status,provider_revision,snapshot_id,
                snapshot_sha256,error_code,started_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                self._run_values(run),
            )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.sync-requested",
                    aggregate_type="knowledge-provider-sync",
                    aggregate_id=run.id,
                    aggregate_version=1,
                    payload={"binding_id": run.binding_id, "source_id": run.source_id},
                    occurred_at=run.started_at,
                ),
            )
            connection.commit()

    def complete_sync(
        self, run: ProviderSyncRun, snapshot: ProviderSnapshotRecord
    ) -> ProviderSnapshotRecord | None:
        if run.status != ProviderSyncStatus.SUCCEEDED or run.completed_at is None:
            raise ValueError("Only a successful Provider Sync Run may be completed")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                """SELECT * FROM knowledge_provider_snapshots
                WHERE binding_id=? AND source_id=? AND provider_revision=?""",
                (snapshot.binding_id, snapshot.source_id, snapshot.provider_revision),
            ).fetchone()
            if existing_row is not None:
                existing = self._snapshot(existing_row)
                if existing.content_sha256 != snapshot.content_sha256:
                    connection.rollback()
                    return None
                snapshot = existing
            else:
                connection.execute(
                    """INSERT INTO knowledge_provider_snapshots(
                    id,binding_id,source_id,provider_revision,content_type,
                    normalized_content_json,normalized_text,content_sha256,source_url,
                    fetched_by_product_user_id,fetched_by_provider_user_id,fetched_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot.id,
                        snapshot.binding_id,
                        snapshot.source_id,
                        snapshot.provider_revision,
                        snapshot.content_type,
                        json.dumps(
                            snapshot.normalized_content,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        snapshot.normalized_text,
                        snapshot.content_sha256,
                        snapshot.source_url,
                        snapshot.fetched_by_product_user_id,
                        snapshot.fetched_by_provider_user_id,
                        snapshot.fetched_at.isoformat(),
                    ),
                )
                connection.execute(
                    """INSERT INTO knowledge_provider_fts(
                    snapshot_id,binding_id,source_id,content) VALUES(?,?,?,?)""",
                    (
                        snapshot.id,
                        snapshot.binding_id,
                        snapshot.source_id,
                        snapshot.normalized_text,
                    ),
                )
            completed = run.model_copy(update={"snapshot_id": snapshot.id})
            updated = connection.execute(
                """UPDATE knowledge_provider_sync_runs SET status=?,provider_revision=?,
                snapshot_id=?,snapshot_sha256=?,error_code=?,started_at=?,completed_at=?
                WHERE id=? AND status=?""",
                (
                    completed.status.value,
                    completed.provider_revision,
                    completed.snapshot_id,
                    completed.snapshot_sha256,
                    completed.error_code,
                    completed.started_at.isoformat() if completed.started_at else None,
                    completed.completed_at.isoformat() if completed.completed_at else None,
                    completed.id,
                    ProviderSyncStatus.RUNNING.value,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise RuntimeError("Provider Sync Run is not running")
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.document-synced",
                    aggregate_type="knowledge-provider-sync",
                    aggregate_id=completed.id,
                    aggregate_version=1,
                    payload={
                        "binding_id": completed.binding_id,
                        "source_id": completed.source_id,
                        "provider_revision": completed.provider_revision or "",
                        "snapshot_id": snapshot.id,
                        "snapshot_sha256": str(snapshot.content_sha256),
                    },
                    occurred_at=run.completed_at,
                ),
            )
            connection.commit()
        return snapshot

    def fail_sync(self, run: ProviderSyncRun) -> None:
        if (
            run.status
            not in {
                ProviderSyncStatus.FAILED,
                ProviderSyncStatus.UNAVAILABLE,
            }
            or run.completed_at is None
        ):
            raise ValueError("Only a failed Provider Sync Run may be finalized")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE knowledge_provider_sync_runs SET status=?,provider_revision=?,
                snapshot_id=?,snapshot_sha256=?,error_code=?,started_at=?,completed_at=?
                WHERE id=? AND status=?""",
                (
                    run.status.value,
                    run.provider_revision,
                    run.snapshot_id,
                    run.snapshot_sha256,
                    run.error_code,
                    run.started_at.isoformat() if run.started_at else None,
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.id,
                    ProviderSyncStatus.RUNNING.value,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise RuntimeError("Provider Sync Run is not running")
            if run.status == ProviderSyncStatus.UNAVAILABLE:
                self._append_event(
                    connection,
                    ProductEvent(
                        event_type="knowledge.source-unavailable",
                        aggregate_type="knowledge-provider-sync",
                        aggregate_id=run.id,
                        aggregate_version=1,
                        payload={
                            "binding_id": run.binding_id,
                            "source_id": run.source_id,
                            "error_code": run.error_code or "",
                        },
                        occurred_at=run.completed_at,
                    ),
                )
            connection.commit()

    def get_snapshot(self, snapshot_id: str) -> ProviderSnapshotRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_provider_snapshots WHERE id=?", (snapshot_id,)
            ).fetchone()
        return None if row is None else self._snapshot(row)

    def list_sync_runs(self, binding_id: str) -> tuple[ProviderSyncRun, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_provider_sync_runs
                WHERE binding_id=? ORDER BY started_at DESC,id""",
                (binding_id,),
            ).fetchall()
        return tuple(self._run(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _binding(row: sqlite3.Row) -> ProviderBinding:
        return ProviderBinding.model_validate(dict(row))

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> ProviderSnapshotRecord:
        payload = dict(row)
        payload["normalized_content"] = JSON_ADAPTER.validate_json(
            str(payload.pop("normalized_content_json"))
        )
        return ProviderSnapshotRecord.model_validate(payload)

    @staticmethod
    def _run(row: sqlite3.Row) -> ProviderSyncRun:
        return ProviderSyncRun.model_validate(dict(row))

    @staticmethod
    def _run_values(run: ProviderSyncRun) -> tuple[object, ...]:
        return (
            run.id,
            run.binding_id,
            run.source_id,
            run.status.value,
            run.provider_revision,
            run.snapshot_id,
            run.snapshot_sha256,
            run.error_code,
            run.started_at.isoformat() if run.started_at else None,
            run.completed_at.isoformat() if run.completed_at else None,
        )

    @staticmethod
    def _append_event(connection: sqlite3.Connection, event: ProductEvent) -> None:
        connection.execute(
            """INSERT INTO product_events(
            event_id,event_type,aggregate_type,aggregate_id,aggregate_version,payload_json,occurred_at)
            VALUES(?,?,?,?,?,?,?)""",
            (
                event.id,
                event.event_type,
                event.aggregate_type,
                event.aggregate_id,
                event.aggregate_version,
                json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
                event.occurred_at.isoformat(),
            ),
        )
