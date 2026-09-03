from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import agent_team_os.modules.workcells.stage_driver as workcell_stage_driver
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


def test_machine_verifier_disables_python_bytecode_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run_command(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="passed", stderr="")

    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "0")
    monkeypatch.setenv("AGENT_TEAM_OS_GITHUB_TOKEN", "must-not-reach-verification")
    monkeypatch.setenv("FEISHU_APP_SECRET", "must-not-reach-verification")
    monkeypatch.setattr(workcell_stage_driver.subprocess, "run", run_command)
    verifier = workcell_stage_driver.CommandWorkcellMachineVerifier(
        lambda _workcell: (("python", "-m", "unittest"),)
    )

    outcome = asyncio.run(
        verifier.verify(
            workcell_key="design",
            workspace=tmp_path,
            candidate=workcell_stage_driver.ExternalCandidateEvidence(
                base_revision="1" * 40,
                candidate_revision="2" * 40,
                diff_sha256="3" * 64,
                candidate_branch="agent-team-os/delivery/design",
                changed_files=("design/contract.json",),
            ),
        )
    )

    assert outcome.status == "passed"
    assert captured["env"]["PYTHONDONTWRITEBYTECODE"] == "1"  # type: ignore[index]
    assert "AGENT_TEAM_OS_GITHUB_TOKEN" not in captured["env"]  # type: ignore[operator]
    assert "FEISHU_APP_SECRET" not in captured["env"]  # type: ignore[operator]


class DeterministicWorkcellAgent:
    def __init__(
        self,
        *,
        citation_ids: tuple[str, ...] = (),
        blocking_review_calls: frozenset[int] = frozenset(),
        writes_candidate: bool = True,
    ) -> None:
        self.invocations: list[WorkcellAgentInvocation] = []
        self.citation_ids = citation_ids
        self.blocking_review_calls = blocking_review_calls
        self.writes_candidate = writes_candidate
        self.review_calls = 0

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
            if self.writes_candidate:
                target = invocation.workspace / "design" / "candidate.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# Candidate\n\nBMAD UX output.\n", encoding="utf-8")
            return WorkcellAgentOutput(
                runtime_identity="deterministic-workcell",
                content={"changed": self.writes_candidate},
                knowledge_citation_ids=self.citation_ids,
            )
        self.review_calls += 1
        review_evidence = json.loads(
            invocation.instruction.split("Candidate Review Evidence：", 1)[1].splitlines()[0]
        )
        findings = (
            [
                {
                    "code": "DESIGN_REVIEW_BLOCKING",
                    "summary": "Candidate 缺少可验证的契约边界。",
                    "evidence_sha256": "f" * 64,
                }
            ]
            if self.review_calls in self.blocking_review_calls
            else []
        )
        return WorkcellAgentOutput(
            runtime_identity="deterministic-workcell",
            content={
                "reviewed_candidate_sha": review_evidence["candidate_revision"],
                "reviewed_diff_sha256": review_evidence["diff_sha256"],
                "blocking_findings": findings,
                "method_id": invocation.method_id,
            },
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


def test_review_output_requires_explicit_blocking_findings() -> None:
    with pytest.raises(ProductError) as invalid:
        workcell_stage_driver._validated_blocking_findings(
            {
                "verdict": "changes_required",
                "findings": [],
            }
        )

    assert invalid.value.code == "WORKCELL_REVIEW_ARTIFACT_INVALID"


@pytest.mark.parametrize(
    "content",
    [
        {"blocking_findings": []},
        {
            "reviewed_candidate_sha": "a" * 40,
            "reviewed_diff_sha256": "b" * 64,
            "blocking_findings": [],
        },
    ],
)
def test_review_output_must_bind_the_verified_candidate(
    content: dict[str, object],
) -> None:
    with pytest.raises(ProductError) as invalid:
        workcell_stage_driver._validated_review_output(
            content,
            candidate_sha="c" * 40,
            diff_sha256="d" * 64,
        )

    assert invalid.value.code == "WORKCELL_REVIEW_EVIDENCE_MISMATCH"


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


def test_cancelling_workcell_execution_stops_the_active_agent() -> None:
    class StableGuard:
        def admit(self, _delivery: DeliveryRun, _stage_path: str) -> object:
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
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()
            self.cancelled: list[str] = []

        async def run(self, _invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped.set()
            raise AssertionError("cancelled Agent should not finish")

        async def cancel(self, agent_run_id: str) -> None:
            self.cancelled.append(agent_run_id)

    async def scenario() -> None:
        agent = CancellableAgent()
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
            knowledge_guard=StableGuard(),
            revocation_poll_seconds=60,
        )
        delivery = DeliveryRun(
            id="delivery-cancelled",
            project_id="project-cancelled",
            workspace_id="project:project-cancelled",
            user_request="stop the active attempt",
            status="executing",
            version=1,
            resolved_journey_sha256="1" * 64,
            evidence_identity="test",
            planning_identity="test",
        )
        invocation = WorkcellAgentInvocation(
            delivery_id=delivery.id,
            workcell_run_id="workcell-run-cancelled",
            agent_run_id="agent-run-cancelled",
            phase="delegate",
            workcell_key="backend",
            stage_path="backend-repair/backend",
            instruction="run until cancelled",
            workspace=Path("/tmp"),
            workspace_access="workspace_write",
        )

        running = asyncio.create_task(driver._run_agent(delivery, invocation))
        await agent.started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        assert agent.cancelled == ["agent-run-cancelled"]
        await asyncio.wait_for(agent.stopped.wait(), timeout=1)

    asyncio.run(scenario())


def test_cancelled_stage_terminalizes_the_workcell_run() -> None:
    class Run:
        status = "delegating"
        version = 7

    class Tree:
        workcell_run = Run()

    class RecordingKernel:
        def __init__(self) -> None:
            self.cancelled: list[tuple[str, int]] = []

        def tree(self, _run_id: str) -> Tree:
            return Tree()

        def cancel(self, run_id: str, *, expected_version: int) -> Tree:
            self.cancelled.append((run_id, expected_version))
            return Tree()

    kernel = RecordingKernel()

    with pytest.raises(
        asyncio.CancelledError
    ), workcell_stage_driver._terminalize_workcell_failure(  # noqa: SLF001
        kernel,  # type: ignore[arg-type]
        "workcell-run-cancelled",
    ):
        raise asyncio.CancelledError

    assert kernel.cancelled == [("workcell-run-cancelled", 7)]


@pytest.mark.parametrize(
    ("blocking_review_calls", "writes_candidate", "expected_status"),
    [
        (frozenset(), True, "succeeded"),
        (frozenset({1}), True, "repair_required"),
        (frozenset(), False, "repair_required"),
    ],
)
def test_stage_driver_terminalizes_children_and_returns_bounded_repair_outcomes(
    tmp_path: Path,
    blocking_review_calls: frozenset[int],
    writes_candidate: bool,
    expected_status: str,
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
    agent = DeterministicWorkcellAgent(
        citation_ids=("citation-allowed",),
        blocking_review_calls=blocking_review_calls,
        writes_candidate=writes_candidate,
    )
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

    assert outcome.status == expected_status
    tree = kernel.tree(outcome.workcell_run_id)
    assert all(
        item.status in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
        for item in tree.agent_runs
        if item.run_role == "child"
    )
    if not writes_candidate:
        assert tree.workcell_run.status == "failed"
        assert tree.workcell_run.error_code == "EMPTY_WORKSPACE_CANDIDATE"
        assert not tree.reviews
        assert outcome.candidate is None
        writer = next(
            item
            for item in tree.agent_runs
            if item.delegate_purpose == "workspace_write"
        )
        assert len(writer.artifact_envelopes) == 1
        diagnostic = writer.artifact_envelopes[0]
        assert diagnostic.contract_id == "workcell-delegate-diagnostic-v1"
        assert diagnostic.reference is not None
        assert artifact_storage.get_json(diagnostic.reference) == {
            "content": {"changed": False},
            "failure_code": "EMPTY_WORKSPACE_CANDIDATE",
            "failure_detail": "Writer 没有产生相对 Base Revision 的 Candidate Commit。",
            "knowledge_citation_ids": ["citation-allowed"],
            "loop_iteration": 1,
            "method_id": "bmad-ux",
            "runtime_identity": "deterministic-workcell",
            "stage_path": "design-repair/design",
            "workcell_key": "design",
        }
        failed_attempt = next(item for item in tree.attempts if item.agent_run_id == writer.id)
        assert failed_attempt.result_artifact_sha256 == diagnostic.sha256
        return
    assert len(tree.reviews) == 2
    if expected_status == "repair_required":
        assert tree.workcell_run.status == "failed"
        assert tree.workcell_run.error_code == "WORKCELL_BLOCKING_REVIEW"
        assert any(item.blocking_findings for item in tree.reviews)
        assert outcome.candidate is None
        return

    assert outcome.activated_conditions == (
        "design-workcell-passed",
        "release-bundle-verified",
    )
    assert outcome.candidate is not None
    assert outcome.candidate.candidate_branch == "agent-team-os/delivery-driver/design"
    assert outcome.release_bundle is not None
    assert tree.workcell_run.status == "succeeded"
    assert [item.run_role for item in tree.agent_runs].count("main") == 1
    assert [item.run_role for item in tree.agent_runs].count("child") == 3
    assert tree.verification is not None and tree.verification.status == "passed"
    assert tree.result is not None
    assert tree.result.knowledge_citation_ids == ("citation-allowed",)
    assert all(item.result_artifact_sha256 is not None for item in tree.attempts)
    writer = next(
        item for item in tree.agent_runs if item.delegate_purpose == "workspace_write"
    )
    assert [item.contract_id for item in writer.artifact_envelopes] == [
        "workspace-candidate-v2",
        "workspace-candidate-diff-v1",
    ]
    diff_envelope = writer.artifact_envelopes[1]
    assert diff_envelope.artifact_key == "diff"
    assert diff_envelope.reference is not None
    assert b"+# Candidate" in artifact_storage.get_bytes(diff_envelope.reference)
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
    assert "不得自行替换验收 ID" in writer_instruction
    assert "当前 AgentAttempt 的唯一交付目标是 design Workcell" in writer_instruction
    assert "用户目标中的其他 Workcell 条目仅是交付背景" in writer_instruction
    assert "当前为 bounded Loop 第 1 轮" in writer_instruction
    assert "Candidate 不得包含 __pycache__、*.pyc 或 *.pyo" in writer_instruction
    assert "必须在当前 Workspace 产生非空 Git Candidate" in writer_instruction
    synthesis_instruction = next(
        item.instruction for item in agent.invocations if item.phase == "synthesis"
    )
    assert "本 Workcell 冻结执行证据" in synthesis_instruction
    assert "workspace-candidate-diff-v1" in synthesis_instruction
    assert "machine_verification" in synthesis_instruction
    assert "review_artifacts" in synthesis_instruction
    review_instructions = [
        item.instruction
        for item in agent.invocations
        if item.workspace_access == "candidate_read"
    ]
    assert review_instructions
    assert all("blocking_findings" in item for item in review_instructions)
    assert all("Candidate Review Evidence：" in item for item in review_instructions)
    assert all("reviewed_candidate_sha" in item for item in review_instructions)
    assert all("reviewed_diff_sha256" in item for item in review_instructions)
    assert all("缺失该键必须视为无效" in item for item in review_instructions)
    assert all("Frozen Acceptance Contract" in item for item in review_instructions)
    assert all("当前 Workcell Candidate 的合规审查" in item for item in review_instructions)
    assert all("不得因其他 Workcell 尚未交付" in item for item in review_instructions)
    assert all("建议性增强" in item for item in review_instructions)
    assert all("diff_sha256 内容寻址并校验" in item for item in review_instructions)
    assert all("必须审查当前只读 Candidate Workspace" in item for item in review_instructions)
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
