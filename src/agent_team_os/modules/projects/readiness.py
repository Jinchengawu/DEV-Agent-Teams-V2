from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ...readiness import ReadinessProbe
from ...shared.errors import ProductError
from .application import ProjectCatalog

SetupCheckStatus = Literal["ready", "blocked", "failed", "not_run"]
BlockingScope = Literal["start_delivery", "complete_onboarding", "release_only"]


class SetupReadinessCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: SetupCheckStatus
    summary: str
    repair: str
    navigation_target: str
    blocking_scope: BlockingScope

    @field_validator("navigation_target")
    @classmethod
    def internal_navigation_only(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "://" in value:
            raise ValueError("navigation_target must be an internal product route")
        allowed = ("/setup", "/projects", "/agents", "/teams", "/orchestration", "/settings")
        path = value.split("#", 1)[0].split("?", 1)[0]
        if not any(path == prefix or path.startswith(prefix + "/") for prefix in allowed):
            raise ValueError("navigation_target is outside the controlled product routes")
        return value


class SetupReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ready", "blocked", "failed"]
    project_id: str | None = None
    global_checks: tuple[SetupReadinessCheck, ...]
    project_checks: tuple[SetupReadinessCheck, ...] = ()
    optional_checks: tuple[SetupReadinessCheck, ...] = ()

    @property
    def blocking_checks(self) -> tuple[SetupReadinessCheck, ...]:
        return tuple(
            check
            for check in (*self.global_checks, *self.project_checks, *self.optional_checks)
            if check.blocking_scope == "start_delivery" and check.status != "ready"
        )


ProjectExecutionInspector = Callable[
    [str, str], tuple[tuple[SetupReadinessCheck, ...], tuple[SetupReadinessCheck, ...]]
]


class SetupReadinessService:
    """Read-only projection over existing runtime and project authorities."""

    def __init__(
        self,
        runtime: ReadinessProbe,
        projects: ProjectCatalog,
        *,
        inspect_execution: ProjectExecutionInspector | None = None,
    ) -> None:
        self.runtime = runtime
        self.projects = projects
        self.inspect_execution = inspect_execution

    def inspect(self, project_id: str | None = None) -> SetupReadinessReport:
        global_checks = self._runtime_checks()
        project_checks: list[SetupReadinessCheck] = []
        optional_checks: tuple[SetupReadinessCheck, ...] = ()
        if project_id is not None:
            project_checks, optional_checks = self._project_checks(project_id)
        all_checks = (*global_checks, *project_checks, *optional_checks)
        status: Literal["ready", "blocked", "failed"] = "ready"
        if any(check.status == "failed" for check in all_checks):
            status = "failed"
        elif any(
            check.status != "ready" and check.blocking_scope == "start_delivery"
            for check in all_checks
        ):
            status = "blocked"
        return SetupReadinessReport(
            status=status,
            project_id=project_id,
            global_checks=global_checks,
            project_checks=tuple(project_checks),
            optional_checks=optional_checks,
        )

    def require_delivery_ready(
        self,
        project_id: str,
        purpose: Literal["product", "onboarding_evaluation"] = "product",
    ) -> SetupReadinessReport:
        report = self.inspect(project_id)
        blocked = list(report.blocking_checks)
        onboarding = self.projects.get(project_id).onboarding
        if purpose == "product" and onboarding.status != "ready":
            blocked.append(
                _check(
                    "project.onboarding",
                    "blocked",
                    "项目尚未完成评测引导",
                    "运行 onboarding_evaluation Delivery，回读 Evidence 后由管理员完成引导。",
                    f"/projects/{project_id}/deliveries",
                )
            )
        if purpose == "onboarding_evaluation" and not (
            onboarding.mode == "guided_evaluation" and onboarding.status == "setup"
        ):
            blocked.append(
                _check(
                    "project.onboarding-evaluation",
                    "blocked",
                    "项目不能启动新的评测交付",
                    "只有 setup 状态的 guided_evaluation 项目允许该用途。",
                    f"/projects/{project_id}/overview",
                )
            )
        if blocked:
            raise ProductError(
                code="DELIVERY_READINESS_BLOCKED",
                title="交付准备度未通过",
                detail="、".join(check.summary for check in blocked),
                repair="按准备中心的顺序修复阻塞项，刷新后重试。",
                status_code=409,
                context={"checks": [check.model_dump(mode="json") for check in blocked]},
            )
        return report

    def _runtime_checks(self) -> tuple[SetupReadinessCheck, ...]:
        try:
            report = self.runtime.inspect()
        except Exception:
            return (
                _check(
                    "runtime.inspect",
                    "failed",
                    "Runtime 准备度检查失败",
                    "检查运行日志并修复 Readiness Probe。",
                    "/setup#runtime.inspect",
                ),
            )
        return tuple(
            _check(
                f"runtime.{item.name}",
                (
                    "ready"
                    if item.status == "ready"
                    else "failed"
                    if item.status == "failed"
                    else "blocked"
                ),
                f"{item.name} {'已就绪' if item.status == 'ready' else '未就绪'}",
                item.repair or "无需修复。",
                f"/setup#runtime.{item.name}",
            )
            for item in report.checks
        )

    def _project_checks(
        self, project_id: str
    ) -> tuple[list[SetupReadinessCheck], tuple[SetupReadinessCheck, ...]]:
        try:
            detail = self.projects.get(project_id)
        except ProductError as error:
            return [
                _check(
                    "project.catalog",
                    "failed",
                    error.title,
                    error.repair,
                    "/projects",
                )
            ], ()
        target = f"/projects/{project_id}/overview"
        checks = [
            _check(
                "project.lifecycle",
                "ready" if detail.project.lifecycle_status == "active" else "blocked",
                "项目可运行" if detail.project.lifecycle_status == "active" else "项目不可运行",
                "完成项目初始化，且不要使用已归档项目。",
                target,
            ),
            _check(
                "project.workspace",
                "ready" if detail.workspace.status == "ready" else "blocked",
                "Workspace 已就绪" if detail.workspace.status == "ready" else "Workspace 未就绪",
                "重试 Workspace 初始化。",
                target,
            ),
            _check(
                "project.active-delivery",
                "ready" if detail.active_delivery_id is None else "blocked",
                "没有活动交付" if detail.active_delivery_id is None else "项目已有活动交付",
                "进入当前活动交付，待其进入终态后重试。",
                f"/projects/{project_id}/deliveries",
            ),
        ]
        unavailable = [
            repository.role
            for repository in detail.repositories
            if repository.status != "ready"
        ]
        checks.append(
            _check(
                "project.repositories",
                "ready" if detail.repositories and not unavailable else "blocked",
                "项目仓库已就绪" if detail.repositories and not unavailable else "项目仓库未就绪",
                "初始化或重试失败的项目仓库。",
                target,
            )
        )
        selected = next(
            (
                binding
                for binding in detail.pipeline_bindings
                if binding.enabled and binding.is_default
            ),
            None,
        )
        checks.append(
            _check(
                "project.pipeline",
                "ready" if selected is not None else "blocked",
                "默认 Pipeline 已固定" if selected is not None else "默认 Pipeline 缺失",
                "为项目启用一个已发布 Pipeline Revision 并设为默认。",
                "/orchestration",
            )
        )
        checks.append(
            _check(
                "project.deployments",
                "ready" if any(item.enabled for item in detail.deployment_access) else "blocked",
                (
                    "Deployment 授权已配置"
                    if any(item.enabled for item in detail.deployment_access)
                    else "Deployment 授权缺失"
                ),
                "授权至少一个已资格化 Deployment。",
                "/agents",
            )
        )
        optional: tuple[SetupReadinessCheck, ...] = ()
        if selected is not None and self.inspect_execution is not None:
            try:
                execution, optional = self.inspect_execution(project_id, selected.revision_id)
                execution_by_id = {check.id: check for check in execution}
                checks = [execution_by_id.pop(check.id, check) for check in checks]
                checks.extend(execution_by_id.values())
            except ProductError as error:
                checks.append(
                    _check(
                        "project.execution-contract",
                        "blocked",
                        error.title,
                        error.repair,
                        target,
                    )
                )
            except Exception:
                checks.append(
                    _check(
                        "project.execution-contract",
                        "failed",
                        "执行契约检查失败",
                        "检查 Pipeline、Team、Deployment 和 Verification 配置。",
                        target,
                    )
                )
        return checks, optional


def _check(
    check_id: str,
    status: SetupCheckStatus,
    summary: str,
    repair: str,
    navigation_target: str,
    blocking_scope: BlockingScope = "start_delivery",
) -> SetupReadinessCheck:
    return SetupReadinessCheck(
        id=check_id,
        status=status,
        summary=summary,
        repair=repair,
        navigation_target=navigation_target,
        blocking_scope=blocking_scope,
    )
