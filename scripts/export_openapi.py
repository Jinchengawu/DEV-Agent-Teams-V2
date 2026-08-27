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
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRunLedger,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
)
from agent_team_os.modules.evidence import EvidenceLedger, SQLiteEvidenceRepository
from agent_team_os.modules.identity import IdentityService, SQLiteIdentityRepository
from agent_team_os.modules.knowledge import (
    KnowledgePublicationLedger,
    KnowledgePublisher,
    KnowledgeSearchIndex,
    SQLiteWikiRepository,
    WikiService,
)
from agent_team_os.modules.orchestration import PipelineCatalog, SQLitePipelineRepository
from agent_team_os.modules.projects import ProjectCatalog, SQLiteProjectRepository
from agent_team_os.modules.settings import SettingsManager, SQLiteSettingsRepository
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


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
        projects = ProjectCatalog(
            SQLiteProjectRepository(database), ProjectGitWorkspaces(Path(directory) / "workspaces")
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
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
