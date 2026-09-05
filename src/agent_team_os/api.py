"""HTTP interface for the Delivery control plane."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from .control_plane import (
    AgentInstance,
    AgentInstanceCreate,
    AgentInstancePatch,
    BindingRequest,
    CapabilityBinding,
    ControlPlaneService,
    JourneyDraft,
    JourneyDraftCreate,
    JourneyDraftPatch,
    JourneyRevision,
    KnowledgeDocument,
    KnowledgeDocumentCreate,
    WorkItemCommand,
)
from .delivery import (
    DeliveryCoordinator,
    DeliveryNotFoundError,
    DeliveryRun,
    DeliveryStateConflictError,
    DeliveryVersionConflictError,
    PlanningServiceError,
    ProjectExecutionSnapshot,
    ReleaseApplier,
    RuntimeBindingConflictError,
)
from .modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRun,
    AgentRunLedger,
    AgentRuntimeDispatcher,
    ProviderManifestCatalog,
    RuntimeAdapterDescriptor,
    create_agent_deployment_router,
    create_agent_profile_router,
)
from .modules.board import BoardProjector, WorkItem
from .modules.evaluation import EvaluationService, create_evaluation_router
from .modules.evidence import (
    EvidenceKind,
    EvidenceLedger,
    EvidenceRecord,
    EvidenceStatus,
    EvidenceVerificationRecord,
)
from .modules.extensions import RuntimeExtensionCatalog, create_runtime_extension_router
from .modules.identity import (
    CSRF_HEADER,
    SESSION_COOKIE,
    IdentityService,
    User,
    create_identity_router,
    ensure_same_origin,
)
from .modules.knowledge import (
    DeliveryKnowledgeContextOverview,
    Document,
    KnowledgeActivityItem,
    KnowledgeActor,
    KnowledgeCitationUsage,
    KnowledgeContextRuntimeGuard,
    KnowledgeDerivationCreate,
    KnowledgeDerivationResult,
    KnowledgeIndexManager,
    KnowledgePreparationInputCompiler,
    KnowledgePublication,
    KnowledgePublicationLedger,
    KnowledgePublisher,
    KnowledgeSearchHit,
    KnowledgeSearchIndex,
    ProviderKnowledgeManager,
    SQLiteKnowledgeContextRepository,
    TenantKnowledgeManager,
    WikiService,
    create_knowledge_index_router,
    create_provider_knowledge_router,
    create_tenant_knowledge_router,
    create_wiki_router,
)
from .modules.orchestration import (
    PipelineCatalog,
    PipelineRunLedger,
    PipelineRunRecord,
    create_pipeline_router,
)
from .modules.projects import (
    Project,
    ProjectAccessActor,
    ProjectAccessAudit,
    ProjectCapability,
    ProjectCatalog,
    ProjectDetail,
    create_project_router,
)
from .modules.releases import (
    ExternalForwardReleaseCoordinator,
    create_external_release_router,
)
from .modules.settings import SettingsManager, create_settings_router
from .modules.workcells import (
    DeliveryExecutionSnapshotCompiler,
    ProjectWorkcellGovernance,
    TeamTemplateCatalog,
    WorkcellExecutionModule,
    WorkcellRunTree,
    WorkcellStageDriver,
    create_project_workcell_router,
    create_team_template_router,
    create_workcell_execution_router,
)
from .readiness import ReadinessProbe, RuntimeReadiness
from .release import GateReport, LatestGateReports, combined_gate_status, latest_reports
from .shared.errors import ProblemDetail, ProductError
from .shared.events import ProductEvent
from .shared.features import FeatureFlags
from .shared.ids import new_id
from .shared.permissions import Permission, Role, permits


class DeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(default=None, min_length=1)
    workspace_id: str | None = Field(default=None, min_length=1)
    user_request: str = Field(min_length=1, max_length=20_000)
    journey_revision_id: str | None = None
    pipeline_revision_id: str | None = None

    @model_validator(mode="after")
    def project_or_legacy_workspace(self) -> "DeliveryRequest":
        if self.project_id is not None and self.workspace_id is not None:
            raise ValueError("workspace_id cannot be combined with project_id")
        return self


class PlanDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)
    expected_subject_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CandidateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject"]
    expected_version: int = Field(ge=1)
    expected_subject_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DesignDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)
    expected_subject_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class KnowledgePublicationRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


def create_app(
    coordinator: DeliveryCoordinator,
    *,
    readiness: ReadinessProbe | None = None,
    report_dir: Path | None = None,
    workspace_reset: Callable[[], str] | None = None,
    control_plane: ControlPlaneService | None = None,
    evidence: EvidenceLedger | None = None,
    settings: SettingsManager | None = None,
    identity: IdentityService | None = None,
    knowledge: WikiService | None = None,
    provider_knowledge: ProviderKnowledgeManager | None = None,
    tenant_knowledge: TenantKnowledgeManager | None = None,
    knowledge_indexes: KnowledgeIndexManager | None = None,
    pipeline_catalog: PipelineCatalog | None = None,
    pipeline_runs: PipelineRunLedger | None = None,
    agent_profiles: AgentProfileCatalog | None = None,
    agent_deployments: AgentDeploymentCatalog | None = None,
    provider_manifests: ProviderManifestCatalog | None = None,
    agent_runs: AgentRunLedger | None = None,
    projects: ProjectCatalog | None = None,
    knowledge_search: KnowledgeSearchIndex | None = None,
    runtime_extensions: RuntimeExtensionCatalog | None = None,
    release_applier: ReleaseApplier | None = None,
    knowledge_publications: KnowledgePublicationLedger | None = None,
    knowledge_publisher: KnowledgePublisher | None = None,
    evaluations: EvaluationService | None = None,
    team_templates: TeamTemplateCatalog | None = None,
    project_workcells: ProjectWorkcellGovernance | None = None,
    workcell_execution: WorkcellExecutionModule | None = None,
    delivery_snapshot_compiler: DeliveryExecutionSnapshotCompiler | None = None,
    external_release: ExternalForwardReleaseCoordinator | None = None,
    workcell_stage_driver: WorkcellStageDriver | None = None,
    knowledge_preparation_compiler: KnowledgePreparationInputCompiler | None = None,
    knowledge_runtime_guard: KnowledgeContextRuntimeGuard | None = None,
    knowledge_context_repository: SQLiteKnowledgeContextRepository | None = None,
    feature_flags: FeatureFlags | None = None,
    runtime_dispatcher: AgentRuntimeDispatcher | None = None,
) -> FastAPI:
    resolved_feature_flags = feature_flags or FeatureFlags()
    resolved_feature_flags.require_valid_dependencies()
    if projects is not None and external_release is not None:
        projects.configure_release_recovery(
            external_release.repository.project_recovery_delivery_ids,
            database=external_release.repository.database,
        )
    if identity is not None and projects is not None:

        def validate_project_member_principal(user_id: str, project_role: str) -> None:
            target = identity.repository.get_user(user_id)
            if target is None:
                raise ProductError(
                    code="PROJECT_MEMBER_USER_NOT_FOUND",
                    title="项目成员用户不存在",
                    detail="不能为不存在的本地身份创建项目成员关系。",
                    repair="刷新用户目录并选择有效用户。",
                    status_code=404,
                )
            if not target.enabled:
                raise ProductError(
                    code="PROJECT_MEMBER_USER_DISABLED",
                    title="项目成员用户已禁用",
                    detail="已禁用用户不能获得新的项目成员权限。",
                    repair="先由管理员启用该用户，或选择其他有效用户。",
                    status_code=409,
                )
            if project_role == "owner" and target.role not in {
                Role.ADMINISTRATOR,
                Role.EDITOR,
            }:
                raise ProductError(
                    code="PROJECT_OWNER_NOT_ELIGIBLE",
                    title="用户不具备 Owner 资格",
                    detail="Project Owner 的全局角色必须至少具备 Editor 能力。",
                    repair="先提升用户的全局角色，或授予 viewer 项目角色。",
                    status_code=409,
                )

        projects.configure_membership_principal_validator(validate_project_member_principal)

        def guard_project_owner_continuity(
            user_id: str, next_role: Role, next_enabled: bool
        ) -> None:
            if next_enabled and next_role in {Role.ADMINISTRATOR, Role.EDITOR}:
                return

            def is_effective_owner(candidate_user_id: str) -> bool:
                candidate = identity.repository.get_user(candidate_user_id)
                return bool(
                    candidate is not None
                    and candidate.enabled
                    and candidate.role.value in {"administrator", "editor"}
                )

            projects.require_alternative_effective_owner(
                user_id,
                is_effective_owner=is_effective_owner,
            )

        identity.configure_user_authorization_change_guard(guard_project_owner_continuity)
    if pipeline_catalog is not None and pipeline_runs is not None:
        coordinator.configure_pipeline_runtime(
            pipeline_catalog,
            pipeline_runs,
            agent_runs,
            release_applier=release_applier,
            publications=knowledge_publications,
            publication_barrier=knowledge_publications,
            document_publisher=knowledge_publisher,
            workcell_stage_driver=workcell_stage_driver,
            external_release=external_release,
            knowledge_runtime_guard=knowledge_runtime_guard,
            runtime_dispatcher=runtime_dispatcher,
        )
    app = FastAPI(
        title="Agent-Team-OS",
        version="0.5.0",
        responses={
            404: {"model": ProblemDetail, "description": "目标资源不存在"},
            409: {"model": ProblemDetail, "description": "状态或版本冲突"},
            422: {"model": ProblemDetail, "description": "输入校验失败"},
            503: {"model": ProblemDetail, "description": "运行依赖未就绪"},
        },
    )
    readiness_probe = readiness or RuntimeReadiness()
    reports = report_dir

    @app.get("/v1/features", response_model=FeatureFlags)
    def get_feature_flags() -> FeatureFlags:
        return resolved_feature_flags

    def require_permission(request: Request, permission: Permission) -> None:
        if identity is None:
            return
        actor = getattr(request.state, "identity_user", None)
        if not isinstance(actor, User):
            raise IdentityService.authentication_required()
        identity.require(actor, permission)

    def current_project_actor(request: Request) -> ProjectAccessActor | None:
        actor = getattr(request.state, "identity_user", None)
        if not isinstance(actor, User):
            return None
        return ProjectAccessActor(user_id=actor.id, global_role=actor.role.value)

    def require_project_capability(
        request: Request,
        project_id: str,
        capability: ProjectCapability,
        *,
        resource: str,
        reason: str,
    ) -> ProjectAccessAudit | None:
        if projects is None:
            return None
        return projects.authorize(
            current_project_actor(request),
            project_id,
            capability,
            resource=resource,
            reason=reason,
        )

    def require_delivery_capability(
        request: Request,
        delivery_id: str,
        capability: ProjectCapability,
        *,
        resource_suffix: str,
        reason: str,
    ) -> DeliveryRun:
        try:
            delivery = coordinator.get(delivery_id)
        except DeliveryNotFoundError as error:
            raise ProductError(
                code="DELIVERY_NOT_FOUND",
                title="交付不存在",
                detail="指定的交付已不存在。",
                repair="刷新交付列表后重试。",
                status_code=404,
            ) from error
        require_project_capability(
            request,
            delivery.project_id,
            capability,
            resource=f"project:{delivery.project_id}:delivery:{delivery_id}{resource_suffix}",
            reason=reason,
        )
        return delivery

    def visible_project_ids(request: Request) -> frozenset[str] | None:
        if projects is None:
            return None
        return frozenset(
            project.id for project in projects.list_for(current_project_actor(request))
        )

    @app.middleware("http")
    async def identity_guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        public_paths = {
            "/v1/readiness",
            "/v1/auth/bootstrap-status",
            "/v1/auth/bootstrap",
            "/v1/auth/login",
        }
        if (
            identity is None
            or not request.url.path.startswith("/v1/")
            or request.url.path in public_paths
        ):
            return await call_next(request)
        try:
            bearer = request.cookies.get(SESSION_COOKIE)
            if request.method in {"GET", "HEAD", "OPTIONS"}:
                actor = identity.authenticate(bearer)
            else:
                ensure_same_origin(request)
                actor = identity.authenticate_mutation(bearer, request.headers.get(CSRF_HEADER))
            request.state.identity_user = actor
        except ProductError as error:
            return JSONResponse(
                status_code=error.status_code,
                content=error.problem(new_id()).model_dump(mode="json", exclude_none=True),
                media_type="application/problem+json",
            )
        return await call_next(request)

    def get_coordinator() -> DeliveryCoordinator:
        return coordinator

    if projects is not None:

        def project_actor_id(request: Request) -> str:
            actor = getattr(request.state, "identity_user", None)
            return actor.id if isinstance(actor, User) else "local-system"

        def project_access_actor(request: Request) -> ProjectAccessActor | None:
            return current_project_actor(request)

        def reconcile_created_project(detail: ProjectDetail) -> None:
            if knowledge is None:
                return
            knowledge.reconcile_project_space(
                detail.project.id,
                detail.project.name,
                detail.project.lifecycle_status,
                actor_id=detail.project.created_by,
            )

        def reconcile_archived_project(project: Project) -> None:
            if knowledge is None:
                return
            knowledge.reconcile_project_space(
                project.id,
                project.name,
                project.lifecycle_status,
                actor_id=project.created_by,
            )

        app.include_router(
            create_project_router(
                projects,
                actor_id=project_actor_id,
                access_actor=project_access_actor,
                authorize_manage=lambda request: require_permission(
                    request, Permission.PROJECT_MANAGE
                ),
                after_create=None if knowledge is None else reconcile_created_project,
                after_archive=None if knowledge is None else reconcile_archived_project,
            )
        )

    if agent_profiles is not None:

        def agent_profile_actor_id(request: Request) -> str:
            actor = getattr(request.state, "identity_user", None)
            return actor.id if isinstance(actor, User) else "local-system"

        app.include_router(
            create_agent_profile_router(
                agent_profiles,
                actor_id=agent_profile_actor_id,
                authorize_edit=lambda request: require_permission(
                    request, Permission.AGENT_PROFILE_EDIT
                ),
                authorize_publish=lambda request: require_permission(
                    request, Permission.AGENT_PROFILE_PUBLISH
                ),
            )
        )

    if agent_deployments is not None and provider_manifests is not None:

        def agent_deployment_actor_id(request: Request) -> str:
            actor = getattr(request.state, "identity_user", None)
            return actor.id if isinstance(actor, User) else "local-system"

        app.include_router(
            create_agent_deployment_router(
                agent_deployments,
                provider_manifests,
                actor_id=agent_deployment_actor_id,
                authorize_manage=lambda request: require_permission(
                    request, Permission.AGENT_DEPLOYMENT_MANAGE
                ),
            )
        )

    if runtime_extensions is not None:

        def runtime_extension_actor_id(request: Request) -> str:
            actor = getattr(request.state, "identity_user", None)
            return actor.id if isinstance(actor, User) else "local-system"

        app.include_router(
            create_runtime_extension_router(
                runtime_extensions,
                actor_id=runtime_extension_actor_id,
                authorize_manage=lambda request: require_permission(
                    request, Permission.RUNTIME_EXTENSION_MANAGE
                ),
            )
        )

    if team_templates is not None:

        def team_template_actor_id(request: Request) -> str:
            actor = getattr(request.state, "identity_user", None)
            return actor.id if isinstance(actor, User) else "local-system"

        app.include_router(
            create_team_template_router(
                team_templates,
                actor_id=team_template_actor_id,
                authorize_edit=lambda request: require_permission(request, Permission.JOURNEY_EDIT),
                authorize_publish=lambda request: require_permission(
                    request, Permission.JOURNEY_PUBLISH
                ),
            )
        )

    if project_workcells is not None:

        def authorize_project_workcell_read(request: Request, project_id: str) -> None:
            require_project_capability(
                request,
                project_id,
                ProjectCapability.READ,
                resource=f"project:{project_id}:workcells",
                reason="read project workcell topology",
            )

        def authorize_project_workcell_manage(request: Request, project_id: str) -> None:
            require_project_capability(
                request,
                project_id,
                ProjectCapability.EDIT,
                resource=f"project:{project_id}:workcells",
                reason="manage project workcell topology",
            )

        def authorize_workspace_binding_manage(request: Request, workspace_id: str) -> None:
            project_id = project_workcells.workspace_project_id(workspace_id)
            require_project_capability(
                request,
                project_id,
                ProjectCapability.EDIT,
                resource=f"project:{project_id}:workspace-binding:{workspace_id}",
                reason="verify project workspace binding",
            )

        app.include_router(
            create_project_workcell_router(
                project_workcells,
                authorize_read=authorize_project_workcell_read,
                authorize_manage=authorize_project_workcell_manage,
                authorize_workspace_manage=authorize_workspace_binding_manage,
            )
        )

    if workcell_execution is not None:

        def authorize_workcell_read(request: Request, delivery_id: str) -> None:
            require_delivery_capability(
                request,
                delivery_id,
                ProjectCapability.READ,
                resource_suffix=":workcell-runs",
                reason="read delivery workcell runs",
            )

        def authorize_workcell_cancel(request: Request, delivery_id: str) -> None:
            require_permission(request, Permission.WORKCELL_CANCEL)
            require_delivery_capability(
                request,
                delivery_id,
                ProjectCapability.DELIVERY_DECIDE,
                resource_suffix=":workcell-runs:cancel",
                reason="cancel delivery workcell run",
            )

        async def cancel_workcell_delivery(tree: WorkcellRunTree) -> None:
            delivery = coordinator.get(tree.workcell_run.delivery_id)
            if delivery.status not in {"completed", "rejected", "failed", "cancelled"}:
                try:
                    await coordinator.cancel(delivery.id, expected_version=delivery.version)
                except (DeliveryStateConflictError, DeliveryVersionConflictError) as error:
                    raise HTTPException(status_code=409, detail="delivery conflict") from error

        app.include_router(
            create_workcell_execution_router(
                workcell_execution,
                authorize_read=authorize_workcell_read,
                authorize_cancel=authorize_workcell_cancel,
                before_cancel=(
                    cancel_workcell_delivery if workcell_stage_driver is not None else None
                ),
            )
        )

    if external_release is not None:

        def authorize_release_project_read(request: Request, project_id: str) -> None:
            require_project_capability(
                request,
                project_id,
                ProjectCapability.READ,
                resource=f"project:{project_id}:release-health",
                reason="read project release health",
            )

        def authorize_release_delivery_read(request: Request, delivery_id: str) -> None:
            require_delivery_capability(
                request,
                delivery_id,
                ProjectCapability.READ,
                resource_suffix=":release",
                reason="read delivery release",
            )

        def authorize_release_apply(request: Request, delivery_id: str) -> None:
            require_permission(request, Permission.CANDIDATE_APPLY)
            require_delivery_capability(
                request,
                delivery_id,
                ProjectCapability.DELIVERY_DECIDE,
                resource_suffix=":release:resume-forward",
                reason="resume delivery forward release",
            )

        app.include_router(
            create_external_release_router(
                external_release,
                authorize_read_project=authorize_release_project_read,
                authorize_read_delivery=authorize_release_delivery_read,
                authorize_apply=authorize_release_apply,
                after_resume=coordinator.finalize_external_release,
            )
        )

    if agent_runs is not None:

        @app.get(
            "/v1/deliveries/{delivery_id}/agent-runs",
            response_model=list[AgentRun],
        )
        def list_delivery_agent_runs(delivery_id: str, request: Request) -> tuple[AgentRun, ...]:
            require_delivery_capability(
                request,
                delivery_id,
                ProjectCapability.READ,
                resource_suffix=":agent-runs",
                reason="list delivery agent runs",
            )
            return agent_runs.list(delivery_id)

    if knowledge_publications is not None:

        @app.get(
            "/v1/deliveries/{delivery_id}/knowledge-publications",
            response_model=tuple[KnowledgePublication, ...],
        )
        def list_delivery_knowledge_publications(
            delivery_id: str,
            request: Request,
        ) -> tuple[KnowledgePublication, ...]:
            require_delivery_capability(
                request,
                delivery_id,
                ProjectCapability.READ,
                resource_suffix=":knowledge-publications",
                reason="list delivery knowledge publications",
            )
            return knowledge_publications.list_for_delivery(delivery_id)

        @app.post(
            "/v1/knowledge/publications/{publication_id}/retry",
            response_model=KnowledgePublication,
        )
        async def retry_knowledge_publication(
            publication_id: str,
            request_body: KnowledgePublicationRetryRequest,
            request: Request,
        ) -> KnowledgePublication:
            require_permission(request, Permission.WIKI_EDIT)
            if knowledge_publisher is None:
                raise ProductError(
                    code="KNOWLEDGE_PUBLISHER_UNAVAILABLE",
                    title="知识发布器不可用",
                    detail="当前进程未配置知识发布器。",
                    repair="恢复 Knowledge Publisher 后重试。",
                    status_code=503,
                )
            try:
                current = knowledge_publications.get(publication_id)
            except KeyError as error:
                raise ProductError(
                    code="KNOWLEDGE_PUBLICATION_NOT_FOUND",
                    title="知识发布记录不存在",
                    detail="指定的知识发布记录已不存在。",
                    repair="刷新交付发布列表后重试。",
                    status_code=404,
                ) from error
            require_project_capability(
                request,
                current.project_id,
                ProjectCapability.SOURCE_MANAGE,
                resource=f"project:{current.project_id}:knowledge-publication:{publication_id}",
                reason="retry knowledge publication",
            )
            if projects is not None:
                projects.assert_writable(current.project_id)
            published = knowledge_publisher.publish(
                publication_id,
                expected_version=request_body.expected_version,
            )
            try:
                delivery = coordinator.get(published.delivery_id)
            except DeliveryNotFoundError:
                return published
            if delivery.pipeline_revision_id is not None and knowledge_publications.is_satisfied(
                delivery.id
            ):
                await coordinator.resume_publications(delivery.id)
            return knowledge_publications.get(publication_id)

    if evaluations is not None:
        app.include_router(
            create_evaluation_router(
                evaluations,
                authorize_run=lambda request: require_permission(
                    request, Permission.EVALUATION_RUN
                ),
                authorize_review=lambda request: require_permission(
                    request, Permission.EVALUATION_REVIEW
                ),
            )
        )

    @app.exception_handler(ProductError)
    async def product_error_handler(_request: Request, error: ProductError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error.problem(new_id()).model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, error: HTTPException) -> JSONResponse:
        code, title, detail, repair = _http_problem(error.status_code)
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": code,
                "title": title,
                "detail": detail,
                "repair": repair,
                "trace_id": new_id(),
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "REQUEST_VALIDATION_FAILED",
                "title": "输入内容不符合要求",
                "detail": "一个或多个字段缺失、格式错误或超出安全范围。",
                "repair": "检查表单中的必填项、版本号和哈希后重新提交。",
                "trace_id": new_id(),
            },
            media_type="application/problem+json",
        )

    @app.get("/v1/readiness")
    def get_readiness() -> JSONResponse:
        report = readiness_probe.inspect()
        return JSONResponse(
            status_code=200 if report.status == "ready" else 503,
            content=report.model_dump(mode="json"),
        )

    if settings is not None:
        app.include_router(
            create_settings_router(
                settings,
                lambda request: require_permission(request, Permission.SETTINGS_EDIT),
            )
        )

    if pipeline_catalog is not None:

        def pipeline_actor_id(request: Request) -> str:
            actor = getattr(request.state, "identity_user", None)
            return actor.id if isinstance(actor, User) else "local-system"

        app.include_router(
            create_pipeline_router(
                pipeline_catalog,
                actor_id=pipeline_actor_id,
                authorize_edit=lambda request: require_permission(request, Permission.JOURNEY_EDIT),
                authorize_publish=lambda request: require_permission(
                    request, Permission.JOURNEY_PUBLISH
                ),
            )
        )

    if identity is not None:
        app.include_router(create_identity_router(identity))

    if knowledge is not None:

        def resolve_knowledge_actor(request: Request) -> KnowledgeActor:
            actor = getattr(request.state, "identity_user", None)
            if not isinstance(actor, User):
                raise IdentityService.authentication_required()
            return KnowledgeActor(user_id=actor.id, role=actor.role)

        def resolve_knowledge_mutation_actor(request: Request) -> KnowledgeActor:
            require_permission(request, Permission.WIKI_EDIT)
            return resolve_knowledge_actor(request)

        app.include_router(
            create_wiki_router(
                knowledge,
                read_actor=resolve_knowledge_actor,
                mutation_actor=resolve_knowledge_mutation_actor,
            )
        )
        if knowledge_search is not None:

            @app.post(
                "/v1/knowledge/derivations",
                response_model=KnowledgeDerivationResult,
                status_code=201,
            )
            def derive_knowledge_source(
                request_body: KnowledgeDerivationCreate,
                request: Request,
                response: Response,
            ) -> KnowledgeDerivationResult:
                require_permission(request, Permission.WIKI_EDIT)
                require_project_capability(
                    request,
                    request_body.project_id,
                    ProjectCapability.SOURCE_USE,
                    resource=(
                        f"project:{request_body.project_id}:knowledge-derivation:"
                        f"{request_body.source_kind}:{request_body.source_id}"
                    ),
                    reason="derive project knowledge source",
                )
                actor = resolve_knowledge_actor(request)
                source = knowledge_search.resolve_source(
                    request_body.project_id,
                    request_body.source_kind,
                    request_body.source_id,
                )
                if source is None:
                    raise ProductError(
                        code="KNOWLEDGE_SOURCE_NOT_AVAILABLE",
                        title="知识来源不可用于提炼",
                        detail="来源不存在、不属于当前项目或尚未通过完整性验证。",
                        repair="刷新知识动态并选择已验证的来源。",
                        status_code=404,
                    )
                result = knowledge.derive_source(actor, request_body, source)
                if not result.created:
                    response.status_code = 200
                return result

        if provider_knowledge is not None:
            app.include_router(
                create_provider_knowledge_router(
                    provider_knowledge,
                    read_actor=resolve_knowledge_actor,
                    mutation_actor=resolve_knowledge_mutation_actor,
                )
            )

    if tenant_knowledge is not None:

        def resolve_tenant_knowledge_actor(request: Request) -> KnowledgeActor:
            actor = getattr(request.state, "identity_user", None)
            if not isinstance(actor, User):
                raise IdentityService.authentication_required()
            return KnowledgeActor(user_id=actor.id, role=actor.role)

        def authorize_tenant_project_source(
            request: Request, project_id: str, binding_id: str
        ) -> KnowledgeActor:
            if projects is None:
                raise ProductError(
                    code="PROJECT_GOVERNANCE_UNAVAILABLE",
                    title="项目治理模块未配置",
                    detail="Tenant Knowledge 同步必须经过项目授权。",
                    repair="启用 Project Catalog 后重试。",
                    status_code=503,
                )
            require_project_capability(
                request,
                project_id,
                ProjectCapability.SOURCE_USE,
                resource=f"project:{project_id}:knowledge-source:{binding_id}",
                reason="use approved project knowledge source",
            )
            projects.require_knowledge_source_approval(project_id, binding_id)
            return resolve_tenant_knowledge_actor(request)

        app.include_router(
            create_tenant_knowledge_router(
                tenant_knowledge,
                actor=resolve_tenant_knowledge_actor,
                authorize_project_source=authorize_tenant_project_source,
            )
        )

    if knowledge_indexes is not None:

        def resolve_knowledge_index_actor(request: Request) -> KnowledgeActor:
            actor = getattr(request.state, "identity_user", None)
            if not isinstance(actor, User):
                raise IdentityService.authentication_required()
            return KnowledgeActor(user_id=actor.id, role=actor.role)

        def authorize_project_retrieval(
            request: Request, project_id: str, binding_id: str
        ) -> tuple[KnowledgeActor, tuple[str, ...]]:
            if projects is None or tenant_knowledge is None:
                raise ProductError(
                    code="PROJECT_GOVERNANCE_UNAVAILABLE",
                    title="项目知识治理未配置",
                    detail="Hybrid Retrieval 必须经过 Project Approval 与 Tenant Binding。",
                    repair="启用 Project Catalog 与 Tenant Knowledge Manager 后重试。",
                    status_code=503,
                )
            require_project_capability(
                request,
                project_id,
                ProjectCapability.SOURCE_USE,
                resource=f"project:{project_id}:knowledge-retrieval:{binding_id}",
                reason="retrieve approved project knowledge",
            )
            projects.require_knowledge_source_approval(
                project_id,
                binding_id,
                rag_required=True,
            )
            return (
                resolve_knowledge_index_actor(request),
                tenant_knowledge.available_source_ids(binding_id),
            )

        app.include_router(
            create_knowledge_index_router(
                knowledge_indexes,
                actor=resolve_knowledge_index_actor,
                authorize_project_retrieval=authorize_project_retrieval,
            )
        )

    if evidence is not None:

        @app.get("/v1/evidence", response_model=list[EvidenceRecord])
        def list_evidence(
            request: Request,
            project_id: str | None = None,
            delivery_id: str | None = None,
            kind: EvidenceKind | None = None,
            evidence_status: EvidenceStatus | None = None,
        ) -> tuple[EvidenceRecord, ...]:
            if delivery_id is not None:
                delivery = require_delivery_capability(
                    request,
                    delivery_id,
                    ProjectCapability.READ,
                    resource_suffix=":evidence",
                    reason="list delivery evidence",
                )
                if project_id is not None and delivery.project_id != project_id:
                    raise ProductError(
                        code="EVIDENCE_PROJECT_DELIVERY_MISMATCH",
                        title="证据查询范围冲突",
                        detail="Delivery 不属于指定 Project。",
                        repair="使用与 Delivery 一致的 Project ID。",
                        status_code=422,
                    )
            elif project_id is not None:
                require_project_capability(
                    request,
                    project_id,
                    ProjectCapability.READ,
                    resource=f"project:{project_id}:evidence",
                    reason="list project evidence",
                )
            visible = visible_project_ids(request)
            for delivery in coordinator.list():
                if visible is not None and delivery.project_id not in visible:
                    continue
                evidence.sync_delivery(delivery.model_dump(mode="json"))
            records = evidence.list(delivery_id, project_id)
            return tuple(
                item
                for item in records
                if (visible is None or item.project_id in visible)
                if (kind is None or item.kind == kind)
                and (evidence_status is None or item.status == evidence_status)
            )

        @app.get("/v1/deliveries/{delivery_id}/evidence", response_model=list[EvidenceRecord])
        def get_delivery_evidence(delivery_id: str, request: Request) -> tuple[EvidenceRecord, ...]:
            delivery = require_delivery_capability(
                request,
                delivery_id,
                ProjectCapability.READ,
                resource_suffix=":evidence",
                reason="read delivery evidence",
            )
            evidence.sync_delivery(delivery.model_dump(mode="json"))
            return evidence.list(delivery_id)

        @app.post("/v1/evidence/{evidence_id}/verify", response_model=EvidenceRecord)
        def verify_evidence(evidence_id: str, request: Request) -> EvidenceRecord:
            require_permission(request, Permission.EVIDENCE_VERIFY)
            current = evidence.get(evidence_id)
            if current is None:
                raise HTTPException(status_code=404, detail="evidence not found")
            require_project_capability(
                request,
                current.project_id,
                ProjectCapability.DELIVERY_DECIDE,
                resource=f"project:{current.project_id}:evidence:{evidence_id}",
                reason="verify project evidence",
            )
            try:
                return evidence.verify(evidence_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="evidence not found") from error

        @app.get(
            "/v1/evidence/{evidence_id}/verifications",
            response_model=list[EvidenceVerificationRecord],
        )
        def list_evidence_verifications(
            evidence_id: str,
            request: Request,
        ) -> tuple[EvidenceVerificationRecord, ...]:
            current = evidence.get(evidence_id)
            if current is None:
                raise HTTPException(status_code=404, detail="evidence not found")
            require_project_capability(
                request,
                current.project_id,
                ProjectCapability.READ,
                resource=f"project:{current.project_id}:evidence:{evidence_id}:verifications",
                reason="read evidence verification history",
            )
            try:
                return evidence.verification_history(evidence_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="evidence not found") from error

    if control_plane is not None:

        @app.get("/v1/runtime-adapters", response_model=list[RuntimeAdapterDescriptor])
        def list_runtime_adapters() -> tuple[RuntimeAdapterDescriptor, ...]:
            return control_plane.list_runtime_adapters()

        @app.post("/v1/agent-instances", response_model=AgentInstance, status_code=201)
        def create_agent_instance(
            request_body: AgentInstanceCreate, request: Request
        ) -> AgentInstance:
            require_permission(request, Permission.AGENT_MANAGE)
            return control_plane.create_instance(request_body)

        @app.get("/v1/agent-instances", response_model=list[AgentInstance])
        def list_agent_instances() -> tuple[AgentInstance, ...]:
            return control_plane.list_instances()

        @app.patch("/v1/agent-instances/{instance_id}", response_model=AgentInstance)
        def patch_agent_instance(
            instance_id: str, request_body: AgentInstancePatch, request: Request
        ) -> AgentInstance:
            require_permission(request, Permission.AGENT_MANAGE)
            try:
                return control_plane.patch_instance(instance_id, request_body)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="agent instance not found") from error
            except RuntimeError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.post(
            "/v1/agent-instances/{instance_id}/health-check",
            response_model=AgentInstance,
        )
        async def health_check_agent_instance(instance_id: str, request: Request) -> AgentInstance:
            require_permission(request, Permission.AGENT_MANAGE)
            try:
                return await control_plane.check_instance(instance_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="agent instance not found") from error

        @app.put("/v1/capability-bindings/{capability_id}", response_model=CapabilityBinding)
        def put_capability_binding(
            capability_id: str, request_body: BindingRequest, request: Request
        ) -> CapabilityBinding:
            require_permission(request, Permission.AGENT_MANAGE)
            try:
                return control_plane.put_binding(capability_id, request_body)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="agent instance not found") from error
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except RuntimeError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.get("/v1/capability-bindings/{capability_id}", response_model=CapabilityBinding)
        def get_capability_binding(capability_id: str) -> CapabilityBinding:
            try:
                return control_plane.get_binding(capability_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="binding not found") from error

        @app.get("/v1/capability-bindings", response_model=list[CapabilityBinding])
        def list_capability_bindings() -> tuple[CapabilityBinding, ...]:
            return control_plane.list_bindings()

        @app.post("/v1/journey-drafts", response_model=JourneyDraft, status_code=201)
        def create_journey_draft(
            request_body: JourneyDraftCreate, request: Request
        ) -> JourneyDraft:
            require_permission(request, Permission.JOURNEY_EDIT)
            return control_plane.create_draft(request_body)

        @app.get("/v1/journey-drafts", response_model=list[JourneyDraft])
        def list_journey_drafts() -> tuple[JourneyDraft, ...]:
            return control_plane.list_drafts()

        @app.get("/v1/journey-drafts/{draft_id}", response_model=JourneyDraft)
        def get_journey_draft(draft_id: str) -> JourneyDraft:
            try:
                return control_plane.get_draft(draft_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="journey draft not found") from error

        @app.patch("/v1/journey-drafts/{draft_id}", response_model=JourneyDraft)
        def patch_journey_draft(
            draft_id: str, request_body: JourneyDraftPatch, request: Request
        ) -> JourneyDraft:
            require_permission(request, Permission.JOURNEY_EDIT)
            try:
                return control_plane.patch_draft(draft_id, request_body)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="journey draft not found") from error
            except RuntimeError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.post("/v1/journey-drafts/{draft_id}/validate", response_model=JourneyDraft)
        def validate_journey_draft(draft_id: str, request: Request) -> JourneyDraft:
            require_permission(request, Permission.JOURNEY_EDIT)
            try:
                return control_plane.validate_draft(draft_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="journey draft not found") from error

        @app.post(
            "/v1/journey-drafts/{draft_id}/publish",
            response_model=JourneyRevision,
            status_code=201,
        )
        def publish_journey_draft(draft_id: str, request: Request) -> JourneyRevision:
            require_permission(request, Permission.JOURNEY_PUBLISH)
            try:
                return control_plane.publish_draft(draft_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="journey draft not found") from error
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.get(
            "/v1/journeys/{journey_id}/revisions/{revision}",
            response_model=JourneyRevision,
        )
        def get_journey_revision(journey_id: str, revision: int) -> JourneyRevision:
            try:
                return control_plane.get_revision(journey_id, revision)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="journey revision not found") from error

        @app.get("/v1/journeys", response_model=list[JourneyRevision])
        def list_journeys() -> tuple[JourneyRevision, ...]:
            return control_plane.list_journeys()

        @app.get("/v1/board", response_model=list[WorkItem])
        def get_board(request: Request, project_id: str | None = None) -> tuple[WorkItem, ...]:
            if project_id is not None:
                require_project_capability(
                    request,
                    project_id,
                    ProjectCapability.READ,
                    resource=f"project:{project_id}:board",
                    reason="read project board",
                )
            visible = visible_project_ids(request)
            events = tuple(
                event
                for delivery in coordinator.list()
                if visible is None or delivery.project_id in visible
                for event in coordinator.events(delivery.id)
            )
            return BoardProjector().rebuild(events, project_id).items

        @app.post(
            "/v1/work-items/{work_item_id}/command",
            response_model=DeliveryRun,
            status_code=202,
        )
        async def command_work_item(
            work_item_id: str, request_body: WorkItemCommand, request: Request
        ) -> DeliveryRun:
            permission = (
                Permission.CANDIDATE_APPLY
                if request_body.command in {"accept-candidate", "reject-candidate"}
                else Permission.PLAN_DECIDE
            )
            require_permission(request, permission)
            try:
                delivery = coordinator.get(work_item_id)
                require_project_capability(
                    request,
                    delivery.project_id,
                    ProjectCapability.DELIVERY_DECIDE,
                    resource=f"project:{delivery.project_id}:work-item:{work_item_id}",
                    reason="command project work item",
                )
                if delivery.version != request_body.expected_version:
                    raise DeliveryVersionConflictError(work_item_id)
                if request_body.command in {"approve-plan", "reject-plan"}:
                    if delivery.plan_gate is None:
                        raise DeliveryStateConflictError(work_item_id)
                    return coordinator.start_plan_decision(
                        work_item_id,
                        decision=(
                            "approve" if request_body.command == "approve-plan" else "reject"
                        ),
                        expected_version=request_body.expected_version,
                        expected_subject_sha256=delivery.plan_gate.subject_sha256,
                    )
                if request_body.command in {"approve-design", "reject-design"}:
                    if delivery.design_gate is None:
                        raise DeliveryStateConflictError(work_item_id)
                    return coordinator.start_design_decision(
                        work_item_id,
                        decision=(
                            "approve" if request_body.command == "approve-design" else "reject"
                        ),
                        expected_version=request_body.expected_version,
                        expected_subject_sha256=delivery.design_gate.subject_sha256,
                    )
                if request_body.command in {"accept-candidate", "reject-candidate"}:
                    if delivery.candidate_gate is None:
                        raise DeliveryStateConflictError(work_item_id)
                    return coordinator.start_candidate_decision(
                        work_item_id,
                        decision=(
                            "accept" if request_body.command == "accept-candidate" else "reject"
                        ),
                        expected_version=request_body.expected_version,
                        expected_subject_sha256=delivery.candidate_gate.subject_sha256,
                    )
                return await coordinator.cancel(
                    work_item_id, expected_version=request_body.expected_version
                )
            except DeliveryNotFoundError as error:
                raise HTTPException(status_code=404, detail="work item not found") from error
            except (DeliveryVersionConflictError, DeliveryStateConflictError) as error:
                raise HTTPException(status_code=409, detail="work item command conflict") from error

        @app.post("/v1/knowledge/documents", status_code=410)
        def create_knowledge_document(
            _request_body: KnowledgeDocumentCreate,
            request: Request,
        ) -> JSONResponse:
            require_permission(request, Permission.WIKI_EDIT)
            error = ProductError(
                code="KNOWLEDGE_LEGACY_WRITE_REMOVED",
                title="旧知识写入接口已移除",
                detail="旧 ControlPlane Knowledge 不再承载项目协作文档。",
                repair="改用 /v1/wiki/documents 创建项目文档。",
                status_code=410,
            )
            return JSONResponse(
                status_code=410,
                content=error.problem(new_id()).model_dump(mode="json", exclude_none=True),
                media_type="application/problem+json",
                headers={
                    "Deprecation": "true",
                    "X-Successor-Path": "/v1/wiki/documents",
                    "Link": '</v1/wiki/documents>; rel="successor-version"',
                },
            )

        @app.get("/v1/knowledge/documents", response_model=list[Document])
        def list_knowledge_documents(request: Request, response: Response) -> tuple[Document, ...]:
            require_permission(request, Permission.USER_MANAGE)
            response.headers["Deprecation"] = "true"
            response.headers["X-Successor-Path"] = "/v1/wiki/documents"
            response.headers["Link"] = '</v1/wiki/documents>; rel="successor-version"'
            if knowledge is None:
                return ()
            actor = resolve_knowledge_actor(request)
            return knowledge.list_documents(
                actor,
                space_id="project-docs:legacy-default",
                source_kind="legacy-migrated",
            )

        @app.get("/v1/knowledge/documents/{document_id}", response_model=Document)
        def get_knowledge_document(
            document_id: str, request: Request, response: Response
        ) -> Document:
            require_permission(request, Permission.USER_MANAGE)
            response.headers["Deprecation"] = "true"
            response.headers["X-Successor-Path"] = "/v1/wiki/documents"
            if knowledge is None:
                raise ProductError(
                    code="KNOWLEDGE_LEGACY_DOCUMENT_NOT_FOUND",
                    title="旧知识映射不存在",
                    detail="指定的旧知识映射不存在。",
                    repair="改用 /v1/wiki/documents 查询项目文档。",
                    status_code=404,
                )
            actor = resolve_knowledge_actor(request)
            try:
                document = knowledge.get_document(actor, document_id)
            except ProductError:
                raise
            if (
                document.space_id != "project-docs:legacy-default"
                or document.source_kind != "legacy-migrated"
            ):
                raise ProductError(
                    code="KNOWLEDGE_LEGACY_DOCUMENT_NOT_FOUND",
                    title="旧知识映射不存在",
                    detail="该文档不属于 legacy-default 迁移映射。",
                    repair="改用 /v1/wiki/documents 查询项目文档。",
                    status_code=404,
                )
            return document

        if knowledge_search is None:

            @app.get("/v1/knowledge/search", response_model=list[KnowledgeDocument])
            def search_knowledge(q: str) -> tuple[KnowledgeDocument, ...]:
                return control_plane.search_documents(q)

        @app.get("/v1/events/stream", include_in_schema=True)
        async def stream_events(after: int = 0) -> StreamingResponse:
            async def events() -> AsyncIterator[str]:
                cursor = after
                while True:
                    batch = control_plane.list_events(cursor)
                    if not batch:
                        yield ": keepalive\n\n"
                    for event in batch:
                        cursor = event.sequence
                        payload = json.dumps(event.model_dump(mode="json"))
                        yield (
                            f"id: {event.sequence}\nevent: {event.event_type}\ndata: {payload}\n\n"
                        )
                    await asyncio.sleep(1)

            return StreamingResponse(events(), media_type="text/event-stream")

    if knowledge_search is not None:

        @app.get("/v1/knowledge/activity", response_model=list[KnowledgeActivityItem])
        def list_project_knowledge_activity(
            request: Request,
            project_id: str,
            include_global: bool = True,
            source_kind: str | None = None,
            delivery_id: str | None = None,
            before: datetime | None = None,
            limit: int = Query(default=50, ge=1, le=100),
        ) -> tuple[KnowledgeActivityItem, ...]:
            require_project_capability(
                request,
                project_id,
                ProjectCapability.READ,
                resource=f"project:{project_id}:knowledge-activity",
                reason="read project knowledge activity",
            )
            return knowledge_search.activity(
                project_id,
                include_global=include_global,
                source_kind=source_kind,
                delivery_id=delivery_id,
                before=before,
                limit=limit,
            )

    if knowledge_search is not None and knowledge is not None:

        @app.get("/v1/knowledge/search", response_model=list[KnowledgeSearchHit])
        def search_project_knowledge(
            request: Request,
            project_id: str,
            q: str = "",
            include_global: bool = True,
        ) -> tuple[KnowledgeSearchHit, ...]:
            require_project_capability(
                request,
                project_id,
                ProjectCapability.SOURCE_USE,
                resource=f"project:{project_id}:knowledge-search",
                reason="search project knowledge",
            )
            actor = resolve_knowledge_actor(request)
            provider_authorizer = (
                None
                if provider_knowledge is None
                else lambda snapshot_id: provider_knowledge.can_read_snapshot(actor, snapshot_id)
            )
            return knowledge_search.search(
                actor,
                project_id,
                q,
                wiki=knowledge,
                include_global=include_global,
                can_read_evidence=permits(actor.role, Permission.EVIDENCE_READ),
                provider_snapshot_authorizer=provider_authorizer,
            )

    @app.get(
        "/v1/deliveries/{delivery_id}/knowledge-context",
        response_model=DeliveryKnowledgeContextOverview,
    )
    def get_delivery_knowledge_context(
        delivery_id: str,
        request: Request,
    ) -> DeliveryKnowledgeContextOverview:
        delivery = require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.READ,
            resource_suffix=":knowledge-context",
            reason="read delivery knowledge context metadata",
        )
        snapshot = delivery.delivery_execution_snapshot
        contexts = (
            ()
            if snapshot is None
            else tuple(
                snapshot.knowledge_contexts[key] for key in sorted(snapshot.knowledge_contexts)
            )
        )
        unavailable = (
            ()
            if snapshot is None
            else tuple(
                snapshot.knowledge_context_unavailable[key]
                for key in sorted(snapshot.knowledge_context_unavailable)
            )
        )
        stage_paths_by_citation: dict[str, set[str]] = {}
        for context in contexts:
            for citation_id in context.citation_ids:
                stage_paths_by_citation.setdefault(citation_id, set()).add(context.stage_path)
        workcell_runs_by_citation: dict[str, set[str]] = {}
        if workcell_execution is not None:
            for tree in workcell_execution.list_delivery(delivery_id):
                if tree.result is None:
                    continue
                for citation_id in tree.result.knowledge_citation_ids:
                    workcell_runs_by_citation.setdefault(citation_id, set()).add(
                        tree.workcell_run.id
                    )
        citation_ids = sorted(set(stage_paths_by_citation) | set(workcell_runs_by_citation))
        return DeliveryKnowledgeContextOverview(
            delivery_id=delivery.id,
            delivery_status=delivery.status,
            preparation_run=(
                None
                if knowledge_context_repository is None
                else knowledge_context_repository.get_for_delivery(delivery.id)
            ),
            contexts=contexts,
            unavailable=unavailable,
            citations=tuple(
                KnowledgeCitationUsage(
                    citation_id=citation_id,
                    stage_paths=tuple(sorted(stage_paths_by_citation.get(citation_id, set()))),
                    workcell_run_ids=tuple(
                        sorted(workcell_runs_by_citation.get(citation_id, set()))
                    ),
                )
                for citation_id in citation_ids
            ),
        )

    @app.get(
        "/v1/deliveries/{delivery_id}/knowledge-context/artifact",
        response_model=dict[str, object],
    )
    def inspect_delivery_knowledge_context(
        delivery_id: str,
        stage_path: str,
        request: Request,
    ) -> dict[str, object]:
        delivery = require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.SOURCE_USE,
            resource_suffix=":knowledge-context:artifact",
            reason="inspect delivery knowledge context body",
        )
        if knowledge_runtime_guard is None:
            raise ProductError(
                code="KNOWLEDGE_CONTEXT_RUNTIME_UNAVAILABLE",
                title="Knowledge Context Runtime 未启用",
                detail="当前实例没有启用 Delivery Knowledge Context Runtime。",
                repair="启用 delivery_knowledge_context_v1 后重试。",
                status_code=503,
            )
        context = knowledge_runtime_guard.admit(delivery, stage_path)
        if context is None:
            raise ProductError(
                code="KNOWLEDGE_CONTEXT_NOT_FOUND",
                title="Stage Knowledge Context 不存在",
                detail="该 Stage 没有可检查的冻结 Knowledge Context。",
                repair="检查 Pipeline Binding 或 Optional Unavailable Receipt。",
                status_code=404,
            )
        return context.content

    @app.post("/v1/deliveries", response_model=DeliveryRun, status_code=status.HTTP_202_ACCEPTED)
    async def create_delivery(
        request_body: DeliveryRequest,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        require_permission(request, Permission.DELIVERY_CREATE)
        delivery_id = new_id()
        project_context = None
        try:
            if (
                projects is not None
                and request_body.project_id is None
                and request_body.workspace_id != "backend-demo"
            ):
                raise ProductError(
                    code="DELIVERY_PROJECT_REQUIRED",
                    title="新交付必须选择项目",
                    detail="任意 Workspace ID 已被项目治理替代。",
                    repair="选择项目后重新创建交付。",
                    status_code=422,
                )
            effective_project_id = request_body.project_id or "legacy-default"
            project_access_audit = require_project_capability(
                request,
                effective_project_id,
                ProjectCapability.DELIVERY_CREATE,
                resource=f"project:{effective_project_id}:deliveries",
                reason="create delivery",
            )
            effective_pipeline_revision_id = request_body.pipeline_revision_id
            if projects is not None:
                project_context = projects.prepare_delivery(
                    effective_project_id, delivery_id, effective_pipeline_revision_id
                )
                effective_pipeline_revision_id = project_context.pipeline_revision_id
            if (
                pipeline_catalog is not None
                and effective_pipeline_revision_id is None
                and request_body.journey_revision_id is None
            ):
                raise ProductError(
                    code="DELIVERY_PIPELINE_REVISION_REQUIRED",
                    title="必须选择已发布的流水线版本",
                    detail="当前服务已启用 Pipeline GraphRun，新交付不得隐式回退到旧线性 Journey。",
                    repair="刷新流水线列表，选择一个已激活的 Pipeline Revision 后重试。",
                )
            if (
                effective_pipeline_revision_id is not None
                and request_body.journey_revision_id is not None
            ):
                raise ProductError(
                    code="DELIVERY_RUNTIME_REFERENCE_CONFLICT",
                    title="运行版本引用冲突",
                    detail="一次交付不能同时选择 Pipeline Revision 与旧 Journey Revision。",
                    repair="保留 Pipeline Revision，移除旧 Journey Revision 后重试。",
                )
            pipeline_revision = (
                None
                if pipeline_catalog is None or effective_pipeline_revision_id is None
                else pipeline_catalog.resolve_active_revision(effective_pipeline_revision_id)
            )
            if (
                pipeline_revision is not None
                and project_context is not None
                and not pipeline_revision.workcell_stage_map
            ):
                assigned: set[str] = set()
                for value in pipeline_revision.resolved_provider_bindings.values():
                    deployment = value.get("deployment")
                    if isinstance(deployment, dict) and deployment.get("id"):
                        assigned.add(str(deployment["id"]))
                forbidden = sorted(assigned - set(project_context.deployment_ids))
                if forbidden:
                    raise ProductError(
                        code="PROJECT_DEPLOYMENT_NOT_ALLOWED",
                        title="项目未授权流水线使用的智能体部署",
                        detail="未授权部署：" + "、".join(forbidden),
                        repair="在项目智能体授权中启用这些 Deployment 后重试。",
                    )
            delivery_execution_snapshot = None
            knowledge_preparation_input = None
            if pipeline_revision is not None and pipeline_revision.workcell_stage_map:
                if delivery_snapshot_compiler is None:
                    raise ProductError(
                        code="DELIVERY_EXECUTION_SNAPSHOT_COMPILER_UNAVAILABLE",
                        title="Workcell Delivery Snapshot Compiler 未配置",
                        detail="无法冻结 Team、Pipeline、Provider、Workspace 与 Method Revision。",
                        repair="配置 DeliveryExecutionSnapshotCompiler 后重新创建交付。",
                        status_code=503,
                    )
                compiled_snapshot = delivery_snapshot_compiler.compile(
                    effective_project_id,
                    f"{pipeline_revision.pipeline_id}:{pipeline_revision.revision}",
                )
                if pipeline_revision.knowledge_context_bindings:
                    if knowledge_preparation_compiler is None:
                        raise ProductError(
                            code="KNOWLEDGE_CONTEXT_PREPARER_UNAVAILABLE",
                            title="Delivery Knowledge Context 未配置",
                            detail="当前 Pipeline 需要冻结知识上下文，但编译器未接线。",
                            repair="启用 delivery_knowledge_context_v1 后重试。",
                            status_code=503,
                        )
                    principal = getattr(request.state, "identity_user", None)
                    if not isinstance(principal, User):
                        raise IdentityService.authentication_required()
                    knowledge_preparation_input = knowledge_preparation_compiler.compile(
                        delivery_id=delivery_id,
                        project_id=effective_project_id,
                        principal_id=principal.id,
                        delivery_goal=request_body.user_request,
                        base_snapshot=compiled_snapshot,
                        bypass_receipt_id=(
                            None if project_access_audit is None else project_access_audit.id
                        ),
                    )
                else:
                    delivery_execution_snapshot = compiled_snapshot
            revision = None
            if control_plane is not None and pipeline_revision is None:
                try:
                    revision = control_plane.resolve_revision(request_body.journey_revision_id)
                    control_plane.ensure_revision_available(revision)
                except KeyError as error:
                    if request_body.journey_revision_id is None:
                        revision = None
                    else:
                        raise HTTPException(
                            status_code=404, detail="journey revision not found"
                        ) from error
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
            pipeline_run_id = (
                new_id() if pipeline_revision is not None and pipeline_runs is not None else None
            )
            if pipeline_revision is not None and pipeline_runs is None:
                raise ProductError(
                    code="PIPELINE_RUN_LEDGER_UNAVAILABLE",
                    title="流水线运行账本未就绪",
                    detail="当前服务无法持久化 ACWM GraphRun。",
                    repair="修复运行账本配置后重新启动交付。",
                )
            delivery = service.enqueue(
                delivery_id=delivery_id,
                project_id=effective_project_id,
                workspace_id=(project_context.workspace_id if project_context else "backend-demo"),
                project_execution_snapshot=(
                    None
                    if project_context is None
                    else ProjectExecutionSnapshot.model_validate(project_context.model_dump())
                ),
                delivery_execution_snapshot=delivery_execution_snapshot,
                user_request=request_body.user_request,
                pipeline_revision_id=(
                    None
                    if pipeline_revision is None
                    else (f"{pipeline_revision.pipeline_id}:{pipeline_revision.revision}")
                ),
                pipeline_run_id=pipeline_run_id,
                journey_revision_id=(
                    None if revision is None else f"{revision.journey_id}:{revision.revision}"
                ),
                journey_binding_snapshot=(
                    pipeline_revision.binding_snapshot
                    if pipeline_revision is not None
                    else None
                    if revision is None
                    else revision.binding_snapshot
                ),
                resolved_provider_bindings=(
                    pipeline_revision.resolved_provider_bindings
                    if pipeline_revision is not None
                    else None
                ),
                resolved_journey_sha256=(
                    pipeline_revision.fingerprint
                    if pipeline_revision is not None
                    else None
                    if revision is None
                    else revision.fingerprint
                ),
                resolved_pipeline_sha256=(
                    None if pipeline_revision is None else pipeline_revision.fingerprint
                ),
                knowledge_preparation_input=knowledge_preparation_input,
            )
            return delivery
        except PlanningServiceError as error:
            raise HTTPException(
                status_code=502, detail="planning service returned invalid output"
            ) from error
        except RuntimeBindingConflictError as error:
            raise ProductError(
                code="DELIVERY_RUNTIME_BINDING_MISMATCH",
                title="运行实例与已发布绑定不匹配",
                detail=(
                    f"能力 {error.capability_id} 绑定身份为 "
                    f"{error.actual or '未提供'}，当前运行时仅配置了 {error.expected}。"
                ),
                repair="重新绑定已接入的运行实例，并发布新的 Journey Revision。",
            ) from error
        except DeliveryStateConflictError as error:
            raise HTTPException(status_code=409, detail="active delivery conflict") from error
        except Exception:
            if projects is not None and project_context is not None:
                projects.release_delivery(project_context.project_id, delivery_id)
            raise

    @app.post(
        "/v1/deliveries/{delivery_id}/plan-decision",
        response_model=DeliveryRun,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def decide_plan(
        delivery_id: str,
        request_body: PlanDecisionRequest,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        require_permission(request, Permission.PLAN_DECIDE)
        require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.DELIVERY_DECIDE,
            resource_suffix=":plan-decision",
            reason="decide delivery plan",
        )
        try:
            return service.start_plan_decision(
                delivery_id,
                decision=request_body.decision,
                expected_version=request_body.expected_version,
                expected_subject_sha256=request_body.expected_subject_sha256,
            )
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error
        except DeliveryVersionConflictError as error:
            raise HTTPException(status_code=409, detail="delivery version conflict") from error
        except DeliveryStateConflictError as error:
            raise HTTPException(status_code=409, detail="delivery state conflict") from error

    @app.get("/v1/deliveries/{delivery_id}", response_model=DeliveryRun)
    def get_delivery(
        delivery_id: str,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.READ,
            resource_suffix="",
            reason="read delivery",
        )
        try:
            return service.get(delivery_id)
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error

    @app.post(
        "/v1/deliveries/{delivery_id}/design-decision",
        response_model=DeliveryRun,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def decide_design(
        delivery_id: str,
        request_body: DesignDecisionRequest,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        require_permission(request, Permission.PLAN_DECIDE)
        require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.DELIVERY_DECIDE,
            resource_suffix=":design-decision",
            reason="decide delivery design",
        )
        try:
            return service.start_design_decision(
                delivery_id,
                decision=request_body.decision,
                expected_version=request_body.expected_version,
                expected_subject_sha256=request_body.expected_subject_sha256,
            )
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error
        except DeliveryVersionConflictError as error:
            raise HTTPException(status_code=409, detail="delivery version conflict") from error
        except DeliveryStateConflictError as error:
            raise HTTPException(status_code=409, detail="delivery state conflict") from error

    @app.get(
        "/v1/deliveries/{delivery_id}/pipeline-run",
        response_model=PipelineRunRecord,
    )
    def get_delivery_pipeline_run(delivery_id: str, request: Request) -> PipelineRunRecord:
        require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.READ,
            resource_suffix=":pipeline-run",
            reason="read delivery pipeline run",
        )
        if pipeline_runs is None:
            raise HTTPException(status_code=404, detail="pipeline run ledger not configured")
        try:
            return pipeline_runs.get_for_delivery(delivery_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline run not found") from error

    @app.get("/v1/pipeline-runs/{run_id}", response_model=PipelineRunRecord)
    def get_pipeline_run(run_id: str, request: Request) -> PipelineRunRecord:
        if pipeline_runs is None:
            raise HTTPException(status_code=404, detail="pipeline run ledger not configured")
        try:
            pipeline_run = pipeline_runs.get(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline run not found") from error
        require_delivery_capability(
            request,
            pipeline_run.delivery_id,
            ProjectCapability.READ,
            resource_suffix=f":pipeline-run:{run_id}",
            reason="read pipeline run",
        )
        return pipeline_run

    @app.get("/v1/deliveries/{delivery_id}/events", response_model=list[ProductEvent])
    def get_delivery_events(
        delivery_id: str,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> tuple[ProductEvent, ...]:
        require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.READ,
            resource_suffix=":events",
            reason="read delivery events",
        )
        try:
            return service.events(delivery_id)
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error

    @app.get("/v1/deliveries", response_model=list[DeliveryRun])
    def list_deliveries(
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
        project_id: str | None = None,
    ) -> tuple[DeliveryRun, ...]:
        if project_id is not None:
            require_project_capability(
                request,
                project_id,
                ProjectCapability.READ,
                resource=f"project:{project_id}:deliveries",
                reason="list project deliveries",
            )
            allowed_project_ids: frozenset[str] | None = frozenset({project_id})
        else:
            allowed_project_ids = visible_project_ids(request)
        return tuple(
            item
            for item in service.list()
            if allowed_project_ids is None or item.project_id in allowed_project_ids
        )

    @app.post(
        "/v1/deliveries/{delivery_id}/candidate-decision",
        response_model=DeliveryRun,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def decide_candidate(
        delivery_id: str,
        request_body: CandidateDecisionRequest,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        require_permission(request, Permission.CANDIDATE_APPLY)
        require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.DELIVERY_DECIDE,
            resource_suffix=":candidate-decision",
            reason="decide delivery candidate",
        )
        try:
            return service.start_candidate_decision(
                delivery_id,
                decision=request_body.decision,
                expected_version=request_body.expected_version,
                expected_subject_sha256=request_body.expected_subject_sha256,
            )
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error
        except DeliveryVersionConflictError as error:
            raise HTTPException(status_code=409, detail="delivery version conflict") from error
        except DeliveryStateConflictError as error:
            raise HTTPException(status_code=409, detail="delivery state conflict") from error

    @app.post("/v1/deliveries/{delivery_id}/cancel", response_model=DeliveryRun)
    async def cancel_delivery(
        delivery_id: str,
        request_body: CancelRequest,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        require_permission(request, Permission.PLAN_DECIDE)
        require_delivery_capability(
            request,
            delivery_id,
            ProjectCapability.DELIVERY_DECIDE,
            resource_suffix=":cancel",
            reason="cancel delivery",
        )
        try:
            return await service.cancel(delivery_id, expected_version=request_body.expected_version)
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error
        except (DeliveryVersionConflictError, DeliveryStateConflictError) as error:
            raise HTTPException(status_code=409, detail="delivery conflict") from error

    @app.get("/v1/release-gates/latest", response_model=LatestGateReports)
    def get_latest_release_gates() -> LatestGateReports:
        found: dict[str, GateReport | None] = (
            latest_reports(reports)
            if reports is not None
            else {
                "deterministic": None,
                "live": None,
            }
        )
        return LatestGateReports(**found, combined=combined_gate_status(found))

    @app.get("/v1/release-gates/history", response_model=list[GateReport])
    def get_release_gate_history() -> list[GateReport]:
        if reports is None or not reports.exists():
            return []
        history: list[GateReport] = []
        for path in sorted(reports.glob("*.json"), reverse=True):
            try:
                history.append(GateReport.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return history

    @app.post("/v1/workspaces/backend-demo/reset")
    def reset_backend_demo(request: Request) -> dict[str, str]:
        require_permission(request, Permission.WORKSPACE_RESET)
        if workspace_reset is None:
            raise HTTPException(status_code=501, detail="workspace reset is not configured")
        try:
            return {"main_revision": workspace_reset()}
        except DeliveryStateConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return app


def _http_problem(status_code: int) -> tuple[str, str, str, str]:
    problems = {
        404: (
            "RESOURCE_NOT_FOUND",
            "未找到请求的数据",
            "目标记录不存在，或已经不属于当前工作区。",
            "刷新列表并重新选择目标记录。",
        ),
        409: (
            "STATE_OR_VERSION_CONFLICT",
            "当前状态不允许此操作",
            "记录版本、审批主题或交付状态已经发生变化。",
            "刷新当前详情，确认最新状态后重新提交。",
        ),
        501: (
            "CAPABILITY_NOT_CONFIGURED",
            "当前能力尚未配置",
            "服务未配置完成此操作所需的运行能力。",
            "在设置或实例管理中完成依赖配置。",
        ),
        502: (
            "AGENT_OUTPUT_INVALID",
            "智能体输出未通过合同校验",
            "规划或执行结果不是系统允许的结构化产物。",
            "检查智能体身份和日志后创建新的交付。",
        ),
        503: (
            "RUNTIME_NOT_READY",
            "运行依赖尚未就绪",
            "一个或多个真实运行依赖未通过就绪检查。",
            "根据就绪报告完成登录、凭据或本地依赖配置。",
        ),
    }
    return problems.get(
        status_code,
        (
            f"HTTP_{status_code}",
            "操作未能完成",
            "服务拒绝了当前请求。",
            "刷新页面并检查运行状态后重试。",
        ),
    )
