"""Runnable V2 preview using Codex role simulation and safe deterministic delivery."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .api import create_app
from .codex_simulation import ACWMCodexRoleRunner, CodexSimulatedHermesPlanning
from .delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from .readiness import ReadinessReport, RuntimeReadiness
from .testing import (
    DeterministicCandidateApplier,
    DeterministicCandidateVerifier,
    DeterministicCodeExecutor,
)
from .ui import install_preview_ui


class CodexPreviewReadiness:
    def inspect(self) -> ReadinessReport:
        checks = tuple(
            check
            for check in RuntimeReadiness().inspect().checks
            if check.name in {"python:acwm", "python:agentscope", "codex-login"}
        )
        return ReadinessReport(
            status="ready" if all(check.status == "ready" for check in checks) else "not_ready",
            checks=checks,
        )


def build_preview_app() -> FastAPI:
    project_root = Path(__file__).parents[2]
    data_dir = Path(
        os.environ.get("AGENT_TEAM_OS_DATA_DIR", str(project_root / ".agent-team-os"))
    )
    runner = ACWMCodexRoleRunner(workspace=project_root)
    coordinator = DeliveryCoordinator(
        planning=CodexSimulatedHermesPlanning(runner),
        executor=DeterministicCodeExecutor(),
        verifier=DeterministicCandidateVerifier(),
        applier=DeterministicCandidateApplier(),
        repository=SQLiteDeliveryRepository(data_dir / "preview.sqlite"),
    )
    app = create_app(coordinator, readiness=CodexPreviewReadiness())
    install_preview_ui(app)

    @app.on_event("shutdown")
    async def close_runner() -> None:
        await runner.close()

    return app


app = build_preview_app()


def main() -> None:
    port = int(os.environ.get("AGENT_TEAM_OS_PORT", "8080"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
