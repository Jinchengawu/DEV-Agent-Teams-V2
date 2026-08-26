"""Runnable V2 preview using Codex role simulation and safe deterministic delivery."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .api import create_app
from .codex_simulation import ACWMCodexRoleRunner, CodexSimulatedHermesPlanning
from .control_plane import ControlPlaneService
from .delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from .git_delivery import (
    ACWMCodexWorkspaceAgent,
    GitCandidateApplier,
    GitCandidateVerifier,
    GitCodeExecutor,
)
from .infrastructure.acwm import (
    ACWMGraphCompiler,
    ACWMPipelineGraphRuntime,
    AgentDeploymentBindingResolver,
    ControlPlaneBindingResolver,
    PipelineBindingResolutionError,
)
from .infrastructure.database import LegacyDatabaseImporter, MigrationRunner
from .infrastructure.git import ProjectGitWorkspaces
from .journey import (
    load_backend_delivery_definition,
    load_fullstack_delivery_definition,
    resolve_backend_delivery_fingerprint,
)
from .modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRunLedger,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
    ensure_builtin_agent_deployments,
    ensure_builtin_fullstack_agent_deployments,
)
from .modules.delivery import BackendDeliveryPipelinePolicy
from .modules.evidence import EvidenceLedger, SQLiteEvidenceRepository
from .modules.extensions import (
    RuntimeExtensionCatalog,
    SQLiteRuntimeExtensionRepository,
)
from .modules.identity import IdentityService, SQLiteIdentityRepository
from .modules.knowledge import KnowledgeSearchIndex, SQLiteWikiRepository, WikiService
from .modules.orchestration import (
    Pipeline,
    PipelineCatalog,
    PipelineCreate,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
)
from .modules.projects import (
    ProjectCatalog,
    ProjectLeaseDeliveryRepository,
    SQLiteProjectRepository,
)
from .modules.releases import ReleaseCoordinator, SQLiteReleaseRepository
from .modules.settings import SettingsManager, SQLiteSettingsRepository
from .readiness import (
    DependencyCheck,
    ReadinessReport,
    RuntimeReadiness,
    inspect_acwm_revision_lock,
)
from .release import combined_gate_status, run_gate
from .ui import install_preview_ui

_logger = logging.getLogger(__name__)


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
            DependencyCheck(
                name="python:acwm-graph-runtime",
                status="ready" if _has_acwm_graph_runtime() else "missing",
                repair=(
                    None
                    if _has_acwm_graph_runtime()
                    else "安装项目锁定的 ACWM v0.4 Graph Runtime 后重试。"
                ),
            ),
            inspect_acwm_revision_lock(
                Path(__file__).parents[2] / "config" / "framework-lock.json"
            ),
        )
        return ReadinessReport(
            status="ready" if all(check.status == "ready" for check in checks) else "not_ready",
            checks=checks,
        )


def _has_acwm_graph_runtime() -> bool:
    return hasattr(import_module("acwm.domain"), "compile_journey_graph")


def _ensure_builtin_pipeline_for_preview(
    catalog: PipelineCatalog, request: PipelineCreate
) -> Pipeline | None:
    """Keep the repair UI online when product-owned bindings need attention."""
    try:
        return catalog.ensure_builtin_pipeline(request, actor_id="system")
    except PipelineBindingResolutionError as error:
        _logger.warning("Built-in Pipeline requires binding repair: %s", error)
        return None


def build_preview_app() -> FastAPI:
    project_root = Path(__file__).parents[2]
    data_dir = Path(os.environ.get("AGENT_TEAM_OS_DATA_DIR", str(project_root / ".agent-team-os")))
    database = data_dir / "agent-team-os.sqlite"
    migrations = MigrationRunner(database, project_root / "migrations")
    migrations.migrate()
    LegacyDatabaseImporter(migrations, data_dir / "backups").import_if_present(
        data_dir / "preview.sqlite", data_dir / "control-plane.sqlite"
    )
    runner = ACWMCodexRoleRunner(workspace=project_root)
    code_agent = ACWMCodexWorkspaceAgent()
    project_workspaces = ProjectGitWorkspaces(data_dir / "workspaces")
    sandbox = project_workspaces.for_workspace("backend-demo")
    sandbox.ensure_initialized()
    projects = ProjectCatalog(SQLiteProjectRepository(database), project_workspaces)

    def reset_workspace() -> str:
        active = {
            "queued",
            "planning",
            "awaiting_plan_decision",
            "awaiting_design_decision",
            "executing",
            "verifying",
            "awaiting_candidate_decision",
            "applying",
        }
        if any(delivery.status in active for delivery in coordinator.list()):
            from .delivery import DeliveryStateConflictError

            raise DeliveryStateConflictError("cannot reset while a Delivery is active")
        return sandbox.reset()

    delivery_repository = ProjectLeaseDeliveryRepository(
        SQLiteDeliveryRepository(database), projects
    )
    candidate_applier = GitCandidateApplier(project_workspaces)
    coordinator = DeliveryCoordinator(
        planning=CodexSimulatedHermesPlanning(runner),
        executor=GitCodeExecutor(project_workspaces, code_agent),
        verifier=GitCandidateVerifier(project_workspaces),
        applier=candidate_applier,
        repository=delivery_repository,
        resolved_journey_sha256=resolve_backend_delivery_fingerprint(project_root / "config"),
    )
    control_plane = ControlPlaneService(database, config_root=project_root / "config")
    control_plane.import_builtin_journey(
        planning_identity="codex-simulated-hermes",
        execution_identity="codex-cli",
    )
    agent_profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    runtime_extensions = RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database))
    provider_manifests = ProviderManifestCatalog()
    agent_deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database),
        agent_profiles,
        control_plane,
        provider_manifests,
        extensions=runtime_extensions,
    )
    builtin_assignments = ensure_builtin_agent_deployments(agent_profiles, agent_deployments)
    fullstack_assignments = ensure_builtin_fullstack_agent_deployments(
        agent_profiles, agent_deployments
    )
    pipeline_catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=ACWMGraphCompiler(),
        binding_resolver=ControlPlaneBindingResolver(
            control_plane.get_binding, control_plane.get_instance
        ),
        provider_binding_resolver=AgentDeploymentBindingResolver(
            agent_deployments, provider_manifests
        ),
        definition_policy=BackendDeliveryPipelinePolicy(),
    )
    builtin_pipeline = _ensure_builtin_pipeline_for_preview(
        pipeline_catalog,
        PipelineCreate(
            id="backend-delivery",
            name="内置后端交付闭环",
            description="需求、计划审批、代码交付、候选审批与原子应用",
            definition=load_backend_delivery_definition(project_root / "config"),
            agent_assignments=builtin_assignments,
        ),
    )
    _ensure_builtin_pipeline_for_preview(
        pipeline_catalog,
        PipelineCreate(
            id="fullstack-product-delivery",
            name="产品规划 → UI 设计 → 前后端 → 测试发布",
            description="五个 Codex 角色、四个隔离仓库、三道人工审批与 Release Manifest",
            definition=load_fullstack_delivery_definition(project_root / "config"),
            agent_assignments=fullstack_assignments,
        ),
    )

    def validate_project_pipeline(revision_id: str) -> None:
        pipeline_catalog.resolve_revision(revision_id)

    def validate_project_deployment(deployment_id: str) -> None:
        deployment = agent_deployments.get(deployment_id)
        if not deployment.enabled or deployment.qualification_status != "qualified":
            raise ValueError("deployment is not enabled and qualified")

    projects.configure_resource_validators(
        pipeline=validate_project_pipeline,
        deployment=validate_project_deployment,
    )
    if builtin_pipeline is not None and getattr(builtin_pipeline, "active_revision", None):
        projects.ensure_legacy_defaults(
            f"backend-delivery:{builtin_pipeline.active_revision}",
            tuple(sorted(set(builtin_assignments.values()))),
        )
    app = create_app(
        coordinator,
        readiness=CodexPreviewReadiness(),
        report_dir=data_dir / "reports",
        workspace_reset=reset_workspace,
        control_plane=control_plane,
        evidence=EvidenceLedger(SQLiteEvidenceRepository(database)),
        settings=SettingsManager(SQLiteSettingsRepository(database)),
        identity=IdentityService(SQLiteIdentityRepository(database)),
        knowledge=WikiService(
            SQLiteWikiRepository(database), project_guard=projects.assert_writable
        ),
        pipeline_catalog=pipeline_catalog,
        pipeline_runs=PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        ),
        agent_profiles=agent_profiles,
        agent_deployments=agent_deployments,
        provider_manifests=provider_manifests,
        agent_runs=AgentRunLedger(database),
        projects=projects,
        knowledge_search=KnowledgeSearchIndex(database),
        runtime_extensions=runtime_extensions,
        release_applier=ReleaseCoordinator(SQLiteReleaseRepository(database), candidate_applier),
    )
    install_preview_ui(app, project_root / "console" / "dist")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        projects.recover_provisioning()
        delivery_repository.reconcile_leases()
        await coordinator.recover()
        try:
            yield
        finally:
            await runner.close()
            await code_agent.close()

    app.router.lifespan_context = lifespan

    return app


def ensure_console_built(project_root: Path) -> None:
    console = project_root / "console"
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm is required to build the V0.3 console; install pnpm and retry")
    if not (console / "node_modules").is_dir():
        subprocess.run(
            [pnpm, "install", "--frozen-lockfile"],
            cwd=console,
            check=True,
        )
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js is required to build the V0.3 console")
    subprocess.run(
        [node, str(console / "node_modules" / "vite" / "bin" / "vite.js"), "build"],
        cwd=console,
        check=True,
    )


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
            if command == "release":
                combined = combined_gate_status({report.kind: report for report in reports})
                print(json.dumps(combined.model_dump(mode="json"), ensure_ascii=False))
                return 0 if combined.status == "passed" else 1
            return 0 if reports[0].status == "passed" else 1

        raise SystemExit(asyncio.run(execute_gates()))
    readiness = CodexPreviewReadiness().inspect()
    if readiness.status != "ready":
        print(readiness.model_dump_json(indent=2))
        raise SystemExit(2)
    try:
        ensure_console_built(project_root)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Console startup failed: {error}")
        raise SystemExit(2) from error
    port = int(os.environ.get("AGENT_TEAM_OS_PORT", "8080"))
    uvicorn.run(build_preview_app(), host="127.0.0.1", port=port, log_level="info")
