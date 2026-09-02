from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from .execution_application import WorkcellExecutionModule
from .execution_domain import AgentAttempt, WorkcellRunCancelRequest, WorkcellRunTree


def create_workcell_execution_router(
    execution: WorkcellExecutionModule,
    *,
    authorize_read: Callable[[Request, str], None] | None = None,
    authorize_cancel: Callable[[Request, str], None] | None = None,
    after_cancel: Callable[[WorkcellRunTree], None] | None = None,
) -> APIRouter:
    router = APIRouter()

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
        tree = execution.cancel(run_id, expected_version=body.expected_version)
        if after_cancel is not None:
            after_cancel(tree)
        return tree

    return router
