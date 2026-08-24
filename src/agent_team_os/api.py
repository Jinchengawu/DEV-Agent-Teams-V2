"""HTTP interface for the Delivery control plane."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
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
    RuntimeBindingConflictError,
)
from .modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRun,
    AgentRunLedger,
    ProviderManifestCatalog,
    RuntimeAdapterDescriptor,
    create_agent_deployment_router,
    create_agent_profile_router,
)
from .modules.board import BoardProjector, WorkItem
from .modules.evidence import (
    EvidenceKind,
    EvidenceLedger,
    EvidenceRecord,
    EvidenceStatus,
    EvidenceVerificationRecord,
)
from .modules.identity import (
    CSRF_HEADER,
    SESSION_COOKIE,
    IdentityService,
    User,
    create_identity_router,
    ensure_same_origin,
)
from .modules.knowledge import (
    KnowledgeActor,
    KnowledgeSearchHit,
    KnowledgeSearchIndex,
    ProviderKnowledgeManager,
    WikiService,
    create_provider_knowledge_router,
    create_wiki_router,
)
from .modules.orchestration import (
    PipelineCatalog,
    PipelineRunLedger,
    PipelineRunRecord,
    create_pipeline_router,
)
from .modules.projects import ProjectCatalog, create_project_router
from .modules.settings import SettingsManager, create_settings_router
from .readiness import ReadinessProbe, RuntimeReadiness
from .release import GateReport, LatestGateReports, combined_gate_status, latest_reports
from .shared.errors import ProblemDetail, ProductError
from .shared.events import ProductEvent
from .shared.ids import new_id
from .shared.permissions import Permission


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


class CancelRequest(BaseModel):
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
    pipeline_catalog: PipelineCatalog | None = None,
    pipeline_runs: PipelineRunLedger | None = None,
    agent_profiles: AgentProfileCatalog | None = None,
    agent_deployments: AgentDeploymentCatalog | None = None,
    provider_manifests: ProviderManifestCatalog | None = None,
    agent_runs: AgentRunLedger | None = None,
    projects: ProjectCatalog | None = None,
    knowledge_search: KnowledgeSearchIndex | None = None,
) -> FastAPI:
    if pipeline_catalog is not None and pipeline_runs is not None:
        coordinator.configure_pipeline_runtime(pipeline_catalog, pipeline_runs, agent_runs)
    app = FastAPI(
        title="Agent-Team-OS",
        version="0.4.0",
        responses={
            404: {"model": ProblemDetail, "description": "目标资源不存在"},
            409: {"model": ProblemDetail, "description": "状态或版本冲突"},
            422: {"model": ProblemDetail, "description": "输入校验失败"},
            503: {"model": ProblemDetail, "description": "运行依赖未就绪"},
        },
    )
    readiness_probe = readiness or RuntimeReadiness()
    reports = report_dir

    def require_permission(request: Request, permission: Permission) -> None:
        if identity is None:
            return
        actor = getattr(request.state, "identity_user", None)
        if not isinstance(actor, User):
            raise IdentityService.authentication_required()
        identity.require(actor, permission)

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

        app.include_router(
            create_project_router(
                projects,
                actor_id=project_actor_id,
                authorize_manage=lambda request: require_permission(
                    request, Permission.PROJECT_MANAGE
                ),
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

    if agent_runs is not None:

        @app.get(
            "/v1/deliveries/{delivery_id}/agent-runs",
            response_model=list[AgentRun],
        )
        def list_delivery_agent_runs(delivery_id: str) -> tuple[AgentRun, ...]:
            coordinator.get(delivery_id)
            return agent_runs.list(delivery_id)

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
            knowledge_actor = KnowledgeActor(user_id=actor.id, role=actor.role)
            return knowledge_actor

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
        if provider_knowledge is not None:
            app.include_router(
                create_provider_knowledge_router(
                    provider_knowledge,
                    read_actor=resolve_knowledge_actor,
                    mutation_actor=resolve_knowledge_mutation_actor,
                )
            )

    if evidence is not None:

        @app.get("/v1/evidence", response_model=list[EvidenceRecord])
        def list_evidence(
            project_id: str | None = None,
            delivery_id: str | None = None,
            kind: EvidenceKind | None = None,
            evidence_status: EvidenceStatus | None = None,
        ) -> tuple[EvidenceRecord, ...]:
            for delivery in coordinator.list():
                evidence.sync_delivery(delivery.model_dump(mode="json"))
            records = evidence.list(delivery_id, project_id)
            return tuple(
                item
                for item in records
                if (kind is None or item.kind == kind)
                and (evidence_status is None or item.status == evidence_status)
            )

        @app.get("/v1/deliveries/{delivery_id}/evidence", response_model=list[EvidenceRecord])
        def get_delivery_evidence(delivery_id: str) -> tuple[EvidenceRecord, ...]:
            try:
                delivery = coordinator.get(delivery_id)
            except DeliveryNotFoundError as error:
                raise HTTPException(status_code=404, detail="delivery not found") from error
            evidence.sync_delivery(delivery.model_dump(mode="json"))
            return evidence.list(delivery_id)

        @app.post("/v1/evidence/{evidence_id}/verify", response_model=EvidenceRecord)
        def verify_evidence(evidence_id: str, request: Request) -> EvidenceRecord:
            require_permission(request, Permission.EVIDENCE_VERIFY)
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
        ) -> tuple[EvidenceVerificationRecord, ...]:
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
        def get_board(project_id: str | None = None) -> tuple[WorkItem, ...]:
            events = tuple(
                event
                for delivery in coordinator.list()
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
                return coordinator.cancel(
                    work_item_id, expected_version=request_body.expected_version
                )
            except DeliveryNotFoundError as error:
                raise HTTPException(status_code=404, detail="work item not found") from error
            except (DeliveryVersionConflictError, DeliveryStateConflictError) as error:
                raise HTTPException(status_code=409, detail="work item command conflict") from error

        @app.post("/v1/knowledge/documents", response_model=KnowledgeDocument, status_code=201)
        def create_knowledge_document(
            request_body: KnowledgeDocumentCreate,
            request: Request,
        ) -> KnowledgeDocument:
            require_permission(request, Permission.WIKI_EDIT)
            return control_plane.create_document(request_body)

        @app.get("/v1/knowledge/documents", response_model=list[KnowledgeDocument])
        def list_knowledge_documents() -> tuple[KnowledgeDocument, ...]:
            if projects is None:
                for delivery in coordinator.list():
                    control_plane.sync_delivery_documents(delivery)
            return control_plane.list_documents()

        @app.get("/v1/knowledge/documents/{document_id}", response_model=KnowledgeDocument)
        def get_knowledge_document(document_id: str) -> KnowledgeDocument:
            try:
                return control_plane.get_document(document_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="document not found") from error

        if knowledge_search is None:

            @app.get("/v1/knowledge/search", response_model=list[KnowledgeDocument])
            def search_knowledge(q: str) -> tuple[KnowledgeDocument, ...]:
                for delivery in coordinator.list():
                    control_plane.sync_delivery_documents(delivery)
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

        @app.get("/v1/knowledge/search", response_model=list[KnowledgeSearchHit])
        def search_project_knowledge(
            project_id: str, q: str = "", include_global: bool = True
        ) -> tuple[KnowledgeSearchHit, ...]:
            return knowledge_search.search(project_id, q, include_global=include_global)

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
            if pipeline_revision is not None and project_context is not None:
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
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            return service.get(delivery_id)
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error

    @app.get(
        "/v1/deliveries/{delivery_id}/pipeline-run",
        response_model=PipelineRunRecord,
    )
    def get_delivery_pipeline_run(delivery_id: str) -> PipelineRunRecord:
        if pipeline_runs is None:
            raise HTTPException(status_code=404, detail="pipeline run ledger not configured")
        try:
            return pipeline_runs.get_for_delivery(delivery_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline run not found") from error

    @app.get("/v1/pipeline-runs/{run_id}", response_model=PipelineRunRecord)
    def get_pipeline_run(run_id: str) -> PipelineRunRecord:
        if pipeline_runs is None:
            raise HTTPException(status_code=404, detail="pipeline run ledger not configured")
        try:
            return pipeline_runs.get(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline run not found") from error

    @app.get("/v1/deliveries/{delivery_id}/events", response_model=list[ProductEvent])
    def get_delivery_events(
        delivery_id: str,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> tuple[ProductEvent, ...]:
        try:
            return service.events(delivery_id)
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error

    @app.get("/v1/deliveries", response_model=list[DeliveryRun])
    def list_deliveries(
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
        project_id: str | None = None,
    ) -> tuple[DeliveryRun, ...]:
        return tuple(
            item for item in service.list() if project_id is None or item.project_id == project_id
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
    def cancel_delivery(
        delivery_id: str,
        request_body: CancelRequest,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        require_permission(request, Permission.PLAN_DECIDE)
        try:
            return service.cancel(delivery_id, expected_version=request_body.expected_version)
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
