from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ...delivery import KnowledgePreparationInputV1
from ...shared.hashes import Sha256
from ...shared.ids import new_id
from .context_domain import (
    KnowledgeAuthorizationStampV1,
    KnowledgeContextPreparationRun,
    KnowledgeContextStageResult,
)


class SQLiteKnowledgeContextRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create_or_get(
        self,
        preparation_input: KnowledgePreparationInputV1,
        *,
        knowledge_binding_hash: Sha256,
        now: datetime,
    ) -> KnowledgeContextPreparationRun:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM knowledge_context_preparation_runs
                WHERE delivery_id=? AND input_sha256=? AND knowledge_binding_hash=?""",
                (
                    preparation_input.delivery_id,
                    preparation_input.input_sha256,
                    knowledge_binding_hash,
                ),
            ).fetchone()
            if row is not None:
                return _run(row)
            run = KnowledgeContextPreparationRun(
                id=new_id(),
                delivery_id=preparation_input.delivery_id,
                input_sha256=preparation_input.input_sha256,
                knowledge_binding_hash=knowledge_binding_hash,
                preparation_input=preparation_input,
                status="queued",
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """INSERT INTO knowledge_context_preparation_runs(
                id,delivery_id,input_sha256,knowledge_binding_hash,input_json,status,
                attempt_count,lease_owner,lease_expires_at,next_attempt_at,
                authorization_stamp_json,authorization_epoch_hash,final_snapshot_json,
                error_code,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                _run_values(run),
            )
        return run

    def get(self, run_id: str) -> KnowledgeContextPreparationRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_context_preparation_runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run(row)

    def get_for_delivery(self, delivery_id: str) -> KnowledgeContextPreparationRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM knowledge_context_preparation_runs
                WHERE delivery_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (delivery_id,),
            ).fetchone()
        return None if row is None else _run(row)

    def acquire(
        self,
        run_id: str,
        *,
        lease_owner: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> KnowledgeContextPreparationRun:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM knowledge_context_preparation_runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = _run(row)
            if current.status == "succeeded":
                return current
            recoverable = (
                current.status == "queued"
                or (
                    current.status == "retry_wait"
                    and current.next_attempt_at is not None
                    and current.next_attempt_at <= now
                )
                or (
                    current.status in {"leased", "running"}
                    and current.lease_expires_at is not None
                    and current.lease_expires_at <= now
                )
            )
            if not recoverable:
                raise RuntimeError("KNOWLEDGE_CONTEXT_PREPARATION_NOT_ACQUIRABLE")
            updated = connection.execute(
                """UPDATE knowledge_context_preparation_runs SET status='running',
                attempt_count=attempt_count+1,lease_owner=?,lease_expires_at=?,
                next_attempt_at=NULL,error_code=NULL,updated_at=? WHERE id=?""",
                (
                    lease_owner,
                    (now + lease_ttl).isoformat(),
                    now.isoformat(),
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_CONTEXT_PREPARATION_LEASE_CONFLICT")
            row = connection.execute(
                "SELECT * FROM knowledge_context_preparation_runs WHERE id=?", (run_id,)
            ).fetchone()
        assert row is not None
        return _run(row)

    def schedule_retry(
        self,
        run_id: str,
        *,
        error_code: str,
        next_attempt_at: datetime,
        now: datetime,
    ) -> KnowledgeContextPreparationRun:
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE knowledge_context_preparation_runs SET status='retry_wait',
                lease_owner=NULL,lease_expires_at=NULL,next_attempt_at=?,error_code=?,updated_at=?
                WHERE id=? AND status='running'""",
                (
                    next_attempt_at.isoformat(),
                    error_code,
                    now.isoformat(),
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_CONTEXT_PREPARATION_STATE_CONFLICT")
            row = connection.execute(
                "SELECT * FROM knowledge_context_preparation_runs WHERE id=?", (run_id,)
            ).fetchone()
        assert row is not None
        return _run(row)

    def put_stage_result(self, result: KnowledgeContextStageResult) -> None:
        context = result.context
        reference = context.artifact_reference
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT artifact_sha256,authorization_epoch_hash
                FROM knowledge_context_stage_results
                WHERE preparation_run_id=? AND stage_path=?""",
                (result.preparation_run_id, result.stage_path),
            ).fetchone()
            if existing is not None:
                if str(existing["artifact_sha256"]) != str(reference.sha256) or str(
                    existing["authorization_epoch_hash"]
                ) != str(context.authorization_epoch_hash):
                    raise RuntimeError("KNOWLEDGE_CONTEXT_STAGE_RESULT_HASH_CONFLICT")
                return
            connection.execute(
                """INSERT INTO knowledge_context_stage_results(
                preparation_run_id,stage_path,query_sha256,retrieval_policy_revision_id,
                artifact_uri,artifact_sha256,artifact_media_type,artifact_size_bytes,
                citation_ids_json,authorization_epoch_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    result.preparation_run_id,
                    result.stage_path,
                    result.query_sha256,
                    result.retrieval_policy_revision_id,
                    reference.uri,
                    reference.sha256,
                    reference.media_type,
                    reference.size_bytes,
                    _json(context.citation_ids),
                    context.authorization_epoch_hash,
                    result.created_at.isoformat(),
                ),
            )

    def list_stage_results(self, run_id: str) -> tuple[KnowledgeContextStageResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_context_stage_results
                WHERE preparation_run_id=? ORDER BY stage_path""",
                (run_id,),
            ).fetchall()
        return tuple(_stage_result(row) for row in rows)

    def succeed(
        self,
        run_id: str,
        *,
        stamp: KnowledgeAuthorizationStampV1,
        final_snapshot_json: str,
        now: datetime,
    ) -> KnowledgeContextPreparationRun:
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE knowledge_context_preparation_runs SET status='succeeded',
                lease_owner=NULL,lease_expires_at=NULL,authorization_stamp_json=?,
                authorization_epoch_hash=?,final_snapshot_json=?,error_code=NULL,
                updated_at=? WHERE id=? AND status='running'""",
                (
                    stamp.model_dump_json(),
                    stamp.authorization_epoch_hash,
                    final_snapshot_json,
                    now.isoformat(),
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_CONTEXT_PREPARATION_STATE_CONFLICT")
            row = connection.execute(
                "SELECT * FROM knowledge_context_preparation_runs WHERE id=?", (run_id,)
            ).fetchone()
        assert row is not None
        return _run(row)

    def fail(self, run_id: str, *, error_code: str, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE knowledge_context_preparation_runs SET status='failed',
                lease_owner=NULL,lease_expires_at=NULL,error_code=?,updated_at=?
                WHERE id=? AND status IN ('queued','leased','running','retry_wait')""",
                (error_code, now.isoformat(), run_id),
            )

    def cancel(self, delivery_id: str, *, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE knowledge_context_preparation_runs SET status='cancelled',
                lease_owner=NULL,lease_expires_at=NULL,error_code='CANCELLED',updated_at=?
                WHERE delivery_id=? AND status IN ('queued','leased','running','retry_wait')""",
                (now.isoformat(), delivery_id),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_values(run: KnowledgeContextPreparationRun) -> tuple[object, ...]:
    return (
        run.id,
        run.delivery_id,
        run.input_sha256,
        run.knowledge_binding_hash,
        run.preparation_input.model_dump_json(),
        run.status,
        run.attempt_count,
        run.lease_owner,
        None if run.lease_expires_at is None else run.lease_expires_at.isoformat(),
        None if run.next_attempt_at is None else run.next_attempt_at.isoformat(),
        None if run.authorization_stamp is None else run.authorization_stamp.model_dump_json(),
        run.authorization_epoch_hash,
        None if run.final_snapshot is None else run.final_snapshot.model_dump_json(),
        run.error_code,
        run.created_at.isoformat(),
        run.updated_at.isoformat(),
    )


def _run(row: sqlite3.Row) -> KnowledgeContextPreparationRun:
    return KnowledgeContextPreparationRun.model_validate(
        {
            "id": row["id"],
            "delivery_id": row["delivery_id"],
            "input_sha256": row["input_sha256"],
            "knowledge_binding_hash": row["knowledge_binding_hash"],
            "preparation_input": json.loads(str(row["input_json"])),
            "status": row["status"],
            "attempt_count": row["attempt_count"],
            "lease_owner": row["lease_owner"],
            "lease_expires_at": row["lease_expires_at"],
            "next_attempt_at": row["next_attempt_at"],
            "authorization_stamp": (
                None
                if row["authorization_stamp_json"] is None
                else json.loads(str(row["authorization_stamp_json"]))
            ),
            "authorization_epoch_hash": row["authorization_epoch_hash"],
            "final_snapshot": (
                None
                if row["final_snapshot_json"] is None
                else json.loads(str(row["final_snapshot_json"]))
            ),
            "error_code": row["error_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _stage_result(row: sqlite3.Row) -> KnowledgeContextStageResult:
    return KnowledgeContextStageResult.model_validate(
        {
            "preparation_run_id": row["preparation_run_id"],
            "stage_path": row["stage_path"],
            "query_sha256": row["query_sha256"],
            "retrieval_policy_revision_id": row["retrieval_policy_revision_id"],
            "context": {
                "stage_path": row["stage_path"],
                "artifact_reference": {
                    "uri": row["artifact_uri"],
                    "sha256": row["artifact_sha256"],
                    "media_type": row["artifact_media_type"],
                    "size_bytes": row["artifact_size_bytes"],
                },
                "citation_ids": json.loads(str(row["citation_ids_json"])),
                "authorization_epoch_hash": row["authorization_epoch_hash"],
                "trust_class": "external-collaborative",
            },
            "created_at": row["created_at"],
        }
    )
