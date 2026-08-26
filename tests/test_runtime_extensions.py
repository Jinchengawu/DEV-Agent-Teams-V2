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
from agent_team_os.modules.extensions import (
    RuntimeExtensionCatalog,
    RuntimeExtensionInstall,
    SQLiteRuntimeExtensionRepository,
)
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class _ReadyProbe:
    async def check(self, runtime_type: str, connection: dict[str, str]) -> HealthResult:
        return HealthResult(status="ready", identity="codex-cli")


def _catalog(tmp_path: Path) -> RuntimeExtensionCatalog:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    return RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database))


def test_installed_skill_requires_explicit_qualification(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.0.0",
            source_uri="skill://open-design@1.0.0",
            revision_sha256="a" * 64,
            requested_permissions=("artifact:write", "workspace:read"),
        ),
        actor_id="admin",
    )

    assert installed.status == "installed"
    qualified = catalog.qualify(installed.id, expected_version=installed.version)
    assert qualified.status == "qualified"
    assert qualified.qualification_sha256 is not None


def test_extension_qualification_fails_closed_on_unsafe_permission(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="unsafe-design",
            name="不安全设计扩展",
            kind="skill",
            version="1.0.0",
            source_uri="skill://unsafe-design@1.0.0",
            revision_sha256="b" * 64,
            requested_permissions=("shell:arbitrary",),
        ),
        actor_id="admin",
    )

    failed = catalog.qualify(installed.id, expected_version=installed.version)
    assert failed.status == "failed"
    assert failed.qualification_errors == ("EXTENSION_PERMISSION_NOT_ALLOWED",)


def test_profile_extension_resolution_freezes_exact_qualified_revision(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.2.0",
            source_uri="skill://open-design@1.2.0",
            revision_sha256="c" * 64,
            requested_permissions=("artifact:write",),
        ),
        actor_id="admin",
    )
    qualified = catalog.qualify(installed.id, expected_version=installed.version)

    snapshot = catalog.resolve({"id": "open-design", "kind": "skill", "version": ">=1,<2"})

    assert snapshot["id"] == "open-design"
    assert snapshot["version"] == "1.2.0"
    assert snapshot["revision_sha256"] == qualified.revision_sha256
    assert snapshot["qualification_sha256"] == qualified.qualification_sha256


def test_agent_deployment_freezes_qualified_profile_extension_snapshot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    extensions = RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database))
    installed = extensions.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.0.0",
            source_uri="skill://open-design@1.0.0",
            revision_sha256="d" * 64,
            requested_permissions=("artifact:write",),
        ),
        actor_id="admin",
    )
    extensions.qualify(installed.id, expected_version=installed.version)
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    spec = AgentProfileSpec.model_validate(
        {
            "schema_version": "1",
            "id": "ui-designer",
            "name": "UI 设计师",
            "instructions": {"custom_text": "输出可审查的设计规范"},
            "capabilities": [{"id": "frontend.implementation", "version": ">=1,<2"}],
            "policies": {
                "tool_policy_ref": "policy://design-tools@1",
                "resource_policy_ref": "policy://design-resources@1",
                "approval_policy_ref": "policy://design-approval@1",
                "memory_policy_ref": "policy://session-isolated@1",
                "delegation_policy_ref": "policy://no-delegation@1",
            },
            "extensions": {
                "runtime_extensions": [
                    {
                        "id": "open-design",
                        "kind": "skill",
                        "version": ">=1,<2",
                    }
                ]
            },
        }
    )
    created = profiles.create(AgentProfileCreate(spec=spec), actor_id="admin")
    validated = profiles.validate_draft(
        spec.id, expected_version=created.draft.version, actor_id="admin"
    )
    profiles.publish(spec.id, expected_version=validated.version, actor_id="admin")
    instances = ControlPlaneService(database, probe=_ReadyProbe())
    instance = instances.create_instance(
        AgentInstanceCreate(
            name="Codex 设计实例",
            runtime_type="codex-cli",
            connection={"command": "codex"},
        )
    )
    instance = __import__("asyncio").run(instances.check_instance(instance.id))
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database),
        profiles,
        instances,
        ProviderManifestCatalog(),
        extensions=extensions,
    )
    deployment = deployments.create(
        AgentDeploymentCreate(
            id="ui-design-codex",
            name="UI 设计 Codex",
            profile_id=spec.id,
            profile_revision=1,
            instance_id=instance.id,
            provider_id="codex-cli-provider",
        ),
        actor_id="admin",
    )
    qualified = deployments.qualify(deployment.id, deployment.version)

    assert qualified.qualification_status == "qualified"
    assert qualified.extension_snapshot[0]["id"] == "open-design"
    assert qualified.extension_snapshot[0]["revision_sha256"] == "d" * 64


def test_runtime_extension_public_interface_installs_and_qualifies(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    with TestClient(create_app(coordinator, runtime_extensions=catalog)) as client:
        installed = client.post(
            "/v1/runtime-extensions",
            json={
                "id": "open-design",
                "name": "Open Design",
                "kind": "skill",
                "version": "1.0.0",
                "source_uri": "skill://open-design@1.0.0",
                "revision_sha256": "e" * 64,
                "requested_permissions": ["artifact:write"],
            },
        )
        qualified = client.post(
            "/v1/runtime-extensions/open-design/qualify",
            json={"expected_version": installed.json()["version"]},
        )
        records = client.get("/v1/runtime-extensions")

    assert installed.status_code == 201
    assert qualified.json()["status"] == "qualified"
    assert records.json() == [qualified.json()]
