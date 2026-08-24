from __future__ import annotations

import sqlite3
from pathlib import Path

from .domain import (
    Project,
    ProjectDeploymentAccess,
    ProjectKnowledgeSource,
    ProjectPipelineBinding,
    ProjectWorkspace,
)


class SQLiteProjectRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(self, project: Project, workspace: ProjectWorkspace) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO projects(id,slug,name,description,lifecycle_status,version,
                created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    project.id,
                    project.slug,
                    project.name,
                    project.description,
                    project.lifecycle_status,
                    project.version,
                    project.created_by,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )
            connection.execute(
                """INSERT INTO project_workspaces(project_id,workspace_id,seed_revision,
                repository_ref,status,provision_attempt,error_code,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    workspace.project_id,
                    workspace.workspace_id,
                    workspace.seed_revision,
                    workspace.repository_ref,
                    workspace.status,
                    workspace.provision_attempt,
                    workspace.error_code,
                    workspace.created_at.isoformat(),
                    workspace.updated_at.isoformat(),
                ),
            )

    def get(self, project_id: str) -> Project | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return None if row is None else _project(row)

    def list(self) -> tuple[Project, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute("SELECT * FROM projects ORDER BY created_at,id").fetchall()
        return tuple(_project(row) for row in rows)

    def get_workspace(self, project_id: str) -> ProjectWorkspace | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT * FROM project_workspaces WHERE project_id=?", (project_id,)
            ).fetchone()
        return None if row is None else _workspace(row)

    def update_project(self, project: Project, expected_version: int) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if project.lifecycle_status == "archived":
                lease = connection.execute(
                    "SELECT 1 FROM project_delivery_leases WHERE project_id=?",
                    (project.id,),
                ).fetchone()
                if lease is not None:
                    raise RuntimeError("PROJECT_ACTIVE_DELIVERY_CONFLICT")
            cursor = connection.execute(
                """UPDATE projects SET name=?,description=?,lifecycle_status=?,version=?,
                updated_at=? WHERE id=? AND version=?""",
                (
                    project.name,
                    project.description,
                    project.lifecycle_status,
                    project.version,
                    project.updated_at.isoformat(),
                    project.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("PROJECT_VERSION_CONFLICT")

    def update_workspace(self, workspace: ProjectWorkspace) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """UPDATE project_workspaces SET seed_revision=?,status=?,provision_attempt=?,
                error_code=?,updated_at=? WHERE project_id=?""",
                (
                    workspace.seed_revision,
                    workspace.status,
                    workspace.provision_attempt,
                    workspace.error_code,
                    workspace.updated_at.isoformat(),
                    workspace.project_id,
                ),
            )

    def acquire_lease(self, project_id: str, delivery_id: str) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            status = connection.execute(
                "SELECT lifecycle_status FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if status is None or status[0] != "active":
                raise RuntimeError("PROJECT_NOT_ACTIVE")
            try:
                connection.execute(
                    """INSERT INTO project_delivery_leases(
                    project_id,delivery_id,acquired_at
                    ) VALUES(?,?,CURRENT_TIMESTAMP)""",
                    (project_id, delivery_id),
                )
            except sqlite3.IntegrityError as error:
                raise RuntimeError("PROJECT_ACTIVE_DELIVERY_CONFLICT") from error

    def release_lease(self, project_id: str, delivery_id: str) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "DELETE FROM project_delivery_leases WHERE project_id=? AND delivery_id=?",
                (project_id, delivery_id),
            )

    def active_delivery_id(self, project_id: str) -> str | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT delivery_id FROM project_delivery_leases WHERE project_id=?", (project_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def list_pipeline_bindings(self, project_id: str) -> tuple[ProjectPipelineBinding, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT * FROM project_pipeline_bindings
                WHERE project_id=? ORDER BY pipeline_id,pipeline_revision""",
                (project_id,),
            ).fetchall()
        return tuple(_pipeline_binding(row) for row in rows)

    def put_pipeline_binding(
        self, binding: ProjectPipelineBinding, expected_version: int | None
    ) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT version FROM project_pipeline_bindings
                WHERE project_id=? AND pipeline_id=? AND pipeline_revision=?""",
                (binding.project_id, binding.pipeline_id, binding.pipeline_revision),
            ).fetchone()
            if (current is None and expected_version is not None) or (
                current is not None and expected_version != int(current[0])
            ):
                raise RuntimeError("PROJECT_BINDING_VERSION_CONFLICT")
            if binding.is_default and binding.enabled:
                connection.execute(
                    """UPDATE project_pipeline_bindings
                    SET is_default=0,version=version+1,updated_at=?
                    WHERE project_id=? AND is_default=1""",
                    (binding.updated_at.isoformat(), binding.project_id),
                )
            connection.execute(
                """INSERT INTO project_pipeline_bindings(
                project_id,pipeline_id,pipeline_revision,enabled,is_default,version,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(project_id,pipeline_id,pipeline_revision) DO UPDATE SET
                enabled=excluded.enabled,is_default=excluded.is_default,
                version=excluded.version,updated_at=excluded.updated_at""",
                (
                    binding.project_id,
                    binding.pipeline_id,
                    binding.pipeline_revision,
                    int(binding.enabled),
                    int(binding.is_default),
                    binding.version,
                    binding.updated_at.isoformat(),
                ),
            )

    def list_deployment_access(self, project_id: str) -> tuple[ProjectDeploymentAccess, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                "SELECT * FROM project_deployment_access WHERE project_id=? ORDER BY deployment_id",
                (project_id,),
            ).fetchall()
        return tuple(_deployment_access(row) for row in rows)

    def put_deployment_access(
        self, access: ProjectDeploymentAccess, expected_version: int | None
    ) -> None:
        with sqlite3.connect(self.database) as connection:
            current = connection.execute(
                """SELECT version FROM project_deployment_access
                WHERE project_id=? AND deployment_id=?""",
                (access.project_id, access.deployment_id),
            ).fetchone()
            if (current is None and expected_version is not None) or (
                current is not None and expected_version != int(current[0])
            ):
                raise RuntimeError("PROJECT_ACCESS_VERSION_CONFLICT")
            connection.execute(
                """INSERT INTO project_deployment_access(
                project_id,deployment_id,enabled,version,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(project_id,deployment_id) DO UPDATE SET
                enabled=excluded.enabled,version=excluded.version,updated_at=excluded.updated_at""",
                (
                    access.project_id,
                    access.deployment_id,
                    int(access.enabled),
                    access.version,
                    access.updated_at.isoformat(),
                ),
            )

    def list_knowledge_sources(self, project_id: str) -> tuple[ProjectKnowledgeSource, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT project_id,binding_id,source_scope,enabled,version,updated_at
                FROM project_knowledge_sources WHERE project_id=?
                ORDER BY binding_id,source_scope""",
                (project_id,),
            ).fetchall()
        return tuple(_knowledge_source(row) for row in rows)

    def put_knowledge_source(
        self, source: ProjectKnowledgeSource, expected_version: int | None
    ) -> None:
        with sqlite3.connect(self.database) as connection:
            current = connection.execute(
                """SELECT version FROM project_knowledge_sources
                WHERE project_id=? AND binding_id=? AND source_scope=?""",
                (source.project_id, source.binding_id, source.source_scope),
            ).fetchone()
            if (current is None and expected_version is not None) or (
                current is not None and expected_version != int(current[0])
            ):
                raise RuntimeError("PROJECT_KNOWLEDGE_SOURCE_VERSION_CONFLICT")
            connection.execute(
                """INSERT INTO project_knowledge_sources(
                project_id,binding_id,source_scope,enabled,version,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(project_id,binding_id,source_scope) DO UPDATE SET
                enabled=excluded.enabled,version=excluded.version,updated_at=excluded.updated_at""",
                (
                    source.project_id,
                    source.binding_id,
                    source.source_scope,
                    int(source.enabled),
                    source.version,
                    source.updated_at.isoformat(),
                ),
            )


def _project(row: tuple[object, ...]) -> Project:
    fields = (
        "id",
        "slug",
        "name",
        "description",
        "lifecycle_status",
        "version",
        "created_by",
        "created_at",
        "updated_at",
    )
    return Project.model_validate(dict(zip(fields, row, strict=True)))


def _workspace(row: tuple[object, ...]) -> ProjectWorkspace:
    fields = (
        "project_id",
        "workspace_id",
        "seed_revision",
        "repository_ref",
        "status",
        "provision_attempt",
        "error_code",
        "created_at",
        "updated_at",
    )
    return ProjectWorkspace.model_validate(dict(zip(fields, row, strict=True)))


def _pipeline_binding(row: tuple[object, ...]) -> ProjectPipelineBinding:
    return ProjectPipelineBinding.model_validate(
        {
            "project_id": row[0],
            "pipeline_id": row[1],
            "pipeline_revision": row[2],
            "enabled": bool(row[3]),
            "is_default": bool(row[4]),
            "version": row[5],
            "updated_at": row[6],
        }
    )


def _deployment_access(row: tuple[object, ...]) -> ProjectDeploymentAccess:
    return ProjectDeploymentAccess.model_validate(
        {
            "project_id": row[0],
            "deployment_id": row[1],
            "enabled": bool(row[2]),
            "version": row[3],
            "updated_at": row[4],
        }
    )


def _knowledge_source(row: tuple[object, ...]) -> ProjectKnowledgeSource:
    return ProjectKnowledgeSource.model_validate(
        {
            "project_id": row[0],
            "binding_id": row[1],
            "source_scope": row[2],
            "enabled": bool(row[3]),
            "version": row[4],
            "updated_at": row[5],
        }
    )
