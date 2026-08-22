"""Runnable V2 preview using Codex role simulation and safe deterministic delivery."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .api import create_app
from .codex_simulation import ACWMCodexRoleRunner, CodexSimulatedHermesPlanning
from .delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from .git_delivery import (
    ACWMCodexWorkspaceAgent,
    GitCandidateApplier,
    GitCandidateVerifier,
    GitCodeExecutor,
)
from .git_sandbox import GitSandbox
from .journey import resolve_backend_delivery_fingerprint
from .readiness import DependencyCheck, ReadinessReport, RuntimeReadiness
from .release import run_gate
from .ui import install_preview_ui


class CodexPreviewReadiness:
    def inspect(self) -> ReadinessReport:
        checks = tuple(
            check
            for check in RuntimeReadiness().inspect().checks
            if check.name in {"python:acwm", "python:agentscope", "codex-login"}
        ) + (
            DependencyCheck(
                name="cli:git",
                status="ready" if shutil.which("git") else "missing",
                repair=None if shutil.which("git") else "Install Git and retry.",
            ),
        )
        return ReadinessReport(
            status="ready" if all(check.status == "ready" for check in checks) else "not_ready",
            checks=checks,
        )


def build_preview_app() -> FastAPI:
    project_root = Path(__file__).parents[2]
    data_dir = Path(os.environ.get("AGENT_TEAM_OS_DATA_DIR", str(project_root / ".agent-team-os")))
    runner = ACWMCodexRoleRunner(workspace=project_root)
    code_agent = ACWMCodexWorkspaceAgent()
    sandbox = GitSandbox(data_dir / "workspaces")
    sandbox.ensure_initialized()

    def reset_workspace() -> str:
        active = {
            "queued",
            "planning",
            "awaiting_plan_decision",
            "executing",
            "verifying",
            "awaiting_candidate_decision",
            "applying",
        }
        if any(delivery.status in active for delivery in coordinator.list()):
            from .delivery import DeliveryStateConflictError

            raise DeliveryStateConflictError("cannot reset while a Delivery is active")
        return sandbox.reset()

    coordinator = DeliveryCoordinator(
        planning=CodexSimulatedHermesPlanning(runner),
        executor=GitCodeExecutor(sandbox, code_agent),
        verifier=GitCandidateVerifier(sandbox),
        applier=GitCandidateApplier(sandbox),
        repository=SQLiteDeliveryRepository(data_dir / "preview.sqlite"),
        resolved_journey_sha256=resolve_backend_delivery_fingerprint(project_root / "config"),
    )
    app = create_app(
        coordinator,
        readiness=CodexPreviewReadiness(),
        report_dir=data_dir / "reports",
        workspace_reset=reset_workspace,
    )
    install_preview_ui(app)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await coordinator.recover()
        try:
            yield
        finally:
            await runner.close()
            await code_agent.close()

    app.router.lifespan_context = lifespan

    return app


app = build_preview_app()


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-team-os")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("demo")
    gate = subcommands.add_parser("gate")
    gate.add_argument("--live", action="store_true")
    subcommands.add_parser("release")
    arguments = parser.parse_args()
    command = arguments.command or "demo"
    project_root = Path(__file__).parents[2]
    data_dir = Path(os.environ.get("AGENT_TEAM_OS_DATA_DIR", str(project_root / ".agent-team-os")))
    if command in {"gate", "release"}:

        async def execute_gates() -> int:
            modes = [False, True] if command == "release" else [bool(arguments.live)]
            reports = []
            for live in modes:
                report = await run_gate(
                    project_root=project_root,
                    report_dir=data_dir / "reports",
                    live=live,
                )
                reports.append(report)
                print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
            return 0 if all(report.status == "passed" for report in reports) else 1

        raise SystemExit(asyncio.run(execute_gates()))
    readiness = CodexPreviewReadiness().inspect()
    if readiness.status != "ready":
        print(readiness.model_dump_json(indent=2))
        raise SystemExit(2)
    port = int(os.environ.get("AGENT_TEAM_OS_PORT", "8080"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
