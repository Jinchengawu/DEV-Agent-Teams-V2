from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_team_os.delivery import (
    DeliveryExecutionSnapshot,
    DeliveryKnowledgeContextSnapshot,
    DeliveryMethodSnapshot,
    DeliveryRun,
    DeliveryWorkspaceSnapshot,
    SQLiteDeliveryRepository,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ExternalGitBinding, ExternalGitWorkspaceManager
from agent_team_os.modules.artifacts import ArtifactReference, ContentAddressedArtifactStorage
from agent_team_os.modules.extensions import ContentAddressedMethodPackStore
from agent_team_os.modules.releases import (
    ExternalReleaseCatalog,
    GitHubPRReceiptCreate,
    SQLiteExternalReleaseRepository,
)
from agent_team_os.modules.workcells import (
    ContentAddressedMethodRuntime,
    MachineVerificationOutcome,
    SQLiteWorkcellExecutionRepository,
    WorkcellAgentInvocation,
    WorkcellAgentOutput,
    WorkcellExecutionModule,
    WorkcellMethodContext,
    WorkcellStageDriver,
)
from agent_team_os.shared.errors import ProductError
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


def test_content_addressed_method_runtime_discovers_explicit_codex_auth_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_file = tmp_path / "operator-auth.json"
    monkeypatch.setenv("AGENT_TEAM_OS_CODEX_AUTH_FILE", str(auth_file))

    runtime = ContentAddressedMethodRuntime.from_environment(
        ContentAddressedMethodPackStore(tmp_path / "method-packs")
    )

    assert runtime.codex_auth_file == auth_file


class DeterministicWorkcellAgent:
    def __init__(self, *, citation_ids: tuple[str, ...] = ()) -> None:
        self.invocations: list[WorkcellAgentInvocation] = []
        self.citation_ids = citation_ids

    async def run(self, invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
        self.invocations.append(invocation)
        if invocation.phase == "planning":
            payload = json.loads(invocation.instruction.split("：", 1)[1])
            return WorkcellAgentOutput(
                runtime_identity="deterministic-workcell",
                content={"assignments": payload},
                knowledge_citation_ids=self.citation_ids,
            )
        if invocation.phase == "synthesis":
            return WorkcellAgentOutput(
                runtime_identity="deterministic-workcell",
                content={"status": "synthesized", "workcell": invocation.workcell_key},
                knowledge_citation_ids=self.citation_ids,
            )
        if invocation.workspace_access == "workspace_write":
            target = invocation.workspace / "design" / "candidate.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Candidate\n\nBMAD UX output.\n", encoding="utf-8")
            return WorkcellAgentOutput(
                runtime_identity="deterministic-workcell",
                content={"changed": "design/candidate.md"},
                knowledge_citation_ids=self.citation_ids,
            )
        return WorkcellAgentOutput(
            runtime_identity="deterministic-workcell",
            content={"blocking_findings": [], "method_id": invocation.method_id},
            knowledge_citation_ids=self.citation_ids,
        )


class RecordingKnowledgeGuard:
    def __init__(self) -> None:
        self.admissions: list[tuple[str, str]] = []
        self.validations: list[tuple[str, ...]] = []

    def admit(self, delivery: DeliveryRun, stage_path: str) -> object:
        self.admissions.append((delivery.id, stage_path))
        return object()

    def validate_citations(
        self,
        delivery: DeliveryRun,
        stage_path: str,
        citation_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        self.admit(delivery, stage_path)
        self.validations.append(citation_ids)
        if citation_ids != ("citation-allowed",):
            raise AssertionError("unexpected citation set")
        return citation_ids


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


def test_running_workcell_agent_is_cancelled_when_authorization_is_revoked() -> None:
    class RevokingGuard:
        def __init__(self) -> None:
            self.admission_count = 0

        def admit(self, _delivery: DeliveryRun, _stage_path: str) -> object:
            self.admission_count += 1
            if self.admission_count >= 2:
                raise ProductError(
                    code="KNOWLEDGE_AUTHORIZATION_REVOKED",
                    title="authorization revoked",
                    detail="authorization epoch changed",
                    repair="start a new Delivery after authorization is restored",
                )
            return object()

        def validate_citations(
            self,
            _delivery: DeliveryRun,
            _stage_path: str,
            citation_ids: tuple[str, ...],
        ) -> tuple[str, ...]:
            return citation_ids

    class CancellableAgent:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def run(self, _invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
            await asyncio.Event().wait()
            raise AssertionError("cancelled Agent should not finish")

        async def cancel(self, agent_run_id: str) -> None:
            self.cancelled.append(agent_run_id)

    async def scenario() -> None:
        agent = CancellableAgent()
        guard = RevokingGuard()
        driver = WorkcellStageDriver(
            kernel=object(),  # type: ignore[arg-type]
            artifacts=object(),  # type: ignore[arg-type]
            methods=object(),  # type: ignore[arg-type]
            agent=agent,
            workspaces=object(),  # type: ignore[arg-type]
            binding_resolver=lambda _workspace_id: ExternalGitBinding(remote_uri="unused"),
            verifier=object(),  # type: ignore[arg-type]
            releases=object(),  # type: ignore[arg-type]
            pull_requests=object(),  # type: ignore[arg-type]
            knowledge_guard=guard,
            revocation_poll_seconds=0.001,
        )
        delivery = DeliveryRun(
            id="delivery-revoked",
            project_id="project-revoked",
            workspace_id="project:project-revoked",
            user_request="consume approved knowledge",
            status="executing",
            version=1,
            resolved_journey_sha256="1" * 64,
            evidence_identity="test",
            planning_identity="test",
        )
        invocation = WorkcellAgentInvocation(
            delivery_id=delivery.id,
            workcell_run_id="workcell-run-revoked",
            agent_run_id="agent-run-revoked",
            phase="delegate",
            workcell_key="design",
            stage_path="design-repair/design",
            instruction="use frozen knowledge",
            workspace=Path("/tmp"),
            workspace_access="artifact_only",
        )

        with pytest.raises(ProductError) as revoked:
            await driver._run_agent(delivery, invocation)

        assert revoked.value.code == "KNOWLEDGE_AUTHORIZATION_REVOKED"
        assert agent.cancelled == ["agent-run-revoked"]

    asyncio.run(scenario())


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
    artifact_storage = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    context_reference = artifact_storage.put_json(
        {
            "contract_id": "knowledge-context-v1",
            "instruction_authority": "none",
            "content": "external data only",
        },
        media_type="application/vnd.agent-team-os.knowledge-context+json",
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
        delivery_execution_snapshot=_delivery_snapshot(
            str(remote),
            base,
            knowledge_reference=context_reference,
        ),
    )
    SQLiteDeliveryRepository(database).save(delivery)
    kernel = WorkcellExecutionModule(
        SQLiteWorkcellExecutionRepository(database),
        artifact_storage=artifact_storage,
    )
    release_repository = SQLiteExternalReleaseRepository(database)
    agent = DeterministicWorkcellAgent(citation_ids=("citation-allowed",))
    knowledge_guard = RecordingKnowledgeGuard()
    pull_requests = DeterministicPRSurface()
    driver = WorkcellStageDriver(
        kernel=kernel,
        artifacts=artifact_storage,
        methods=StaticMethodRuntime(tmp_path / "method-runtime"),
        agent=agent,
        workspaces=ExternalGitWorkspaceManager(tmp_path / "runtime-workspaces"),
        binding_resolver=lambda _workspace_id: ExternalGitBinding(remote_uri=str(remote)),
        verifier=PassedVerifier(),
        releases=ExternalReleaseCatalog(release_repository),
        pull_requests=pull_requests,
        knowledge_guard=knowledge_guard,
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
    assert tree.result is not None
    assert tree.result.knowledge_citation_ids == ("citation-allowed",)
    assert all(item.result_artifact_sha256 is not None for item in tree.attempts)
    delegate_instructions = [
        item.instruction for item in agent.invocations if item.phase == "delegate"
    ]
    planning_instruction = next(
        item.instruction for item in agent.invocations if item.phase == "planning"
    )
    assert "assignments 必须逐项等于" in planning_instruction
    assert "禁止改名为 delegations" in planning_instruction
    assert "禁止添加 depends_on" in planning_instruction
    assert delegate_instructions
    assert all("AC-LOGIN" in item for item in delegate_instructions)
    assert all("external-collaborative" in item for item in delegate_instructions)
    writer_instruction = next(
        item.instruction
        for item in agent.invocations
        if item.workspace_access == "workspace_write"
    )
    assert '"design/**"' in writer_instruction
    assert '"tests/**"' in writer_instruction
    assert "禁止修改允许路径之外的文件" in writer_instruction
    assert len(knowledge_guard.admissions) >= len(agent.invocations)
    assert pull_requests.calls == 2
    assert release_repository.get_pr(outcome.candidate.id) is not None
    assert _git(remote, "rev-parse", "refs/heads/main") == base
    assert (
        _git(
            remote,
            "rev-parse",
            "refs/heads/agent-team-os/delivery-driver/design",
        )
        == outcome.candidate.candidate_revision
    )


def _delivery_snapshot(
    repository_uri: str,
    base: str,
    *,
    knowledge_reference: ArtifactReference | None = None,
) -> DeliveryExecutionSnapshot:
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
    knowledge_binding = {
        "stage_path": "design-repair/design",
        "acwm_artifact_slot": "knowledge-context-v1",
        "acwm_artifact_contract_version": "1.0.0",
        "acwm_artifact_contract_sha256": "d" * 64,
        "retrieval_policy_revision_id": "retrieval-v1",
        "required": True,
        "max_context_bytes": 16_384,
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
        knowledge_context_bindings=(
            {} if knowledge_reference is None else {"design-repair/design": knowledge_binding}
        ),
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
        knowledge_contexts=(
            {}
            if knowledge_reference is None
            else {
                "design-repair/design": DeliveryKnowledgeContextSnapshot(
                    stage_path="design-repair/design",
                    artifact_reference=knowledge_reference,
                    citation_ids=("citation-allowed",),
                    authorization_epoch_hash="c" * 64,
                )
            }
        ),
        knowledge_authorization_stamp=(
            None if knowledge_reference is None else {"authorization_epoch_hash": "c" * 64}
        ),
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
