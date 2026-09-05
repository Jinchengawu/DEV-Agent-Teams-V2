from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ...shared.errors import ProductError
from .artifact_read import WorkcellArtifactPreview, read_workcell_artifact
from .execution_application import WorkcellExecutionModule
from .execution_domain import AgentAttempt, WorkcellRunCancelRequest, WorkcellRunTree


def create_workcell_execution_router(
    execution: WorkcellExecutionModule,
    *,
    authorize_read: Callable[[Request, str], None] | None = None,
    authorize_cancel: Callable[[Request, str], None] | None = None,
    before_cancel: Callable[[WorkcellRunTree], Awaitable[None]] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/v1/deliveries/{delivery_id}/workcell-runs/{run_id}/artifacts/{sha256}",
        response_model=WorkcellArtifactPreview,
    )
    def get_workcell_artifact(
        delivery_id: str, run_id: str, sha256: str, request: Request, response: Response
    ) -> WorkcellArtifactPreview | JSONResponse:
        if authorize_read is not None:
            authorize_read(request, delivery_id)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        try:
            return read_workcell_artifact(
                execution, delivery_id=delivery_id, run_id=run_id, sha256=sha256
            )
        except ProductError as error:
            return JSONResponse(
                status_code=error.status_code,
                content=error.problem(str(uuid4())).model_dump(mode="json"),
                media_type="application/problem+json",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

    @router.get(
        "/v1/deliveries/{delivery_id}/workcell-runs",
        response_model=tuple[WorkcellRunTree, ...],
    )
    def list_delivery_workcell_runs(
        delivery_id: str, request: Request
    ) -> tuple[WorkcellRunTree, ...]:
        if authorize_read is not None:
            authorize_read(request, delivery_id)
        return execution.list_delivery(delivery_id)

    @router.get(
        "/v1/workcell-runs/{run_id}",
        response_model=WorkcellRunTree,
    )
    def get_workcell_run(run_id: str, request: Request) -> WorkcellRunTree:
        tree = execution.tree(run_id)
        if authorize_read is not None:
            authorize_read(request, tree.workcell_run.delivery_id)
        return tree

    @router.get(
        "/v1/workcell-runs/{run_id}/agent-attempts",
        response_model=tuple[AgentAttempt, ...],
    )
    def list_workcell_agent_attempts(run_id: str, request: Request) -> tuple[AgentAttempt, ...]:
        tree = execution.tree(run_id)
        if authorize_read is not None:
            authorize_read(request, tree.workcell_run.delivery_id)
        return tree.attempts

    @router.post(
        "/v1/workcell-runs/{run_id}/cancel",
        response_model=WorkcellRunTree,
    )
    async def cancel_workcell_run(
        run_id: str,
        body: WorkcellRunCancelRequest,
        request: Request,
    ) -> WorkcellRunTree:
        current = execution.tree(run_id)
        if authorize_cancel is not None:
            authorize_cancel(request, current.workcell_run.delivery_id)
        if current.workcell_run.version != body.expected_version:
            # 复用公开 Kernel 的过期版本错误；此调用不会改变状态。
            return execution.cancel(run_id, expected_version=body.expected_version)
        if current.workcell_run.status in {
            "succeeded",
            "failed",
            "cancelled",
            "timed_out",
            "interrupted",
        }:
            return execution.cancel(run_id, expected_version=body.expected_version)
        if before_cancel is not None:
            await before_cancel(current)
            current = execution.tree(run_id)
            if current.workcell_run.status == "cancelled":
                return current
        return execution.cancel(run_id, expected_version=current.workcell_run.version)

    return router
