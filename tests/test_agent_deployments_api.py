from pathlib import Path

from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.control_plane import AgentInstanceCreate, ControlPlaneService, HealthResult
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentDeploymentCreate,
    AgentProfileCatalog,
    AgentProfileCreate,
    AgentProfileSpec,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
)
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class ReadyProbe:
    async def check(self, runtime_type: str, connection: dict[str, str]) -> HealthResult:
        return HealthResult(status="ready", identity=f"{runtime_type}:test")


def _spec() -> dict[str, object]:
    return {
        "schema_version": "1",
        "id": "frontend-engineer",
        "name": "前端开发工程师",
        "description": "负责前端实现",
        "tags": ["frontend"],
        "instructions": {"custom_text": "遵守公共接口", "examples": []},
        "capabilities": [{"id": "frontend.implementation", "version": ">=1,<2"}],
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


def test_published_profile_can_be_qualified_and_enabled_as_deployment(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    providers = ProviderManifestCatalog()
    instances = ControlPlaneService(database, probe=ReadyProbe())
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database), profiles, instances, providers
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    with TestClient(
        create_app(
            coordinator,
            control_plane=instances,
            agent_profiles=profiles,
            agent_deployments=deployments,
            provider_manifests=providers,
        )
    ) as client:
        created_profile = client.post("/v1/agent-profiles", json={"spec": _spec()}).json()
        validated = client.post(
            "/v1/agent-profiles/frontend-engineer/validate",
            json={"expected_version": created_profile["draft"]["version"]},
        ).json()
        client.post(
            "/v1/agent-profiles/frontend-engineer/publish",
            json={"expected_version": validated["version"]},
        )
        instance = client.post(
            "/v1/agent-instances",
            json={
                "name": "Codex 前端执行器",
                "runtime_type": "codex-cli",
                "connection": {"command": "codex"},
            },
        ).json()
        client.post(f"/v1/agent-instances/{instance['id']}/health-check")
        created = client.post(
            "/v1/agent-deployments",
            json={
                "id": "frontend-codex",
                "name": "前端 Codex 部署",
                "profile_id": "frontend-engineer",
                "profile_revision": 1,
                "instance_id": instance["id"],
                "provider_id": "codex-cli-provider",
            },
        )
        qualified = client.post(
            "/v1/agent-deployments/frontend-codex/qualify",
            json={"expected_version": created.json()["version"]},
        )
        enabled = client.post(
            "/v1/agent-deployments/frontend-codex/enable",
            json={"expected_version": qualified.json()["version"]},
        )
        providers_response = client.get("/v1/provider-manifests")

    assert created.status_code == 201
    assert qualified.json()["qualification_status"] == "qualified"
    assert qualified.json()["qualification_errors"] == []
    assert enabled.json()["enabled"] is True
    assert providers_response.json()[0]["fingerprint"] != "0" * 64


def test_unqualified_deployment_cannot_be_enabled(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    providers = ProviderManifestCatalog()
    instances = ControlPlaneService(database, probe=ReadyProbe())
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database), profiles, instances, providers
    )
    profile = profiles.create(
        AgentProfileCreate(spec=AgentProfileSpec.model_validate(_spec())),
        actor_id="test",
    )
    validated = profiles.validate_draft(
        profile.profile.id, expected_version=profile.draft.version, actor_id="test"
    )
    profiles.publish(
        profile.profile.id, expected_version=validated.version, actor_id="test"
    )
    instance = instances.create_instance(
        AgentInstanceCreate(
            name="Codex",
            runtime_type="codex-cli",
            connection={"command": "codex"},
        )
    )
    created = deployments.create(
        AgentDeploymentCreate(
            id="frontend-codex",
            name="前端 Codex",
            profile_id="frontend-engineer",
            profile_revision=1,
            instance_id=instance.id,
            provider_id="codex-cli-provider",
        ),
        actor_id="test",
    )

    try:
        deployments.set_enabled(created.id, created.version, True)
    except Exception as error:
        assert getattr(error, "code", None) == "AGENT_DEPLOYMENT_NOT_QUALIFIED"
    else:
        raise AssertionError("unqualified deployment was enabled")
