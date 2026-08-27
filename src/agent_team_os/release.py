"""Release gates with real Git evidence and optional live Codex execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from acwm.config import CodexCLIConfig
from pydantic import BaseModel, ConfigDict, Field

from .codex_simulation import ACWMCodexRoleRunner, CodexSimulatedHermesPlanning
from .delivery import (
    DeliveryCoordinator,
    DeliveryRun,
    PlanningService,
    SQLiteDeliveryRepository,
)
from .git_delivery import (
    ACWMCodexWorkspaceAgent,
    GitCandidateApplier,
    GitCandidateVerifier,
    GitCodeExecutor,
    WorkspaceAgent,
)
from .git_sandbox import GitSandbox
from .infrastructure.acwm import ACWMGraphCompiler, ACWMPipelineGraphRuntime
from .infrastructure.database import MigrationRunner
from .journey import (
    load_backend_delivery_definition,
    resolve_backend_delivery_fingerprint,
)
from .modules.delivery import BackendDeliveryPipelinePolicy
from .modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    PipelineRevision,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
)
from .readiness import imported_acwm_revision
from .testing import DeterministicPlanningService


class GateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["deterministic", "live"]
    status: Literal["passed", "failed"]
    fail: int
    warn: int
    skipped: int
    created_at: datetime
    dev_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    acwm_revision: str
    planning_identity: str
    execution_identity: str
    pipeline_revision_id: str | None = None
    pipeline_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    pipeline_run_id: str | None = None
    pipeline_run_status: str | None = None
    candidate_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    diff_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    verification_exit_code: int | None = None
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    browser_e2e: bool = False
    browser_restart_recovery: bool = False
    browser_multi_pipeline_e2e: bool = False
    browser_verified_evidence_count: int = Field(default=0, ge=0)
    browser_candidate_matches_main: bool = False
    error: str | None = None


class CombinedGateStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["unknown", "failed", "passed"]
    code: str
    reason: str


class LatestGateReports(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    deterministic: GateReport | None
    live: GateReport | None
    combined: CombinedGateStatus


class BrowserGateEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    multi_pipeline_e2e: Literal[True]
    verified_evidence_count: int = Field(ge=7)
    candidate_matches_main: Literal[True]


class DeterministicWorkspaceAgent:
    evidence_identity = "deterministic-model-boundary"

    async def run(self, *, instruction: str, workspace: Path) -> str:
        marker = re.sub(r"[^a-z0-9]", "_", workspace.name.lower())
        (workspace / "src" / f"delivery_{marker}.py").write_text(
            "def delivered() -> bool:\n    return True\n", encoding="utf-8"
        )
        (workspace / "tests" / f"test_delivery_{marker}.py").write_text(
            "import unittest\n\n"
            f"from src.delivery_{marker} import delivered\n\n\n"
            "class DeliveryTest(unittest.TestCase):\n"
            "    def test_delivered(self) -> None:\n"
            "        self.assertTrue(delivered())\n",
            encoding="utf-8",
        )
        return "deterministic boundary completed"


class GateBindingResolver:
    """Publish an immutable gate-only binding snapshot without control-plane fixtures."""

    def __init__(self, *, planning_identity: str, execution_identity: str) -> None:
        self.planning_identity = planning_identity
        self.execution_identity = execution_identity

    def snapshot(
        self, capability_ids: tuple[str, ...]
    ) -> dict[str, dict[str, object]]:
        return {
            capability_id: {
                "instance_id": f"release-gate:{capability_id}",
                "instance_version": 1,
                "runtime_type": (
                    "codex-cli" if capability_id == "codex-backend" else "role-turn"
                ),
                "identity": (
                    self.execution_identity
                    if capability_id == "codex-backend"
                    else self.planning_identity
                ),
            }
            for capability_id in capability_ids
        }


def _live_gate_codex_config(
    sandbox: Literal["read-only", "workspace-write"],
) -> CodexCLIConfig:
    return CodexCLIConfig(sandbox=sandbox, timeout_seconds=300)


def _live_gate_planning_timeout_seconds() -> int:
    """覆盖 requirements 与 tasking 两个串行规划回合及收尾开销。"""
    return 2 * _live_gate_codex_config("read-only").timeout_seconds + 30


async def run_gate(*, project_root: Path, report_dir: Path, live: bool) -> GateReport:
    kind = "live" if live else "deterministic"
    created_at = datetime.now(UTC)
    dev_revision = _git_revision(project_root)
    acwm_revision = imported_acwm_revision()
    planning_identity = "codex-simulated-hermes" if live else "deterministic-test"
    execution_identity = "codex-cli" if live else "deterministic-model-boundary"
    initial_status = _git_status(project_root)
    runner: ACWMCodexRoleRunner | None = None
    code_agent: ACWMCodexWorkspaceAgent | None = None
    try:
        if initial_status:
            raise RuntimeError("DEV worktree is dirty; commit the release candidate first")
        with tempfile.TemporaryDirectory(prefix=f"agent-team-os-{kind}-gate-") as directory:
            runtime = Path(directory)
            sandbox = GitSandbox(runtime / "workspaces")
            sandbox.ensure_initialized()
            if live:
                runner = ACWMCodexRoleRunner(
                    workspace=project_root,
                    config=_live_gate_codex_config("read-only"),
                )
                planning: PlanningService = CodexSimulatedHermesPlanning(runner)
                code_agent = ACWMCodexWorkspaceAgent(
                    _live_gate_codex_config("workspace-write")
                )
                agent: WorkspaceAgent = code_agent
            else:
                planning = DeterministicPlanningService()
                agent = DeterministicWorkspaceAgent()
            database = runtime / "agent-team-os.sqlite"
            MigrationRunner(database, project_root / "migrations").migrate()
            repository = SQLiteDeliveryRepository(database)
            coordinator = DeliveryCoordinator(
                planning=planning,
                executor=GitCodeExecutor(sandbox, agent),
                verifier=GitCandidateVerifier(sandbox),
                applier=GitCandidateApplier(sandbox),
                repository=repository,
                resolved_journey_sha256=resolve_backend_delivery_fingerprint(
                    project_root / "config"
                ),
            )
            pipeline_catalog = PipelineCatalog(
                SQLitePipelineRepository(database),
                graph_compiler=ACWMGraphCompiler(),
                binding_resolver=GateBindingResolver(
                    planning_identity=planning_identity,
                    execution_identity=execution_identity,
                ),
                definition_policy=BackendDeliveryPipelinePolicy(),
            )
            pipeline = pipeline_catalog.ensure_builtin_pipeline(
                PipelineCreate(
                    id="backend-delivery",
                    name="内置后端交付闭环",
                    description="需求、计划审批、代码修复 LOOP、候选审批与原子应用",
                    definition=load_backend_delivery_definition(project_root / "config"),
                ),
                actor_id="release-gate",
            )
            if pipeline.active_revision is None:
                raise RuntimeError("built-in Pipeline has no active revision")
            pipeline_revision = pipeline_catalog.get_revision(
                pipeline.id, pipeline.active_revision
            )
            pipeline_runs = PipelineRunLedger(
                SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
            )
            coordinator.configure_pipeline_runtime(pipeline_catalog, pipeline_runs)

            reject_request = (
                "Create a new function rescue_reject_probe() that returns the exact string "
                "'reject-candidate' and add a standard-library unittest for it. The symbol "
                "does not exist yet, so this must produce a non-empty source and test diff."
                if live
                else "Add a bounded health status helper with standard-library tests."
            )
            accept_request = (
                "Create a new function rescue_accept_probe() that returns the exact string "
                "'accept-candidate' and add a standard-library unittest for it. The symbol "
                "does not exist yet, so this must produce a non-empty source and test diff."
                if live
                else "Add a version status helper with standard-library unit tests."
            )

            rejected_plan = await _enqueue_gate_delivery(
                coordinator,
                pipeline_revision,
                workspace_id="backend-demo",
                user_request=reject_request,
                timeout_seconds=(
                    _live_gate_planning_timeout_seconds() if live else 180
                ),
            )
            if rejected_plan.plan_gate is None:
                raise RuntimeError("reject journey did not open the plan gate")
            rejected_candidate = await coordinator.decide_plan(
                rejected_plan.id,
                decision="approve",
                expected_version=rejected_plan.version,
                expected_subject_sha256=rejected_plan.plan_gate.subject_sha256,
            )
            if rejected_candidate.candidate is None or rejected_candidate.candidate_gate is None:
                raise RuntimeError("reject journey did not produce a candidate")
            before_reject = sandbox.main_revision()
            await coordinator.decide_candidate(
                rejected_candidate.id,
                decision="reject",
                expected_version=rejected_candidate.version,
                expected_subject_sha256=rejected_candidate.candidate_gate.subject_sha256,
            )
            if sandbox.main_revision() != before_reject:
                raise RuntimeError("reject changed Main")

            accepted_plan = await _enqueue_gate_delivery(
                coordinator,
                pipeline_revision,
                workspace_id="backend-demo",
                user_request=accept_request,
                timeout_seconds=(
                    _live_gate_planning_timeout_seconds() if live else 180
                ),
            )
            if accepted_plan.plan_gate is None:
                raise RuntimeError("accept journey did not open the plan gate")
            accepted_candidate = await coordinator.decide_plan(
                accepted_plan.id,
                decision="approve",
                expected_version=accepted_plan.version,
                expected_subject_sha256=accepted_plan.plan_gate.subject_sha256,
            )
            if accepted_candidate.candidate is None or accepted_candidate.candidate_gate is None:
                raise RuntimeError("accept journey did not produce a candidate")
            completed = await coordinator.decide_candidate(
                accepted_candidate.id,
                decision="accept",
                expected_version=accepted_candidate.version,
                expected_subject_sha256=accepted_candidate.candidate_gate.subject_sha256,
            )
            if sandbox.main_revision() != accepted_candidate.candidate.candidate_revision:
                raise RuntimeError("Main does not equal displayed candidate")
            restarted = DeliveryCoordinator(
                planning=planning,
                executor=GitCodeExecutor(sandbox, agent),
                verifier=GitCandidateVerifier(sandbox),
                applier=GitCandidateApplier(sandbox),
                repository=SQLiteDeliveryRepository(database),
            )
            restarted_runs = PipelineRunLedger(
                SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
            )
            restarted.configure_pipeline_runtime(pipeline_catalog, restarted_runs)
            await restarted.recover()
            recovered = restarted.get(completed.id)
            if recovered.apply_receipt is None:
                raise RuntimeError("restart lost apply evidence")
            recovered_graph = restarted_runs.get_for_delivery(completed.id)
            if recovered_graph.status != "completed":
                raise RuntimeError("restart lost completed ACWM GraphRun")
            browser_e2e = False
            browser_restart_recovery = False
            browser_multi_pipeline_e2e = False
            browser_verified_evidence_count = 0
            browser_candidate_matches_main = False
            if not live:
                browser_evidence = _run_browser_gate(project_root, runtime)
                browser_e2e = True
                browser_restart_recovery = True
                browser_multi_pipeline_e2e = browser_evidence.multi_pipeline_e2e
                browser_verified_evidence_count = (
                    browser_evidence.verified_evidence_count
                )
                browser_candidate_matches_main = (
                    browser_evidence.candidate_matches_main
                )
            if _git_status(project_root) != initial_status:
                raise RuntimeError("release gate changed the DEV worktree")
            verification = accepted_candidate.verification
            report = _report(
                kind=kind,
                created_at=created_at,
                dev_revision=dev_revision,
                acwm_revision=acwm_revision,
                planning_identity=planning_identity,
                execution_identity=execution_identity,
                pipeline_revision_id=(
                    f"{pipeline_revision.pipeline_id}:{pipeline_revision.revision}"
                ),
                pipeline_fingerprint=pipeline_revision.fingerprint,
                pipeline_run_id=recovered_graph.id,
                pipeline_run_status=recovered_graph.status,
                candidate_revision=accepted_candidate.candidate.candidate_revision,
                diff_sha256=accepted_candidate.candidate.diff_sha256,
                verification_exit_code=(verification.exit_code if verification else None),
                browser_e2e=browser_e2e,
                browser_restart_recovery=browser_restart_recovery,
                browser_multi_pipeline_e2e=browser_multi_pipeline_e2e,
                browser_verified_evidence_count=browser_verified_evidence_count,
                browser_candidate_matches_main=browser_candidate_matches_main,
            )
    except Exception as error:
        report = _report(
            kind=kind,
            created_at=created_at,
            dev_revision=dev_revision,
            acwm_revision=acwm_revision,
            planning_identity=planning_identity,
            execution_identity=execution_identity,
            error=f"{type(error).__name__}: {error}",
        )
    finally:
        if runner is not None:
            await runner.close()
        if code_agent is not None:
            await code_agent.close()
    write_report(report_dir, report)
    return report


def write_report(report_dir: Path, report: GateReport) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = report.created_at.strftime("%Y%m%dT%H%M%SZ")
    stem = f"{timestamp}-{report.kind}"
    payload = report.model_dump(mode="json")
    (report_dir / f"{stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"# Agent-Team-OS {report.kind.title()} Gate",
        "",
        f"- Status: {report.status}",
        f"- FAIL/WARN/skipped: {report.fail}/{report.warn}/{report.skipped}",
        f"- DEV Revision: `{report.dev_revision}`",
        f"- ACWM Revision: `{report.acwm_revision}`",
        f"- Planning: `{report.planning_identity}`",
        f"- Execution: `{report.execution_identity}`",
        f"- Pipeline Revision: `{report.pipeline_revision_id or 'n/a'}`",
        f"- Pipeline Fingerprint: `{report.pipeline_fingerprint or 'n/a'}`",
        f"- Pipeline Run: `{report.pipeline_run_id or 'n/a'}`",
        f"- Pipeline Run Status: `{report.pipeline_run_status or 'n/a'}`",
        f"- Candidate: `{report.candidate_revision or 'n/a'}`",
        f"- Diff SHA-256: `{report.diff_sha256 or 'n/a'}`",
        f"- Evidence SHA-256: `{report.evidence_sha256}`",
        f"- Browser E2E: `{report.browser_e2e}`",
        f"- Browser Restart Recovery: `{report.browser_restart_recovery}`",
        f"- Browser Multi-Pipeline E2E: `{report.browser_multi_pipeline_e2e}`",
        f"- Browser Verified Evidence: `{report.browser_verified_evidence_count}`",
        f"- Browser Main Equals Candidate: `{report.browser_candidate_matches_main}`",
    ]
    if report.error:
        lines.extend(("", f"Error: `{report.error}`"))
    (report_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def latest_reports(report_dir: Path) -> dict[str, GateReport | None]:
    result: dict[str, GateReport | None] = {"deterministic": None, "live": None}
    if not report_dir.exists():
        return result
    for kind in result:
        paths = sorted(report_dir.glob(f"*-{kind}.json"), reverse=True)
        if paths:
            try:
                result[kind] = GateReport.model_validate_json(
                    paths[0].read_text(encoding="utf-8")
                )
            except Exception:
                result[kind] = None
    return result


def combined_gate_status(
    found: dict[str, GateReport | None], *, now: datetime | None = None
) -> CombinedGateStatus:
    deterministic = found.get("deterministic")
    live = found.get("live")
    if deterministic is None or live is None:
        return CombinedGateStatus(
            status="unknown",
            code="RELEASE_GATE_REPORT_MISSING_OR_INVALID",
            reason="双门禁报告缺失或最新报告无法解析。",
        )
    current = now or datetime.now(UTC)
    if any(report.created_at > current + timedelta(minutes=5) for report in (deterministic, live)):
        return CombinedGateStatus(
            status="unknown",
            code="RELEASE_GATE_REPORT_FROM_FUTURE",
            reason="门禁报告时间晚于当前系统时间，无法确认时效性。",
        )
    if any(current - report.created_at > timedelta(hours=24) for report in (deterministic, live)):
        return CombinedGateStatus(
            status="unknown",
            code="RELEASE_GATE_REPORT_EXPIRED",
            reason="至少一份门禁报告已超过 24 小时。",
        )
    if (
        deterministic.dev_revision != live.dev_revision
        or deterministic.acwm_revision != live.acwm_revision
    ):
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_REVISION_MISMATCH",
            reason="确定性门禁与真实门禁不是同一代码和 ACWM Revision。",
        )
    if deterministic.kind != "deterministic" or live.kind != "live":
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_KIND_INVALID",
            reason="门禁报告类型与报告槽位不一致。",
        )
    if not all(_report_evidence_is_valid(report) for report in (deterministic, live)):
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_EVIDENCE_HASH_INVALID",
            reason="门禁报告内容与证据哈希不一致。",
        )
    if (
        deterministic.pipeline_revision_id != live.pipeline_revision_id
        or deterministic.pipeline_fingerprint != live.pipeline_fingerprint
    ):
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_PIPELINE_MISMATCH",
            reason="确定性门禁与真实门禁执行的 Pipeline Revision 或图指纹不一致。",
        )
    if any(
        report.status != "passed" or report.fail or report.warn or report.skipped
        for report in (deterministic, live)
    ):
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_NOT_CLEAN",
            reason="门禁存在失败、警告或跳过项。",
        )
    if any(
        report.candidate_revision is None
        or report.diff_sha256 is None
        or report.verification_exit_code != 0
        or report.pipeline_revision_id is None
        or report.pipeline_fingerprint is None
        or report.pipeline_run_id is None
        or report.pipeline_run_status != "completed"
        for report in (deterministic, live)
    ):
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_DELIVERY_EVIDENCE_INCOMPLETE",
            reason="门禁缺少候选 Revision、Diff 哈希或成功的机器测试证据。",
        )
    if (
        deterministic.planning_identity != "deterministic-test"
        or deterministic.execution_identity != "deterministic-model-boundary"
        or not deterministic.browser_e2e
        or not deterministic.browser_restart_recovery
    ):
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_DETERMINISTIC_IDENTITY_INVALID",
            reason="确定性门禁身份或浏览器重启恢复证据不完整。",
        )
    if (
        not deterministic.browser_multi_pipeline_e2e
        or deterministic.browser_verified_evidence_count < 7
        or not deterministic.browser_candidate_matches_main
    ):
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_BROWSER_EVIDENCE_INCOMPLETE",
            reason="浏览器门禁未证明多流水线、已验证证据和 Main=Candidate。",
        )
    if (
        live.planning_identity != "codex-simulated-hermes"
        or live.execution_identity != "codex-cli"
    ):
        return CombinedGateStatus(
            status="failed",
            code="RELEASE_GATE_LIVE_IDENTITY_INVALID",
            reason="真实门禁没有明确记录 Codex 规划与代码执行身份。",
        )
    return CombinedGateStatus(
        status="passed",
        code="RELEASE_GATE_PASSED",
        reason="同一 Revision 的确定性门禁与真实 Codex 门禁均已通过。",
    )


async def _enqueue_gate_delivery(
    coordinator: DeliveryCoordinator,
    revision: PipelineRevision,
    *,
    workspace_id: str,
    user_request: str,
    timeout_seconds: float = 180,
) -> DeliveryRun:
    created = coordinator.enqueue(
        workspace_id=workspace_id,
        user_request=user_request,
        pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
        journey_binding_snapshot=revision.binding_snapshot,
        resolved_journey_sha256=revision.fingerprint,
        resolved_pipeline_sha256=revision.fingerprint,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = coordinator.get(created.id)
        if current.status == "awaiting_plan_decision":
            return current
        if current.status in {"failed", "cancelled", "rejected"}:
            raise RuntimeError(
                f"Pipeline planning failed: {current.error_code or current.status}"
            )
        await asyncio.sleep(0.05)
    current = coordinator.get(created.id)
    await coordinator.cancel_and_wait(
        current.id,
        expected_version=current.version,
    )
    raise TimeoutError("Pipeline planning gate timed out")


def _report(**values: object) -> GateReport:
    failed = 1 if values.get("error") else 0
    report = GateReport.model_validate(
        {
            **values,
            "status": "failed" if failed else "passed",
            "fail": failed,
            "warn": 0,
            "skipped": 0,
            "evidence_sha256": "0" * 64,
        }
    )
    evidence = _report_evidence_sha256(report)
    return report.model_copy(update={"evidence_sha256": evidence})


def _report_evidence_is_valid(report: GateReport) -> bool:
    return report.evidence_sha256 == _report_evidence_sha256(report)


def _report_evidence_sha256(report: GateReport) -> str:
    payload = report.model_dump(exclude={"evidence_sha256"})
    return hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _git_revision(project_root: Path) -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _git_status(project_root: Path) -> str:
    return subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _run_browser_gate(project_root: Path, runtime: Path) -> BrowserGateEvidence:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        **os.environ,
        "AGENT_TEAM_OS_DATA_DIR": str(runtime / "browser"),
        "AGENT_TEAM_OS_TEST_PASSWORD": "Pipeline-Gate-2026-Verified",
    }
    state = runtime / "browser-state.json"
    checkpoint = runtime / "browser-checkpoint.json"
    server = _start_browser_gate_server(project_root, environment, port)
    try:
        _run_pipeline_browser_phase(
            project_root,
            port,
            phase="execute",
            state=state,
            checkpoint=checkpoint,
        )
    finally:
        _stop_browser_gate_server(server)

    restarted = _start_browser_gate_server(project_root, environment, port)
    try:
        _run_pipeline_browser_phase(
            project_root,
            port,
            phase="recover",
            state=state,
            checkpoint=checkpoint,
            screenshot=runtime / "browser-completed.png",
        )
    finally:
        _stop_browser_gate_server(restarted)
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    return BrowserGateEvidence.model_validate(payload["browser_evidence"])


def _start_browser_gate_server(
    project_root: Path, environment: dict[str, str], port: int
) -> subprocess.Popen[str]:
    server = subprocess.Popen(
        (
            sys.executable,
            "-m",
            "uvicorn",
            "agent_team_os.gate_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ),
        cwd=project_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.poll() is not None:
            stdout, stderr = server.communicate()
            raise RuntimeError(f"browser gate server failed: {stdout}{stderr}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
            return server
        except Exception:
            time.sleep(0.1)
    _stop_browser_gate_server(server)
    raise RuntimeError("browser gate server readiness timed out")


def _run_pipeline_browser_phase(
    project_root: Path,
    port: int,
    *,
    phase: str,
    state: Path,
    checkpoint: Path,
    screenshot: Path | None = None,
) -> None:
    command = [
        sys.executable,
        str(project_root / "scripts" / "browser_pipeline_graph_e2e.py"),
        "--url",
        f"http://127.0.0.1:{port}",
        "--phase",
        phase,
        "--state",
        str(state),
        "--checkpoint",
        str(checkpoint),
    ]
    if screenshot is not None:
        command.extend(("--screenshot", str(screenshot)))
    result = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"browser E2E {phase} phase failed: {result.stdout}{result.stderr}"
        )


def _stop_browser_gate_server(server: subprocess.Popen[str]) -> None:
    if server.poll() is not None:
        return
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
