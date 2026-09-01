from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_team_os.delivery import (
    DeliveryExecutionSnapshot,
    DeliveryMethodSnapshot,
    DeliveryRun,
    DeliveryWorkspaceSnapshot,
    SQLiteDeliveryRepository,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ExternalGitBinding, ExternalGitWorkspaceManager
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.releases import (
    ExternalReleaseCatalog,
    GitHubPRReceiptCreate,
    SQLiteExternalReleaseRepository,
)
from agent_team_os.modules.workcells import (
    MachineVerificationOutcome,
    SQLiteWorkcellExecutionRepository,
    WorkcellAgentInvocation,
    WorkcellAgentOutput,
    WorkcellExecutionModule,
    WorkcellMethodContext,
    WorkcellStageDriver,
)
from agent_team_os.shared.hashes import sha256_json


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


class DeterministicWorkcellAgent:
    def __init__(self) -> None:
        self.invocations: list[WorkcellAgentInvocation] = []

    async def run(self, invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
        self.invocations.append(invocation)
        if invocation.phase == "planning":
            payload = json.loads(invocation.instruction.split("：", 1)[1])
            return WorkcellAgentOutput(
                runtime_identity="deterministic-workcell",
                content={"assignments": payload},
            )
        if invocation.phase == "synthesis":
            return WorkcellAgentOutput(
                runtime_identity="deterministic-workcell",
                content={"status": "synthesized", "workcell": invocation.workcell_key},
            )
        if invocation.workspace_access == "workspace_write":
            target = invocation.workspace / "design" / "candidate.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Candidate\n\nBMAD UX output.\n", encoding="utf-8")
            return WorkcellAgentOutput(
                runtime_identity="deterministic-workcell",
                content={"changed": "design/candidate.md"},
            )
        return WorkcellAgentOutput(
            runtime_identity="deterministic-workcell",
            content={"blocking_findings": [], "method_id": invocation.method_id},
        )


class PassedVerifier:
    async def verify(self, **_kwargs: object) -> MachineVerificationOutcome:
        return MachineVerificationOutcome(
            status="passed",
            report={"commands": [{"command": ["fixture"], "exit_code": 0}]},
        )


class DeterministicPRSurface:
    def __init__(self) -> None:
        self.calls = 0

    def ensure(self, candidate, _binding) -> GitHubPRReceiptCreate:
        self.calls += 1
        return GitHubPRReceiptCreate(
            pull_request_id=11,
            url="https://github.com/deterministic/design/pull/11",
            head_branch=candidate.candidate_branch,
            head_candidate_sha=candidate.candidate_revision,
            state="open",
        )


def test_stage_driver_creates_observable_main_children_candidate_reviews_and_pr(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    remote, base = _remote(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO projects(
            id,slug,name,description,lifecycle_status,version,created_by,created_at,updated_at)
            VALUES('project-driver','project-driver','Driver','', 'active',1,'test',
            CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO workspace_bindings(
            id,project_id,kind,adapter_type,repository_uri,credential_reference,status,
            verification_sha256,verification_json,error_code,version,created_at,updated_at)
            VALUES('workspace-design','project-driver','git_repository_v1','external-git',?,
            NULL,'ready',?,'{}',NULL,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (str(remote), "b" * 64),
        )
    delivery = DeliveryRun(
        id="delivery-driver",
        project_id="project-driver",
        workspace_id="project:project-driver",
        user_request="设计一个登录页",
        status="executing",
        version=1,
        pipeline_run_id="pipeline-run-driver",
        pipeline_revision_id="agent-workcell-delivery:1",
        resolved_pipeline_sha256="1" * 64,
        resolved_journey_sha256="2" * 64,
        evidence_identity="deterministic-workcell",
        planning_identity="deterministic-workcell",
        delivery_execution_snapshot=_delivery_snapshot(str(remote), base),
    )
    SQLiteDeliveryRepository(database).save(delivery)
    artifact_storage = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    kernel = WorkcellExecutionModule(
        SQLiteWorkcellExecutionRepository(database),
        artifact_storage=artifact_storage,
    )
    release_repository = SQLiteExternalReleaseRepository(database)
    agent = DeterministicWorkcellAgent()
    pull_requests = DeterministicPRSurface()
    driver = WorkcellStageDriver(
        kernel=kernel,
        artifacts=artifact_storage,
        methods=StaticMethodRuntime(tmp_path / "method-runtime"),
        agent=agent,
        workspaces=ExternalGitWorkspaceManager(tmp_path / "runtime-workspaces"),
        binding_resolver=lambda _workspace_id: ExternalGitBinding(
            remote_uri=str(remote)
        ),
        verifier=PassedVerifier(),
        releases=ExternalReleaseCatalog(release_repository),
        pull_requests=pull_requests,
    )
    requirements = artifact_storage.put_json(
        {
            "artifact_kind": "requirements",
            "acceptance_criteria": ["AC-LOGIN"],
        }
    )

    outcome = __import__("asyncio").run(
        driver.execute(
            delivery,
            stage_path="design-repair/design",
            stage_attempt_id="design-attempt-1",
            loop_iteration=1,
            input_artifacts=(requirements,),
        )
    )

    assert outcome.status == "succeeded"
    assert outcome.activated_conditions == (
        "design-workcell-passed",
        "release-bundle-verified",
    )
    assert outcome.candidate is not None
    assert outcome.candidate.candidate_branch == "agent-team-os/delivery-driver/design"
    assert outcome.release_bundle is not None
    tree = kernel.tree(outcome.workcell_run_id)
    assert tree.workcell_run.status == "succeeded"
    assert [item.run_role for item in tree.agent_runs].count("main") == 1
    assert [item.run_role for item in tree.agent_runs].count("child") == 3
    assert len(tree.reviews) == 2
    assert tree.verification is not None and tree.verification.status == "passed"
    assert all(item.result_artifact_sha256 is not None for item in tree.attempts)
    delegate_instructions = [
        item.instruction for item in agent.invocations if item.phase == "delegate"
    ]
    assert delegate_instructions
    assert all("AC-LOGIN" in item for item in delegate_instructions)
    assert pull_requests.calls == 2
    assert release_repository.get_pr(outcome.candidate.id) is not None
    assert _git(remote, "rev-parse", "refs/heads/main") == base
    assert _git(
        remote,
        "rev-parse",
        "refs/heads/agent-team-os/delivery-driver/design",
    ) == outcome.candidate.candidate_revision


def _delivery_snapshot(repository_uri: str, base: str) -> DeliveryExecutionSnapshot:
    methods = DeliveryMethodSnapshot(
        snapshot_id="method-pack-set-v1:test",
        qualification_sha256="3" * 64,
        packages=(),
        method_entries={
            "bmad-ux": {"qualification_sha256": "4" * 64},
            "bmad-review": {"qualification_sha256": "4" * 64},
        },
    )
    stage = {
        "workcell_key": "design",
        "slot_bindings": {
            slot: f"design-repair/design.{slot}"
            for slot in ("main", "delegate_1", "delegate_2", "delegate_3")
        },
        "delegate_methods": {
            "delegate_1": "bmad-ux",
            "delegate_2": "bmad-review",
            "delegate_3": "bmad-review",
        },
        "delegate_purposes": {
            "delegate_1": "workspace_write",
            "delegate_2": "review",
            "delegate_3": "review",
        },
    }
    providers = {
        f"design-repair/design.{slot}": {
            "deployment": {"id": f"deployment-{slot}"},
            "binding": {"binding_fingerprint": character * 64},
            "runtime_identity": "deterministic-workcell",
        }
        for slot, character in (
            ("main", "5"),
            ("delegate_1", "6"),
            ("delegate_2", "7"),
            ("delegate_3", "8"),
        )
    }
    team_definition = {
        "workcell_key": "design",
        "name": "Design",
        "responsibility": "UI design",
        "primary_workspace": {"kind": "git_repository_v1"},
        "delegate_purposes": ["workspace_write", "review"],
        "delegation_policy": {
            "max_children": 3,
            "max_concurrency": 2,
            "max_writers": 1,
            "max_depth": 1,
            "wall_clock_budget_seconds": 900,
        },
    }
    payload = {
        "project_id": "project-driver",
        "team": team_definition,
        "stage": stage,
        "providers": providers,
        "repository_uri": repository_uri,
        "base": base,
        "methods": methods.model_dump(mode="json"),
    }
    return DeliveryExecutionSnapshot(
        project_id="project-driver",
        project_version=1,
        team_template_revision_id="software-delivery-team:1",
        team_template_sha256="9" * 64,
        team_workcells={"design": team_definition},
        pipeline_revision_id="agent-workcell-delivery:1",
        pipeline_revision_sha256="a" * 64,
        workcell_stage_map={"design-repair/design": stage},
        release_contract_snapshot=("design",),
        resolved_provider_bindings=providers,
        workspaces=(
            DeliveryWorkspaceSnapshot(
                workcell_key="design",
                workspace_binding_id="workspace-design",
                kind="git_repository_v1",
                adapter_type="external-git",
                repository_uri=repository_uri,
                base_revision=base,
                verification_sha256="b" * 64,
            ),
        ),
        method_snapshot=methods,
        snapshot_sha256=sha256_json(payload),
    )


def _remote(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "design.git"
    seed = tmp_path / "seed"
    _run("git", "init", "--bare", "--initial-branch=main", str(remote))
    _run("git", "init", "--initial-branch=main", str(seed))
    (seed / "design").mkdir()
    (seed / "tests").mkdir()
    (seed / "design" / "system.md").write_text("# Seed\n", encoding="utf-8")
    (seed / "tests" / "test_seed.py").write_text("def test_seed():\n    assert True\n")
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
