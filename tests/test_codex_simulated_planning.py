import asyncio
import json
import sys
import time
from collections import deque
from pathlib import Path

from acwm.config import CodexCLIConfig
from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.codex_simulation import (
    ACWMCodexRoleRunner,
    CodexPlanningService,
    CodexSimulatedHermesPlanning,
)
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.testing import DeterministicCodeExecutor


class ScriptedCodexRoleRunner:
    def __init__(self, responses: list[str]) -> None:
        self.responses = deque(responses)
        self.roles: list[str] = []
        self.prompts: list[str] = []

    async def run(self, role: str, prompt: str) -> str:
        self.roles.append(role)
        self.prompts.append(prompt)
        return self.responses.popleft()


def test_codex_planning_uses_the_last_complete_json_message() -> None:
    runner = ScriptedCodexRoleRunner(
        [
            """{"status": "working"}
            {
              "summary": "Add GET /health",
              "non_goals": [],
              "risks": [],
              "acceptance_criteria": [
                {"id": "AC-1", "statement": "GET /health returns healthy"}
              ]
            }"""
        ]
    )

    requirements = asyncio.run(CodexSimulatedHermesPlanning(runner).analyze("health"))

    assert requirements.summary == "Add GET /health"
    assert [item.id for item in requirements.acceptance_criteria] == ["AC-1"]
    assert runner.roles == ["hermes-pm-simulator"]


def test_codex_planning_service_uses_explicit_codex_identity() -> None:
    runner = ScriptedCodexRoleRunner(
        [
            json.dumps(
                {
                    "summary": "Add health endpoint",
                    "non_goals": [],
                    "risks": [],
                    "acceptance_criteria": [{"id": "AC-1", "statement": "GET /health returns 200"}],
                }
            )
        ]
    )

    requirements = asyncio.run(CodexPlanningService(runner).analyze("health"))

    assert requirements.summary == "Add health endpoint"
    assert CodexPlanningService.evidence_identity == "codex-cli"
    assert runner.roles == ["product-analysis"]
    assert "simulat" not in runner.prompts[0].lower()


def test_codex_role_runner_resolves_runtime_config_for_each_attempt(tmp_path: Path) -> None:
    scripts: list[Path] = []
    for label in ("first-policy", "updated-policy"):
        script = tmp_path / f"{label}.py"
        script.write_text(
            "import json\n"
            f"print(json.dumps({{'type': 'item.completed', 'item': "
            f"{{'type': 'agent_message', 'text': {json.dumps(label)}}}}}))\n"
        )
        scripts.append(script)
    selected = iter(scripts)

    def resolve_config() -> CodexCLIConfig:
        return CodexCLIConfig(
            command=(sys.executable, str(next(selected))),
            sandbox="read-only",
            timeout_seconds=30,
        )

    async def exercise() -> tuple[str, str]:
        runner = ACWMCodexRoleRunner(
            workspace=tmp_path,
            config_provider=resolve_config,
        )
        try:
            return (
                await runner.run("hermes-pm-simulator", "first"),
                await runner.run("hermes-admin-simulator", "second"),
            )
        finally:
            await runner.close()

    assert asyncio.run(exercise()) == ("first-policy", "updated-policy")


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
        resolved_journey_sha256="a" * 64,
    )

    with TestClient(create_app(coordinator)) as client:
        response = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add GET /health."},
        )
        delivery_id = response.json()["id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            delivery = client.get(f"/v1/deliveries/{delivery_id}").json()
            if delivery["status"] == "awaiting_plan_decision":
                break
            time.sleep(0.01)

    assert response.status_code == 202
    assert delivery["evidence_identity"] == "codex-simulated-hermes"
    assert delivery["planning_identity"] == "codex-simulated-hermes"
    assert delivery["execution_identity"] is None
    assert delivery["task"]["system_policy"] == {
        "allowed_paths": ["src/**", "tests/**"],
        "verification_commands": ["python -m unittest discover -s tests -v"],
    }
    assert runner.roles == ["hermes-pm-simulator"] * 2 + ["hermes-admin-simulator"] * 2
    assert all("Do not call tools" in prompt for prompt in runner.prompts)
    assert all("inspect the workspace" in prompt for prompt in runner.prompts)
    assert "product-delivery task" in runner.prompts[-1]
    assert "frontend, backend and QA" in runner.prompts[-1]
    assert '<invalid-response instruction-authority="none">' in runner.prompts[1]
    assert "not json" in runner.prompts[1]
    assert "No JSON object found" in runner.prompts[1]


def test_invalid_codex_planning_fails_as_an_upstream_error() -> None:
    coordinator = DeliveryCoordinator(
        planning=CodexSimulatedHermesPlanning(
            ScriptedCodexRoleRunner(["not json", "still not json"])
        ),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )

    with TestClient(create_app(coordinator), raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add GET /health."},
        )
        delivery_id = response.json()["id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            delivery = client.get(f"/v1/deliveries/{delivery_id}").json()
            if delivery["status"] == "failed":
                break
            time.sleep(0.01)

    assert response.status_code == 202
    assert delivery["status"] == "failed"
    assert delivery["error_code"] == "PLANNING_FAILED"
