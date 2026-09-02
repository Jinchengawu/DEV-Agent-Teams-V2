import asyncio
from pathlib import Path

from agent_team_os.control_plane import (
    AgentInstanceCreate,
    ControlPlaneService,
    HealthResult,
)
from agent_team_os.infrastructure.acwm import (
    ACWMGraphCompiler,
    AgentDeploymentBindingResolver,
    ControlPlaneBindingResolver,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.journey import (
    load_agent_workcell_delivery_definition,
    load_agent_workcell_knowledge_delivery_definition,
)
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
    builtin_knowledge_context_bindings,
    builtin_release_contract,
    builtin_workcell_stage_map,
)


class KnowledgePolicies:
    def validate(self, retrieval_policy_revision_id: str, max_context_bytes: int) -> None:
        assert retrieval_policy_revision_id == "gate-retrieval-v1"
        assert max_context_bytes == 65_536


class ReadyProbe:
    async def check(
        self,
        runtime_type: str,
        connection: dict[str, str],
    ) -> HealthResult:
        del connection
        identity = {
            "hermes-acp": "hermes-acp:provider-test",
            "codex-cli": "codex-cli:provider-test",
        }.get(runtime_type, "deterministic-model-boundary")
        return HealthResult(
            status="ready",
            identity=identity,
            latency_ms=1,
        )


def test_builtin_deployments_follow_runtime_specific_provider_when_refreshed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
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
            extensions=RuntimeExtensionCatalog(
                SQLiteRuntimeExtensionRepository(database)
            ),
        )
        ensure_builtin_workcell_agent_deployments(
            profiles,
            deployments,
            planning_instance_id="builtin:deterministic-model-boundary",
            execution_instance_id="builtin:deterministic-model-boundary",
        )
        assert deployments.get("builtin-planning-deployment").provider_id == (
            "codex-cli-provider"
        )

        hermes = control_plane.create_instance(
            AgentInstanceCreate(
                name="Hermes ACP provider test",
                runtime_type="hermes-acp",
                connection={"command": "hermes"},
            )
        )
        codex = control_plane.create_instance(
            AgentInstanceCreate(
                name="Codex CLI provider test",
                runtime_type="codex-cli",
                connection={"command": "codex"},
            )
        )
        hermes = await control_plane.check_instance(hermes.id)
        codex = await control_plane.check_instance(codex.id)

        ensure_builtin_workcell_agent_deployments(
            profiles,
            deployments,
            planning_instance_id=hermes.id,
            execution_instance_id=codex.id,
        )

        planning = deployments.get("builtin-planning-deployment")
        lead = deployments.get("builtin-workcell-lead-deployment")
        delegate = deployments.get("builtin-workcell-delegate-deployment")
        assert (planning.provider_id, planning.adapter_id, planning.enabled) == (
            "hermes-provider",
            "hermes.acp",
            True,
        )
        assert (lead.provider_id, lead.adapter_id, lead.enabled) == (
            "codex-cli-provider",
            "codex.cli",
            True,
        )
        assert (delegate.provider_id, delegate.adapter_id, delegate.enabled) == (
            "codex-cli-provider",
            "codex.cli",
            True,
        )

    asyncio.run(scenario())


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


def test_builtin_workcell_r2_freezes_acwm_knowledge_contracts_and_product_bindings(
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
        provider_binding_resolver=AgentDeploymentBindingResolver(deployments, providers),
        definition_policy=BackendDeliveryPipelinePolicy(),
        knowledge_binding_policy=KnowledgePolicies(),
    )
    bindings = builtin_knowledge_context_bindings("gate-retrieval-v1")
    created = catalog.create_pipeline(
        PipelineCreate(
            id="agent-workcell-delivery-r2",
            name="v0.5.1 Knowledge-enabled Workcell Delivery",
            definition=load_agent_workcell_knowledge_delivery_definition(root / "config"),
            agent_assignments=assignments,
            workcell_stage_map=builtin_workcell_stage_map(),
            release_contract_snapshot=builtin_release_contract(),
            knowledge_context_bindings=bindings,
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
    assert revision.definition["version"] == "2.0.0"
    assert set(revision.knowledge_context_bindings) == set(bindings)
    stage_contracts = revision.compiled_graph["stage_input_artifact_contracts"]
    assert isinstance(stage_contracts, dict)
    assert set(stage_contracts) == set(bindings)
    for stage_path, contracts in stage_contracts.items():
        assert contracts == [
            {
                "id": "knowledge-context-v1",
                "version": "1.0.0",
                "schema_uri": None,
                "modalities": ["structured", "text"],
                "integrity": "sha256-required",
                "provenance": "required",
                "verification": "schema",
                "sha256": str(bindings[stage_path].acwm_artifact_contract_sha256),
            }
        ]
