"""HTTP interface for the Delivery control plane."""

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


class DeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    user_request: str = Field(min_length=1, max_length=20_000)


class PlanDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)


class CandidateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject"]
    expected_version: int = Field(ge=1)


def create_app(
    coordinator: DeliveryCoordinator, *, readiness: ReadinessProbe | None = None
) -> FastAPI:
    app = FastAPI(title="Agent-Team-OS", version="0.1.0")
    readiness_probe = readiness or RuntimeReadiness()

    def get_coordinator() -> DeliveryCoordinator:
        return coordinator

    @app.get("/v1/readiness")
    def get_readiness() -> JSONResponse:
        report = readiness_probe.inspect()
        return JSONResponse(
            status_code=200 if report.status == "ready" else 503,
            content=report.model_dump(mode="json"),
        )

    @app.post("/v1/deliveries", response_model=DeliveryRun, status_code=status.HTTP_201_CREATED)
    async def create_delivery(
        request: DeliveryRequest,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            return await service.submit(
                workspace_id=request.workspace_id,
                user_request=request.user_request,
            )
        except PlanningServiceError as error:
            raise HTTPException(
                status_code=502, detail="planning service returned invalid output"
            ) from error

    @app.post("/v1/deliveries/{delivery_id}/plan-decision", response_model=DeliveryRun)
    async def decide_plan(
        delivery_id: str,
        request: PlanDecisionRequest,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            return await service.decide_plan(
                delivery_id,
                decision=request.decision,
                expected_version=request.expected_version,
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

    @app.post("/v1/deliveries/{delivery_id}/candidate-decision", response_model=DeliveryRun)
    async def decide_candidate(
        delivery_id: str,
        request: CandidateDecisionRequest,
        service: Annotated[DeliveryCoordinator, Depends(get_coordinator)],
    ) -> DeliveryRun:
        try:
            return await service.decide_candidate(
                delivery_id,
                decision=request.decision,
                expected_version=request.expected_version,
            )
        except DeliveryNotFoundError as error:
            raise HTTPException(status_code=404, detail="delivery not found") from error
        except DeliveryVersionConflictError as error:
            raise HTTPException(status_code=409, detail="delivery version conflict") from error
        except DeliveryStateConflictError as error:
            raise HTTPException(status_code=409, detail="delivery state conflict") from error

    return app
