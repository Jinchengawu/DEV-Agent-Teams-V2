"""Export the deterministic Agent-Team-OS HTTP contract for the web console."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from agent_team_os.api import create_app
from agent_team_os.control_plane import ControlPlaneService
from agent_team_os.delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ProjectGitWorkspaces
from agent_team_os.infrastructure.knowledge import SQLiteVectorIndexAdapter
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRunLedger,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
)
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.evidence import EvidenceLedger, SQLiteEvidenceRepository
from agent_team_os.modules.identity import IdentityService, SQLiteIdentityRepository
from agent_team_os.modules.knowledge import (
    EmbeddingModelDescriptor,
    KnowledgeIndexManager,
    KnowledgePublicationLedger,
    KnowledgePublisher,
    KnowledgeSearchIndex,
    SQLiteKnowledgeIndexRepository,
    SQLiteTenantKnowledgeRepository,
    SQLiteWikiRepository,
    TenantKnowledgeManager,
    WikiService,
)
from agent_team_os.modules.orchestration import PipelineCatalog, SQLitePipelineRepository
from agent_team_os.modules.projects import ProjectCatalog, SQLiteProjectRepository
from agent_team_os.modules.releases import (
    ExternalForwardReleaseCoordinator,
    SQLiteExternalReleaseRepository,
)
from agent_team_os.modules.settings import SettingsManager, SQLiteSettingsRepository
from agent_team_os.modules.workcells import (
    ProjectWorkcellGovernance,
    SQLiteProjectWorkcellRepository,
    SQLiteTeamTemplateRepository,
    SQLiteWorkcellExecutionRepository,
    TeamTemplateCatalog,
    WorkcellExecutionModule,
)
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class _SchemaOnlyRemote:
    def revision(self, _candidate: object) -> str:
        raise RuntimeError("OpenAPI schema remote must not be called")


class _SchemaOnlyEmbedding:
    adapter_revision = "schema-only"

    def describe(self, _model_name: str) -> EmbeddingModelDescriptor:
        raise RuntimeError("OpenAPI schema embedding must not be called")

    def embed(
        self,
        _texts: tuple[str, ...],
        *,
        model_name: str,
        truncate: bool,
    ) -> tuple[tuple[float, ...], ...]:
        del model_name, truncate
        raise RuntimeError("OpenAPI schema embedding must not be called")

    def apply(self, _candidate: object, *, ordinal: int) -> object:
        del ordinal
        raise RuntimeError("OpenAPI schema remote must not be called")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    project_root = Path(__file__).parents[1]
    with tempfile.TemporaryDirectory(prefix="agent-team-os-openapi-") as directory:
        database = Path(directory) / "contract.sqlite"
        MigrationRunner(database, project_root / "migrations").migrate()
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=SQLiteDeliveryRepository(database),
            resolved_journey_sha256="a" * 64,
        )
        control_plane = ControlPlaneService(database, config_root=project_root / "config")
        agent_profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
        providers = ProviderManifestCatalog()
        knowledge_publications = KnowledgePublicationLedger(database)
        project_repository = SQLiteProjectRepository(database)
        managed_git = ProjectGitWorkspaces(Path(directory) / "workspaces")
        team_templates = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database))
        project_workcells = ProjectWorkcellGovernance(
            SQLiteProjectWorkcellRepository(database),
            teams=team_templates,
            projects=project_repository,
            managed_git=managed_git,
        )
        projects = ProjectCatalog(
            project_repository,
            managed_git,
            team_governance=project_workcells,
        )
        artifacts = ContentAddressedArtifactStorage(Path(directory) / "artifacts")
        tenant_repository = SQLiteTenantKnowledgeRepository(database)
        tenant_knowledge = TenantKnowledgeManager(
            tenant_repository,
            artifact_storage=artifacts,
        )
        knowledge_indexes = KnowledgeIndexManager(
            SQLiteKnowledgeIndexRepository(database),
            tenant_repository=tenant_repository,
            artifact_storage=artifacts,
            index_root=Path(directory) / "indexes",
            embedding_port=_SchemaOnlyEmbedding(),
            vector_index_port=SQLiteVectorIndexAdapter(),
        )
        workcell_execution = WorkcellExecutionModule(
            SQLiteWorkcellExecutionRepository(database),
            artifact_storage=artifacts,
        )
        external_release_repository = SQLiteExternalReleaseRepository(database)
        external_release = ExternalForwardReleaseCoordinator(
            external_release_repository,
            _SchemaOnlyRemote(),  # type: ignore[arg-type]
        )
        app = create_app(
            coordinator,
            control_plane=control_plane,
            evidence=EvidenceLedger(SQLiteEvidenceRepository(database)),
            settings=SettingsManager(SQLiteSettingsRepository(database)),
            identity=IdentityService(SQLiteIdentityRepository(database)),
            knowledge=WikiService(SQLiteWikiRepository(database)),
            pipeline_catalog=PipelineCatalog(SQLitePipelineRepository(database)),
            agent_profiles=agent_profiles,
            agent_deployments=AgentDeploymentCatalog(
                SQLiteAgentDeploymentRepository(database),
                agent_profiles,
                control_plane,
                providers,
            ),
            provider_manifests=providers,
            agent_runs=AgentRunLedger(database),
            projects=projects,
            knowledge_search=KnowledgeSearchIndex(database),
            knowledge_publications=knowledge_publications,
            knowledge_publisher=KnowledgePublisher(database, knowledge_publications),
            team_templates=team_templates,
            project_workcells=project_workcells,
            workcell_execution=workcell_execution,
            external_release=external_release,
            tenant_knowledge=tenant_knowledge,
            knowledge_indexes=knowledge_indexes,
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
