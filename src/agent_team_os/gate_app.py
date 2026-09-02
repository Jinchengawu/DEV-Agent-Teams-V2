"""Deterministic model-boundary app used only by the release browser gate."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from pydantic import JsonValue

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
from .infrastructure.knowledge import SQLiteVectorIndexAdapter
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
    DeliveryKnowledgeContextPreparationService,
    EmbeddingModelDescriptor,
    KnowledgeAuthorizationResolver,
    KnowledgeContextRuntimeGuard,
    KnowledgeDirectoryReconciler,
    KnowledgeIndexManager,
    KnowledgePreparationInputCompiler,
    KnowledgePublicationLedger,
    KnowledgePublisher,
    KnowledgeSearchIndex,
    KnowledgeSyncPolicy,
    KnowledgeSyncScheduler,
    KnowledgeSyncSupervisor,
    KnowledgeSyncWorker,
    ProviderNode,
    ProviderNodeKind,
    ProviderSnapshot,
    ProviderSpace,
    SQLiteKnowledgeContextRepository,
    SQLiteKnowledgeIndexRepository,
    SQLiteTenantKnowledgeRepository,
    SQLiteWikiRepository,
    TenantConnection,
    TenantKnowledgeManager,
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
from .readiness import snapshot_delivery_build_identity
from .release import DeterministicWorkspaceAgent
from .shared.features import FeatureFlags
from .shared.hashes import sha256_json
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


class DeterministicGateTenantKnowledgeProvider:
    """Release-gate-only Feishu boundary; never valid Live evidence."""

    def list_spaces(self) -> tuple[ProviderSpace, ...]:
        return (
            ProviderSpace(external_id="gate-space", title="Gate 研发知识库"),
        )

    def list_nodes(self, external_space_id: str) -> tuple[ProviderNode, ...]:
        return (
            ProviderNode(
                external_id="gate-architecture",
                external_space_id=external_space_id,
                source_id="docx:gate-architecture",
                title="四仓隔离架构规范",
                kind=ProviderNodeKind.DOCUMENT,
                provider_revision="gate-rev-1",
            ),
            ProviderNode(
                external_id="gate-release",
                external_space_id=external_space_id,
                source_id="docx:gate-release",
                title="Forward-only Release 规范",
                kind=ProviderNodeKind.DOCUMENT,
                provider_revision="gate-rev-1",
            ),
        )

    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
        text_by_source = {
            "docx:gate-architecture": (
                "# 四仓隔离架构规范\n"
                "Design、Frontend、Backend、QA 必须使用独立 Git Repository Workspace。"
            ),
            "docx:gate-release": (
                "# Forward-only Release 规范\n"
                "Candidate 通过 Verification 和 Review 后才能快进 Apply。"
            ),
        }
        text = text_by_source[source_id]
        normalized: JsonValue = {"type": "feishu-docx-raw", "text": text}
        return ProviderSnapshot(
            source_id=source_id,
            provider_revision="gate-rev-1",
            content_type="text/markdown; charset=utf-8",
            normalized_content=normalized,
            normalized_text=text,
            content_sha256=sha256_json(normalized),
            source_url=f"https://example.invalid/wiki/{source_id.split(':', 1)[1]}",
            fetched_at=datetime(2026, 9, 2, tzinfo=UTC),
        )


class DeterministicGateTenantKnowledgeResolver:
    def __init__(self) -> None:
        self.provider = DeterministicGateTenantKnowledgeProvider()

    def resolve(self, _connection: TenantConnection) -> DeterministicGateTenantKnowledgeProvider:
        return self.provider


class DeterministicGateEmbeddingPort:
    """Stable four-dimensional embedding for deterministic browser evidence."""

    adapter_revision = "deterministic-gate-embedding-v1"
    model_digest = "sha256:" + "1" * 64

    def describe(self, model_name: str) -> EmbeddingModelDescriptor:
        return EmbeddingModelDescriptor(
            model_name=model_name,
            model_digest=self.model_digest,
        )

    def embed(
        self,
        texts: tuple[str, ...],
        *,
        model_name: str,
        truncate: bool,
    ) -> tuple[tuple[float, ...], ...]:
        del model_name, truncate
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                (
                    0.01 + float("workspace" in lowered or "仓" in text),
                    float("frontend" in lowered or "前端" in text),
                    float("backend" in lowered or "后端" in text),
                    float("apply" in lowered or "发布" in text),
                )
            )
        return tuple(vectors)


def build_gate_app() -> FastAPI:
    project_root = Path(__file__).parents[2]
    data_dir = Path(os.environ["AGENT_TEAM_OS_DATA_DIR"])
    database = data_dir / "agent-team-os.sqlite"
    MigrationRunner(database, project_root / "migrations").migrate()
    feature_flags = FeatureFlags.from_environment()
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
    identity_service = IdentityService(SQLiteIdentityRepository(database))
    tenant_knowledge: TenantKnowledgeManager | None = None
    knowledge_indexes: KnowledgeIndexManager | None = None
    knowledge_preparation_compiler: KnowledgePreparationInputCompiler | None = None
    knowledge_runtime_guard: KnowledgeContextRuntimeGuard | None = None
    knowledge_context_repository: SQLiteKnowledgeContextRepository | None = None
    authorization: KnowledgeAuthorizationResolver | None = None
    knowledge_sync_supervisor: KnowledgeSyncSupervisor | None = None
    if feature_flags.feishu_tenant_sync_v1:
        tenant_knowledge = TenantKnowledgeManager(
            SQLiteTenantKnowledgeRepository(database),
            provider_resolver=DeterministicGateTenantKnowledgeResolver(),
            artifact_storage=artifact_storage,
        )
        projects.configure_knowledge_binding_validator(tenant_knowledge.require_binding)
        sync_policy = KnowledgeSyncPolicy()
        knowledge_sync_supervisor = KnowledgeSyncSupervisor(
            KnowledgeSyncScheduler(
                tenant_knowledge,
                project_repository,
                policy=sync_policy,
            ),
            KnowledgeDirectoryReconciler(
                tenant_knowledge,
                policy=sync_policy,
            ),
            KnowledgeSyncWorker(
                tenant_knowledge,
                policy=sync_policy,
            ),
            policy=sync_policy,
        )
    if feature_flags.knowledge_hybrid_index_v1:
        assert tenant_knowledge is not None
        knowledge_indexes = KnowledgeIndexManager(
            SQLiteKnowledgeIndexRepository(database),
            tenant_repository=tenant_knowledge.repository,
            artifact_storage=artifact_storage,
            index_root=data_dir / "knowledge-indexes",
            embedding_port=DeterministicGateEmbeddingPort(),
            vector_index_port=SQLiteVectorIndexAdapter(),
        )
        pipeline_catalog.configure_knowledge_binding_policy(knowledge_indexes)
    if feature_flags.delivery_knowledge_context_v1:
        assert tenant_knowledge is not None and knowledge_indexes is not None
        authorization = KnowledgeAuthorizationResolver(
            identity=identity_service,
            projects=projects,
            tenant=tenant_knowledge,
        )
        knowledge_preparation_compiler = KnowledgePreparationInputCompiler(
            authorization=authorization,
            projects=projects,
            artifacts=artifact_storage,
        )
        knowledge_runtime_guard = KnowledgeContextRuntimeGuard(
            authorization=authorization,
            artifacts=artifact_storage,
        )
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
        knowledge_guard=knowledge_runtime_guard,
    )
    delivery_snapshot_compiler = DeliveryExecutionSnapshotCompiler(
        governance=project_workcells,
        projects=project_repository,
        pipelines=pipeline_catalog,
        method_snapshot=method_set.snapshot,
        build_identity=lambda: snapshot_delivery_build_identity(project_root),
    )
    if feature_flags.delivery_knowledge_context_v1:
        assert (
            tenant_knowledge is not None
            and knowledge_indexes is not None
            and authorization is not None
        )
        knowledge_context_repository = SQLiteKnowledgeContextRepository(database)
        knowledge_preparer = DeliveryKnowledgeContextPreparationService(
            knowledge_context_repository,
            authorization=authorization,
            projects=projects,
            tenant=tenant_knowledge,
            indexes=knowledge_indexes,
            artifacts=artifact_storage,
            snapshot_compiler=delivery_snapshot_compiler,
        )
        coordinator.configure_knowledge_context(knowledge_preparer)
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
        identity=identity_service,
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
        tenant_knowledge=tenant_knowledge,
        knowledge_indexes=knowledge_indexes,
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
        knowledge_preparation_compiler=knowledge_preparation_compiler,
        knowledge_runtime_guard=knowledge_runtime_guard,
        knowledge_context_repository=knowledge_context_repository,
        feature_flags=feature_flags,
    )
    install_preview_ui(result, project_root / "console" / "dist")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if tenant_knowledge is not None:
            tenant_knowledge.recover_expired_sync_jobs()
        if knowledge_sync_supervisor is not None:
            knowledge_sync_supervisor.start()
        try:
            yield
        finally:
            if knowledge_sync_supervisor is not None:
                await knowledge_sync_supervisor.stop()

    result.router.lifespan_context = lifespan
    return result


app = build_gate_app()
