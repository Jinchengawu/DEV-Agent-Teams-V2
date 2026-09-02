from __future__ import annotations

import asyncio

import pytest

from agent_team_os.delivery import DeliveryRun, InMemoryDeliveryRepository
from agent_team_os.modules.agents import RuntimeDispatchRequest, RuntimeDispatchResult
from agent_team_os.modules.delivery import PipelineExecutionModule
from agent_team_os.shared.errors import ProductError
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class RevokingGuard:
    def __init__(self) -> None:
        self.admission_count = 0

    def admit(self, _delivery: DeliveryRun, _stage_path: str) -> None:
        self.admission_count += 1
        if self.admission_count >= 2:
            raise ProductError(
                code="KNOWLEDGE_AUTHORIZATION_REVOKED",
                title="authorization revoked",
                detail="authorization epoch changed",
                repair="start a new Delivery after authorization is restored",
            )

    def validate_citations(
        self,
        _delivery: DeliveryRun,
        _stage_path: str,
        citation_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        return citation_ids


class CancellableDispatcher:
    def __init__(self) -> None:
        self.cancelled = False

    async def dispatch(self, _request: RuntimeDispatchRequest) -> RuntimeDispatchResult:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("cancelled runtime should not return")


def test_provider_runtime_is_cancelled_when_authorization_is_revoked() -> None:
    async def scenario() -> None:
        guard = RevokingGuard()
        dispatcher = CancellableDispatcher()
        execution = PipelineExecutionModule(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            verifier=None,
            applier=None,
            repository=InMemoryDeliveryRepository(),
            catalog=object(),  # type: ignore[arg-type]
            runs=object(),  # type: ignore[arg-type]
            runtime_dispatcher=dispatcher,  # type: ignore[arg-type]
            knowledge_runtime_guard=guard,
            revocation_poll_seconds=0.001,
        )
        delivery = DeliveryRun(
            id="delivery-provider-revoked",
            project_id="project-revoked",
            workspace_id="project:project-revoked",
            user_request="use frozen knowledge",
            status="executing",
            version=1,
            resolved_journey_sha256="1" * 64,
            evidence_identity="test",
            planning_identity="test",
        )
        request = RuntimeDispatchRequest(
            delivery_id=delivery.id,
            binding_site="requirements.actor",
            workflow_mode="agentscope.role-turn",
            objective="analyze requirements",
            expected_artifact_contract_id="requirement-artifact-v1",
            workspace_id=delivery.workspace_id,
            resolved_binding_hash="2" * 64,
            binding_snapshot={},
        )

        with pytest.raises(ProductError) as revoked:
            await execution._dispatch_with_knowledge_guard(
                delivery,
                "requirements",
                request,
            )

        assert revoked.value.code == "KNOWLEDGE_AUTHORIZATION_REVOKED"
        assert dispatcher.cancelled is True

    asyncio.run(scenario())
