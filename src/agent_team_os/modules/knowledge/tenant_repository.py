from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ...shared.events import ProductEvent
from ..artifacts import ArtifactReference
from .provider_domain import ProviderNode
from .tenant_domain import (
    KnowledgeSyncJob,
    TenantConnection,
    TenantProviderBinding,
    TenantProviderSnapshotRecord,
)


class SQLiteTenantKnowledgeRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create_connection(self, connection_record: TenantConnection) -> TenantConnection | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO knowledge_connections(
                    id,provider_kind,display_name,access_model,app_id_ref,app_secret_ref,status,
                    authorization_version,version,created_by,created_at,updated_at,
                    last_diagnosed_at,last_error_code)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        connection_record.id,
                        connection_record.provider_kind.value,
                        connection_record.display_name,
                        connection_record.access_model,
                        connection_record.app_id_ref,
                        connection_record.app_secret_ref,
                        connection_record.status,
                        connection_record.authorization_version,
                        connection_record.version,
                        connection_record.created_by,
                        connection_record.created_at.isoformat(),
                        connection_record.updated_at.isoformat(),
                        None,
                        None,
                    ),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return None
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.connection-created",
                    aggregate_type="knowledge-connection",
                    aggregate_id=connection_record.id,
                    aggregate_version=connection_record.version,
                    payload={
                        "provider_kind": connection_record.provider_kind.value,
                        "access_model": connection_record.access_model,
                    },
                    occurred_at=connection_record.created_at,
                ),
            )
            connection.commit()
        return connection_record

    def get_connection(self, connection_id: str) -> TenantConnection | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_connections WHERE id=?", (connection_id,)
            ).fetchone()
        return None if row is None else TenantConnection.model_validate(dict(row))

    def list_connections(self) -> tuple[TenantConnection, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_connections ORDER BY display_name,id"
            ).fetchall()
        return tuple(TenantConnection.model_validate(dict(row)) for row in rows)

    def update_connection(self, connection_record: TenantConnection, expected_version: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE knowledge_connections SET status=?,authorization_version=?,
                version=?,updated_at=?,
                last_diagnosed_at=?,last_error_code=? WHERE id=? AND version=?""",
                (
                    connection_record.status,
                    connection_record.authorization_version,
                    connection_record.version,
                    connection_record.updated_at.isoformat(),
                    (
                        None
                        if connection_record.last_diagnosed_at is None
                        else connection_record.last_diagnosed_at.isoformat()
                    ),
                    connection_record.last_error_code,
                    connection_record.id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_CONNECTION_VERSION_CONFLICT")
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.connection-diagnosed",
                    aggregate_type="knowledge-connection",
                    aggregate_id=connection_record.id,
                    aggregate_version=connection_record.version,
                    payload={
                        "status": connection_record.status,
                        "error_code": connection_record.last_error_code or "",
                    },
                    occurred_at=connection_record.updated_at,
                ),
            )
            connection.commit()

    def create_binding(
        self,
        binding: TenantProviderBinding,
        nodes: tuple[ProviderNode, ...],
    ) -> TenantProviderBinding | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO knowledge_provider_bindings_v2(
                    id,connection_id,display_name,external_space_id,root_node_token,status,
                    authorization_version,version,replaces_binding_id,created_by,created_at,
                    updated_at,last_permission_probe_at,last_error_code)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        binding.id,
                        binding.connection_id,
                        binding.display_name,
                        binding.external_space_id,
                        binding.root_node_token,
                        binding.status,
                        binding.authorization_version,
                        binding.version,
                        binding.replaces_binding_id,
                        binding.created_by,
                        binding.created_at.isoformat(),
                        binding.updated_at.isoformat(),
                        (
                            None
                            if binding.last_permission_probe_at is None
                            else binding.last_permission_probe_at.isoformat()
                        ),
                        binding.last_error_code,
                    ),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return None
            self._insert_binding_nodes(connection, binding.id, nodes)
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.tenant-binding-created",
                    aggregate_type="knowledge-provider-binding-v2",
                    aggregate_id=binding.id,
                    aggregate_version=binding.version,
                    payload={
                        "connection_id": binding.connection_id,
                        "external_space_id": binding.external_space_id,
                        "root_node_token": binding.root_node_token or "",
                    },
                    occurred_at=binding.created_at,
                ),
            )
            connection.commit()
        return binding

    def get_binding(self, binding_id: str) -> TenantProviderBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_provider_bindings_v2 WHERE id=?", (binding_id,)
            ).fetchone()
        return None if row is None else TenantProviderBinding.model_validate(dict(row))

    def list_bindings(self) -> tuple[TenantProviderBinding, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_provider_bindings_v2 ORDER BY display_name,id"
            ).fetchall()
        return tuple(TenantProviderBinding.model_validate(dict(row)) for row in rows)

    def list_binding_nodes(self, binding_id: str) -> tuple[ProviderNode, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT external_id,external_space_id,parent_external_id,source_id,
                title,kind,provider_revision,updated_at
                FROM knowledge_provider_binding_nodes_v2 WHERE binding_id=?
                ORDER BY external_id""",
                (binding_id,),
            ).fetchall()
        return tuple(ProviderNode.model_validate(dict(row)) for row in rows)

    def refresh_binding_nodes(
        self,
        binding: TenantProviderBinding,
        nodes: tuple[ProviderNode, ...],
        *,
        probed_at: datetime,
    ) -> TenantProviderBinding:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old_rows = connection.execute(
                """SELECT external_id,parent_external_id,source_id,kind,provider_revision
                FROM knowledge_provider_binding_nodes_v2 WHERE binding_id=?
                ORDER BY external_id""",
                (binding.id,),
            ).fetchall()
            old_scope = tuple(tuple(row) for row in old_rows)
            new_scope = tuple(
                sorted(
                    (
                        node.external_id,
                        node.parent_external_id,
                        node.source_id,
                        node.kind.value,
                        node.provider_revision,
                    )
                    for node in nodes
                )
            )
            old_source_ids = {
                str(row["source_id"]) for row in old_rows if row["source_id"] is not None
            }
            new_source_ids = {node.source_id for node in nodes if node.source_id is not None}
            removed_source_ids = tuple(sorted(old_source_ids - new_source_ids))
            next_authorization_version = binding.authorization_version + int(old_scope != new_scope)
            updated = connection.execute(
                """UPDATE knowledge_provider_bindings_v2 SET status='ready',
                authorization_version=?,version=version+1,updated_at=?,
                last_permission_probe_at=?,last_error_code=NULL WHERE id=? AND version=?""",
                (
                    next_authorization_version,
                    probed_at.isoformat(),
                    probed_at.isoformat(),
                    binding.id,
                    binding.version,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_PROVIDER_BINDING_VERSION_CONFLICT")
            if removed_source_ids:
                connection.executemany(
                    """UPDATE knowledge_provider_source_heads_v2
                    SET status='tombstoned',permission_probe_at=?,
                    authorization_version=authorization_version+1,updated_at=?
                    WHERE binding_id=? AND source_id=?""",
                    (
                        (
                            probed_at.isoformat(),
                            probed_at.isoformat(),
                            binding.id,
                            source_id,
                        )
                        for source_id in removed_source_ids
                    ),
                )
            connection.execute(
                "DELETE FROM knowledge_provider_binding_nodes_v2 WHERE binding_id=?",
                (binding.id,),
            )
            self._insert_binding_nodes(connection, binding.id, nodes)
            row = connection.execute(
                "SELECT * FROM knowledge_provider_bindings_v2 WHERE id=?", (binding.id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return TenantProviderBinding.model_validate(dict(row))

    def create_sync_job(self, job: KnowledgeSyncJob) -> tuple[KnowledgeSyncJob, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM knowledge_sync_jobs WHERE project_id=? AND idempotency_key=?",
                (job.project_id, job.idempotency_key),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return _sync_job(existing), False
            connection.execute(
                """INSERT INTO knowledge_sync_jobs(
                id,project_id,binding_id,source_id,idempotency_key,status,attempt,max_attempts,
                lease_owner,lease_expires_at,retry_at,provider_revision,snapshot_id,
                snapshot_sha256,error_code,requested_by,version,created_at,updated_at,
                started_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.id,
                    job.project_id,
                    job.binding_id,
                    job.source_id,
                    job.idempotency_key,
                    job.status,
                    job.attempt,
                    job.max_attempts,
                    job.lease_owner,
                    _iso(job.lease_expires_at),
                    _iso(job.retry_at),
                    job.provider_revision,
                    job.snapshot_id,
                    job.snapshot_sha256,
                    job.error_code,
                    job.requested_by,
                    job.version,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    _iso(job.started_at),
                    _iso(job.completed_at),
                ),
            )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.sync-job-requested",
                    aggregate_type="knowledge-sync-job",
                    aggregate_id=job.id,
                    aggregate_version=job.version,
                    payload={
                        "project_id": job.project_id,
                        "binding_id": job.binding_id,
                        "source_id": job.source_id,
                    },
                    occurred_at=job.created_at,
                ),
            )
            connection.commit()
        return job, True

    def get_sync_job(self, job_id: str) -> KnowledgeSyncJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_sync_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return None if row is None else _sync_job(row)

    def list_sync_jobs(
        self, project_id: str, binding_id: str
    ) -> tuple[KnowledgeSyncJob, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_sync_jobs
                WHERE project_id=? AND binding_id=?
                ORDER BY created_at DESC,id DESC""",
                (project_id, binding_id),
            ).fetchall()
        return tuple(_sync_job(row) for row in rows)

    def list_due_sync_jobs(
        self,
        now: datetime,
        *,
        limit: int,
    ) -> tuple[KnowledgeSyncJob, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_sync_jobs
                WHERE attempt < max_attempts AND (
                    status='queued' OR (
                        status='retry_wait' AND (retry_at IS NULL OR retry_at<=?)
                    )
                )
                ORDER BY COALESCE(retry_at,created_at),created_at,id
                LIMIT ?""",
                (now.isoformat(), limit),
            ).fetchall()
        return tuple(_sync_job(row) for row in rows)

    def list_active_source_ids(
        self,
        binding_id: str,
        *,
        permission_probe_not_before: datetime,
    ) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT source_id FROM knowledge_provider_source_heads_v2
                WHERE binding_id=? AND status='active' AND permission_probe_at>=?
                ORDER BY source_id""",
                (binding_id, permission_probe_not_before.isoformat()),
            ).fetchall()
        return tuple(str(row["source_id"]) for row in rows)

    def get_snapshot(self, snapshot_id: str) -> TenantProviderSnapshotRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_provider_snapshots_v2 WHERE id=?",
                (snapshot_id,),
            ).fetchone()
        return None if row is None else _snapshot(row)

    def list_active_snapshots(self, binding_id: str) -> tuple[TenantProviderSnapshotRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT snapshot.* FROM knowledge_provider_source_heads_v2 source
                JOIN knowledge_provider_snapshots_v2 snapshot
                  ON snapshot.id=source.snapshot_id
                WHERE source.binding_id=? AND source.status='active'
                ORDER BY source.source_id,snapshot.id""",
                (binding_id,),
            ).fetchall()
        return tuple(_snapshot(row) for row in rows)

    def mark_source_head_status(
        self,
        *,
        binding_id: str,
        source_id: str,
        status: str,
        permission_probe_at: datetime,
        binding_authorization_version: int,
        updated_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT authorization_version FROM knowledge_provider_source_heads_v2
                WHERE binding_id=? AND source_id=?""",
                (binding_id, source_id),
            ).fetchone()
            source_authorization_version = (
                binding_authorization_version
                if current is None
                else int(current["authorization_version"]) + 1
            )
            connection.execute(
                """INSERT INTO knowledge_provider_source_heads_v2(
                binding_id,source_id,provider_revision,snapshot_id,status,permission_probe_at,
                authorization_version,updated_at) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(binding_id,source_id) DO UPDATE SET
                status=excluded.status,permission_probe_at=excluded.permission_probe_at,
                authorization_version=excluded.authorization_version,
                updated_at=excluded.updated_at""",
                (
                    binding_id,
                    source_id,
                    None,
                    None,
                    status,
                    permission_probe_at.isoformat(),
                    source_authorization_version,
                    updated_at.isoformat(),
                ),
            )
            connection.commit()

    def acquire_sync_job(
        self,
        job_id: str,
        *,
        expected_version: int,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> KnowledgeSyncJob | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE knowledge_sync_jobs
                SET status='running',attempt=attempt+1,lease_owner=?,lease_expires_at=?,
                    started_at=COALESCE(started_at,?),updated_at=?,version=version+1
                WHERE id=? AND version=? AND status IN ('queued','retry_wait')
                AND (retry_at IS NULL OR retry_at<=?)""",
                (
                    lease_owner,
                    lease_expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    job_id,
                    expected_version,
                    now.isoformat(),
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            row = connection.execute(
                "SELECT * FROM knowledge_sync_jobs WHERE id=?", (job_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _sync_job(row)

    def complete_sync_job(
        self,
        job: KnowledgeSyncJob,
        snapshot: TenantProviderSnapshotRecord,
        *,
        permission_probe_at: datetime,
        authorization_version: int,
        completed_at: datetime,
    ) -> KnowledgeSyncJob:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM knowledge_provider_snapshots_v2
                WHERE binding_id=? AND source_id=? AND provider_revision=?""",
                (snapshot.binding_id, snapshot.source_id, snapshot.provider_revision),
            ).fetchone()
            snapshot_id = snapshot.id
            if existing is not None:
                existing_snapshot = _snapshot(existing)
                if existing_snapshot.artifact.sha256 != snapshot.artifact.sha256:
                    raise RuntimeError("KNOWLEDGE_PROVIDER_REVISION_HASH_CONFLICT")
                snapshot_id = existing_snapshot.id
            else:
                connection.execute(
                    """INSERT INTO knowledge_provider_snapshots_v2(
                    id,binding_id,source_id,provider_revision,content_type,artifact_uri,
                    artifact_sha256,artifact_media_type,artifact_size_bytes,
                    normalized_text_sha256,source_url,fetched_by_product_user_id,fetched_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot.id,
                        snapshot.binding_id,
                        snapshot.source_id,
                        snapshot.provider_revision,
                        snapshot.content_type,
                        snapshot.artifact.uri,
                        snapshot.artifact.sha256,
                        snapshot.artifact.media_type,
                        snapshot.artifact.size_bytes,
                        snapshot.normalized_text_sha256,
                        snapshot.source_url,
                        snapshot.fetched_by_product_user_id,
                        snapshot.fetched_at.isoformat(),
                    ),
                )
            connection.execute(
                """INSERT INTO knowledge_provider_source_heads_v2(
                binding_id,source_id,provider_revision,snapshot_id,status,permission_probe_at,
                authorization_version,updated_at) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(binding_id,source_id) DO UPDATE SET
                provider_revision=excluded.provider_revision,snapshot_id=excluded.snapshot_id,
                status=excluded.status,permission_probe_at=excluded.permission_probe_at,
                authorization_version=excluded.authorization_version,updated_at=excluded.updated_at""",
                (
                    snapshot.binding_id,
                    snapshot.source_id,
                    snapshot.provider_revision,
                    snapshot_id,
                    "active",
                    permission_probe_at.isoformat(),
                    authorization_version,
                    completed_at.isoformat(),
                ),
            )
            updated = connection.execute(
                """UPDATE knowledge_sync_jobs SET status='succeeded',provider_revision=?,
                snapshot_id=?,snapshot_sha256=?,error_code=NULL,lease_owner=NULL,
                lease_expires_at=NULL,updated_at=?,completed_at=?,version=version+1
                WHERE id=? AND version=? AND status='running'""",
                (
                    snapshot.provider_revision,
                    snapshot_id,
                    snapshot.artifact.sha256,
                    completed_at.isoformat(),
                    completed_at.isoformat(),
                    job.id,
                    job.version,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_SYNC_JOB_VERSION_CONFLICT")
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.sync-job-succeeded",
                    aggregate_type="knowledge-sync-job",
                    aggregate_id=job.id,
                    aggregate_version=job.version + 1,
                    payload={
                        "snapshot_id": snapshot_id,
                        "snapshot_sha256": str(snapshot.artifact.sha256),
                        "provider_revision": snapshot.provider_revision,
                    },
                    occurred_at=completed_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_sync_jobs WHERE id=?", (job.id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _sync_job(row)

    def fail_sync_job(
        self,
        job: KnowledgeSyncJob,
        *,
        error_code: str,
        completed_at: datetime,
    ) -> KnowledgeSyncJob:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE knowledge_sync_jobs SET status='failed',error_code=?,
                lease_owner=NULL,lease_expires_at=NULL,updated_at=?,completed_at=?,
                version=version+1 WHERE id=? AND version=? AND status='running'""",
                (
                    error_code,
                    completed_at.isoformat(),
                    completed_at.isoformat(),
                    job.id,
                    job.version,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_SYNC_JOB_VERSION_CONFLICT")
            row = connection.execute(
                "SELECT * FROM knowledge_sync_jobs WHERE id=?", (job.id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _sync_job(row)

    def defer_sync_job(
        self,
        job: KnowledgeSyncJob,
        *,
        error_code: str,
        retry_at: datetime,
        updated_at: datetime,
    ) -> KnowledgeSyncJob:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE knowledge_sync_jobs SET status='retry_wait',error_code=?,
                retry_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?,
                version=version+1 WHERE id=? AND version=? AND status='running'""",
                (
                    error_code,
                    retry_at.isoformat(),
                    updated_at.isoformat(),
                    job.id,
                    job.version,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_SYNC_JOB_VERSION_CONFLICT")
            row = connection.execute(
                "SELECT * FROM knowledge_sync_jobs WHERE id=?", (job.id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _sync_job(row)

    def recover_expired_sync_jobs(self, now: datetime) -> tuple[KnowledgeSyncJob, ...]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT id,attempt,max_attempts FROM knowledge_sync_jobs WHERE status='running'
                AND lease_expires_at IS NOT NULL AND lease_expires_at<=? ORDER BY id""",
                (now.isoformat(),),
            ).fetchall()
            queued_ids = tuple(
                str(row["id"])
                for row in rows
                if int(row["attempt"]) < int(row["max_attempts"])
            )
            failed_ids = tuple(
                str(row["id"])
                for row in rows
                if int(row["attempt"]) >= int(row["max_attempts"])
            )
            if queued_ids:
                connection.executemany(
                    """UPDATE knowledge_sync_jobs SET status='queued',lease_owner=NULL,
                    lease_expires_at=NULL,retry_at=NULL,error_code='KNOWLEDGE_SYNC_LEASE_EXPIRED',
                    updated_at=?,version=version+1 WHERE id=? AND status='running'""",
                    ((now.isoformat(), job_id) for job_id in queued_ids),
                )
            if failed_ids:
                connection.executemany(
                    """UPDATE knowledge_sync_jobs SET status='failed',lease_owner=NULL,
                    lease_expires_at=NULL,retry_at=NULL,
                    error_code='KNOWLEDGE_SYNC_ATTEMPTS_EXHAUSTED',updated_at=?,
                    completed_at=?,version=version+1
                    WHERE id=? AND status='running'""",
                    ((now.isoformat(), now.isoformat(), job_id) for job_id in failed_ids),
                )
            job_ids = queued_ids + failed_ids
            recovered = tuple(
                _sync_job(
                    connection.execute(
                        "SELECT * FROM knowledge_sync_jobs WHERE id=?", (job_id,)
                    ).fetchone()
                )
                for job_id in job_ids
            )
            connection.commit()
        return recovered

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

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

    @staticmethod
    def _insert_binding_nodes(
        connection: sqlite3.Connection,
        binding_id: str,
        nodes: tuple[ProviderNode, ...],
    ) -> None:
        connection.executemany(
            """INSERT INTO knowledge_provider_binding_nodes_v2(
            binding_id,external_id,external_space_id,parent_external_id,source_id,title,
            kind,provider_revision,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                (
                    binding_id,
                    node.external_id,
                    node.external_space_id,
                    node.parent_external_id,
                    node.source_id,
                    node.title,
                    node.kind.value,
                    node.provider_revision,
                    _iso(node.updated_at),
                )
                for node in nodes
            ),
        )


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _sync_job(row: sqlite3.Row) -> KnowledgeSyncJob:
    return KnowledgeSyncJob.model_validate(dict(row))


def _snapshot(row: sqlite3.Row) -> TenantProviderSnapshotRecord:
    values = dict(row)
    return TenantProviderSnapshotRecord.model_validate(
        {
            "id": values["id"],
            "binding_id": values["binding_id"],
            "source_id": values["source_id"],
            "provider_revision": values["provider_revision"],
            "content_type": values["content_type"],
            "artifact": ArtifactReference(
                uri=values["artifact_uri"],
                sha256=values["artifact_sha256"],
                media_type=values["artifact_media_type"],
                size_bytes=values["artifact_size_bytes"],
            ),
            "normalized_text_sha256": values["normalized_text_sha256"],
            "source_url": values["source_url"],
            "fetched_by_product_user_id": values["fetched_by_product_user_id"],
            "fetched_at": values["fetched_at"],
        }
    )
