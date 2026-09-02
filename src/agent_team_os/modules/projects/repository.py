from __future__ import annotations

import sqlite3
from pathlib import Path

from .domain import (
    Project,
    ProjectAccessAudit,
    ProjectDeploymentAccess,
    ProjectKnowledgeSource,
    ProjectKnowledgeSourceApproval,
    ProjectMembership,
    ProjectPipelineBinding,
    ProjectRepository,
    ProjectWorkspace,
)


class SQLiteProjectRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(
        self,
        project: Project,
        workspace: ProjectWorkspace,
        *,
        legacy_repository: bool = True,
    ) -> None:
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
            connection.execute(
                """INSERT INTO project_authorization_versions(project_id,version,updated_at)
                VALUES(?,1,?)""",
                (project.id, project.created_at.isoformat()),
            )
            connection.execute(
                """INSERT INTO project_memberships(
                project_id,user_id,role,version,created_at,updated_at)
                SELECT ?,id,'owner',1,?,? FROM users WHERE id=?""",
                (
                    project.id,
                    project.created_at.isoformat(),
                    project.created_at.isoformat(),
                    project.created_by,
                ),
            )
            if legacy_repository:
                connection.execute(
                    """INSERT INTO project_repositories(
                    project_id,role,workspace_ref,repository_ref,seed_revision,status,
                    provision_attempt,error_code,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        workspace.project_id,
                        "backend",
                        workspace.workspace_id,
                        workspace.repository_ref,
                        workspace.seed_revision,
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

    def list_memberships(self, project_id: str) -> tuple[ProjectMembership, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT project_id,user_id,role,version
                FROM project_memberships WHERE project_id=? ORDER BY role,user_id""",
                (project_id,),
            ).fetchall()
        return tuple(_membership(row) for row in rows)

    def get_membership(self, project_id: str, user_id: str) -> ProjectMembership | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT project_id,user_id,role,version FROM project_memberships
                WHERE project_id=? AND user_id=?""",
                (project_id, user_id),
            ).fetchone()
        return None if row is None else _membership(row)

    def list_membership_project_ids(self, user_id: str) -> tuple[str, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT project_id FROM project_memberships
                WHERE user_id=? ORDER BY project_id""",
                (user_id,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def get_authorization_version(self, project_id: str) -> int:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT version FROM project_authorization_versions WHERE project_id=?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return int(row[0])

    def put_membership(self, membership: ProjectMembership, expected_version: int | None) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT role,version FROM project_memberships
                WHERE project_id=? AND user_id=?""",
                (membership.project_id, membership.user_id),
            ).fetchone()
            if (current is None and expected_version is not None) or (
                current is not None and int(current[1]) != expected_version
            ):
                raise RuntimeError("PROJECT_MEMBERSHIP_VERSION_CONFLICT")
            if current is not None and str(current[0]) == "owner" and membership.role != "owner":
                owner_count = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM project_memberships
                        JOIN users ON users.id=project_memberships.user_id
                        WHERE project_id=? AND project_memberships.role='owner'
                        AND users.enabled=1""",
                        (membership.project_id,),
                    ).fetchone()[0]
                )
                if owner_count <= 1:
                    raise RuntimeError("PROJECT_LAST_OWNER_REQUIRED")
            connection.execute(
                """INSERT INTO project_memberships(
                project_id,user_id,role,version,created_at,updated_at)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT(project_id,user_id) DO UPDATE SET
                role=excluded.role,version=excluded.version,updated_at=CURRENT_TIMESTAMP""",
                (
                    membership.project_id,
                    membership.user_id,
                    membership.role,
                    membership.version,
                ),
            )

    def delete_membership(self, project_id: str, user_id: str, expected_version: int) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT role,version FROM project_memberships
                WHERE project_id=? AND user_id=?""",
                (project_id, user_id),
            ).fetchone()
            if current is None or int(current[1]) != expected_version:
                raise RuntimeError("PROJECT_MEMBERSHIP_VERSION_CONFLICT")
            if str(current[0]) == "owner":
                owner_count = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM project_memberships
                        JOIN users ON users.id=project_memberships.user_id
                        WHERE project_id=? AND project_memberships.role='owner'
                        AND users.enabled=1""",
                        (project_id,),
                    ).fetchone()[0]
                )
                if owner_count <= 1:
                    raise RuntimeError("PROJECT_LAST_OWNER_REQUIRED")
            connection.execute(
                """DELETE FROM project_memberships
                WHERE project_id=? AND user_id=? AND version=?""",
                (project_id, user_id, expected_version),
            )

    def append_access_audit(self, audit: ProjectAccessAudit) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT INTO project_access_audit(
                id,actor_user_id,project_id,capability,resource,reason,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    audit.id,
                    audit.actor_user_id,
                    audit.project_id,
                    audit.capability,
                    audit.resource,
                    audit.reason,
                    audit.created_at.isoformat(),
                ),
            )

    def list_access_audits(self, project_id: str) -> tuple[ProjectAccessAudit, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT id,actor_user_id,project_id,capability,resource,reason,created_at
                FROM project_access_audit WHERE project_id=? ORDER BY created_at,id""",
                (project_id,),
            ).fetchall()
        return tuple(_access_audit(row) for row in rows)

    def get_workspace(self, project_id: str) -> ProjectWorkspace | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT * FROM project_workspaces WHERE project_id=?", (project_id,)
            ).fetchone()
        return None if row is None else _workspace(row)

    def update_project(self, project: Project, expected_version: int) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT lifecycle_status FROM projects WHERE id=? AND version=?",
                (project.id, expected_version),
            ).fetchone()
            if current is None:
                raise RuntimeError("PROJECT_VERSION_CONFLICT")
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
            if str(current[0]) != project.lifecycle_status:
                connection.execute(
                    """UPDATE project_authorization_versions
                    SET version=version+1,updated_at=? WHERE project_id=?""",
                    (project.updated_at.isoformat(), project.id),
                )

    def update_workspace(self, workspace: ProjectWorkspace) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
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
            connection.execute(
                """UPDATE project_repositories SET seed_revision=?,status=?,
                provision_attempt=?,error_code=?,updated_at=?
                WHERE project_id=? AND role='backend'""",
                (
                    workspace.seed_revision,
                    workspace.status,
                    workspace.provision_attempt,
                    workspace.error_code,
                    workspace.updated_at.isoformat(),
                    workspace.project_id,
                ),
            )

    def list_repositories(self, project_id: str) -> tuple[ProjectRepository, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT project_id,role,workspace_ref,repository_ref,seed_revision,
                status,provision_attempt,error_code,created_at,updated_at
                FROM project_repositories WHERE project_id=? ORDER BY role""",
                (project_id,),
            ).fetchall()
        return tuple(_repository(row) for row in rows)

    def put_repository(self, repository: ProjectRepository) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT INTO project_repositories(
                project_id,role,workspace_ref,repository_ref,seed_revision,status,
                provision_attempt,error_code,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id,role) DO UPDATE SET
                workspace_ref=excluded.workspace_ref,
                repository_ref=excluded.repository_ref,
                seed_revision=excluded.seed_revision,
                status=excluded.status,
                provision_attempt=excluded.provision_attempt,
                error_code=excluded.error_code,
                updated_at=excluded.updated_at""",
                (
                    repository.project_id,
                    repository.role,
                    repository.workspace_ref,
                    repository.repository_ref,
                    repository.seed_revision,
                    repository.status,
                    repository.provision_attempt,
                    repository.error_code,
                    repository.created_at.isoformat(),
                    repository.updated_at.isoformat(),
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

    def list_knowledge_source_approvals(
        self, project_id: str
    ) -> tuple[ProjectKnowledgeSourceApproval, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT id,project_id,binding_id,enabled,rag_enabled,version,
                created_by,created_at,updated_at
                FROM project_knowledge_source_approvals_v2
                WHERE project_id=? ORDER BY binding_id,id""",
                (project_id,),
            ).fetchall()
        return tuple(_knowledge_source_approval(row) for row in rows)

    def list_all_knowledge_source_approvals(
        self,
    ) -> tuple[ProjectKnowledgeSourceApproval, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT id,project_id,binding_id,enabled,rag_enabled,version,
                created_by,created_at,updated_at
                FROM project_knowledge_source_approvals_v2
                ORDER BY project_id,binding_id,id"""
            ).fetchall()
        return tuple(_knowledge_source_approval(row) for row in rows)

    def put_knowledge_source_approval(
        self,
        approval: ProjectKnowledgeSourceApproval,
        expected_version: int | None,
    ) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT version FROM project_knowledge_source_approvals_v2
                WHERE project_id=? AND binding_id=?""",
                (approval.project_id, approval.binding_id),
            ).fetchone()
            if (current is None and expected_version is not None) or (
                current is not None and int(current[0]) != expected_version
            ):
                raise RuntimeError("PROJECT_KNOWLEDGE_APPROVAL_VERSION_CONFLICT")
            connection.execute(
                """INSERT INTO project_knowledge_source_approvals_v2(
                id,project_id,binding_id,enabled,rag_enabled,version,created_by,
                created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id,binding_id) DO UPDATE SET
                enabled=excluded.enabled,rag_enabled=excluded.rag_enabled,
                version=excluded.version,updated_at=excluded.updated_at""",
                (
                    approval.id,
                    approval.project_id,
                    approval.binding_id,
                    int(approval.enabled),
                    int(approval.rag_enabled),
                    approval.version,
                    approval.created_by,
                    approval.created_at.isoformat(),
                    approval.updated_at.isoformat(),
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


def _repository(row: tuple[object, ...]) -> ProjectRepository:
    fields = (
        "project_id",
        "role",
        "workspace_ref",
        "repository_ref",
        "seed_revision",
        "status",
        "provision_attempt",
        "error_code",
        "created_at",
        "updated_at",
    )
    return ProjectRepository.model_validate(dict(zip(fields, row, strict=True)))


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


def _knowledge_source_approval(
    row: tuple[object, ...],
) -> ProjectKnowledgeSourceApproval:
    return ProjectKnowledgeSourceApproval.model_validate(
        {
            "id": row[0],
            "project_id": row[1],
            "binding_id": row[2],
            "enabled": bool(row[3]),
            "rag_enabled": bool(row[4]),
            "version": row[5],
            "created_by": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }
    )


def _membership(row: tuple[object, ...]) -> ProjectMembership:
    return ProjectMembership.model_validate(
        {
            "project_id": row[0],
            "user_id": row[1],
            "role": row[2],
            "version": row[3],
        }
    )


def _access_audit(row: tuple[object, ...]) -> ProjectAccessAudit:
    return ProjectAccessAudit.model_validate(
        {
            "id": row[0],
            "actor_user_id": row[1],
            "project_id": row[2],
            "capability": row[3],
            "resource": row[4],
            "reason": row[5],
            "created_at": row[6],
        }
    )
