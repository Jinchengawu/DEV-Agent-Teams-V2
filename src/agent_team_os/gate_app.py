"""Deterministic model-boundary app used only by the release browser gate."""

from __future__ import annotations

import os
import sys
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
from .infrastructure.git import (
    ExternalForwardGitRemote,
    ExternalGitBinding,
    ExternalGitCapabilityProbe,
    ExternalGitWorkspaceManager,
    ProjectGitWorkspaces,
)
from .journey import (
    load_agent_workcell_delivery_definition,
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
    ensure_builtin_workcell_agent_deployments,
)
from .modules.artifacts import ContentAddressedArtifactStorage
from .modules.delivery import BackendDeliveryPipelinePolicy
from .modules.evaluation import EvaluationService, SQLiteEvaluationRepository
from .modules.evidence import EvidenceLedger, SQLiteEvidenceRepository
from .modules.extensions import (
    ContentAddressedMethodPackStore,
    FrozenMethodPackSet,
    RuntimeExtensionCatalog,
    SQLiteRuntimeExtensionRepository,
)
from .modules.identity import IdentityService, SQLiteIdentityRepository
from .modules.knowledge import (
    KnowledgePublicationLedger,
    KnowledgePublisher,
    KnowledgeSearchIndex,
    SQLiteWikiRepository,
    WikiService,
)
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
from .modules.releases import (
    ExternalForwardReleaseCoordinator,
    ExternalReleaseCatalog,
    ReleaseCoordinator,
    SQLiteExternalReleaseRepository,
    SQLiteReleaseRepository,
)
from .modules.settings import SettingsManager, SQLiteSettingsRepository
from .modules.workcells import (
    CommandWorkcellMachineVerifier,
    ContentAddressedMethodRuntime,
    DeliveryExecutionSnapshotCompiler,
    ProjectWorkcellGovernance,
    SQLiteProjectWorkcellRepository,
    SQLiteTeamTemplateRepository,
    SQLiteWorkcellExecutionRepository,
    TeamTemplateCatalog,
    WorkcellExecutionModule,
    WorkcellStageDriver,
    builtin_release_contract,
    builtin_workcell_stage_map,
)
from .release import DeterministicWorkspaceAgent
from .testing import (
    DeterministicPlanningService,
    DeterministicPullRequestSurface,
    DeterministicWorkcellAgent,
)
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
    project_repository = SQLiteProjectRepository(database)
    team_templates = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database))
    project_workcell_repository = SQLiteProjectWorkcellRepository(database)
    project_workcells = ProjectWorkcellGovernance(
        project_workcell_repository,
        teams=team_templates,
        projects=project_repository,
        managed_git=project_workspaces,
        external_git=ExternalGitCapabilityProbe(
            data_dir / "external-git-probe",
            allow_local_test_transport=True,
        ),
    )
    projects = ProjectCatalog(
        project_repository,
        project_workspaces,
        team_governance=project_workcells,
    )
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
    workcell_assignments = ensure_builtin_workcell_agent_deployments(
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
    pipeline_catalog.ensure_builtin_pipeline(
        PipelineCreate(
            id="agent-workcell-delivery",
            name="v0.5 Agent Workcell 四仓交付",
            description="可观察 Main/Child Workcell、BMAD/TEA 与 Forward-only Release",
            definition=load_agent_workcell_delivery_definition(project_root / "config"),
            agent_assignments=workcell_assignments,
            workcell_stage_map=builtin_workcell_stage_map(),
            release_contract_snapshot=builtin_release_contract(),
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
    evidence_ledger = EvidenceLedger(SQLiteEvidenceRepository(database))
    knowledge_publications = KnowledgePublicationLedger(database)
    artifact_storage = ContentAddressedArtifactStorage(data_dir / "artifacts")
    workcell_execution = WorkcellExecutionModule(
        SQLiteWorkcellExecutionRepository(database),
        artifact_storage=artifact_storage,
    )
    method_store = ContentAddressedMethodPackStore(data_dir / "method-packs")
    method_set = FrozenMethodPackSet(
        project_root / "config" / "method-packs-v050.json",
        method_store,
    )

    def resolve_workspace_binding(workspace_id: str) -> ExternalGitBinding:
        workspace = project_workcell_repository.get_workspace(workspace_id)
        return ExternalGitBinding(
            remote_uri=(
                project_workspaces.remote_uri(workspace.repository_uri)
                if workspace.adapter_type == "managed-bare-git"
                else workspace.repository_uri
            ),
            credential_reference=workspace.credential_reference,
        )

    release_v2_repository = SQLiteExternalReleaseRepository(database)
    external_release = ExternalForwardReleaseCoordinator(
        release_v2_repository,
        ExternalForwardGitRemote(resolve_workspace_binding),
    )
    workcell_stage_driver = WorkcellStageDriver(
        kernel=workcell_execution,
        artifacts=artifact_storage,
        methods=ContentAddressedMethodRuntime(method_store),
        agent=DeterministicWorkcellAgent(),
        workspaces=ExternalGitWorkspaceManager(data_dir / "workcell-runtime"),
        binding_resolver=resolve_workspace_binding,
        verifier=CommandWorkcellMachineVerifier(
            lambda _workcell: (
                (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"),
            )
        ),
        releases=ExternalReleaseCatalog(release_v2_repository),
        pull_requests=DeterministicPullRequestSurface(),
    )
    delivery_snapshot_compiler = DeliveryExecutionSnapshotCompiler(
        governance=project_workcells,
        projects=project_repository,
        pipelines=pipeline_catalog,
        method_snapshot=method_set.snapshot,
    )
    wiki_service = WikiService(
        SQLiteWikiRepository(database), project_guard=projects.assert_writable
    )
    for project in projects.list():
        wiki_service.reconcile_project_space(
            project.id, project.name, project.lifecycle_status
        )
    result = create_app(
        coordinator,
        control_plane=control_plane,
        evidence=evidence_ledger,
        settings=SettingsManager(SQLiteSettingsRepository(database)),
        identity=IdentityService(SQLiteIdentityRepository(database)),
        knowledge=wiki_service,
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
        knowledge_publications=knowledge_publications,
        knowledge_publisher=KnowledgePublisher(database, knowledge_publications),
        evaluations=EvaluationService(
            SQLiteEvaluationRepository(database),
            pipeline_catalog,
            report_dir=data_dir / "reports" / "evaluations",
            project_root=project_root,
            evidence=evidence_ledger,
        ),
        team_templates=team_templates,
        project_workcells=project_workcells,
        workcell_execution=workcell_execution,
        delivery_snapshot_compiler=delivery_snapshot_compiler,
        external_release=external_release,
        workcell_stage_driver=workcell_stage_driver,
    )
    install_preview_ui(result, project_root / "console" / "dist")
    return result


app = build_gate_app()
