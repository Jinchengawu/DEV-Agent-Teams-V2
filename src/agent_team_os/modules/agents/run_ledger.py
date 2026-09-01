from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256
from ...shared.ids import new_id
from ..artifacts import ArtifactReference, ContentAddressedArtifactStorage


class ArtifactEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    contract_id: str
    artifact_key: str = Field(default="primary", min_length=1, max_length=120)
    content: dict[str, object] | None = None
    reference: ArtifactReference | None = None
    sha256: Sha256

    @model_validator(mode="after")
    def has_one_content_location(self) -> ArtifactEnvelope:
        if (self.content is None) == (self.reference is None):
            raise ValueError("Artifact Envelope requires exactly one content location")
        if self.reference is not None and self.reference.sha256 != self.sha256:
            raise ValueError("Artifact reference hash differs from Envelope hash")
        return self


class AgentRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    delivery_id: str
    pipeline_revision_id: str
    binding_site: str
    resolved_binding_hash: Sha256
    deployment_snapshot: dict[str, object]
    attempt_id: str
    runtime_identity: str | None = None
    status: str
    workcell_run_id: str | None = None
    parent_agent_run_id: str | None = None
    root_agent_run_id: str | None = None
    depth: int = Field(default=0, ge=0, le=1)
    run_role: str = "main"
    delegate_purpose: str | None = None
    workspace_access: str = "legacy"
    slot_key: str | None = None
    artifact_envelopes: tuple[ArtifactEnvelope, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentRunLedger:
    def __init__(
        self,
        database: Path,
        *,
        artifact_storage: ContentAddressedArtifactStorage | None = None,
    ) -> None:
        self.database = database
        self.artifact_storage = artifact_storage or ContentAddressedArtifactStorage(
            database.parent / "artifacts"
        )

    def start(
        self,
        *,
        delivery_id: str,
        pipeline_revision_id: str,
        binding_site: str,
        resolved_binding_hash: str,
        deployment_snapshot: dict[str, object],
        runtime_identity: str | None,
    ) -> AgentRun:
        run = AgentRun(
            delivery_id=delivery_id,
            pipeline_revision_id=pipeline_revision_id,
            binding_site=binding_site,
            resolved_binding_hash=Sha256.validate(resolved_binding_hash),
            deployment_snapshot=deployment_snapshot,
            attempt_id=new_id(),
            runtime_identity=runtime_identity,
            status="running",
        )
        run = run.model_copy(update={"root_agent_run_id": run.id})
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO agent_runs(
                id,delivery_id,pipeline_revision_id,binding_site,resolved_binding_hash,
                deployment_snapshot_json,attempt_id,runtime_identity,status,
                artifact_envelopes_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                _values(run),
            )
            if _table_exists(connection, "agent_attempts"):
                connection.execute(
                    "UPDATE agent_runs SET root_agent_run_id=id WHERE id=?",
                    (run.id,),
                )
                connection.execute(
                    """INSERT INTO agent_attempts(
                    id,agent_run_id,phase,ordinal,provider_binding_hash,runtime_identity,
                    status,error_code,result_artifact_sha256,started_at,finished_at)
                    VALUES(?,?,'legacy',1,?,?,?,NULL,NULL,?,NULL)""",
                    (
                        run.attempt_id,
                        run.id,
                        run.resolved_binding_hash,
                        run.runtime_identity,
                        run.status,
                        run.created_at.isoformat(),
                    ),
                )
        return run

    def finish(
        self,
        run: AgentRun,
        *,
        status: str,
        artifacts: tuple[ArtifactEnvelope, ...] = (),
    ) -> AgentRun:
        with self._connect() as connection:
            return self.finish_on(connection, run, status=status, artifacts=artifacts)

    def finish_on(
        self,
        connection: sqlite3.Connection,
        run: AgentRun,
        *,
        status: str,
        artifacts: tuple[ArtifactEnvelope, ...] = (),
    ) -> AgentRun:
        persisted_artifacts = tuple(self._persist(item) for item in artifacts)
        updated = run.model_copy(
            update={
                "status": status,
                "artifact_envelopes": persisted_artifacts,
                "updated_at": datetime.now(UTC),
            }
        )
        cursor = connection.execute(
            """UPDATE agent_runs SET status=?,artifact_envelopes_json=?,updated_at=?
            WHERE id=? AND status='running'""",
            (
                updated.status,
                _json(
                    [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in persisted_artifacts
                    ]
                ),
                updated.updated_at.isoformat(),
                run.id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("AgentRun is no longer running")
        if _table_exists(connection, "agent_attempts"):
            connection.execute(
                """UPDATE agent_attempts SET status=?,finished_at=?
                WHERE id=? AND status='running'""",
                (updated.status, updated.updated_at.isoformat(), run.attempt_id),
            )
        return updated

    def _persist(self, envelope: ArtifactEnvelope) -> ArtifactEnvelope:
        if envelope.reference is not None:
            self.artifact_storage.get_bytes(envelope.reference)
            return envelope
        if envelope.content is None:
            raise RuntimeError("Artifact Envelope content is unavailable")
        reference = self.artifact_storage.put_json(envelope.content)
        if reference.sha256 != envelope.sha256:
            raise RuntimeError("Artifact Envelope hash differs from serialized content")
        return envelope.model_copy(update={"content": None, "reference": reference})

    def get(self, run_id: str) -> AgentRun:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id,delivery_id,pipeline_revision_id,binding_site,
                resolved_binding_hash,deployment_snapshot_json,attempt_id,runtime_identity,
                status,artifact_envelopes_json,created_at,updated_at
                FROM agent_runs WHERE id=?""",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run(row)

    def list(self, delivery_id: str) -> tuple[AgentRun, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,delivery_id,pipeline_revision_id,binding_site,
                resolved_binding_hash,deployment_snapshot_json,attempt_id,runtime_identity,
                status,artifact_envelopes_json,created_at,updated_at
                FROM agent_runs WHERE delivery_id=? ORDER BY created_at,id""",
                (delivery_id,),
            ).fetchall()
        return tuple(_run(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


def _values(run: AgentRun) -> tuple[object, ...]:
    return (
        run.id,
        run.delivery_id,
        run.pipeline_revision_id,
        run.binding_site,
        run.resolved_binding_hash,
        _json(run.deployment_snapshot),
        run.attempt_id,
        run.runtime_identity,
        run.status,
        _json([item.model_dump(mode="json", exclude_none=True) for item in run.artifact_envelopes]),
        run.created_at.isoformat(),
        run.updated_at.isoformat(),
    )


def _run(row: tuple[object, ...]) -> AgentRun:
    keys = (
        "id",
        "delivery_id",
        "pipeline_revision_id",
        "binding_site",
        "resolved_binding_hash",
        "deployment_snapshot",
        "attempt_id",
        "runtime_identity",
        "status",
        "artifact_envelopes",
        "created_at",
        "updated_at",
    )
    values = dict(zip(keys, row, strict=True))
    values["deployment_snapshot"] = json.loads(str(values["deployment_snapshot"]))
    values["artifact_envelopes"] = json.loads(str(values["artifact_envelopes"]))
    return AgentRun.model_validate(values)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )
