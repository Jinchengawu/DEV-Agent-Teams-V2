"""内部查询执行账本；不拥有 Preparation、Stage 或业务终态。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict

from ...shared.hashes import Sha256
from .context_domain import KnowledgeContextStageResult
from .query_admission import PreparationQueryBudget, QueryAdmission, admit_query_plans
from .query_plan import QueryInputQualification, QueryPlan
from .query_safety import QuerySafeError as QuerySafeError


class QueryExecutionIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    preparation_run_id: str
    stage_path: str
    binding_id: str
    input_sha256: Sha256
    index_sha256: Sha256
    policy_sha256: Sha256
    qualification_sha256: Sha256
    budget_sha256: Sha256
    authorization_epoch_hash: Sha256
    index_revision_id: str | None = None
    base_qualification_id: str | None = None
    base_qualification_sha256: Sha256 | None = None
    source_ids: tuple[str, ...] = ()
    policy_revision_id: str | None = None

    @property
    def execution_key(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class QueryPlanRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    identity: QueryExecutionIdentity
    plan_sha256: Sha256
    plan_json: str
    unit_count: int
    artifact_sha256: Sha256 | None = None
    final_context_sha256: Sha256 | None = None


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("KNOWLEDGE_QUERY_TIMEZONE_REQUIRED")
    return value.astimezone(UTC).isoformat()


class SQLiteKnowledgeQueryRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def freeze_plan(self, identity: QueryExecutionIdentity, *, plan: QueryPlan) -> QueryPlanRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._freeze_plan(connection, identity, plan)

    @staticmethod
    def _freeze_plan(
        connection: sqlite3.Connection, identity: QueryExecutionIdentity, plan: QueryPlan
    ) -> QueryPlanRecord:
        plan = QueryPlan.model_validate_json(plan.model_dump_json())
        plan_sha256, unit_count, plan_json = (
            plan.plan_sha256,
            len(plan.units),
            plan.model_dump_json(),
        )
        if (
            plan.source_sha256 != identity.input_sha256
            or plan.qualification_sha256 != identity.qualification_sha256
            or plan.budget_sha256 != identity.budget_sha256
        ):
            raise RuntimeError("KNOWLEDGE_QUERY_PLAN_IDENTITY_CONFLICT")
        record = QueryPlanRecord(
            identity=identity, plan_sha256=plan_sha256, unit_count=unit_count, plan_json=plan_json
        )
        if unit_count < 1:
            raise ValueError("KNOWLEDGE_QUERY_EMPTY_PLAN")
        stage = connection.execute(
            """SELECT execution_key FROM knowledge_query_plans
                WHERE json_extract(identity_json,'$.preparation_run_id')=?
                AND json_extract(identity_json,'$.stage_path')=?
                AND json_extract(identity_json,'$.binding_id')=?""",
            (identity.preparation_run_id, identity.stage_path, identity.binding_id),
        ).fetchone()
        if stage is not None and stage[0] != identity.execution_key:
            raise RuntimeError("KNOWLEDGE_QUERY_IMMUTABLE_CONFLICT")
        existing = connection.execute(
            "SELECT * FROM knowledge_query_plans WHERE execution_key=?",
            (identity.execution_key,),
        ).fetchone()
        if existing:
            if (
                existing["identity_json"],
                existing["plan_sha256"],
                existing["plan_json"],
                existing["unit_count"],
            ) != (identity.model_dump_json(), plan_sha256, plan_json, unit_count):
                raise RuntimeError("KNOWLEDGE_QUERY_IMMUTABLE_CONFLICT")
        else:
            connection.execute(
                """INSERT INTO knowledge_query_plans
                    (execution_key,identity_json,plan_sha256,plan_json,unit_count)
                    VALUES(?,?,?,?,?)""",
                (
                    identity.execution_key,
                    identity.model_dump_json(),
                    plan_sha256,
                    plan_json,
                    unit_count,
                ),
            )
        return record

    def freeze_preparation(
        self,
        entries: tuple[tuple[QueryExecutionIdentity, QueryPlan, QueryInputQualification], ...],
        *,
        budget: PreparationQueryBudget,
    ) -> Sha256:
        if not entries or len({entry[0].preparation_run_id for entry in entries}) != 1:
            raise ValueError("KNOWLEDGE_QUERY_ADMISSION_PREPARATION_MISMATCH")
        if len({entry[0].authorization_epoch_hash for entry in entries}) != 1:
            raise ValueError("KNOWLEDGE_QUERY_ADMISSION_AUTHORIZATION_MISMATCH")
        for identity, _, qualification in entries:
            if (
                not identity.index_revision_id
                or not identity.base_qualification_id
                or not identity.policy_revision_id
                or identity.base_qualification_sha256 is None
                or identity.base_qualification_sha256 != qualification.base_qualification_sha256
                or qualification.status != "qualified"
                or len(set(identity.source_ids)) != len(identity.source_ids)
            ):
                raise ValueError("KNOWLEDGE_QUERY_ADMISSION_FROZEN_IDENTITY_REQUIRED")
        admission = admit_query_plans(
            tuple(
                (identity.stage_path, identity.execution_key, plan, qualification)
                for identity, plan, qualification in entries
            ),
            budget,
        )
        run_id = entries[0][0].preparation_run_id
        payload = json.dumps(
            admission.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT admission_sha256,payload_json FROM knowledge_query_admissions "
                "WHERE preparation_run_id=?",
                (run_id,),
            ).fetchone()
            if existing and (existing[0] != admission.admission_sha256 or existing[1] != payload):
                raise RuntimeError("KNOWLEDGE_QUERY_ADMISSION_IMMUTABLE_CONFLICT")
            for identity, plan, _ in entries:
                self._freeze_plan(connection, identity, plan)
            frozen = connection.execute(
                "SELECT execution_key FROM knowledge_query_plans "
                "WHERE json_extract(identity_json,'$.preparation_run_id')=?",
                (run_id,),
            ).fetchall()
            if {row[0] for row in frozen} != set(admission.plans):
                raise RuntimeError("KNOWLEDGE_QUERY_ADMISSION_PLAN_SET_MISMATCH")
            if not existing:
                connection.execute(
                    "INSERT INTO knowledge_query_admissions VALUES(?,?,?)",
                    (run_id, admission.admission_sha256, payload),
                )
        return admission.admission_sha256

    def assert_admitted(self, identity: QueryExecutionIdentity, plan: QueryPlan) -> QueryAdmission:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT admission_sha256,payload_json FROM knowledge_query_admissions "
                "WHERE preparation_run_id=?",
                (identity.preparation_run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("KNOWLEDGE_QUERY_PREPARATION_NOT_ADMITTED")
        admission = QueryAdmission.model_validate_json(row[1])
        if (
            admission.admission_sha256 != row[0]
            or admission.plans.get(identity.execution_key) != plan.plan_sha256
        ):
            raise RuntimeError("KNOWLEDGE_QUERY_ADMISSION_INTEGRITY_FAILED")
        self.get_for_execution(identity)
        return admission

    def get_stage_plan(
        self, preparation_run_id: str, stage_path: str, binding_id: str
    ) -> QueryPlanRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT identity_json FROM knowledge_query_plans
                WHERE json_extract(identity_json,'$.preparation_run_id')=?
                AND json_extract(identity_json,'$.stage_path')=?
                AND json_extract(identity_json,'$.binding_id')=?""",
                (preparation_run_id, stage_path, binding_id),
            ).fetchone()
        if row is None:
            return None
        return self.get_for_execution(QueryExecutionIdentity.model_validate_json(row[0]))

    def list_safe_errors(self, preparation_run_id: str) -> tuple[QuerySafeError, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT errors.payload_json FROM knowledge_query_safe_errors AS errors
                JOIN knowledge_query_plans AS plans USING(execution_key)
                WHERE json_extract(plans.identity_json,'$.preparation_run_id')=?
                ORDER BY errors.correlation_id""",
                (preparation_run_id,),
            ).fetchall()
        return tuple(QuerySafeError.model_validate_json(row[0]) for row in rows)

    def get_for_execution(self, identity: QueryExecutionIdentity) -> QueryPlanRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_query_plans WHERE execution_key=?",
                (identity.execution_key,),
            ).fetchone()
        if row is None:
            raise KeyError(identity.execution_key)
        record = QueryPlanRecord(
            identity=QueryExecutionIdentity.model_validate_json(row["identity_json"]),
            plan_sha256=row["plan_sha256"],
            plan_json=row["plan_json"],
            unit_count=row["unit_count"],
            artifact_sha256=row["artifact_sha256"],
            final_context_sha256=row["final_context_sha256"],
        )
        plan = QueryPlan.model_validate_json(record.plan_json)
        if plan.plan_sha256 != record.plan_sha256 or record.identity != identity:
            raise RuntimeError("KNOWLEDGE_QUERY_INTEGRITY_FAILED")
        return record

    @staticmethod
    def link_context_in_transaction(
        connection: sqlite3.Connection,
        result: KnowledgeContextStageResult,
        binding_ids: tuple[str, ...],
    ) -> None:
        """由 Context Repository 的同一写事务调用；本方法不提交事务。"""
        if (
            not connection.in_transaction
            or not binding_ids
            or len(set(binding_ids)) != len(binding_ids)
        ):
            raise RuntimeError("KNOWLEDGE_QUERY_CONTEXT_TRANSACTION_REQUIRED")
        run = connection.execute(
            "SELECT status FROM knowledge_context_preparation_runs WHERE id=?",
            (result.preparation_run_id,),
        ).fetchone()
        if run is None or run[0] != "running":
            raise RuntimeError("KNOWLEDGE_QUERY_CONTEXT_PREPARATION_CONFLICT")
        if result.stage_path != result.context.stage_path:
            raise RuntimeError("KNOWLEDGE_QUERY_CONTEXT_STAGE_CONFLICT")
        rows = connection.execute(
            """SELECT * FROM knowledge_query_plans
            WHERE json_extract(identity_json,'$.preparation_run_id')=?
            AND json_extract(identity_json,'$.stage_path')=?""",
            (result.preparation_run_id, result.stage_path),
        ).fetchall()
        identities = [
            QueryExecutionIdentity.model_validate_json(row["identity_json"]) for row in rows
        ]
        if {identity.binding_id for identity in identities} != set(binding_ids):
            raise RuntimeError("KNOWLEDGE_QUERY_CONTEXT_BINDINGS_INCOMPLETE")
        digest = result.context.artifact_reference.sha256
        for row, identity in zip(rows, identities, strict=True):
            if (
                identity.input_sha256 != result.query_sha256
                or identity.authorization_epoch_hash != result.context.authorization_epoch_hash
            ):
                raise RuntimeError("KNOWLEDGE_QUERY_CONTEXT_IDENTITY_CONFLICT")
            plan = QueryPlan.model_validate_json(row["plan_json"])
            if plan.plan_sha256 != row["plan_sha256"] or len(plan.units) != row["unit_count"]:
                raise RuntimeError("KNOWLEDGE_QUERY_INTEGRITY_FAILED")
            count = connection.execute(
                "SELECT count(*) FROM knowledge_query_unit_receipts WHERE execution_key=?",
                (row["execution_key"],),
            ).fetchone()[0]
            failure = connection.execute(
                "SELECT 1 FROM knowledge_query_safe_errors WHERE execution_key=? LIMIT 1",
                (row["execution_key"],),
            ).fetchone()
            if row["artifact_sha256"] is None or count != len(plan.units) or failure:
                raise RuntimeError("KNOWLEDGE_QUERY_CONTEXT_INCOMPLETE")
            if row["final_context_sha256"] not in (None, digest):
                raise RuntimeError("KNOWLEDGE_QUERY_IMMUTABLE_CONFLICT")
        for row in rows:
            connection.execute(
                "UPDATE knowledge_query_plans SET final_context_sha256=? WHERE execution_key=?",
                (digest, row["execution_key"]),
            )

    def claim(self, key: str, *, owner: str, now: datetime, ttl: timedelta) -> int:
        if ttl <= timedelta(0):
            raise ValueError("KNOWLEDGE_QUERY_INVALID_LEASE")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT * FROM knowledge_query_plans WHERE execution_key=?", (key,)
            ).fetchone()
            elapsed = 0 if previous is None else self._elapsed(previous, now)
            updated = connection.execute(
                """UPDATE knowledge_query_plans SET fence=fence+1,
                lease_owner=?,lease_expires_at=?,lease_started_at=?,elapsed_ms=?
                WHERE execution_key=? AND artifact_sha256 IS NULL
                AND (lease_expires_at IS NULL OR lease_expires_at<=?)""",
                (owner, _timestamp(now + ttl), _timestamp(now), elapsed, key, _timestamp(now)),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_QUERY_LEASE_CONFLICT")
            row = connection.execute(
                "SELECT fence FROM knowledge_query_plans WHERE execution_key=?", (key,)
            ).fetchone()
            assert row is not None
            return int(row["fence"])

    @staticmethod
    def _elapsed(row: sqlite3.Row, now: datetime) -> int:
        elapsed = int(row["elapsed_ms"])
        if row["lease_started_at"] is not None and row["lease_expires_at"] is not None:
            started = datetime.fromisoformat(row["lease_started_at"])
            end = min(now, datetime.fromisoformat(row["lease_expires_at"]))
            elapsed += max(0, int((end - started).total_seconds() * 1000))
        return elapsed

    def elapsed_ms(self, key: str, *, now: datetime) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_query_plans WHERE execution_key=?", (key,)
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return self._elapsed(row, now)

    def release(self, key: str, *, fence: int, now: datetime) -> None:
        """只释放当前 fence；累计耗时保留，旧 worker 不得清掉新租约。"""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._fenced(connection, key, fence, now)
            connection.execute(
                "UPDATE knowledge_query_plans SET lease_owner=NULL,lease_expires_at=NULL,"
                "lease_started_at=NULL,elapsed_ms=? WHERE execution_key=? AND fence=?",
                (self._elapsed(row, now), key, fence),
            )

    @staticmethod
    def _fenced(connection: sqlite3.Connection, key: str, fence: int, now: datetime) -> sqlite3.Row:
        row = connection.execute(
            """SELECT * FROM knowledge_query_plans WHERE execution_key=?
            AND fence=? AND lease_expires_at>? AND artifact_sha256 IS NULL""",
            (key, fence, _timestamp(now)),
        ).fetchone()
        if row is None:
            raise RuntimeError("KNOWLEDGE_QUERY_FENCE_CONFLICT")
        return cast(sqlite3.Row, row)

    def put_qualification(self, qualification: QueryInputQualification) -> None:
        qualification = QueryInputQualification.model_validate_json(qualification.model_dump_json())
        payload = qualification.model_dump_json()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT payload_json FROM knowledge_query_qualifications "
                "WHERE qualification_sha256=?",
                (qualification.qualification_sha256,),
            ).fetchone()
            if old is not None:
                if old[0] != payload:
                    raise RuntimeError("KNOWLEDGE_QUERY_IMMUTABLE_CONFLICT")
                return
            connection.execute(
                "INSERT INTO knowledge_query_qualifications VALUES(?,?)",
                (qualification.qualification_sha256, payload),
            )

    def get_qualification(self, digest: Sha256) -> QueryInputQualification:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM knowledge_query_qualifications "
                "WHERE qualification_sha256=?",
                (digest,),
            ).fetchone()
        if row is None:
            raise KeyError(digest)
        qualification = QueryInputQualification.model_validate_json(row[0])
        if qualification.qualification_sha256 != digest:
            raise RuntimeError("KNOWLEDGE_QUERY_INTEGRITY_FAILED")
        return qualification

    def put_unit_receipt(
        self, key: str, *, fence: int, ordinal: int, result_sha256: Sha256, now: datetime
    ) -> None:
        # result_sha256 指向受控内容寻址命中/排名缓存；此账本不保存向量或正文。
        Sha256.validate(result_sha256)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._fenced(connection, key, fence, now)
            if not 0 <= ordinal < plan["unit_count"]:
                raise ValueError("KNOWLEDGE_QUERY_UNIT_OUT_OF_RANGE")
            old = connection.execute(
                "SELECT result_sha256 FROM knowledge_query_unit_receipts "
                "WHERE execution_key=? AND ordinal=?",
                (key, ordinal),
            ).fetchone()
            if old:
                if old[0] != result_sha256:
                    raise RuntimeError("KNOWLEDGE_QUERY_IMMUTABLE_CONFLICT")
                return
            connection.execute(
                "INSERT INTO knowledge_query_unit_receipts VALUES(?,?,?)",
                (key, ordinal, result_sha256),
            )

    def unit_receipts(self, key: str) -> dict[int, str]:
        with self._connect() as connection:
            return dict(
                connection.execute(
                    "SELECT ordinal,result_sha256 FROM knowledge_query_unit_receipts "
                    "WHERE execution_key=? ORDER BY ordinal",
                    (key,),
                ).fetchall()
            )

    def complete(self, key: str, *, fence: int, artifact_sha256: Sha256, now: datetime) -> None:
        Sha256.validate(artifact_sha256)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._fenced(connection, key, fence, now)
            count = connection.execute(
                "SELECT count(*) FROM knowledge_query_unit_receipts WHERE execution_key=?", (key,)
            ).fetchone()[0]
            if count != plan["unit_count"]:
                raise RuntimeError("KNOWLEDGE_QUERY_INCOMPLETE")
            if connection.execute(
                "SELECT 1 FROM knowledge_query_safe_errors WHERE execution_key=? LIMIT 1", (key,)
            ).fetchone():
                raise RuntimeError("KNOWLEDGE_QUERY_FAILED")
            connection.execute(
                "UPDATE knowledge_query_plans SET artifact_sha256=?,"
                "lease_owner=NULL,lease_expires_at=NULL,lease_started_at=NULL,elapsed_ms=? "
                "WHERE execution_key=?",
                (artifact_sha256, self._elapsed(plan, now), key),
            )

    def put_safe_error(self, key: str, *, fence: int, error: QuerySafeError, now: datetime) -> None:
        error = QuerySafeError.model_validate_json(error.model_dump_json())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._fenced(connection, key, fence, now)
            identity = QueryExecutionIdentity.model_validate_json(plan["identity_json"])
            if (
                error.plan_sha256 != plan["plan_sha256"]
                or error.qualification_sha256 != identity.qualification_sha256
                or error.budget_sha256 != identity.budget_sha256
                or error.unit >= plan["unit_count"]
            ):
                raise RuntimeError("KNOWLEDGE_QUERY_ERROR_IDENTITY_CONFLICT")
            payload = error.model_dump_json()
            old = connection.execute(
                "SELECT payload_json FROM knowledge_query_safe_errors "
                "WHERE execution_key=? AND correlation_id=?",
                (key, str(error.correlation_id)),
            ).fetchone()
            if old:
                if old[0] != payload:
                    raise RuntimeError("KNOWLEDGE_QUERY_IMMUTABLE_CONFLICT")
                return
            connection.execute(
                "INSERT INTO knowledge_query_safe_errors VALUES(?,?,?)",
                (key, str(error.correlation_id), payload),
            )
