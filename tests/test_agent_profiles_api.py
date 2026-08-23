from pathlib import Path

from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.agents import AgentProfileCatalog, SQLiteAgentProfileRepository
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


def _client(tmp_path: Path) -> TestClient:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    return TestClient(create_app(coordinator, agent_profiles=profiles))


def _spec() -> dict[str, object]:
    return {
        "schema_version": "1",
        "id": "frontend-engineer",
        "name": "前端开发工程师",
        "description": "负责前端实现与组件测试",
        "tags": ["development", "frontend"],
        "instructions": {
            "template_ref": "prompt://frontend-engineer@1",
            "custom_text": "遵守中文界面、公共 API 和前端架构规范",
            "variables_schema": "schema://agent-prompt-variables@1",
            "examples": [],
        },
        "capabilities": [
            {"id": "frontend.implementation", "version": ">=1,<2"}
        ],
        "policies": {
            "tool_policy_ref": "policy://frontend-tools@1",
            "resource_policy_ref": "policy://frontend-resources@1",
            "approval_policy_ref": "policy://candidate-approval@1",
            "memory_policy_ref": "policy://session-isolated@1",
            "delegation_policy_ref": "policy://no-delegation@1",
        },
        "isolation_preference": "shared",
        "extensions": {},
    }


def test_agent_profile_draft_uses_cas_and_publishes_immutable_revision(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        created = client.post("/v1/agent-profiles", json={"spec": _spec()})
        profile = created.json()
        stale = client.patch(
            "/v1/agent-profiles/frontend-engineer/draft",
            json={"expected_version": 9, "spec": _spec()},
        )
        validated = client.post(
            "/v1/agent-profiles/frontend-engineer/validate",
            json={"expected_version": profile["draft"]["version"]},
        )
        published = client.post(
            "/v1/agent-profiles/frontend-engineer/publish",
            json={"expected_version": validated.json()["version"]},
        )
        revisions = client.get("/v1/agent-profiles/frontend-engineer/revisions")

    assert created.status_code == 201
    assert stale.status_code == 409
    assert stale.json()["code"] == "AGENT_PROFILE_VERSION_CONFLICT"
    assert validated.json()["validation_status"] == "valid"
    assert published.status_code == 201
    assert published.json()["revision"] == 1
    assert len(published.json()["sha256"]) == 64
    assert published.json()["sha256"] != "0" * 64
    assert revisions.json() == [published.json()]


def test_agent_spec_json_and_yaml_import_are_canonically_equivalent(tmp_path: Path) -> None:
    yaml_content = """\
schema_version: "1"
id: frontend-engineer
name: 前端开发工程师
description: 负责前端实现与组件测试
tags: [development, frontend]
instructions:
  template_ref: prompt://frontend-engineer@1
  custom_text: 遵守中文界面、公共 API 和前端架构规范
  variables_schema: schema://agent-prompt-variables@1
  examples: []
capabilities:
  - id: frontend.implementation
    version: ">=1,<2"
policies:
  tool_policy_ref: policy://frontend-tools@1
  resource_policy_ref: policy://frontend-resources@1
  approval_policy_ref: policy://candidate-approval@1
  memory_policy_ref: policy://session-isolated@1
  delegation_policy_ref: policy://no-delegation@1
isolation_preference: shared
extensions: {}
"""
    with _client(tmp_path) as client:
        imported = client.post(
            "/v1/agent-spec/import",
            json={"format": "yaml", "content": yaml_content},
        )
        validated = client.post(
            "/v1/agent-profiles/frontend-engineer/validate",
            json={"expected_version": imported.json()["draft"]["version"]},
        )
        published = client.post(
            "/v1/agent-profiles/frontend-engineer/publish",
            json={"expected_version": validated.json()["version"]},
        )
        exported_json = client.get(
            "/v1/agent-profiles/frontend-engineer/revisions/1/export?format=json"
        )
        exported_yaml = client.get(
            "/v1/agent-profiles/frontend-engineer/revisions/1/export?format=yaml"
        )

    assert imported.status_code == 201
    assert exported_json.json()["sha256"] == published.json()["sha256"]
    assert exported_yaml.json()["sha256"] == published.json()["sha256"]
    assert exported_json.json()["canonical_json"] == exported_yaml.json()[
        "canonical_json"
    ]
