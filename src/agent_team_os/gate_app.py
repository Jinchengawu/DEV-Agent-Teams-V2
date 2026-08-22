"""Deterministic model-boundary app used only by the release browser gate."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .api import create_app
from .delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from .git_delivery import GitCandidateApplier, GitCandidateVerifier, GitCodeExecutor
from .git_sandbox import GitSandbox
from .journey import resolve_backend_delivery_fingerprint
from .release import DeterministicWorkspaceAgent
from .testing import DeterministicPlanningService
from .ui import install_preview_ui


def build_gate_app() -> FastAPI:
    project_root = Path(__file__).parents[2]
    data_dir = Path(os.environ["AGENT_TEAM_OS_DATA_DIR"])
    sandbox = GitSandbox(data_dir / "browser-workspaces")
    sandbox.ensure_initialized()
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=GitCodeExecutor(sandbox, DeterministicWorkspaceAgent()),
        verifier=GitCandidateVerifier(sandbox),
        applier=GitCandidateApplier(sandbox),
        repository=SQLiteDeliveryRepository(data_dir / "browser.sqlite"),
        resolved_journey_sha256=resolve_backend_delivery_fingerprint(project_root / "config"),
    )
    result = create_app(coordinator)
    install_preview_ui(result)
    return result


app = build_gate_app()
