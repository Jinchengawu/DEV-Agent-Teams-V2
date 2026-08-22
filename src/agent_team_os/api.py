"""HTTP interface for the Delivery control plane."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

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
    WorkItem,
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
from .readiness import ReadinessProbe, RuntimeReadiness
from .release import GateReport, latest_reports


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
) -> FastAPI:
    app = FastAPI(title="Agent-Team-OS", version="0.2.0")
    readiness_probe = readiness or RuntimeReadiness()
    reports = report_dir

    def get_coordinator() -> DeliveryCoordinator:
        return coordinator

    @app.get("/v1/readiness")
    def get_readiness() -> JSONResponse:
        report = readiness_probe.inspect()
        return JSONResponse(
            status_code=200 if report.status == "ready" else 503,
            content=report.model_dump(mode="json"),
        )

    if control_plane is not None:

        @app.post("/v1/agent-instances", response_model=AgentInstance, status_code=201)
        def create_agent_instance(request: AgentInstanceCreate) -> AgentInstance:
            return control_plane.create_instance(request)

        @app.get("/v1/agent-instances", response_model=list[AgentInstance])
        def list_agent_instances() -> tuple[AgentInstance, ...]:
            return control_plane.list_instances()

        @app.patch("/v1/agent-instances/{instance_id}", response_model=AgentInstance)
        def patch_agent_instance(instance_id: str, request: AgentInstancePatch) -> AgentInstance:
            try:
                return control_plane.patch_instance(instance_id, request)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="agent instance not found") from error
            except RuntimeError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.post(
            "/v1/agent-instances/{instance_id}/health-check",
            response_model=AgentInstance,
        )
        async def health_check_agent_instance(instance_id: str) -> AgentInstance:
            try:
                return await control_plane.check_instance(instance_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="agent instance not found") from error

        @app.put("/v1/capability-bindings/{capability_id}", response_model=CapabilityBinding)
        def put_capability_binding(
            capability_id: str, request: BindingRequest
        ) -> CapabilityBinding:
            try:
                return control_plane.put_binding(capability_id, request)
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
        def create_journey_draft(request: JourneyDraftCreate) -> JourneyDraft:
            return control_plane.create_draft(request)

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
        def patch_journey_draft(draft_id: str, request: JourneyDraftPatch) -> JourneyDraft:
            try:
                return control_plane.patch_draft(draft_id, request)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="journey draft not found") from error
            except RuntimeError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.post("/v1/journey-drafts/{draft_id}/validate", response_model=JourneyDraft)
        def validate_journey_draft(draft_id: str) -> JourneyDraft:
            try:
                return control_plane.validate_draft(draft_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="journey draft not found") from error

        @app.post(
            "/v1/journey-drafts/{draft_id}/publish",
            response_model=JourneyRevision,
            status_code=201,
        )
        def publish_journey_draft(draft_id: str) -> JourneyRevision:
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
            return control_plane.board(coordinator.list())

        @app.post(
            "/v1/work-items/{work_item_id}/command",
            response_model=DeliveryRun,
            status_code=202,
        )
        async def command_work_item(work_item_id: str, request: WorkItemCommand) -> DeliveryRun:
            try:
                delivery = coordinator.get(work_item_id)
                if delivery.version != request.expected_version:
                    raise DeliveryVersionConflictError(work_item_id)
                if request.command in {"approve-plan", "reject-plan"}:
                    if delivery.plan_gate is None:
                        raise DeliveryStateConflictError(work_item_id)
                    return coordinator.start_plan_decision(
                        work_item_id,
                        decision=("approve" if request.command == "approve-plan" else "reject"),
                        expected_version=request.expected_version,
                        expected_subject_sha256=delivery.plan_gate.subject_sha256,
                    )
                if request.command in {"accept-candidate", "reject-candidate"}:
                    if delivery.candidate_gate is None:
                        raise DeliveryStateConflictError(work_item_id)
                    return coordinator.start_candidate_decision(
                        work_item_id,
                        decision=("accept" if request.command == "accept-candidate" else "reject"),
                        expected_version=request.expected_version,
                        expected_subject_sha256=delivery.candidate_gate.subject_sha256,
                    )
                return coordinator.cancel(work_item_id, expected_version=request.expected_version)
            except DeliveryNotFoundError as error:
                raise HTTPException(status_code=404, detail="work item not found") from error
            except (DeliveryVersionConflictError, DeliveryStateConflictError) as error:
                raise HTTPException(status_code=409, detail="work item command conflict") from error

        @app.post("/v1/knowledge/documents", response_model=KnowledgeDocument, status_code=201)
        def create_knowledge_document(
            request: KnowledgeDocumentCreate,
        ) -> KnowledgeDocument:
            return control_plane.create_document(request)

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
        request: DeliveryRequest,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            revision = None
            if control_plane is not None:
                try:
                    revision = control_plane.resolve_revision(request.journey_revision_id)
                except KeyError as error:
                    if request.journey_revision_id is None:
                        revision = None
                    else:
                        raise HTTPException(
                            status_code=404, detail="journey revision not found"
                        ) from error
            return service.enqueue(
                workspace_id=request.workspace_id,
                user_request=request.user_request,
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
        request: PlanDecisionRequest,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            return service.start_plan_decision(
                delivery_id,
                decision=request.decision,
                expected_version=request.expected_version,
                expected_subject_sha256=request.expected_subject_sha256,
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
        request: CandidateDecisionRequest,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            return service.start_candidate_decision(
                delivery_id,
                decision=request.decision,
                expected_version=request.expected_version,
                expected_subject_sha256=request.expected_subject_sha256,
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
        request: CancelRequest,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            return service.cancel(delivery_id, expected_version=request.expected_version)
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
    def reset_backend_demo() -> dict[str, str]:
        if workspace_reset is None:
            raise HTTPException(status_code=501, detail="workspace reset is not configured")
        try:
            return {"main_revision": workspace_reset()}
        except DeliveryStateConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return app


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
