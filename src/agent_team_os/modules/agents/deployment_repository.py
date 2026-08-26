from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ...shared.events import ProductEvent
from .deployment_domain import AgentDeployment


class SQLiteAgentDeploymentRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(self, deployment: AgentDeployment) -> AgentDeployment:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert(connection, deployment)
            self._event(connection, deployment, "agent-deployment.created")
        return deployment

    def get(self, deployment_id: str) -> AgentDeployment:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM agent_deployments WHERE id=?",  # noqa: S608
                (deployment_id,),
            ).fetchone()
        if row is None:
            raise KeyError(deployment_id)
        return _deployment(row)

    def list(self) -> tuple[AgentDeployment, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM agent_deployments ORDER BY id"  # noqa: S608
            ).fetchall()
        return tuple(_deployment(row) for row in rows)

    def compare_and_swap(
        self, expected_version: int, updated: AgentDeployment, event_type: str
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE agent_deployments SET
                name=?,profile_id=?,profile_revision=?,profile_sha256=?,capability_requirements_json=?,instance_id=?,
                instance_version=?,adapter_id=?,adapter_version=?,provider_id=?,
                provider_revision=?,provider_fingerprint=?,isolation_mode=?,policy_snapshot_json=?,extension_snapshot_json=?,
                qualification_status=?,qualification_errors_json=?,enabled=?,version=?,updated_at=?
                WHERE id=? AND version=?""",
                (
                    updated.name,
                    updated.profile_id,
                    updated.profile_revision,
                    updated.profile_sha256,
                    _json(
                        [item.model_dump(mode="json") for item in updated.capability_requirements]
                    ),
                    updated.instance_id,
                    updated.instance_version,
                    updated.adapter_id,
                    updated.adapter_version,
                    updated.provider_id,
                    updated.provider_revision,
                    updated.provider_fingerprint,
                    updated.isolation_mode,
                    _json(updated.policy_snapshot),
                    _json(updated.extension_snapshot),
                    updated.qualification_status,
                    _json(updated.qualification_errors),
                    int(updated.enabled),
                    updated.version,
                    updated.updated_at.isoformat(),
                    updated.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            self._event(connection, updated, event_type)
        return True

    def _insert(self, connection: sqlite3.Connection, deployment: AgentDeployment) -> None:
        connection.execute(
            f"INSERT INTO agent_deployments({_COLUMNS}) VALUES({','.join('?' * 23)})",  # noqa: S608
            _values(deployment),
        )

    @staticmethod
    def _event(
        connection: sqlite3.Connection, deployment: AgentDeployment, event_type: str
    ) -> None:
        event = ProductEvent(
            event_type=event_type,
            aggregate_type="agent-deployment",
            aggregate_id=deployment.id,
            aggregate_version=deployment.version,
            payload={
                "qualification_status": deployment.qualification_status,
                "enabled": deployment.enabled,
                "profile_revision": deployment.profile_revision,
            },
        )
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
                _json(event.payload),
                event.occurred_at.isoformat(),
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


_COLUMNS = """id,name,profile_id,profile_revision,profile_sha256,
capability_requirements_json,instance_id,
instance_version,adapter_id,adapter_version,provider_id,provider_revision,
provider_fingerprint,isolation_mode,policy_snapshot_json,extension_snapshot_json,qualification_status,
qualification_errors_json,enabled,version,created_by,created_at,updated_at"""


def _values(deployment: AgentDeployment) -> tuple[object, ...]:
    return (
        deployment.id,
        deployment.name,
        deployment.profile_id,
        deployment.profile_revision,
        deployment.profile_sha256,
        _json([item.model_dump(mode="json") for item in deployment.capability_requirements]),
        deployment.instance_id,
        deployment.instance_version,
        deployment.adapter_id,
        deployment.adapter_version,
        deployment.provider_id,
        deployment.provider_revision,
        deployment.provider_fingerprint,
        deployment.isolation_mode,
        _json(deployment.policy_snapshot),
        _json(deployment.extension_snapshot),
        deployment.qualification_status,
        _json(deployment.qualification_errors),
        int(deployment.enabled),
        deployment.version,
        deployment.created_by,
        deployment.created_at.isoformat(),
        deployment.updated_at.isoformat(),
    )


def _deployment(row: tuple[object, ...]) -> AgentDeployment:
    keys = tuple(item.strip() for item in _COLUMNS.replace("\n", "").split(","))
    values = dict(zip(keys, row, strict=True))
    values["capability_requirements"] = json.loads(str(values.pop("capability_requirements_json")))
    values["policy_snapshot"] = json.loads(str(values.pop("policy_snapshot_json")))
    values["extension_snapshot"] = json.loads(str(values.pop("extension_snapshot_json")))
    values["qualification_errors"] = json.loads(str(values.pop("qualification_errors_json")))
    values["enabled"] = bool(values["enabled"])
    return AgentDeployment.model_validate(values)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
