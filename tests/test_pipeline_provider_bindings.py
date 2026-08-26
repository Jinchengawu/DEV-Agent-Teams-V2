import asyncio
from pathlib import Path
from typing import cast

from agent_team_os.control_plane import ControlPlaneService
from agent_team_os.delivery import DeliveryCoordinator, InMemoryDeliveryRepository
from agent_team_os.infrastructure.acwm import (
    ACWMGraphCompiler,
    ACWMPipelineGraphRuntime,
    AgentDeploymentBindingResolver,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.journey import (
    load_backend_delivery_definition,
    load_fullstack_delivery_definition,
)
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRunLedger,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
    ensure_builtin_agent_deployments,
    ensure_builtin_fullstack_agent_deployments,
)
from agent_team_os.modules.delivery import BackendDeliveryPipelinePolicy
from agent_team_os.modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
)
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class CapturingPlanningService(DeterministicPlanningService):
    evidence_identity = "codex-simulated-hermes"

    def __init__(self) -> None:
        self.analyze_inputs: list[str] = []

    async def analyze(self, user_request: str):
        self.analyze_inputs.append(user_request)
        return await super().analyze(user_request)


def _catalog(tmp_path: Path) -> tuple[PipelineCatalog, dict[str, str]]:
    root = Path(__file__).parents[1]
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, root / "migrations").migrate()
    instances = ControlPlaneService(database, config_root=root / "config")
    instances.import_builtin_journey(
        planning_identity="codex-simulated-hermes",
        execution_identity="codex-cli",
    )
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    providers = ProviderManifestCatalog()
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database), profiles, instances, providers
    )
    assignments = ensure_builtin_agent_deployments(profiles, deployments)
    return (
        PipelineCatalog(
            SQLitePipelineRepository(database),
            graph_compiler=ACWMGraphCompiler(),
            provider_binding_resolver=AgentDeploymentBindingResolver(deployments, providers),
            definition_policy=BackendDeliveryPipelinePolicy(),
        ),
        assignments,
    )


def test_pipeline_publication_freezes_provider_bindings_by_stage_site(
    tmp_path: Path,
) -> None:
    catalog, assignments = _catalog(tmp_path)
    definition = load_backend_delivery_definition(Path(__file__).parents[1] / "config")
    created = catalog.create_pipeline(
        PipelineCreate(
            id="provider-delivery",
            name="Provider 交付",
            definition=definition,
            agent_assignments=assignments,
        ),
        created_by="test",
    )
    validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)
    revision = catalog.publish_draft(
        created.draft.id,
        expected_version=validated.version,
        published_by="test",
    )

    assert validated.validation_status == "valid"
    assert revision.binding_model == "provider-v1"
    assert revision.binding_snapshot == {}
    assert set(revision.resolved_provider_bindings) == set(assignments)
    for snapshot in revision.resolved_provider_bindings.values():
        profile = cast(dict[str, object], snapshot["profile"])
        deployment = cast(dict[str, object], snapshot["deployment"])
        binding = cast(dict[str, object], snapshot["binding"])
        assert len(cast(str, profile["sha256"])) == 64
        assert deployment["enabled"] is True
        assert len(cast(str, binding["binding_fingerprint"])) == 64


def test_pipeline_validation_rejects_missing_deployment_assignment(
    tmp_path: Path,
) -> None:
    catalog, assignments = _catalog(tmp_path)
    assignments.pop("tasking.actor")
    created = catalog.create_pipeline(
        PipelineCreate(
            id="missing-assignment",
            name="缺失分配",
            definition=load_backend_delivery_definition(Path(__file__).parents[1] / "config"),
            agent_assignments=assignments,
        ),
        created_by="test",
    )

    validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)

    assert validated.validation_status == "invalid"
    assert "PROVIDER_ASSIGNMENT_MISSING" in validated.validation_errors[0]


def test_fullstack_pipeline_freezes_five_role_assignments(tmp_path: Path) -> None:
    catalog, _ = _catalog(tmp_path)
    database = tmp_path / "agent-team-os.sqlite"
    instances = ControlPlaneService(database, config_root=Path(__file__).parents[1] / "config")
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    providers = ProviderManifestCatalog()
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database), profiles, instances, providers
    )
    assignments = ensure_builtin_fullstack_agent_deployments(profiles, deployments)
    created = catalog.create_pipeline(
        PipelineCreate(
            id="fullstack-provider-runtime",
            name="五角色产品交付",
            definition=load_fullstack_delivery_definition(Path(__file__).parents[1] / "config"),
            agent_assignments=assignments,
        ),
        created_by="test",
    )

    validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)
    revision = catalog.publish_draft(
        created.draft.id,
        expected_version=validated.version,
        published_by="test",
    )

    assert validated.validation_status == "valid"
    assert revision.binding_model == "provider-v1"
    assert set(revision.resolved_provider_bindings) == set(assignments)
    assert {
        snapshot["deployment"]["id"]  # type: ignore[index]
        for snapshot in revision.resolved_provider_bindings.values()
    } == set(assignments.values())


def test_provider_pipeline_records_frozen_agent_runs_and_typed_artifacts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        catalog, assignments = _catalog(tmp_path)
        created = catalog.create_pipeline(
            PipelineCreate(
                id="provider-runtime",
                name="Provider 运行",
                definition=load_backend_delivery_definition(Path(__file__).parents[1] / "config"),
                agent_assignments=assignments,
            ),
            created_by="test",
        )
        validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)
        revision = catalog.publish_draft(
            created.draft.id,
            expected_version=validated.version,
            published_by="test",
        )
        database = tmp_path / "agent-team-os.sqlite"
        runs = PipelineRunLedger(SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime())
        agent_runs = AgentRunLedger(database)
        planning = CapturingPlanningService()
        coordinator = DeliveryCoordinator(
            planning=planning,
            executor=DeterministicCodeExecutor(),
            repository=InMemoryDeliveryRepository(),
            resolved_journey_sha256=revision.fingerprint,
        )
        coordinator.configure_pipeline_runtime(catalog, runs, agent_runs)
        delivery = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="增加健康检查",
            pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
            resolved_provider_bindings=revision.resolved_provider_bindings,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )
        for _ in range(100):
            current = coordinator.get(delivery.id)
            if current.status == "awaiting_plan_decision":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("provider pipeline did not reach plan gate")

        recorded = agent_runs.list(delivery.id)
        assert [item.binding_site for item in recorded] == [
            "requirements.actor",
            "tasking.actor",
        ]
        assert all(item.status == "succeeded" for item in recorded)
        assert all(item.runtime_identity == "codex-simulated-hermes" for item in recorded)
        assert [item.artifact_envelopes[0].contract_id for item in recorded] == [
            "requirement-artifact-v1",
            "task-contract-v1",
        ]
        for item in recorded:
            snapshot = revision.resolved_provider_bindings[item.binding_site]
            binding = cast(dict[str, object], snapshot["binding"])
            assert item.resolved_binding_hash == binding["binding_fingerprint"]
        assert len(planning.analyze_inputs) == 1
        assert "遵守已发布 Pipeline 与系统安全策略" in planning.analyze_inputs[0]
        assert "增加健康检查" in planning.analyze_inputs[0]

    asyncio.run(scenario())
