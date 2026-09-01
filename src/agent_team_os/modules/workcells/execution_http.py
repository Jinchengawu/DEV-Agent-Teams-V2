from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from .execution_application import WorkcellExecutionModule
from .execution_domain import AgentAttempt, WorkcellRunCancelRequest, WorkcellRunTree


def create_workcell_execution_router(
    execution: WorkcellExecutionModule,
    *,
    authorize_cancel: Callable[[Request], None] | None = None,
    after_cancel: Callable[[WorkcellRunTree], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/v1/deliveries/{delivery_id}/workcell-runs",
        response_model=tuple[WorkcellRunTree, ...],
    )
    def list_delivery_workcell_runs(delivery_id: str) -> tuple[WorkcellRunTree, ...]:
        return execution.list_delivery(delivery_id)

    @router.get(
        "/v1/workcell-runs/{run_id}",
        response_model=WorkcellRunTree,
    )
    def get_workcell_run(run_id: str) -> WorkcellRunTree:
        return execution.tree(run_id)

    @router.get(
        "/v1/workcell-runs/{run_id}/agent-attempts",
        response_model=tuple[AgentAttempt, ...],
    )
    def list_workcell_agent_attempts(run_id: str) -> tuple[AgentAttempt, ...]:
        return execution.tree(run_id).attempts

    @router.post(
        "/v1/workcell-runs/{run_id}/cancel",
        response_model=WorkcellRunTree,
    )
    async def cancel_workcell_run(
        run_id: str,
        body: WorkcellRunCancelRequest,
        request: Request,
    ) -> WorkcellRunTree:
        if authorize_cancel is not None:
            authorize_cancel(request)
        tree = execution.cancel(run_id, expected_version=body.expected_version)
        if after_cancel is not None:
            after_cancel(tree)
        return tree

    return router
