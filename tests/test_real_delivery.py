import asyncio
from pathlib import Path

from agent_team_os.delivery import (
    DeliveryCoordinator,
    InMemoryDeliveryRepository,
)
from agent_team_os.git_delivery import (
    GitCandidateApplier,
    GitCandidateVerifier,
    GitCodeExecutor,
)
from agent_team_os.git_sandbox import GitSandbox
from agent_team_os.testing import DeterministicPlanningService


class ScriptedWorkspaceAgent:
    evidence_identity = "scripted-external-boundary"

    async def run(self, *, instruction: str, workspace: Path) -> str:
        assert "AC-1" in instruction
        assert "source change and a corresponding test change" in instruction
        (workspace / "src" / "health.py").write_text(
            'def health() -> dict[str, str]:\n    return {"status": "ok"}\n',
            encoding="utf-8",
        )
        (workspace / "tests" / "test_health.py").write_text(
            "import unittest\n\n"
            "from src.health import health\n\n\n"
            "class HealthTest(unittest.TestCase):\n"
            "    def test_status(self) -> None:\n"
            '        self.assertEqual(health(), {"status": "ok"})\n',
            encoding="utf-8",
        )
        return "implemented"


def test_delivery_uses_real_git_candidate_and_cas_apply(tmp_path: Path) -> None:
    asyncio.run(_exercise_real_delivery(tmp_path))


async def _exercise_real_delivery(tmp_path: Path) -> None:
    sandbox = GitSandbox(tmp_path / "runtime")
    sandbox.ensure_initialized()
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=GitCodeExecutor(sandbox, ScriptedWorkspaceAgent()),
        verifier=GitCandidateVerifier(sandbox),
        applier=GitCandidateApplier(sandbox),
        repository=InMemoryDeliveryRepository(),
    )

    planned = await coordinator.submit(
        workspace_id="backend-demo", user_request="Add a health function."
    )
    candidate = await coordinator.decide_plan(
        planned.id,
        decision="approve",
        expected_version=planned.version,
        expected_subject_sha256=planned.plan_gate.subject_sha256,
    )

    assert candidate.status == "awaiting_candidate_decision"
    assert candidate.candidate is not None
    assert candidate.verification is not None
    assert candidate.verification.status == "passed"
    assert candidate.candidate.candidate_revision != candidate.candidate.base_revision
    assert sandbox.main_revision() == candidate.candidate.base_revision

    completed = await coordinator.decide_candidate(
        candidate.id,
        decision="accept",
        expected_version=candidate.version,
        expected_subject_sha256=candidate.candidate_gate.subject_sha256,
    )

    assert completed.status == "completed"
    assert completed.apply_receipt is not None
    assert sandbox.main_revision() == candidate.candidate.candidate_revision
