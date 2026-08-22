"""HTTP interface for the Delivery control plane."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
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
)
from .modules.board import BoardProjector, WorkItem
from .modules.evidence import EvidenceKind, EvidenceLedger, EvidenceRecord, EvidenceStatus
from .modules.identity import (
    CSRF_HEADER,
    SESSION_COOKIE,
    IdentityService,
    User,
    create_identity_router,
    ensure_same_origin,
)
from .modules.knowledge import KnowledgeActor, WikiService, create_wiki_router
from .modules.settings import SettingsManager, create_settings_router
from .readiness import ReadinessProbe, RuntimeReadiness
from .release import GateReport, latest_reports
from .shared.errors import ProblemDetail, ProductError
from .shared.events import ProductEvent
from .shared.ids import new_id
from .shared.permissions import Permission


class DeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    user_request: str = Field(min_length=1, max_length=20_000)
    journey_revision_id: str | None = None


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
) -> FastAPI:
    app = FastAPI(
        title="Agent-Team-OS",
        version="0.2.1",
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
    async def identity_guard(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
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
                actor = identity.authenticate_mutation(
                    bearer, request.headers.get(CSRF_HEADER)
                )
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

    if evidence is not None:

        @app.get("/v1/evidence", response_model=list[EvidenceRecord])
        def list_evidence(
            delivery_id: str | None = None,
            kind: EvidenceKind | None = None,
            evidence_status: EvidenceStatus | None = None,
        ) -> tuple[EvidenceRecord, ...]:
            for delivery in coordinator.list():
                evidence.sync_delivery(delivery.model_dump(mode="json"))
            records = evidence.list(delivery_id)
            return tuple(
                item
                for item in records
                if (kind is None or item.kind == kind)
                and (evidence_status is None or item.status == evidence_status)
            )

        @app.get(
            "/v1/deliveries/{delivery_id}/evidence", response_model=list[EvidenceRecord]
        )
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

    if control_plane is not None:

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
        async def health_check_agent_instance(
            instance_id: str, request: Request
        ) -> AgentInstance:
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
        def get_board() -> tuple[WorkItem, ...]:
            events = tuple(
                event
                for delivery in coordinator.list()
                for event in coordinator.events(delivery.id)
            )
            return BoardProjector().rebuild(events).items

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
                            "approve"
                            if request_body.command == "approve-plan"
                            else "reject"
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
                            "accept"
                            if request_body.command == "accept-candidate"
                            else "reject"
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
            for delivery in coordinator.list():
                control_plane.sync_delivery_documents(delivery)
            return control_plane.list_documents()

        @app.get("/v1/knowledge/documents/{document_id}", response_model=KnowledgeDocument)
        def get_knowledge_document(document_id: str) -> KnowledgeDocument:
            try:
                return control_plane.get_document(document_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="document not found") from error

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

    @app.post("/v1/deliveries", response_model=DeliveryRun, status_code=status.HTTP_202_ACCEPTED)
    async def create_delivery(
        request_body: DeliveryRequest,
        request: Request,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        require_permission(request, Permission.DELIVERY_CREATE)
        try:
            revision = None
            if control_plane is not None:
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
            return service.enqueue(
                workspace_id=request_body.workspace_id,
                user_request=request_body.user_request,
                journey_revision_id=(
                    None if revision is None else f"{revision.journey_id}:{revision.revision}"
                ),
                journey_binding_snapshot=(None if revision is None else revision.binding_snapshot),
                resolved_journey_sha256=(None if revision is None else revision.fingerprint),
            )
        except PlanningServiceError as error:
            raise HTTPException(
                status_code=502, detail="planning service returned invalid output"
            ) from error
        except DeliveryStateConflictError as error:
            raise HTTPException(status_code=409, detail="active delivery conflict") from error

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
    ) -> tuple[DeliveryRun, ...]:
        return service.list()

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

    @app.get("/v1/release-gates/latest")
    def get_latest_release_gates() -> dict[str, object]:
        found: dict[str, GateReport | None] = (
            latest_reports(reports)
            if reports is not None
            else {
                "deterministic": None,
                "live": None,
            }
        )
        return {**found, "combined": _combined_gate_status(found)}

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


def _combined_gate_status(found: dict[str, GateReport | None]) -> dict[str, str]:
    deterministic = found["deterministic"]
    live = found["live"]
    if deterministic is None or live is None:
        return {"status": "unknown", "reason": "gate report missing"}
    if datetime.now(UTC) - deterministic.created_at > timedelta(hours=24) or datetime.now(
        UTC
    ) - live.created_at > timedelta(hours=24):
        return {"status": "unknown", "reason": "gate report expired"}
    if (
        deterministic.dev_revision != live.dev_revision
        or deterministic.acwm_revision != live.acwm_revision
    ):
        return {"status": "failed", "reason": "revision mismatch"}
    if any(
        report.status != "passed" or report.fail or report.warn or report.skipped
        for report in (deterministic, live)
    ):
        return {"status": "failed", "reason": "gate evidence is not clean"}
    return {"status": "passed", "reason": "both release gates passed"}
