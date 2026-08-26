"""Deterministic model-boundary app used only by the release browser gate."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .api import create_app
from .control_plane import ControlPlaneService, HealthResult
from .delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from .git_delivery import GitCandidateApplier, GitCandidateVerifier, GitCodeExecutor
from .infrastructure.acwm import (
    ACWMGraphCompiler,
    ACWMPipelineGraphRuntime,
    AgentDeploymentBindingResolver,
    ControlPlaneBindingResolver,
)
from .infrastructure.database import MigrationRunner
from .infrastructure.git import ProjectGitWorkspaces
from .journey import (
    load_backend_delivery_definition,
    load_fullstack_delivery_definition,
    resolve_backend_delivery_fingerprint,
)
from .modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRunLedger,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
    ensure_builtin_agent_deployments,
    ensure_builtin_fullstack_agent_deployments,
)
from .modules.delivery import BackendDeliveryPipelinePolicy
from .modules.evidence import EvidenceLedger, SQLiteEvidenceRepository
from .modules.extensions import RuntimeExtensionCatalog, SQLiteRuntimeExtensionRepository
from .modules.identity import IdentityService, SQLiteIdentityRepository
from .modules.knowledge import KnowledgeSearchIndex, SQLiteWikiRepository, WikiService
from .modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
)
from .modules.projects import (
    ProjectCatalog,
    ProjectLeaseDeliveryRepository,
    SQLiteProjectRepository,
)
from .modules.releases import ReleaseCoordinator, SQLiteReleaseRepository
from .modules.settings import SettingsManager, SQLiteSettingsRepository
from .release import DeterministicWorkspaceAgent
from .testing import DeterministicPlanningService
from .ui import install_preview_ui


class DeterministicGateHealthProbe:
    """Make browser-created instances verifiable without calling a live provider."""

    async def check(self, runtime_type: str, connection: dict[str, str]) -> HealthResult:
        if runtime_type == "codex-cli" and connection.get("command") == "codex":
            return HealthResult(
                status="ready",
                identity="deterministic-model-boundary",
                latency_ms=1,
            )
        return HealthResult(
            status="failed",
            error_code="DETERMINISTIC_GATE_RUNTIME_UNSUPPORTED",
            latency_ms=1,
        )


def build_gate_app() -> FastAPI:
    project_root = Path(__file__).parents[2]
    data_dir = Path(os.environ["AGENT_TEAM_OS_DATA_DIR"])
    database = data_dir / "agent-team-os.sqlite"
    MigrationRunner(database, project_root / "migrations").migrate()
    project_workspaces = ProjectGitWorkspaces(data_dir / "browser-workspaces")
    sandbox = project_workspaces.for_workspace("backend-demo")
    sandbox.ensure_initialized()
    projects = ProjectCatalog(SQLiteProjectRepository(database), project_workspaces)
    delivery_repository = ProjectLeaseDeliveryRepository(
        SQLiteDeliveryRepository(database), projects
    )
    candidate_applier = GitCandidateApplier(project_workspaces)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=GitCodeExecutor(project_workspaces, DeterministicWorkspaceAgent()),
        verifier=GitCandidateVerifier(project_workspaces),
        applier=candidate_applier,
        repository=delivery_repository,
        resolved_journey_sha256=resolve_backend_delivery_fingerprint(project_root / "config"),
    )
    control_plane = ControlPlaneService(
        database,
        config_root=project_root / "config",
        probe=DeterministicGateHealthProbe(),
    )
    control_plane.import_builtin_journey(
        planning_identity="deterministic-test",
        execution_identity="deterministic-model-boundary",
    )
    agent_profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    runtime_extensions = RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database))
    provider_manifests = ProviderManifestCatalog()
    agent_deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database),
        agent_profiles,
        control_plane,
        provider_manifests,
        extensions=runtime_extensions,
    )
    builtin_assignments = ensure_builtin_agent_deployments(
        agent_profiles,
        agent_deployments,
        planning_instance_id="builtin:deterministic-test",
        execution_instance_id="builtin:deterministic-model-boundary",
    )
    fullstack_assignments = ensure_builtin_fullstack_agent_deployments(
        agent_profiles,
        agent_deployments,
        planning_instance_id="builtin:deterministic-test",
        execution_instance_id="builtin:deterministic-model-boundary",
    )
    pipeline_catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=ACWMGraphCompiler(),
        binding_resolver=ControlPlaneBindingResolver(
            control_plane.get_binding, control_plane.get_instance
        ),
        provider_binding_resolver=AgentDeploymentBindingResolver(
            agent_deployments, provider_manifests
        ),
        definition_policy=BackendDeliveryPipelinePolicy(),
    )
    builtin_pipeline = pipeline_catalog.ensure_builtin_pipeline(
        PipelineCreate(
            id="backend-delivery",
            name="内置后端交付闭环",
            description="需求、计划审批、代码交付、候选审批与原子应用",
            definition=load_backend_delivery_definition(project_root / "config"),
            agent_assignments=builtin_assignments,
        ),
        actor_id="system",
    )
    pipeline_catalog.ensure_builtin_pipeline(
        PipelineCreate(
            id="fullstack-product-delivery",
            name="产品规划 → UI 设计 → 前后端 → 测试发布",
            description="五个确定性边界角色、四仓验证与 Release Manifest",
            definition=load_fullstack_delivery_definition(project_root / "config"),
            agent_assignments=fullstack_assignments,
        ),
        actor_id="system",
    )

    def validate_project_pipeline(revision_id: str) -> None:
        pipeline_catalog.resolve_revision(revision_id)

    def validate_project_deployment(deployment_id: str) -> None:
        deployment = agent_deployments.get(deployment_id)
        if not deployment.enabled or deployment.qualification_status != "qualified":
            raise ValueError("deployment is not enabled and qualified")

    projects.configure_resource_validators(
        pipeline=validate_project_pipeline,
        deployment=validate_project_deployment,
    )
    if builtin_pipeline.active_revision is not None:
        projects.ensure_legacy_defaults(
            f"backend-delivery:{builtin_pipeline.active_revision}",
            tuple(sorted(set(builtin_assignments.values()))),
        )
    result = create_app(
        coordinator,
        control_plane=control_plane,
        evidence=EvidenceLedger(SQLiteEvidenceRepository(database)),
        settings=SettingsManager(SQLiteSettingsRepository(database)),
        identity=IdentityService(SQLiteIdentityRepository(database)),
        knowledge=WikiService(
            SQLiteWikiRepository(database), project_guard=projects.assert_writable
        ),
        pipeline_catalog=pipeline_catalog,
        pipeline_runs=PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        ),
        agent_profiles=agent_profiles,
        agent_deployments=agent_deployments,
        provider_manifests=provider_manifests,
        agent_runs=AgentRunLedger(database),
        projects=projects,
        knowledge_search=KnowledgeSearchIndex(database),
        runtime_extensions=runtime_extensions,
        release_applier=ReleaseCoordinator(SQLiteReleaseRepository(database), candidate_applier),
    )
    install_preview_ui(result, project_root / "console" / "dist")
    return result


app = build_gate_app()
