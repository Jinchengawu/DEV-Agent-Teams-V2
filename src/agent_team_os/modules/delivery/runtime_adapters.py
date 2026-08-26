from __future__ import annotations

from ...delivery import (
    CodeExecutor,
    PlanningService,
    RequirementArtifact,
    TaskContract,
)
from ..agents import (
    RuntimeAdapterInvocation,
    RuntimeDispatchError,
    RuntimeDispatchResult,
    RuntimeOutputArtifact,
)


class PlanningRoleTurnRuntimeAdapter:
    """Translate a frozen Agent Profile invocation to the planning seam."""

    workflow_mode = "agentscope.role-turn"

    def __init__(self, planning: PlanningService, *, adapter_id: str = "codex.cli") -> None:
        self.adapter_id = adapter_id
        self._planning = planning

    async def execute(self, invocation: RuntimeAdapterInvocation) -> RuntimeDispatchResult:
        contract_id = invocation.expected_artifact_contract_id
        artifact: RequirementArtifact | TaskContract
        if contract_id == "requirement-artifact-v1":
            artifact = await self._planning.analyze(invocation.instruction)
        elif contract_id == "task-contract-v1":
            requirements = _input_model(invocation, "requirement-artifact-v1", RequirementArtifact)
            assert isinstance(requirements, RequirementArtifact)
            contextualized = requirements.model_copy(
                update={
                    "summary": (
                        f"{invocation.instruction}\n\n已批准需求摘要：\n{requirements.summary}"
                    )
                }
            )
            artifact = await self._planning.plan(contextualized)
        else:
            raise RuntimeDispatchError(
                "ROLE_TURN_ARTIFACT_UNSUPPORTED",
                f"planning adapter cannot produce {contract_id}",
            )
        return _result(
            self._planning.evidence_identity,
            contract_id,
            artifact.model_dump(mode="json"),
        )


class CodeDeliveryRuntimeAdapter:
    """Translate a frozen Agent Profile invocation to controlled workspace-write."""

    workflow_mode = "code-delivery"

    def __init__(self, executor: CodeExecutor, *, adapter_id: str = "codex.cli") -> None:
        self.adapter_id = adapter_id
        self._executor = executor

    async def execute(self, invocation: RuntimeAdapterInvocation) -> RuntimeDispatchResult:
        if invocation.expected_artifact_contract_id != "candidate-change-v1":
            raise RuntimeDispatchError(
                "CODE_DELIVERY_ARTIFACT_UNSUPPORTED",
                "code-delivery must produce candidate-change-v1",
            )
        task = _input_model(invocation, "task-contract-v1", TaskContract)
        assert isinstance(task, TaskContract)
        runtime_task = task.model_copy(update={"instructions": invocation.instruction})
        candidate = await self._executor.execute(
            runtime_task, invocation.workspace_id, invocation.delivery_id
        )
        return _result(
            self._executor.evidence_identity,
            invocation.expected_artifact_contract_id,
            candidate.model_dump(mode="json"),
        )


def _input_model(
    invocation: RuntimeAdapterInvocation,
    contract_id: str,
    model: type[RequirementArtifact] | type[TaskContract],
) -> RequirementArtifact | TaskContract:
    item = next(
        (artifact for artifact in invocation.inputs if artifact.contract_id == contract_id),
        None,
    )
    if item is None:
        raise RuntimeDispatchError(
            "RUNTIME_INPUT_ARTIFACT_MISSING",
            f"{invocation.binding_site} requires {contract_id}",
        )
    try:
        return model.model_validate(item.content)
    except ValueError as error:
        raise RuntimeDispatchError(
            "RUNTIME_INPUT_ARTIFACT_INVALID",
            f"{contract_id} does not match its product projection",
        ) from error


def _result(
    runtime_identity: str,
    contract_id: str,
    content: dict[str, object],
) -> RuntimeDispatchResult:
    return RuntimeDispatchResult(
        runtime_identity=runtime_identity,
        artifacts=(
            RuntimeOutputArtifact(
                contract_id=contract_id,
                media_type="application/json",
                content=content,
            ),
        ),
    )
