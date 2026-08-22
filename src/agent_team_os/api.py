"""HTTP interface for the Delivery control plane."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

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
) -> FastAPI:
    app = FastAPI(title="Agent-Team-OS", version="0.1.0")
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

    @app.post("/v1/deliveries", response_model=DeliveryRun, status_code=status.HTTP_202_ACCEPTED)
    async def create_delivery(
        request: DeliveryRequest,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            return service.enqueue(
                workspace_id=request.workspace_id,
                user_request=request.user_request,
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
