from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_team_os.control_plane import ControlPlaneService, HealthResult
from agent_team_os.delivery import (
    DeliveryExecutionSnapshot,
    DeliveryMethodSnapshot,
    DeliveryRun,
    DeliveryWorkspaceSnapshot,
    SQLiteDeliveryRepository,
)
from agent_team_os.infrastructure.acwm import (
    ACWMGraphCompiler,
    ACWMPipelineGraphRuntime,
    AgentDeploymentBindingResolver,
    ControlPlaneBindingResolver,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import (
    ExternalForwardGitRemote,
    ExternalGitBinding,
    ExternalGitWorkspaceManager,
)
from agent_team_os.journey import load_agent_workcell_delivery_definition
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
    ensure_builtin_workcell_agent_deployments,
)
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.delivery import (
    BackendDeliveryPipelinePolicy,
    PipelineExecutionModule,
)
from agent_team_os.modules.extensions import (
    RuntimeExtensionCatalog,
    SQLiteRuntimeExtensionRepository,
)
from agent_team_os.modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
)
from agent_team_os.modules.releases import (
    ExternalForwardReleaseCoordinator,
    ExternalReleaseCatalog,
    GitHubPRReceiptCreate,
    SQLiteExternalReleaseRepository,
)
from agent_team_os.modules.workcells import (
    CommandWorkcellMachineVerifier,
    SQLiteTeamTemplateRepository,
    SQLiteWorkcellExecutionRepository,
    TeamTemplateCatalog,
    WorkcellAgentInvocation,
    WorkcellAgentOutput,
    WorkcellExecutionModule,
    WorkcellMethodContext,
    WorkcellStageDriver,
    builtin_release_contract,
    builtin_workcell_stage_map,
)
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class ReadyProbe:
    async def check(
        self,
        runtime_type: str,
        connection: dict[str, str],
    ) -> HealthResult:
        del runtime_type, connection
        return HealthResult(status="ready", identity="deterministic-test", latency_ms=1)


class StaticMethodRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def activate(self, snapshot: DeliveryMethodSnapshot) -> Iterator[WorkcellMethodContext]:
        self.root.mkdir(parents=True, exist_ok=True)
        yield WorkcellMethodContext(
            control_workspace=self.root,
            environment={"CODEX_HOME": str(self.root / "codex-home")},
            method_entries=snapshot.method_entries,
        )


class FourRepositoryAgent:
    async def run(self, invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
        if invocation.phase == "planning":
            content = {
                "assignments": json.loads(invocation.instruction.split("：", 1)[1])
            }
        elif invocation.phase == "synthesis":
            content = {"status": "passed", "workcell": invocation.workcell_key}
        elif invocation.workspace_access == "workspace_write":
            source, test = {
                "design": ("design/candidate.md", "tests/test_design_candidate.py"),
                "frontend": ("src/candidate.txt", "tests/test_frontend_candidate.py"),
                "backend": ("src/candidate.txt", "tests/test_backend_candidate.py"),
                "qa": ("reports/candidate.md", "tests/test_qa_candidate.py"),
            }[invocation.workcell_key]
            source_path = invocation.workspace / source
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                f"{invocation.workcell_key} candidate\n",
                encoding="utf-8",
            )
            test_path = invocation.workspace / test
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(
                "import unittest\n\n"
                "class CandidateTest(unittest.TestCase):\n"
                "    def test_candidate(self) -> None:\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            content = {"changed_files": [source, test]}
        elif invocation.workspace_access == "candidate_read":
            content = {"blocking_findings": [], "method_id": invocation.method_id}
        else:
            content = {
                "artifact": invocation.method_id,
                "acceptance_coverage": ["AC-1"],
            }
        return WorkcellAgentOutput(
            runtime_identity="deterministic-test",
            content=content,
        )


class PRSurface:
    def ensure(self, candidate, _binding) -> GitHubPRReceiptCreate:
        ordinal = {"design": 1, "frontend": 2, "backend": 3, "qa": 4}[
            candidate.workcell_key
        ]
        return GitHubPRReceiptCreate(
            pull_request_id=ordinal,
            url=f"https://github.com/deterministic/{candidate.workcell_key}/pull/{ordinal}",
            head_branch=candidate.candidate_branch,
            head_candidate_sha=candidate.candidate_revision,
            state="open",
        )


def test_four_repository_workcell_pipeline_and_forward_only_release(
    tmp_path: Path,
) -> None:
    asyncio.run(_run_four_repository_pipeline(tmp_path))


async def _run_four_repository_pipeline(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, root / "migrations").migrate()
    remotes = {role: _remote(tmp_path, role) for role in builtin_release_contract()}
    _project_and_workspaces(database, remotes)
    control_plane = ControlPlaneService(
        database,
        config_root=root / "config",
        probe=ReadyProbe(),
    )
    control_plane.import_builtin_journey(
        planning_identity="deterministic-test",
        execution_identity="deterministic-test",
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
        planning_instance_id="builtin:deterministic-test",
        execution_instance_id="builtin:deterministic-test",
    )
    pipelines = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=ACWMGraphCompiler(),
        binding_resolver=ControlPlaneBindingResolver(
            control_plane.get_binding,
            control_plane.get_instance,
        ),
        provider_binding_resolver=AgentDeploymentBindingResolver(deployments, providers),
        definition_policy=BackendDeliveryPipelinePolicy(),
    )
    created = pipelines.create_pipeline(
        PipelineCreate(
            id="agent-workcell-delivery",
            name="Agent Workcell Delivery",
            definition=load_agent_workcell_delivery_definition(root / "config"),
            agent_assignments=assignments,
            workcell_stage_map=builtin_workcell_stage_map(),
            release_contract_snapshot=builtin_release_contract(),
        ),
        created_by="system",
    )
    validated = pipelines.validate_draft(
        created.draft.id,
        expected_version=created.draft.version,
    )
    revision = pipelines.publish_draft(
        validated.id,
        expected_version=validated.version,
        published_by="system",
    )
    delivery = DeliveryRun(
        id="delivery-four-repositories",
        project_id="project-four-repositories",
        workspace_id="project:project-four-repositories",
        user_request="实现四仓可验证交付",
        status="queued",
        version=1,
        pipeline_run_id="pipeline-run-four-repositories",
        pipeline_revision_id="agent-workcell-delivery:1",
        resolved_pipeline_sha256=revision.fingerprint,
        resolved_journey_sha256="1" * 64,
        resolved_provider_bindings=revision.resolved_provider_bindings,
        delivery_execution_snapshot=_snapshot(database, revision, remotes),
        evidence_identity="deterministic-test",
        planning_identity="deterministic-test",
    )
    delivery_repository = SQLiteDeliveryRepository(database)
    delivery_repository.save(delivery)
    artifacts = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    kernel = WorkcellExecutionModule(
        SQLiteWorkcellExecutionRepository(database),
        artifact_storage=artifacts,
    )
    release_repository = SQLiteExternalReleaseRepository(database)
    bindings = {
        f"workspace-{role}": ExternalGitBinding(remote_uri=str(remote))
        for role, (remote, _base) in remotes.items()
    }
    driver = WorkcellStageDriver(
        kernel=kernel,
        artifacts=artifacts,
        methods=StaticMethodRuntime(tmp_path / "method-runtime"),
        agent=FourRepositoryAgent(),
        workspaces=ExternalGitWorkspaceManager(tmp_path / "workcell-runtime"),
        binding_resolver=bindings.__getitem__,
        verifier=CommandWorkcellMachineVerifier(
            lambda _workcell: (
                (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"),
            )
        ),
        releases=ExternalReleaseCatalog(release_repository),
        pull_requests=PRSurface(),
    )
    external_release = ExternalForwardReleaseCoordinator(
        release_repository,
        ExternalForwardGitRemote(bindings.__getitem__),
    )
    execution = PipelineExecutionModule(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        verifier=None,
        applier=None,
        repository=delivery_repository,
        catalog=pipelines,
        runs=PipelineRunLedger(
            SQLitePipelineRunRepository(database),
            ACWMPipelineGraphRuntime(),
        ),
        workcell_stage_driver=driver,
        external_release=external_release,
    )
    execution.start(delivery)
    await execution.advance(delivery.id)
    planned = _delivery(delivery_repository, delivery.id)
    assert planned.status == "awaiting_plan_decision"
    assert planned.plan_gate is not None
    await execution.decide_plan(
        planned,
        decision="approve",
        expected_version=planned.version,
        expected_subject_sha256=planned.plan_gate.subject_sha256,
    )
    designed = _delivery(delivery_repository, delivery.id)
    assert designed.status == "awaiting_design_decision"
    assert designed.design_gate is not None
    await execution.decide_design(
        designed,
        decision="approve",
        expected_version=designed.version,
        expected_subject_sha256=designed.design_gate.subject_sha256,
    )
    gated = _delivery(delivery_repository, delivery.id)
    assert gated.status == "awaiting_candidate_decision", (
        gated.error_code,
        [
            (tree.workcell_run.stage_path, tree.workcell_run.status, tree.workcell_run.error_code)
            for tree in kernel.list_delivery(delivery.id)
        ],
    )
    assert gated.candidate_gate is not None
    assert set(gated.workcell_candidates) == set(builtin_release_contract())
    assert gated.release_bundle_v2_sha256 is not None
    assert len(kernel.list_delivery(delivery.id)) == 5

    await execution.decide_candidate(
        gated,
        decision="accept",
        expected_version=gated.version,
        expected_subject_sha256=gated.candidate_gate.subject_sha256,
    )

    completed = _delivery(delivery_repository, delivery.id)
    manifest = release_repository.get_manifest(delivery.project_id)
    assert completed.status == "completed"
    assert manifest is not None
    assert completed.release_manifest_v2_sha256 == manifest.manifest_sha256
    assert len(manifest.repositories) == 4
    for role, (remote, _base) in remotes.items():
        assert _git(remote, "rev-parse", "refs/heads/main") == (
            completed.workcell_candidates[role].candidate_revision
        )


def _snapshot(database: Path, revision, remotes) -> DeliveryExecutionSnapshot:
    team = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database)).get_revision(
        "software-delivery-team",
        1,
    )
    methods = {
        method
        for stage in revision.workcell_stage_map.values()
        for method in stage.delegate_methods.values()
    }
    method_snapshot = DeliveryMethodSnapshot(
        snapshot_id="method-pack-set-v1:deterministic",
        qualification_sha256="2" * 64,
        packages=(),
        method_entries={method: {"fixture": True} for method in methods},
    )
    workspaces = tuple(
        DeliveryWorkspaceSnapshot(
            workcell_key=role,
            workspace_binding_id=f"workspace-{role}",
            kind="git_repository_v1",
            adapter_type="external-git",
            repository_uri=str(remote),
            base_revision=base,
            verification_sha256=character * 64,
        )
        for (role, (remote, base)), character in zip(
            remotes.items(),
            "3456",
            strict=True,
        )
    )
    payload = {
        "team": team.sha256,
        "pipeline": revision.fingerprint,
        "workspaces": [item.model_dump(mode="json") for item in workspaces],
    }
    return DeliveryExecutionSnapshot(
        project_id="project-four-repositories",
        project_version=1,
        team_template_revision_id="software-delivery-team:1",
        team_template_sha256=team.sha256,
        team_workcells={
            item.workcell_key: item.model_dump(mode="json") for item in team.workcells
        },
        pipeline_revision_id="agent-workcell-delivery:1",
        pipeline_revision_sha256=revision.fingerprint,
        workcell_stage_map={
            key: value.model_dump(mode="json")
            for key, value in revision.workcell_stage_map.items()
        },
        release_contract_snapshot=revision.release_contract_snapshot,
        resolved_provider_bindings=revision.resolved_provider_bindings,
        workspaces=workspaces,
        method_snapshot=method_snapshot,
        snapshot_sha256=sha256_json(payload),
    )


def _project_and_workspaces(database: Path, remotes) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO projects(
            id,slug,name,description,lifecycle_status,version,created_by,created_at,updated_at)
            VALUES('project-four-repositories','project-four-repositories','Four repos','',
            'active',1,'test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        for role, (remote, _base) in remotes.items():
            connection.execute(
                """INSERT INTO workspace_bindings(
                id,project_id,kind,adapter_type,repository_uri,credential_reference,status,
                verification_sha256,verification_json,error_code,version,created_at,updated_at)
                VALUES(?, 'project-four-repositories','git_repository_v1','external-git',?,
                NULL,'ready',?,'{}',NULL,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (f"workspace-{role}", str(remote), sha256_json({"role": role})),
            )


def _remote(tmp_path: Path, role: str) -> tuple[Path, str]:
    remote = tmp_path / f"{role}.git"
    seed = tmp_path / f"{role}-seed"
    _run("git", "init", "--bare", "--initial-branch=main", str(remote))
    _run("git", "init", "--initial-branch=main", str(seed))
    source = {
        "design": "design/system.md",
        "frontend": "src/index.txt",
        "backend": "src/service.py",
        "qa": "reports/README.md",
    }[role]
    source_path = seed / source
    source_path.parent.mkdir(parents=True)
    source_path.write_text(f"{role} seed\n", encoding="utf-8")
    tests = seed / "tests"
    tests.mkdir()
    (tests / "test_seed.py").write_text(
        "import unittest\n\n"
        "class SeedTest(unittest.TestCase):\n"
        "    def test_seed(self) -> None:\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    _run("git", "add", "--all", cwd=seed)
    _run(
        "git",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "seed",
        cwd=seed,
    )
    _run("git", "remote", "add", "origin", str(remote), cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)
    return remote, _git(remote, "rev-parse", "refs/heads/main")


def _delivery(repository: SQLiteDeliveryRepository, delivery_id: str) -> DeliveryRun:
    delivery = repository.get(delivery_id)
    assert delivery is not None
    return delivery


def _git(repository: Path, *arguments: str) -> str:
    return _run("git", "--git-dir", str(repository), *arguments).strip()


def _run(*arguments: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
