"""Release gates with real Git evidence and optional live Codex execution."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .codex_simulation import ACWMCodexRoleRunner, CodexSimulatedHermesPlanning
from .delivery import DeliveryCoordinator, PlanningService, SQLiteDeliveryRepository
from .git_delivery import (
    ACWMCodexWorkspaceAgent,
    GitCandidateApplier,
    GitCandidateVerifier,
    GitCodeExecutor,
    WorkspaceAgent,
)
from .git_sandbox import GitSandbox
from .journey import resolve_backend_delivery_fingerprint
from .testing import DeterministicPlanningService


class GateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    status: str
    fail: int
    warn: int
    skipped: int
    created_at: datetime
    dev_revision: str
    acwm_revision: str
    planning_identity: str
    execution_identity: str
    candidate_revision: str | None = None
    diff_sha256: str | None = None
    verification_exit_code: int | None = None
    evidence_sha256: str
    error: str | None = None


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


async def run_gate(*, project_root: Path, report_dir: Path, live: bool) -> GateReport:
    kind = "live" if live else "deterministic"
    created_at = datetime.now(UTC)
    dev_revision = _git_revision(project_root)
    acwm_revision = _acwm_revision()
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
                runner = ACWMCodexRoleRunner(workspace=project_root)
                planning: PlanningService = CodexSimulatedHermesPlanning(runner)
                code_agent = ACWMCodexWorkspaceAgent()
                agent: WorkspaceAgent = code_agent
            else:
                planning = DeterministicPlanningService()
                agent = DeterministicWorkspaceAgent()
            repository = SQLiteDeliveryRepository(runtime / "deliveries.sqlite")
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

            rejected_plan = await coordinator.submit(
                workspace_id="backend-demo",
                user_request="Add a bounded health status helper with standard-library tests.",
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

            accepted_plan = await coordinator.submit(
                workspace_id="backend-demo",
                user_request="Add a version status helper with standard-library unit tests.",
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
                repository=SQLiteDeliveryRepository(runtime / "deliveries.sqlite"),
            )
            recovered = restarted.get(completed.id)
            if recovered.apply_receipt is None:
                raise RuntimeError("restart lost apply evidence")
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
                candidate_revision=accepted_candidate.candidate.candidate_revision,
                diff_sha256=accepted_candidate.candidate.diff_sha256,
                verification_exit_code=(verification.exit_code if verification else None),
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
        f"- Candidate: `{report.candidate_revision or 'n/a'}`",
        f"- Diff SHA-256: `{report.diff_sha256 or 'n/a'}`",
        f"- Evidence SHA-256: `{report.evidence_sha256}`",
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
        for path in paths:
            try:
                result[kind] = GateReport.model_validate_json(path.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
    return result


def _report(**values: object) -> GateReport:
    failed = 1 if values.get("error") else 0
    payload = {
        **values,
        "status": "failed" if failed else "passed",
        "fail": failed,
        "warn": 0,
        "skipped": 0,
    }
    evidence = hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return GateReport.model_validate({**payload, "evidence_sha256": evidence})


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


def _acwm_revision() -> str:
    direct_url = importlib.metadata.distribution("agent-capability-workflow-matrix").read_text(
        "direct_url.json"
    )
    if direct_url:
        data = json.loads(direct_url)
        commit = data.get("vcs_info", {}).get("commit_id")
        if commit:
            return str(commit)
    return importlib.metadata.version("agent-capability-workflow-matrix")
