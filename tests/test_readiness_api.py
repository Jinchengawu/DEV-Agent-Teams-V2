from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.readiness import DependencyCheck, ReadinessReport
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class MissingCodexReadiness:
    def inspect(self) -> ReadinessReport:
        return ReadinessReport(
            status="not_ready",
            checks=(
                DependencyCheck(
                    name="codex-cli",
                    status="missing",
                    repair="Install Codex CLI and run `codex login`.",
                ),
            ),
        )


def test_readiness_fails_closed_with_a_repair_action() -> None:
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
    )

    with TestClient(create_app(coordinator, readiness=MissingCodexReadiness())) as client:
        response = client.get("/v1/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": [
            {
                "name": "codex-cli",
                "status": "missing",
                "repair": "Install Codex CLI and run `codex login`.",
            }
        ],
    }
