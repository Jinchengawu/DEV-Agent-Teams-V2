from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ...shared.hashes import Sha256
from ...shared.ids import new_id


class ArtifactEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    contract_id: str
    content: dict[str, object]
    sha256: Sha256


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
    artifact_envelopes: tuple[ArtifactEnvelope, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentRunLedger:
    def __init__(self, database: Path) -> None:
        self.database = database

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
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_runs(
                id,delivery_id,pipeline_revision_id,binding_site,resolved_binding_hash,
                deployment_snapshot_json,attempt_id,runtime_identity,status,
                artifact_envelopes_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                _values(run),
            )
        return run

    def finish(
        self,
        run: AgentRun,
        *,
        status: str,
        artifacts: tuple[ArtifactEnvelope, ...] = (),
    ) -> AgentRun:
        updated = run.model_copy(
            update={
                "status": status,
                "artifact_envelopes": artifacts,
                "updated_at": datetime.now(UTC),
            }
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE agent_runs SET status=?,artifact_envelopes_json=?,updated_at=?
                WHERE id=? AND status='running'""",
                (
                    updated.status,
                    _json([item.model_dump(mode="json") for item in artifacts]),
                    updated.updated_at.isoformat(),
                    run.id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AgentRun is no longer running")
        return updated

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
        _json([item.model_dump(mode="json") for item in run.artifact_envelopes]),
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
