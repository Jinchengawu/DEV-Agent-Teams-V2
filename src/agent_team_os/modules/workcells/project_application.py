from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ...infrastructure.git import (
    ExternalGitBinding,
    ExternalGitCapabilityProbe,
    ProjectGitWorkspaces,
)
from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from ...shared.ids import new_id
from ..projects.ports import ProjectRepository
from .application import TeamTemplateCatalog
from .domain import (
    ProjectTeamBinding,
    ProjectWorkcellBinding,
    ProjectWorkcellTopology,
    WorkspaceBinding,
    WorkspaceBindingAssignment,
    WorkspaceBindingCreate,
)
from .project_repository import SQLiteProjectWorkcellRepository


class ProjectWorkcellGovernance:
    """Compile TeamTemplate and workspace identities into project governance facts."""

    def __init__(
        self,
        repository: SQLiteProjectWorkcellRepository,
        *,
        teams: TeamTemplateCatalog,
        projects: ProjectRepository,
        managed_git: ProjectGitWorkspaces,
        external_git: ExternalGitCapabilityProbe | None = None,
    ) -> None:
        self.repository = repository
        self.teams = teams
        self.projects = projects
        self.managed_git = managed_git
        self.external_git = external_git

    def validate_team_revision(self, revision_id: str) -> None:
        template_id, revision = _revision_id(revision_id)
        try:
            self.teams.get_revision(template_id, revision)
        except KeyError as error:
            raise _error(
                "TEAM_TEMPLATE_REVISION_NOT_FOUND",
                "TeamTemplate Revision 不存在",
                "绑定一个已经发布且可查询的 TeamTemplate Revision。",
            ) from error

    def bind_project(self, project_id: str, revision_id: str) -> ProjectTeamBinding:
        template_id, revision_number = _revision_id(revision_id)
        try:
            revision = self.teams.get_revision(template_id, revision_number)
            return self.repository.bind_team(project_id, revision)
        except KeyError as error:
            raise _error(
                "TEAM_TEMPLATE_REVISION_NOT_FOUND",
                "TeamTemplate Revision 不存在",
                "绑定一个已经发布且可查询的 TeamTemplate Revision。",
            ) from error
        except sqlite3.IntegrityError as error:
            raise _error(
                "PROJECT_TEAM_BINDING_CONFLICT",
                "项目已经绑定 TeamTemplate",
                "读取现有绑定；v0.5 不允许原地切换已绑定的团队 Revision。",
            ) from error

    def has_binding(self, project_id: str) -> bool:
        try:
            self.repository.get_team(project_id)
        except KeyError:
            return False
        return True

    def topology(self, project_id: str) -> ProjectWorkcellTopology:
        project = self.projects.get(project_id)
        if project is None:
            raise _error("PROJECT_NOT_FOUND", "项目不存在", "刷新项目列表后重试。", 404)
        try:
            team = self.repository.get_team(project_id)
        except KeyError as error:
            raise _error(
                "PROJECT_TEAM_BINDING_NOT_FOUND",
                "项目尚未绑定 TeamTemplate",
                "先绑定一个 Published TeamTemplate Revision。",
            ) from error
        revision = self.teams.get_revision(team.template_id, team.template_revision)
        workcells = self.repository.list_workcells(project_id)
        workspaces = self.repository.list_workspaces(project_id)
        order = {
            definition.workcell_key: index
            for index, definition in enumerate(revision.workcells)
        }
        ordered_workcells = tuple(
            sorted(workcells, key=lambda item: order.get(item.workcell_key, len(order)))
        )
        workspace_by_id = {item.id: item for item in workspaces}
        ordered_workspaces = tuple(
            workspace_by_id[item.workspace_binding_id]
            for item in ordered_workcells
            if item.workspace_binding_id in workspace_by_id
        )
        return ProjectWorkcellTopology(
            project_id=project_id,
            project_status=project.lifecycle_status,
            team_binding=team,
            team_revision=revision,
            workcell_bindings=ordered_workcells,
            workspace_bindings=ordered_workspaces,
        )

    def create_workspace_binding(
        self,
        project_id: str,
        request: WorkspaceBindingCreate | dict[str, object],
    ) -> WorkspaceBindingAssignment:
        body = WorkspaceBindingCreate.model_validate(request)
        topology = self.topology(project_id)
        if topology.team_binding.status != "provisioning":
            raise _error(
                "PROJECT_TEAM_ALREADY_ACTIVE",
                "活动团队不能修改 Workspace Binding",
                "创建新项目或等待后续受控的 Team Revision 迁移能力。",
            )
        known = {item.workcell_key for item in topology.team_revision.workcells}
        if body.workcell_key not in known:
            raise _error(
                "PROJECT_WORKCELL_UNKNOWN",
                "Workcell 不属于冻结的 TeamTemplate Revision",
                "从项目组织拓扑选择有效 Workcell。",
            )
        if body.adapter_type == "managed-bare-git":
            expected_uri = f"projects/{project_id}/{body.workcell_key}"
            if body.repository_uri != expected_uri:
                raise _error(
                    "MANAGED_WORKSPACE_REFERENCE_INVALID",
                    "Managed Workspace 引用不属于当前 Workcell",
                    f"使用产品生成的引用 {expected_uri}。",
                )
        workspace = WorkspaceBinding(
            id=new_id(),
            project_id=project_id,
            kind=body.kind,
            adapter_type=body.adapter_type,
            repository_uri=body.repository_uri,
            credential_reference=body.credential_reference,
            status="pending",
            version=1,
        )
        workcell = ProjectWorkcellBinding(
            project_id=project_id,
            workcell_key=body.workcell_key,
            workspace_binding_id=workspace.id,
            version=1,
        )
        try:
            self.repository.create_assignment(workcell, workspace)
        except sqlite3.IntegrityError as error:
            raise _error(
                "PROJECT_WORKSPACE_BINDING_CONFLICT",
                "Workcell 或 Repository 已经绑定",
                "每个 Workcell 只能绑定一个 Primary Repository，且四仓地址必须互不相同。",
            ) from error
        return WorkspaceBindingAssignment(
            workcell_binding=workcell,
            workspace_binding=workspace,
        )

    def verify_workspace(self, workspace_id: str, *, expected_version: int) -> WorkspaceBinding:
        try:
            current = self.repository.get_workspace(workspace_id)
        except KeyError as error:
            raise _error(
                "WORKSPACE_BINDING_NOT_FOUND",
                "Workspace Binding 不存在",
                "刷新项目 Workspace 列表后重试。",
                404,
            ) from error
        if current.version != expected_version:
            raise _version_error("WORKSPACE_BINDING_VERSION_CONFLICT")
        now = datetime.now(UTC)
        try:
            if current.adapter_type == "managed-bare-git":
                main_sha = self.managed_git.provision(current.repository_uri)
                receipt: dict[str, object] = {
                    "adapter_type": current.adapter_type,
                    "repository_uri": current.repository_uri,
                    "main_sha": main_sha,
                    "direct_fast_forward_main": True,
                    "verification_policy": "managed-bare-git-v1",
                }
            else:
                if self.external_git is None:
                    raise _error(
                        "EXTERNAL_GIT_VERIFIER_UNAVAILABLE",
                        "External Git 能力探测器未配置",
                        "配置 External Git Verifier 后重新验证。",
                        503,
                    )
                external_receipt = self.external_git.verify(
                    ExternalGitBinding(
                        remote_uri=current.repository_uri,
                        credential_reference=current.credential_reference,
                    )
                )
                receipt = external_receipt.model_dump(mode="json")
            verification_sha = sha256_json(
                {
                    "workspace_binding_id": current.id,
                    "project_id": current.project_id,
                    "kind": current.kind,
                    "repository_uri": current.repository_uri,
                    "credential_reference": current.credential_reference,
                    "receipt": receipt,
                }
            )
            updated = current.model_copy(
                update={
                    "status": "ready",
                    "verification_sha256": verification_sha,
                    "verification": receipt,
                    "error_code": None,
                    "version": current.version + 1,
                    "updated_at": now,
                }
            )
        except ProductError as error:
            failed = current.model_copy(
                update={
                    "status": "failed",
                    "verification_sha256": None,
                    "verification": {},
                    "error_code": error.code,
                    "version": current.version + 1,
                    "updated_at": now,
                }
            )
            self._swap_workspace(current, failed)
            raise
        except Exception as error:
            failed = current.model_copy(
                update={
                    "status": "failed",
                    "verification_sha256": None,
                    "verification": {},
                    "error_code": "WORKSPACE_VERIFICATION_FAILED",
                    "version": current.version + 1,
                    "updated_at": now,
                }
            )
            self._swap_workspace(current, failed)
            raise _error(
                "WORKSPACE_VERIFICATION_FAILED",
                "Workspace 验证失败",
                "检查仓库地址、权限和 main 引用后重新验证。",
            ) from error
        self._swap_workspace(current, updated)
        return updated

    def activate(self, project_id: str, *, expected_version: int) -> ProjectWorkcellTopology:
        topology = self.topology(project_id)
        team = topology.team_binding
        if team.version != expected_version:
            raise _version_error("PROJECT_TEAM_BINDING_VERSION_CONFLICT")
        if team.status == "active":
            return topology
        if team.status != "provisioning":
            raise _error(
                "PROJECT_TEAM_ACTIVATION_NOT_ALLOWED",
                "当前 Team Binding 不允许激活",
                "检查项目迁移状态后重试。",
            )
        required = {item.workcell_key for item in topology.team_revision.workcells}
        bound = {item.workcell_key for item in topology.workcell_bindings}
        if bound != required:
            missing = sorted(required - bound)
            raise _error(
                "PROJECT_WORKCELL_BINDINGS_INCOMPLETE",
                "项目尚未绑定全部 Workcell Workspace",
                "补齐缺失 Workcell：" + "、".join(missing),
            )
        if any(
            item.status != "ready" or item.verification_sha256 is None
            for item in topology.workspace_bindings
        ):
            raise _error(
                "PROJECT_WORKSPACE_NOT_VERIFIED",
                "项目存在未验证的 Workspace",
                "逐一完成 Workspace Verify 后再激活 Team。",
            )
        repository_uris = tuple(item.repository_uri for item in topology.workspace_bindings)
        if len(set(repository_uris)) != len(repository_uris):
            raise _error(
                "PROJECT_WORKSPACE_REPOSITORY_NOT_UNIQUE",
                "多个 Workcell 指向同一个 Repository",
                "为每个 Workcell 绑定独立 Git Repository。",
            )
        if any(
            item.adapter_type == "external-git"
            and item.verification.get("direct_fast_forward_main") is not True
            for item in topology.workspace_bindings
        ):
            raise _error(
                "REMOTE_MAIN_APPLY_NOT_ALLOWED",
                "服务身份不能直接 Fast-forward main",
                "调整仓库保护规则或使用具备直推权限的服务身份。",
            )
        try:
            self.repository.activate_team(team, expected_version=expected_version)
        except RuntimeError as error:
            if str(error) == "PROJECT_TEAM_BINDING_VERSION_CONFLICT":
                raise _version_error("PROJECT_TEAM_BINDING_VERSION_CONFLICT") from error
            raise _error(
                "PROJECT_TEAM_ACTIVATION_NOT_ALLOWED",
                "项目当前状态不允许 Team Activation",
                "刷新项目状态并确认没有冲突操作。",
            ) from error
        return self.topology(project_id)

    def _swap_workspace(
        self,
        current: WorkspaceBinding,
        updated: WorkspaceBinding,
    ) -> None:
        if not self.repository.compare_and_swap_workspace(current.version, updated):
            raise _version_error("WORKSPACE_BINDING_VERSION_CONFLICT")


def _revision_id(value: str) -> tuple[str, int]:
    template_id, separator, raw_revision = value.rpartition(":")
    if not separator or not template_id:
        raise _error(
            "TEAM_TEMPLATE_REVISION_ID_INVALID",
            "TeamTemplate Revision ID 无效",
            "使用 <template_id>:<revision> 格式。",
            422,
        )
    try:
        revision = int(raw_revision)
    except ValueError as error:
        raise _error(
            "TEAM_TEMPLATE_REVISION_ID_INVALID",
            "TeamTemplate Revision ID 无效",
            "Revision 必须是正整数。",
            422,
        ) from error
    if revision < 1:
        raise _error(
            "TEAM_TEMPLATE_REVISION_ID_INVALID",
            "TeamTemplate Revision ID 无效",
            "Revision 必须是正整数。",
            422,
        )
    return template_id, revision


def _version_error(code: str) -> ProductError:
    return _error(code, "资源版本冲突", "刷新最新版本后重新提交。")


def _error(
    code: str,
    title: str,
    repair: str,
    status_code: int = 409,
) -> ProductError:
    return ProductError(
        code=code,
        title=title,
        detail=title,
        repair=repair,
        status_code=status_code,
    )
