from __future__ import annotations

import asyncio
from pathlib import Path

from agent_team_os.control_plane import ControlPlaneService
from agent_team_os.delivery import (
    DeliveryCoordinator,
    InMemoryDeliveryRepository,
    ProjectExecutionSnapshot,
)
from agent_team_os.git_delivery import (
    GitCandidateApplier,
    GitCandidateVerifier,
    GitCodeExecutor,
)
from agent_team_os.infrastructure.acwm import (
    ACWMGraphCompiler,
    ACWMPipelineGraphRuntime,
    AgentDeploymentBindingResolver,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ProjectGitWorkspaces
from agent_team_os.journey import load_fullstack_delivery_definition
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRunLedger,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
    ensure_builtin_fullstack_agent_deployments,
)
from agent_team_os.modules.delivery import BackendDeliveryPipelinePolicy
from agent_team_os.modules.evidence import (
    EvidenceKind,
    EvidenceLedger,
    SQLiteEvidenceRepository,
)
from agent_team_os.modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
)
from agent_team_os.modules.releases import ReleaseCoordinator, SQLiteReleaseRepository
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.repositories import RepositorySnapshot
from agent_team_os.testing import DeterministicPlanningService


class CodexPlanningAgent(DeterministicPlanningService):
    evidence_identity = "codex-simulated-hermes"


class FourRepositoryAgent:
    evidence_identity = "codex-cli"

    def __init__(self) -> None:
        self.instructions: dict[str, str] = {}

    async def run(self, *, instruction: str, workspace: Path) -> str:
        assert "系统安全策略" in instruction
        role = workspace.parents[1].name
        self.instructions[role] = instruction
        if role == "backend":
            _append(
                workspace / "src/service.py",
                '\n\ndef fullstack_ready() -> str:\n    return "ready"\n',
            )
            _write(
                workspace / "tests/test_fullstack.py",
                "import unittest\nfrom src.service import fullstack_ready\n\n"
                "class FullstackBackendTest(unittest.TestCase):\n"
                "    def test_ready(self) -> None:\n"
                "        self.assertEqual(fullstack_ready(), 'ready')\n",
            )
        elif role == "frontend":
            _append(workspace / "src/app.js", '\ndocument.body.dataset.release = "ready";\n')
            _write(
                workspace / "tests/test_fullstack.py",
                "import unittest\nfrom pathlib import Path\n\n"
                "class FullstackFrontendTest(unittest.TestCase):\n"
                "    def test_release_marker(self) -> None:\n"
                "        self.assertIn('release', Path('src/app.js').read_text())\n",
            )
        elif role == "design":
            _append(workspace / "design/system.md", "\n## Release\n\n状态：ready。\n")
            _write(
                workspace / "tests/test_fullstack.py",
                "import unittest\nfrom pathlib import Path\n\n"
                "class FullstackDesignTest(unittest.TestCase):\n"
                "    def test_release_section(self) -> None:\n"
                "        self.assertIn('## Release', Path('design/system.md').read_text())\n",
            )
        elif role == "qa":
            _write(workspace / "reports/fullstack.md", "# 全栈验收\n\nPASS\n")
            _write(
                workspace / "tests/test_fullstack.py",
                "import unittest\nfrom pathlib import Path\n\n"
                "class FullstackQATest(unittest.TestCase):\n"
                "    def test_report_passed(self) -> None:\n"
                "        self.assertIn('PASS', Path('reports/fullstack.md').read_text())\n",
            )
        else:
            raise AssertionError(f"unexpected repository role: {role}")
        return role


def test_five_role_pipeline_runs_four_git_candidates_and_release_manifest(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = Path(__file__).parents[1]
        database = tmp_path / "agent-team-os.sqlite"
        MigrationRunner(database, root / "migrations").migrate()
        control = ControlPlaneService(database, config_root=root / "config")
        control.import_builtin_journey(
            planning_identity="codex-simulated-hermes",
            execution_identity="codex-cli",
        )
        profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
        providers = ProviderManifestCatalog()
        deployments = AgentDeploymentCatalog(
            SQLiteAgentDeploymentRepository(database), profiles, control, providers
        )
        assignments = ensure_builtin_fullstack_agent_deployments(profiles, deployments)
        catalog = PipelineCatalog(
            SQLitePipelineRepository(database),
            graph_compiler=ACWMGraphCompiler(),
            provider_binding_resolver=AgentDeploymentBindingResolver(deployments, providers),
            definition_policy=BackendDeliveryPipelinePolicy(),
        )
        pipeline = catalog.create_pipeline(
            PipelineCreate(
                id="fullstack-product-delivery-test",
                name="五角色测试流水线",
                definition=load_fullstack_delivery_definition(root / "config"),
                agent_assignments=assignments,
            ),
            created_by="test",
        )
        validated = catalog.validate_draft(
            pipeline.draft.id, expected_version=pipeline.draft.version
        )
        revision = catalog.publish_draft(
            pipeline.draft.id,
            expected_version=validated.version,
            published_by="test",
        )
        workspaces = ProjectGitWorkspaces(tmp_path / "workspaces")
        repositories = tuple(
            _provision_repository(workspaces, role)
            for role in ("backend", "design", "frontend", "qa")
        )
        snapshot = ProjectExecutionSnapshot(
            project_id="pj1",
            project_version=1,
            workspace_id="project:pj1",
            repository_ref="projects/pj1",
            pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
            deployment_ids=tuple(sorted(set(assignments.values()))),
            repositories=repositories,
            repository_set_sha256=sha256_json(
                [item.model_dump(mode="json") for item in repositories]
            ),
        )
        applier = GitCandidateApplier(workspaces)
        workspace_agent = FourRepositoryAgent()
        coordinator = DeliveryCoordinator(
            planning=CodexPlanningAgent(),
            executor=GitCodeExecutor(workspaces, workspace_agent),
            verifier=GitCandidateVerifier(workspaces),
            applier=applier,
            repository=InMemoryDeliveryRepository(),
            resolved_journey_sha256=revision.fingerprint,
        )
        runs = PipelineRunLedger(SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime())
        agent_runs = AgentRunLedger(database)
        coordinator.configure_pipeline_runtime(
            catalog,
            runs,
            agent_runs,
            release_applier=ReleaseCoordinator(SQLiteReleaseRepository(database), applier),
        )
        delivery = coordinator.enqueue(
            workspace_id="project:pj1",
            project_id="pj1",
            project_execution_snapshot=snapshot,
            user_request="交付一个带状态页的前后端功能并完成测试",
            pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
            resolved_provider_bindings=revision.resolved_provider_bindings,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )

        plan = await _wait(coordinator, delivery.id, "awaiting_plan_decision")
        design = await coordinator.decide_plan(
            delivery.id,
            decision="approve",
            expected_version=plan.version,
            expected_subject_sha256=plan.plan_gate.subject_sha256,  # type: ignore[union-attr]
        )
        assert design.status == "awaiting_design_decision"
        release = await coordinator.decide_design(
            delivery.id,
            decision="approve",
            expected_version=design.version,
            expected_subject_sha256=design.design_gate.subject_sha256,  # type: ignore[union-attr]
        )
        assert release.status == "awaiting_candidate_decision"
        assert release.release_bundle is not None
        assert tuple(item.role for item in release.repository_candidates) == (
            "backend",
            "design",
            "frontend",
            "qa",
        )
        completed = await coordinator.decide_candidate(
            delivery.id,
            decision="accept",
            expected_version=release.version,
            expected_subject_sha256=release.candidate_gate.subject_sha256,  # type: ignore[union-attr]
        )

        assert completed.status == "completed"
        assert completed.release_manifest is not None
        assert "已验证的上游候选证据" in workspace_agent.instructions["frontend"]
        assert "[design] Candidate" in workspace_agent.instructions["backend"]
        assert all(
            f"[{role}] Candidate" in workspace_agent.instructions["qa"]
            for role in ("design", "backend", "frontend")
        )
        assert all(
            workspaces.for_workspace(item.workspace_ref).main_revision()
            == item.candidate.candidate_revision
            for item in release.repository_candidates
        )
        evidence = EvidenceLedger(SQLiteEvidenceRepository(database)).sync_delivery(
            completed.model_dump(mode="json")
        )
        assert sum(item.kind == EvidenceKind.CANDIDATE for item in evidence) == 4
        assert sum(item.kind == EvidenceKind.DIFF for item in evidence) == 4
        assert sum(item.kind == EvidenceKind.VERIFICATION for item in evidence) == 4
        assert any(item.kind == EvidenceKind.DESIGN_GATE for item in evidence)
        assert any(item.kind == EvidenceKind.REQUIREMENT for item in evidence)
        assert any(item.kind == EvidenceKind.TASK for item in evidence)
        assert any(item.kind == EvidenceKind.RELEASE_BUNDLE for item in evidence)
        assert any(item.kind == EvidenceKind.RELEASE_MANIFEST for item in evidence)
        candidate_runs = tuple(
            run
            for run in agent_runs.list(completed.id)
            if run.binding_site
            in {
                "design.developer",
                "implementation-repair/backend.developer",
                "implementation-repair/frontend.developer",
                "implementation-repair/qa.developer",
            }
        )
        assert len(candidate_runs) == 4
        assert all(
            run.artifact_envelopes[0].contract_id == "candidate-change-v1"
            for run in candidate_runs
        )

    asyncio.run(scenario())


async def _wait(coordinator: DeliveryCoordinator, delivery_id: str, status: str):
    for _ in range(200):
        delivery = coordinator.get(delivery_id)
        if delivery.status == status:
            return delivery
        if delivery.status == "failed":
            raise AssertionError(f"delivery failed: {delivery.error_code}")
        await asyncio.sleep(0.01)
    raise AssertionError(f"delivery did not reach {status}")


def _provision_repository(workspaces: ProjectGitWorkspaces, role: str) -> RepositorySnapshot:
    workspace_ref = f"project:pj1:{role}"
    repository_ref = f"projects/pj1/{role}"
    revision = workspaces.provision(repository_ref)
    return RepositorySnapshot(
        role=role,  # type: ignore[arg-type]
        workspace_ref=workspace_ref,
        repository_ref=repository_ref,
        seed_revision=revision,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append(path: Path, content: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + content, encoding="utf-8")
