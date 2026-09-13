from __future__ import annotations

from types import SimpleNamespace

from agent_team_os.delivery import DeliveryRun, InMemoryDeliveryRepository
from agent_team_os.modules.delivery import PipelineExecutionModule
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class WorkcellKernelFixture:
    def __init__(self, *stage_statuses: tuple[str, str, str]) -> None:
        self._trees = tuple(
            SimpleNamespace(
                workcell_run=SimpleNamespace(
                    stage_path=stage_path,
                    workcell_key=workcell_key,
                    status=status,
                )
            )
            for stage_path, workcell_key, status in stage_statuses
        )

    def list_delivery(self, _delivery_id: str) -> tuple[SimpleNamespace, ...]:
        return self._trees


def _execution(*stage_statuses: tuple[str, str, str]) -> PipelineExecutionModule:
    repository = InMemoryDeliveryRepository()
    repository.save(
        DeliveryRun(
            id="delivery-loop-exit",
            project_id="project-loop-exit",
            workspace_id="project:project-loop-exit",
            user_request="verify workcell loop exit",
            status="executing",
            version=1,
            resolved_journey_sha256="1" * 64,
            evidence_identity="test",
            planning_identity="test",
        )
    )
    driver = SimpleNamespace(kernel=WorkcellKernelFixture(*stage_statuses))
    return PipelineExecutionModule(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        verifier=None,
        applier=None,
        repository=repository,
        catalog=object(),  # type: ignore[arg-type]
        runs=object(),  # type: ignore[arg-type]
        workcell_stage_driver=driver,  # type: ignore[arg-type]
    )


def test_qa_preparation_does_not_satisfy_qa_delivery_loop_exit() -> None:
    execution = _execution(
        ("qa-preparation-repair/qa-preparation", "qa", "succeeded"),
        ("qa-delivery-repair/qa-delivery", "qa", "failed"),
    )

    assert execution._exit_condition_met("delivery-loop-exit", "qa-candidate-passed") is False


def test_each_workcell_loop_exit_requires_its_exact_stage_path() -> None:
    execution = _execution(
        ("design-repair/design", "design", "succeeded"),
        ("qa-preparation-repair/qa-preparation", "qa", "succeeded"),
        ("frontend-repair/frontend", "frontend", "succeeded"),
        ("backend-repair/backend", "backend", "succeeded"),
        ("qa-delivery-repair/qa-delivery", "qa", "succeeded"),
    )

    conditions = (
        "design-workcell-passed",
        "qa-preparation-artifacts-passed",
        "frontend-candidate-passed",
        "backend-candidate-passed",
        "qa-candidate-passed",
    )
    assert all(execution._exit_condition_met("delivery-loop-exit", item) for item in conditions)
