from pathlib import Path

from agent_team_os.control_plane import ControlPlaneService, HealthResult
from agent_team_os.infrastructure.acwm import (
    ACWMGraphCompiler,
    AgentDeploymentBindingResolver,
    ControlPlaneBindingResolver,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.journey import load_agent_workcell_delivery_definition
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
    ensure_builtin_workcell_agent_deployments,
)
from agent_team_os.modules.delivery import BackendDeliveryPipelinePolicy
from agent_team_os.modules.extensions import (
    RuntimeExtensionCatalog,
    SQLiteRuntimeExtensionRepository,
)
from agent_team_os.modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    SQLitePipelineRepository,
)
from agent_team_os.modules.workcells import (
    builtin_release_contract,
    builtin_workcell_stage_map,
)


class ReadyProbe:
    async def check(
        self,
        runtime_type: str,
        connection: dict[str, str],
    ) -> HealthResult:
        del runtime_type, connection
        return HealthResult(
            status="ready",
            identity="deterministic-model-boundary",
            latency_ms=1,
        )


def test_builtin_workcell_pipeline_publishes_all_frozen_provider_and_method_slots(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[1]
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, root / "migrations").migrate()
    control_plane = ControlPlaneService(
        database,
        config_root=root / "config",
        probe=ReadyProbe(),
    )
    control_plane.import_builtin_journey(
        planning_identity="deterministic-model-boundary",
        execution_identity="deterministic-model-boundary",
    )
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    providers = ProviderManifestCatalog()
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database),
        profiles,
        control_plane,
        providers,
        extensions=RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database)),
    )
    assignments = ensure_builtin_workcell_agent_deployments(
        profiles,
        deployments,
        planning_instance_id="builtin:deterministic-model-boundary",
        execution_instance_id="builtin:deterministic-model-boundary",
    )
    catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=ACWMGraphCompiler(),
        binding_resolver=ControlPlaneBindingResolver(
            control_plane.get_binding,
            control_plane.get_instance,
        ),
        provider_binding_resolver=AgentDeploymentBindingResolver(deployments, providers),
        definition_policy=BackendDeliveryPipelinePolicy(),
    )
    created = catalog.create_pipeline(
        PipelineCreate(
            id="agent-workcell-delivery",
            name="v0.5 Agent Workcell Delivery",
            definition=load_agent_workcell_delivery_definition(root / "config"),
            agent_assignments=assignments,
            workcell_stage_map=builtin_workcell_stage_map(),
            release_contract_snapshot=builtin_release_contract(),
        ),
        created_by="system",
    )

    validated = catalog.validate_draft(
        created.draft.id,
        expected_version=created.draft.version,
    )
    revision = catalog.publish_draft(
        validated.id,
        expected_version=validated.version,
        published_by="system",
    )

    assert validated.validation_status == "valid"
    assert validated.validation_errors == ()
    assert len(revision.resolved_provider_bindings) == 22
    assert revision.release_contract_snapshot == ("design", "frontend", "backend", "qa")
    assert {
        method
        for stage in revision.workcell_stage_map.values()
        for method in stage.delegate_methods.values()
    } == {
        "bmad-ux",
        "bmad-review",
        "bmad-build",
        "bmad-code-review",
        "bmad-testarch-test-design",
        "bmad-testarch-atdd",
        "bmad-testarch-automate",
        "bmad-testarch-test-review",
        "bmad-testarch-trace",
    }
