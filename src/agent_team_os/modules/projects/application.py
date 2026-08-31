from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from ...shared.repositories import RepositoryRole
from .domain import (
    Project,
    ProjectBindingUpdate,
    ProjectCreate,
    ProjectDeploymentAccess,
    ProjectDeploymentUpdate,
    ProjectDetail,
    ProjectExecutionContext,
    ProjectKnowledgeSource,
    ProjectKnowledgeSourceUpdate,
    ProjectPatch,
    ProjectPipelineBinding,
    ProjectWorkspace,
)
from .domain import (
    ProjectRepository as ProjectRepositoryRecord,
)
from .ports import ProjectRepository, ProjectTeamGovernance, ProjectWorkspaceProvisioner

_FULLSTACK_ROLES: tuple[RepositoryRole, ...] = (
    "backend",
    "design",
    "frontend",
    "qa",
)


class ProjectCatalog:
    def __init__(
        self,
        repository: ProjectRepository,
        provisioner: ProjectWorkspaceProvisioner,
        *,
        team_governance: ProjectTeamGovernance | None = None,
    ) -> None:
        self.repository = repository
        self.provisioner = provisioner
        self._pipeline_validator: Callable[[str], None] | None = None
        self._deployment_validator: Callable[[str], None] | None = None
        self.team_governance = team_governance

    def configure_resource_validators(
        self,
        *,
        pipeline: Callable[[str], None],
        deployment: Callable[[str], None],
    ) -> None:
        self._pipeline_validator = pipeline
        self._deployment_validator = deployment

    def create(self, request: ProjectCreate, actor_id: str) -> ProjectDetail:
        self._validate_pipeline(request.default_pipeline_revision_id)
        if request.team_template_revision_id is not None:
            if self.team_governance is None:
                raise ProductError(
                    code="PROJECT_TEAM_GOVERNANCE_UNAVAILABLE",
                    title="项目团队治理模块未配置",
                    detail="当前运行实例不能创建 Workcell 项目。",
                    repair="启用 Project Workcell Governance 后重试。",
                    status_code=503,
                )
            self.team_governance.validate_team_revision(request.team_template_revision_id)
        for deployment_id in request.deployment_ids:
            self._validate_deployment(deployment_id)
        if self.repository.get(request.id) is not None:
            raise _conflict("PROJECT_ALREADY_EXISTS", "项目标识已经存在", "请使用其他项目标识。")
        now = datetime.now(UTC)
        project = Project(
            id=request.id,
            slug=request.id,
            name=request.name,
            description=request.description,
            lifecycle_status="provisioning",
            version=1,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        workcell_project = request.team_template_revision_id is not None
        workspace = ProjectWorkspace(
            project_id=project.id,
            workspace_id=f"project:{project.id}",
            repository_ref=(
                f"workspace-set/{project.id}" if workcell_project else f"projects/{project.id}"
            ),
            status="provisioning",
            provision_attempt=1,
            created_at=now,
            updated_at=now,
        )
        self.repository.create(project, workspace, legacy_repository=not workcell_project)
        if workcell_project:
            assert request.team_template_revision_id is not None
            assert self.team_governance is not None
            self.team_governance.bind_project(project.id, request.team_template_revision_id)
        else:
            self._provision(project, workspace)
        self.put_pipeline_binding(
            project.id,
            ProjectBindingUpdate(
                pipeline_revision_id=request.default_pipeline_revision_id, is_default=True
            ),
        )
        for deployment_id in request.deployment_ids:
            self.put_deployment_access(
                project.id, ProjectDeploymentUpdate(deployment_id=deployment_id)
            )
        if not workcell_project and request.repository_mode == "fullstack":
            self.provision_fullstack(project.id)
        return self.get(project.id)

    def list(self) -> tuple[Project, ...]:
        return self.repository.list()

    def recover_provisioning(self) -> None:
        """Resume the database-to-Git provisioning saga after a process interruption."""
        for project in self.repository.list():
            if project.lifecycle_status != "provisioning":
                continue
            workspace = self.repository.get_workspace(project.id)
            if workspace is None:
                continue
            if self.team_governance is not None and self.team_governance.has_binding(project.id):
                continue
            self._provision(project, workspace)

    def get(self, project_id: str) -> ProjectDetail:
        project = self._project(project_id)
        workspace = self.repository.get_workspace(project_id)
        if workspace is None:
            raise _not_found()
        return ProjectDetail(
            project=project,
            workspace=workspace,
            pipeline_bindings=self.repository.list_pipeline_bindings(project_id),
            deployment_access=self.repository.list_deployment_access(project_id),
            knowledge_sources=self.repository.list_knowledge_sources(project_id),
            repositories=self.repository.list_repositories(project_id),
            active_delivery_id=self.repository.active_delivery_id(project_id),
        )

    def provision_fullstack(self, project_id: str) -> ProjectDetail:
        project = self._project(project_id)
        if self.team_governance is not None and self.team_governance.has_binding(project_id):
            raise _conflict(
                "PROJECT_WORKCELL_MANAGED_REPOSITORIES_REQUIRED",
                "Workcell 项目不使用旧 RepositoryRole 初始化",
                "通过 Workspace Binding API 为动态 Workcell 绑定独立仓库。",
            )
        if project.lifecycle_status != "active":
            raise _archived() if project.lifecycle_status == "archived" else _not_ready()
        if self.repository.active_delivery_id(project_id) is not None:
            raise _conflict(
                "PROJECT_ACTIVE_DELIVERY_CONFLICT",
                "项目存在活动交付",
                "等待交付进入终态后再初始化全栈仓库。",
            )
        existing = {item.role: item for item in self.repository.list_repositories(project_id)}
        now = datetime.now(UTC)
        for role in _FULLSTACK_ROLES:
            current = existing.get(role)
            if current is not None and current.status == "ready":
                continue
            repository = current or ProjectRepositoryRecord(
                project_id=project_id,
                role=role,
                workspace_ref=f"project:{project_id}:{role}",
                repository_ref=f"projects/{project_id}/{role}",
                status="provisioning",
                provision_attempt=1,
                created_at=now,
                updated_at=now,
            )
            if current is not None:
                repository = current.model_copy(
                    update={
                        "status": "provisioning",
                        "provision_attempt": current.provision_attempt + 1,
                        "error_code": None,
                        "updated_at": now,
                    }
                )
            self.repository.put_repository(repository)
            try:
                revision = self.provisioner.provision(repository.repository_ref)
            except Exception:
                self.repository.put_repository(
                    repository.model_copy(
                        update={
                            "status": "failed",
                            "error_code": "PROJECT_REPOSITORY_PROVISION_FAILED",
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
                continue
            self.repository.put_repository(
                repository.model_copy(
                    update={
                        "status": "ready",
                        "seed_revision": revision,
                        "error_code": None,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
        return self.get(project_id)

    def patch(self, project_id: str, request: ProjectPatch) -> Project:
        project = self._project(project_id)
        if project.lifecycle_status == "archived":
            raise _archived()
        updated = project.model_copy(
            update={
                "name": request.name if request.name is not None else project.name,
                "description": request.description
                if request.description is not None
                else project.description,
                "version": project.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._update(updated, request.expected_version)
        return updated

    def retry_workspace(self, project_id: str) -> ProjectDetail:
        project = self._project(project_id)
        workspace = self._workspace(project_id)
        if project.lifecycle_status != "provision_failed":
            raise _conflict(
                "PROJECT_WORKSPACE_RETRY_NOT_ALLOWED",
                "当前项目无需重试初始化",
                "刷新项目状态后重试。",
            )
        now = datetime.now(UTC)
        provisioning = project.model_copy(
            update={
                "lifecycle_status": "provisioning",
                "version": project.version + 1,
                "updated_at": now,
            }
        )
        self._update(provisioning, project.version)
        workspace = workspace.model_copy(
            update={
                "status": "provisioning",
                "provision_attempt": workspace.provision_attempt + 1,
                "error_code": None,
                "updated_at": now,
            }
        )
        self.repository.update_workspace(workspace)
        return self._provision(provisioning, workspace)

    def reset_workspace(self, project_id: str) -> str:
        project = self._project(project_id)
        if project.lifecycle_status != "active":
            raise _archived() if project.lifecycle_status == "archived" else _not_ready()
        if self.repository.active_delivery_id(project_id):
            raise _conflict(
                "PROJECT_ACTIVE_DELIVERY_CONFLICT",
                "项目存在活动交付",
                "等待交付进入终态后再重置工作区。",
            )
        return self.provisioner.reset(self._workspace(project_id).repository_ref)

    def archive(self, project_id: str, expected_version: int) -> Project:
        project = self._project(project_id)
        if project.lifecycle_status == "archived":
            return project
        if self.repository.active_delivery_id(project_id):
            raise _conflict(
                "PROJECT_ACTIVE_DELIVERY_CONFLICT", "项目存在活动交付", "结束活动交付后再归档项目。"
            )
        archived = project.model_copy(
            update={
                "lifecycle_status": "archived",
                "version": project.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._update(archived, expected_version)
        return archived

    def put_pipeline_binding(
        self, project_id: str, request: ProjectBindingUpdate
    ) -> ProjectPipelineBinding:
        self._ensure_mutable(project_id)
        self._validate_pipeline(request.pipeline_revision_id)
        pipeline_id, revision = _revision(request.pipeline_revision_id)
        current = next(
            (
                item
                for item in self.repository.list_pipeline_bindings(project_id)
                if item.pipeline_id == pipeline_id and item.pipeline_revision == revision
            ),
            None,
        )
        binding = ProjectPipelineBinding(
            project_id=project_id,
            pipeline_id=pipeline_id,
            pipeline_revision=revision,
            enabled=request.enabled,
            is_default=request.is_default,
            version=1 if current is None else current.version + 1,
        )
        try:
            self.repository.put_pipeline_binding(binding, request.expected_version)
        except RuntimeError as error:
            raise _conflict(
                "PROJECT_BINDING_VERSION_CONFLICT",
                "项目流水线绑定版本冲突",
                "刷新项目绑定后重新提交。",
            ) from error
        return binding

    def put_deployment_access(
        self, project_id: str, request: ProjectDeploymentUpdate
    ) -> ProjectDeploymentAccess:
        self._ensure_mutable(project_id)
        self._validate_deployment(request.deployment_id)
        current = next(
            (
                item
                for item in self.repository.list_deployment_access(project_id)
                if item.deployment_id == request.deployment_id
            ),
            None,
        )
        access = ProjectDeploymentAccess(
            project_id=project_id,
            deployment_id=request.deployment_id,
            enabled=request.enabled,
            version=1 if current is None else current.version + 1,
        )
        try:
            self.repository.put_deployment_access(access, request.expected_version)
        except RuntimeError as error:
            raise _conflict(
                "PROJECT_ACCESS_VERSION_CONFLICT",
                "项目智能体授权版本冲突",
                "刷新项目授权后重新提交。",
            ) from error
        return access

    def put_knowledge_source(
        self, project_id: str, request: ProjectKnowledgeSourceUpdate
    ) -> ProjectKnowledgeSource:
        self._ensure_mutable(project_id)
        current = next(
            (
                item
                for item in self.repository.list_knowledge_sources(project_id)
                if item.binding_id == request.binding_id
                and item.source_scope == request.source_scope
            ),
            None,
        )
        source = ProjectKnowledgeSource(
            project_id=project_id,
            binding_id=request.binding_id,
            source_scope=request.source_scope,
            enabled=request.enabled,
            version=1 if current is None else current.version + 1,
        )
        try:
            self.repository.put_knowledge_source(source, request.expected_version)
        except RuntimeError as error:
            raise _conflict(
                "PROJECT_KNOWLEDGE_SOURCE_VERSION_CONFLICT",
                "项目知识来源版本冲突",
                "刷新项目知识来源后重新提交。",
            ) from error
        return source

    def prepare_delivery(
        self, project_id: str, delivery_id: str, requested_pipeline_revision_id: str | None
    ) -> ProjectExecutionContext:
        project = self._project(project_id)
        if project.lifecycle_status != "active":
            raise _archived() if project.lifecycle_status == "archived" else _not_ready()
        workspace = self._workspace(project_id)
        if workspace.status != "ready":
            raise _not_ready()
        bindings = tuple(
            item for item in self.repository.list_pipeline_bindings(project_id) if item.enabled
        )
        selected = next(
            (item for item in bindings if item.revision_id == requested_pipeline_revision_id), None
        )
        if requested_pipeline_revision_id is None:
            selected = next((item for item in bindings if item.is_default), None)
        if selected is None:
            raise _conflict(
                "PROJECT_PIPELINE_NOT_ALLOWED",
                "项目未启用所选流水线",
                "在项目设置中启用并固定一个流水线版本。",
            )
        deployments = tuple(
            item.deployment_id
            for item in self.repository.list_deployment_access(project_id)
            if item.enabled
        )
        repository_records = self.repository.list_repositories(project_id)
        unavailable = tuple(item for item in repository_records if item.status != "ready")
        if unavailable:
            roles = "、".join(item.role for item in unavailable)
            raise ProductError(
                code="PROJECT_REPOSITORY_NOT_READY",
                title="项目仓库集合尚未就绪",
                detail=f"以下仓库尚未完成初始化：{roles}。",
                repair="在项目概览重新初始化全栈仓库，确认全部仓库状态为就绪后再创建交付。",
                status_code=409,
            )
        repositories = tuple(
            item.snapshot().model_copy(
                update={"seed_revision": self.provisioner.revision(item.repository_ref)}
            )
            for item in repository_records
        )
        repository_set_sha256 = sha256_json(
            [item.model_dump(mode="json") for item in repositories]
        )
        return ProjectExecutionContext(
            project_id=project_id,
            project_version=project.version,
            workspace_id=workspace.workspace_id,
            repository_ref=workspace.repository_ref,
            pipeline_revision_id=selected.revision_id,
            deployment_ids=deployments,
            repositories=repositories,
            repository_set_sha256=repository_set_sha256,
        )

    def release_delivery(self, project_id: str, delivery_id: str) -> None:
        self.repository.release_lease(project_id, delivery_id)

    def ensure_legacy_defaults(
        self, pipeline_revision_id: str, deployment_ids: tuple[str, ...]
    ) -> None:
        if not self.repository.list_pipeline_bindings("legacy-default"):
            self.put_pipeline_binding(
                "legacy-default",
                ProjectBindingUpdate(pipeline_revision_id=pipeline_revision_id, is_default=True),
            )
        existing = {
            item.deployment_id for item in self.repository.list_deployment_access("legacy-default")
        }
        for deployment_id in deployment_ids:
            if deployment_id not in existing:
                self.put_deployment_access(
                    "legacy-default", ProjectDeploymentUpdate(deployment_id=deployment_id)
                )

    def assert_writable(self, project_id: str) -> None:
        project = self._project(project_id)
        if project.lifecycle_status == "archived":
            raise _archived()
        if project.lifecycle_status != "active":
            raise _not_ready()

    def _provision(self, project: Project, workspace: ProjectWorkspace) -> ProjectDetail:
        now = datetime.now(UTC)
        try:
            revision = self.provisioner.provision(workspace.repository_ref)
        except Exception:
            failed_workspace = workspace.model_copy(
                update={
                    "status": "failed",
                    "error_code": "PROJECT_WORKSPACE_PROVISION_FAILED",
                    "updated_at": now,
                }
            )
            self.repository.update_workspace(failed_workspace)
            failed = project.model_copy(
                update={
                    "lifecycle_status": "provision_failed",
                    "version": project.version + 1,
                    "updated_at": now,
                }
            )
            self._update(failed, project.version)
            return self.get(project.id)
        ready_workspace = workspace.model_copy(
            update={
                "status": "ready",
                "seed_revision": revision,
                "error_code": None,
                "updated_at": now,
            }
        )
        self.repository.update_workspace(ready_workspace)
        active = project.model_copy(
            update={"lifecycle_status": "active", "version": project.version + 1, "updated_at": now}
        )
        self._update(active, project.version)
        return self.get(project.id)

    def _project(self, project_id: str) -> Project:
        project = self.repository.get(project_id)
        if project is None:
            raise _not_found()
        return project

    def _workspace(self, project_id: str) -> ProjectWorkspace:
        workspace = self.repository.get_workspace(project_id)
        if workspace is None:
            raise _not_found()
        return workspace

    def _ensure_mutable(self, project_id: str) -> None:
        project = self._project(project_id)
        if project.lifecycle_status == "archived":
            raise _archived()

    def _update(self, project: Project, expected_version: int) -> None:
        try:
            self.repository.update_project(project, expected_version)
        except RuntimeError as error:
            if str(error) == "PROJECT_ACTIVE_DELIVERY_CONFLICT":
                raise _conflict(
                    "PROJECT_ACTIVE_DELIVERY_CONFLICT",
                    "项目存在活动交付",
                    "等待交付进入终态后再归档项目。",
                ) from error
            raise _conflict(
                "PROJECT_VERSION_CONFLICT", "项目版本冲突", "刷新项目详情后重新提交。"
            ) from error

    def _validate_pipeline(self, revision_id: str) -> None:
        if self._pipeline_validator is None:
            return
        try:
            self._pipeline_validator(revision_id)
        except ProductError:
            raise
        except Exception as error:
            raise _conflict(
                "PROJECT_PIPELINE_REVISION_NOT_FOUND",
                "流水线版本不存在",
                "选择已经发布且可查询的固定流水线版本。",
            ) from error

    def _validate_deployment(self, deployment_id: str) -> None:
        if self._deployment_validator is None:
            return
        try:
            self._deployment_validator(deployment_id)
        except ProductError:
            raise
        except Exception as error:
            raise _conflict(
                "PROJECT_DEPLOYMENT_NOT_AVAILABLE",
                "Agent 部署不可用于项目",
                "选择已启用且资格检查通过的 Agent 部署。",
            ) from error


def _revision(value: str) -> tuple[str, int]:
    try:
        pipeline_id, revision = value.rsplit(":", 1)
        return pipeline_id, int(revision)
    except (ValueError, TypeError) as error:
        raise _conflict(
            "PROJECT_PIPELINE_REVISION_INVALID", "流水线版本格式无效", "请选择已发布的流水线版本。"
        ) from error


def _not_found() -> ProductError:
    return ProductError(
        code="PROJECT_NOT_FOUND",
        title="项目不存在",
        detail="目标项目不存在。",
        repair="刷新项目列表后重新选择。",
        status_code=404,
    )


def _not_ready() -> ProductError:
    return _conflict(
        "PROJECT_WORKSPACE_NOT_READY",
        "项目工作区尚未就绪",
        "检查初始化错误并重试工作区 Provisioning。",
    )


def _archived() -> ProductError:
    return _conflict("PROJECT_ARCHIVED", "项目已经归档", "归档项目仅允许读取历史和重新验证证据。")


def _conflict(code: str, title: str, repair: str) -> ProductError:
    return ProductError(code=code, title=title, detail=title, repair=repair)
