from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ...shared.events import ProductEvent
from .domain import (
    ProjectTeamBinding,
    ProjectWorkcellBinding,
    TeamTemplateRevision,
    WorkspaceBinding,
)


class SQLiteProjectWorkcellRepository:
    """Persist project organization and workspace facts with local CAS boundaries."""

    def __init__(self, database: Path) -> None:
        self.database = database

    def bind_team(self, project_id: str, revision: TeamTemplateRevision) -> ProjectTeamBinding:
        binding = ProjectTeamBinding(
            project_id=project_id,
            template_id=revision.template_id,
            template_revision=revision.revision,
            template_sha256=revision.sha256,
            status="provisioning",
            version=1,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO project_team_bindings(
                project_id,template_id,template_revision,template_sha256,status,version,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    binding.project_id,
                    binding.template_id,
                    binding.template_revision,
                    binding.template_sha256,
                    binding.status,
                    binding.version,
                    binding.updated_at.isoformat(),
                ),
            )
            _append_event(
                connection,
                ProductEvent(
                    event_type="project.team-bound",
                    aggregate_type="project-team-binding",
                    aggregate_id=project_id,
                    aggregate_version=binding.version,
                    project_id=project_id,
                    payload={
                        "team_template_revision_id": binding.revision_id,
                        "team_template_sha256": binding.template_sha256,
                    },
                ),
            )
        return binding

    def get_team(self, project_id: str) -> ProjectTeamBinding:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_TEAM_COLUMNS} FROM project_team_bindings WHERE project_id=?",  # noqa: S608
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return _team(row)

    def create_assignment(
        self,
        workcell: ProjectWorkcellBinding,
        workspace: WorkspaceBinding,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO workspace_bindings(
                id,project_id,kind,adapter_type,repository_uri,credential_reference,status,
                verification_sha256,verification_json,error_code,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    workspace.id,
                    workspace.project_id,
                    workspace.kind,
                    workspace.adapter_type,
                    workspace.repository_uri,
                    workspace.credential_reference,
                    workspace.status,
                    workspace.verification_sha256,
                    _json(workspace.verification),
                    workspace.error_code,
                    workspace.version,
                    workspace.created_at.isoformat(),
                    workspace.updated_at.isoformat(),
                ),
            )
            connection.execute(
                """INSERT INTO project_workcell_bindings(
                project_id,workcell_key,workspace_binding_id,version,updated_at)
                VALUES(?,?,?,?,?)""",
                (
                    workcell.project_id,
                    workcell.workcell_key,
                    workcell.workspace_binding_id,
                    workcell.version,
                    workcell.updated_at.isoformat(),
                ),
            )
            _append_event(
                connection,
                ProductEvent(
                    event_type="project.workspace-bound",
                    aggregate_type="workspace-binding",
                    aggregate_id=workspace.id,
                    aggregate_version=workspace.version,
                    project_id=workspace.project_id,
                    payload={
                        "workcell_key": workcell.workcell_key,
                        "kind": workspace.kind,
                        "adapter_type": workspace.adapter_type,
                        "repository_uri": workspace.repository_uri,
                    },
                ),
            )

    def get_workspace(self, workspace_id: str) -> WorkspaceBinding:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_WORKSPACE_COLUMNS} FROM workspace_bindings WHERE id=?",  # noqa: S608
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise KeyError(workspace_id)
        return _workspace(row)

    def list_workcells(self, project_id: str) -> tuple[ProjectWorkcellBinding, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_WORKCELL_COLUMNS} FROM project_workcell_bindings
                WHERE project_id=? ORDER BY workcell_key""",  # noqa: S608
                (project_id,),
            ).fetchall()
        return tuple(_workcell(row) for row in rows)

    def list_workspaces(self, project_id: str) -> tuple[WorkspaceBinding, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_WORKSPACE_COLUMNS} FROM workspace_bindings
                WHERE project_id=? ORDER BY id""",  # noqa: S608
                (project_id,),
            ).fetchall()
        return tuple(_workspace(row) for row in rows)

    def compare_and_swap_workspace(
        self,
        expected_version: int,
        workspace: WorkspaceBinding,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE workspace_bindings SET
                status=?,verification_sha256=?,verification_json=?,error_code=?,version=?,
                updated_at=? WHERE id=? AND version=?""",
                (
                    workspace.status,
                    workspace.verification_sha256,
                    _json(workspace.verification),
                    workspace.error_code,
                    workspace.version,
                    workspace.updated_at.isoformat(),
                    workspace.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            _append_event(
                connection,
                ProductEvent(
                    event_type="workspace.verification-completed",
                    aggregate_type="workspace-binding",
                    aggregate_id=workspace.id,
                    aggregate_version=workspace.version,
                    project_id=workspace.project_id,
                    payload={
                        "status": workspace.status,
                        "verification_sha256": workspace.verification_sha256,
                        "error_code": workspace.error_code,
                    },
                ),
            )
        return True

    def activate_team(
        self,
        binding: ProjectTeamBinding,
        *,
        expected_version: int,
    ) -> tuple[ProjectTeamBinding, str]:
        activated = binding.model_copy(
            update={
                "status": "active",
                "version": binding.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE project_team_bindings SET status=?,version=?,updated_at=?
                WHERE project_id=? AND version=?""",
                (
                    activated.status,
                    activated.version,
                    activated.updated_at.isoformat(),
                    activated.project_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("PROJECT_TEAM_BINDING_VERSION_CONFLICT")
            project = connection.execute(
                "SELECT lifecycle_status,version FROM projects WHERE id=?",
                (binding.project_id,),
            ).fetchone()
            if project is None:
                raise KeyError(binding.project_id)
            if str(project[0]) not in {"provisioning", "active"}:
                raise RuntimeError("PROJECT_TEAM_ACTIVATION_NOT_ALLOWED")
            if str(project[0]) == "provisioning":
                connection.execute(
                    """UPDATE projects SET lifecycle_status='active',version=version+1,
                    updated_at=? WHERE id=? AND version=?""",
                    (activated.updated_at.isoformat(), binding.project_id, int(project[1])),
                )
                connection.execute(
                    """UPDATE project_workspaces SET status='ready',error_code=NULL,updated_at=?
                    WHERE project_id=?""",
                    (activated.updated_at.isoformat(), binding.project_id),
                )
            _append_event(
                connection,
                ProductEvent(
                    event_type="project.team-activated",
                    aggregate_type="project-team-binding",
                    aggregate_id=binding.project_id,
                    aggregate_version=activated.version,
                    project_id=binding.project_id,
                    payload={
                        "team_template_revision_id": binding.revision_id,
                        "team_template_sha256": binding.template_sha256,
                    },
                ),
            )
        return activated, "active"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


_TEAM_COLUMNS = (
    "project_id,template_id,template_revision,template_sha256,status,version,updated_at"
)
_WORKSPACE_COLUMNS = (
    "id,project_id,kind,adapter_type,repository_uri,credential_reference,status,"
    "verification_sha256,verification_json,error_code,version,created_at,updated_at"
)
_WORKCELL_COLUMNS = (
    "project_id,workcell_key,workspace_binding_id,version,updated_at"
)


def _team(row: tuple[object, ...]) -> ProjectTeamBinding:
    return ProjectTeamBinding.model_validate(dict(zip(_TEAM_COLUMNS.split(","), row, strict=True)))


def _workspace(row: tuple[object, ...]) -> WorkspaceBinding:
    values = dict(zip(_WORKSPACE_COLUMNS.split(","), row, strict=True))
    values["verification"] = json.loads(str(values.pop("verification_json")))
    return WorkspaceBinding.model_validate(values)


def _workcell(row: tuple[object, ...]) -> ProjectWorkcellBinding:
    return ProjectWorkcellBinding.model_validate(
        dict(zip(_WORKCELL_COLUMNS.split(","), row, strict=True))
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
            _json(event.payload),
            event.occurred_at.isoformat(),
        ),
    )
