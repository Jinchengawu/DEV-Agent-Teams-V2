from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Request, status

from .application import EvaluationService
from .domain import (
    EvaluationCaseResult,
    EvaluationReport,
    EvaluationRun,
    EvaluationRunRequest,
    HumanReview,
    HumanReviewImport,
)


def create_evaluation_router(
    service: EvaluationService,
    *,
    authorize_run: Callable[[Request], None] | None = None,
    authorize_review: Callable[[Request], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/evaluation-runs",
        response_model=EvaluationRun,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_run(
        body: EvaluationRunRequest,
        request: Request,
        background: BackgroundTasks,
    ) -> EvaluationRun:
        if authorize_run is not None:
            authorize_run(request)
        run = service.create(body)
        background.add_task(service.execute, run.id)
        return run

    @router.get("/v1/evaluation-runs/{run_id}", response_model=EvaluationRun)
    def get_run(run_id: str) -> EvaluationRun:
        return service.get(run_id)

    @router.get(
        "/v1/evaluation-runs/{run_id}/cases",
        response_model=list[EvaluationCaseResult],
    )
    def list_cases(run_id: str) -> tuple[EvaluationCaseResult, ...]:
        return service.cases(run_id)

    @router.get("/v1/evaluation-runs/{run_id}/report", response_model=EvaluationReport)
    def get_report(run_id: str) -> EvaluationReport:
        return service.report(run_id)

    @router.post("/v1/evaluation-runs/{run_id}/cancel", response_model=EvaluationRun)
    def cancel_run(run_id: str, request: Request) -> EvaluationRun:
        if authorize_run is not None:
            authorize_run(request)
        return service.cancel(run_id)

    @router.post(
        "/v1/evaluation-runs/{run_id}/human-reviews/import",
        response_model=list[HumanReview],
    )
    def import_human_reviews(
        run_id: str, body: HumanReviewImport, request: Request
    ) -> tuple[HumanReview, ...]:
        if authorize_review is not None:
            authorize_review(request)
        return service.import_reviews(run_id, body.reviews)

    return router
