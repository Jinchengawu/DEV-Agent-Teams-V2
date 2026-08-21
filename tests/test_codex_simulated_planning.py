from collections import deque

from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.codex_simulation import CodexSimulatedHermesPlanning
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.testing import DeterministicCodeExecutor


class ScriptedCodexRoleRunner:
    def __init__(self, responses: list[str]) -> None:
        self.responses = deque(responses)
        self.roles: list[str] = []

    async def run(self, role: str, prompt: str) -> str:
        self.roles.append(role)
        return self.responses.popleft()


def test_codex_simulates_hermes_with_one_retry_and_cannot_override_policy() -> None:
    runner = ScriptedCodexRoleRunner(
        [
            "not json",
            """{
              "summary": "Add GET /health",
              "non_goals": ["No frontend"],
              "risks": ["Route collision"],
              "acceptance_criteria": [
                {"id": "AC-1", "statement": "GET /health returns a healthy status"}
              ]
            }""",
            """{
              "title": "Implement health route",
              "instructions": "Add and test GET /health",
              "acceptance_ids": ["AC-1"],
              "system_policy": {"allowed_paths": ["pyproject.toml"]}
            }""",
            """{
              "title": "Implement health route",
              "instructions": "Add and test GET /health",
              "acceptance_ids": ["AC-1"]
            }""",
        ]
    )
    coordinator = DeliveryCoordinator(
        planning=CodexSimulatedHermesPlanning(runner),
        executor=DeterministicCodeExecutor(),
    )

    with TestClient(create_app(coordinator)) as client:
        response = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add GET /health."},
        )

    assert response.status_code == 201
    delivery = response.json()
    assert delivery["evidence_identity"] == "codex-simulated-hermes"
    assert delivery["planning_identity"] == "codex-simulated-hermes"
    assert delivery["execution_identity"] is None
    assert delivery["task"]["system_policy"] == {
        "allowed_paths": ["src/**", "tests/**"],
        "verification_commands": ["python -m pytest"],
    }
    assert runner.roles == ["hermes-pm-simulator"] * 2 + ["hermes-admin-simulator"] * 2


def test_invalid_codex_planning_fails_as_an_upstream_error() -> None:
    coordinator = DeliveryCoordinator(
        planning=CodexSimulatedHermesPlanning(
            ScriptedCodexRoleRunner(["not json", "still not json"])
        ),
        executor=DeterministicCodeExecutor(),
    )

    with TestClient(create_app(coordinator), raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add GET /health."},
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "planning service returned invalid output"
