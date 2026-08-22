"""Deterministic model-boundary app used only by the release browser gate."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .api import create_app
from .control_plane import ControlPlaneService
from .delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from .git_delivery import GitCandidateApplier, GitCandidateVerifier, GitCodeExecutor
from .git_sandbox import GitSandbox
from .infrastructure.database import MigrationRunner
from .journey import resolve_backend_delivery_fingerprint
from .modules.evidence import EvidenceLedger, SQLiteEvidenceRepository
from .modules.identity import IdentityService, SQLiteIdentityRepository
from .modules.knowledge import SQLiteWikiRepository, WikiService
from .modules.settings import SettingsManager, SQLiteSettingsRepository
from .release import DeterministicWorkspaceAgent
from .testing import DeterministicPlanningService
from .ui import install_preview_ui


def build_gate_app() -> FastAPI:
    project_root = Path(__file__).parents[2]
    data_dir = Path(os.environ["AGENT_TEAM_OS_DATA_DIR"])
    database = data_dir / "agent-team-os.sqlite"
    MigrationRunner(database, project_root / "migrations").migrate()
    sandbox = GitSandbox(data_dir / "browser-workspaces")
    sandbox.ensure_initialized()
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=GitCodeExecutor(sandbox, DeterministicWorkspaceAgent()),
        verifier=GitCandidateVerifier(sandbox),
        applier=GitCandidateApplier(sandbox),
        repository=SQLiteDeliveryRepository(database),
        resolved_journey_sha256=resolve_backend_delivery_fingerprint(project_root / "config"),
    )
    control_plane = ControlPlaneService(
        database, config_root=project_root / "config"
    )
    control_plane.import_builtin_journey(
        planning_identity="deterministic-test",
        execution_identity="deterministic-model-boundary",
    )
    result = create_app(
        coordinator,
        control_plane=control_plane,
        evidence=EvidenceLedger(SQLiteEvidenceRepository(database)),
        settings=SettingsManager(SQLiteSettingsRepository(database)),
        identity=IdentityService(SQLiteIdentityRepository(database)),
        knowledge=WikiService(SQLiteWikiRepository(database)),
    )
    install_preview_ui(result, project_root / "console" / "dist")
    return result


app = build_gate_app()
