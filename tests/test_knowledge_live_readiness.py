from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_team_os.knowledge_live_readiness import (
    KnowledgeLiveFacts,
    _runtime_bindings_wired,
    evaluate_knowledge_live_readiness,
)
from agent_team_os.preview import main as preview_main
from agent_team_os.readiness import DependencyCheck, ReadinessReport, RuntimeReadiness
from agent_team_os.shared.features import FeatureFlags


def _ready_facts() -> KnowledgeLiveFacts:
    return KnowledgeLiveFacts(
        database_ready=True,
        project_status="active",
        team_status="active",
        workcell_keys=("backend", "design", "frontend", "qa"),
        workspace_count=4,
        external_workspace_count=4,
        ready_workspace_count=4,
        qualified_verification_workspace_count=4,
        unique_repository_count=4,
        direct_fast_forward_main_count=4,
        resolvable_git_credential_count=4,
        pipeline_revision_id="agent-workcell-delivery:2",
        pipeline_binding_model="provider-v1",
        release_contract=("backend", "design", "frontend", "qa"),
        workcell_stage_paths=(
            "backend-repair/backend",
            "design-repair/design",
            "frontend-repair/frontend",
            "qa-delivery-repair/qa-delivery",
            "qa-preparation-repair/qa-preparation",
        ),
        knowledge_context_stage_paths=(
            "backend-repair/backend",
            "design-repair/design",
            "frontend-repair/frontend",
            "qa-delivery-repair/qa-delivery",
            "qa-preparation-repair/qa-preparation",
            "requirements",
            "tasking",
        ),
        required_knowledge_context_count=7,
        resolved_provider_binding_count=22,
        planning_runtime_kind="hermes",
        planning_binding_count=2,
        product_planning_runtime_wired=True,
        hermes_planning_binding_count=2,
        codex_workcell_binding_count=20,
        product_hermes_runtime_wired=True,
        product_knowledge_sync_runtime_wired=True,
        required_retrieval_policy_count=1,
        ready_retrieval_policy_count=1,
        approved_source_count=1,
        ready_source_count=1,
        fresh_permission_probe_count=1,
        resolvable_feishu_credential_count=1,
        active_index_count=1,
        passed_evaluation_count=1,
        qualified_ollama_model_count=1,
        verified_index_policy_count=1,
        live_ollama_model_count=1,
    )


def _ready_codex_facts() -> KnowledgeLiveFacts:
    return _ready_facts().model_copy(
        update={
            "planning_runtime_kind": "codex",
            "planning_binding_count": 2,
            "product_planning_runtime_wired": True,
            "hermes_planning_binding_count": 0,
            "product_hermes_runtime_wired": False,
        }
    )


def _runtime_ready() -> ReadinessReport:
    return ReadinessReport(
        status="ready",
        checks=(
            DependencyCheck(name="python:agentscope", status="ready"),
            DependencyCheck(name="cli:hermes", status="ready"),
            DependencyCheck(name="hermes-credentials", status="ready"),
            DependencyCheck(name="codex-login", status="ready"),
        ),
    )


def test_live_readiness_is_ready_but_not_run_when_every_precondition_is_proven() -> None:
    report = evaluate_knowledge_live_readiness(
        project_id="alpha",
        facts=_ready_facts(),
        flags=FeatureFlags(
            feishu_tenant_sync_v1=True,
            knowledge_hybrid_index_v1=True,
            delivery_knowledge_context_v1=True,
        ),
        framework_revision=DependencyCheck(name="python:acwm-revision", status="ready"),
        runtime=_runtime_ready(),
    )

    assert report.status == "ready"
    assert report.execution_status == "not_run"
    assert report.capability == "feishu-knowledge-delivery-v1"
    assert all(check.status == "ready" for check in report.checks)


def test_live_readiness_fails_closed_without_leaking_credentials() -> None:
    facts = _ready_facts().model_copy(
        update={
            "resolvable_git_credential_count": 0,
            "resolvable_feishu_credential_count": 0,
            "live_ollama_model_count": 0,
        }
    )
    runtime = ReadinessReport(
        status="not_ready",
        checks=(
            DependencyCheck(
                name="hermes-credentials",
                status="missing",
                repair="Set HERMES_API_KEY for the Hermes model provider.",
            ),
        ),
    )

    report = evaluate_knowledge_live_readiness(
        project_id="alpha",
        facts=facts,
        flags=FeatureFlags(),
        framework_revision=DependencyCheck(
            name="python:acwm-revision",
            status="failed",
            repair="revision mismatch",
        ),
        runtime=runtime,
    )

    assert report.status == "blocked"
    assert report.execution_status == "not_run"
    blocked = {check.name for check in report.checks if check.status == "blocked"}
    assert {
        "feature-flags",
        "framework-lock",
        "runtime",
        "external-git-workspaces",
        "feishu-approved-source",
        "ollama-model",
    } <= blocked
    checks = {check.name: check for check in report.checks}
    assert checks["framework-lock"].detail == "ACWM Revision/Dependency Attestation 未通过。"
    assert checks["external-git-workspaces"].detail == (
        "四个独立 external-git Workspace、凭据解析或直接 Fast-forward main 权限尚未全部验证。"
    )
    assert checks["feishu-approved-source"].detail == (
        "没有同时满足凭据可解析、权限探测新鲜且项目已批准 RAG 的 Feishu Source。"
    )
    serialized = report.model_dump_json()
    assert "tenant-app-secret" not in serialized
    assert "session-only-token" not in serialized


def test_managed_git_or_incomplete_pipeline_cannot_satisfy_live_readiness() -> None:
    facts = _ready_facts().model_copy(
        update={
            "external_workspace_count": 0,
            "resolvable_git_credential_count": 0,
            "pipeline_binding_model": "legacy-v0",
            "knowledge_context_stage_paths": (),
            "required_knowledge_context_count": 0,
        }
    )

    report = evaluate_knowledge_live_readiness(
        project_id="alpha",
        facts=facts,
        flags=FeatureFlags(
            feishu_tenant_sync_v1=True,
            knowledge_hybrid_index_v1=True,
            delivery_knowledge_context_v1=True,
        ),
        framework_revision=DependencyCheck(name="python:acwm-revision", status="ready"),
        runtime=_runtime_ready(),
    )

    assert report.status == "blocked"
    blocked = {check.name for check in report.checks if check.status == "blocked"}
    assert "external-git-workspaces" in blocked
    assert "published-knowledge-pipeline" in blocked


def test_evaluation_and_qualification_cannot_be_joined_across_index_revisions() -> None:
    facts = _ready_facts().model_copy(update={"verified_index_policy_count": 0})

    report = evaluate_knowledge_live_readiness(
        project_id="alpha",
        facts=facts,
        flags=FeatureFlags(
            feishu_tenant_sync_v1=True,
            knowledge_hybrid_index_v1=True,
            delivery_knowledge_context_v1=True,
        ),
        framework_revision=DependencyCheck(name="python:acwm-revision", status="ready"),
        runtime=_runtime_ready(),
    )

    checks = {check.name: check.status for check in report.checks}
    assert checks["qualified-hybrid-index"] == "blocked"
    assert report.status == "blocked"


def test_simulated_planning_provider_cannot_satisfy_live_readiness() -> None:
    facts = _ready_facts().model_copy(
        update={
            "planning_runtime_kind": "unknown",
            "planning_binding_count": 0,
            "product_planning_runtime_wired": False,
            "hermes_planning_binding_count": 0,
            "product_hermes_runtime_wired": False,
        }
    )

    report = evaluate_knowledge_live_readiness(
        project_id="alpha",
        facts=facts,
        flags=FeatureFlags(
            feishu_tenant_sync_v1=True,
            knowledge_hybrid_index_v1=True,
            delivery_knowledge_context_v1=True,
        ),
        framework_revision=DependencyCheck(name="python:acwm-revision", status="ready"),
        runtime=_runtime_ready(),
    )

    checks = {check.name: check.status for check in report.checks}
    assert checks["live-provider-bindings"] == "blocked"
    assert checks["product-runtime-adapters"] == "blocked"
    details = {check.name: check.detail for check in report.checks}
    assert details["product-runtime-adapters"] == (
        "Published Planning Binding 尚未选择产品已接线的 role-turn Adapter，或实例/配置验证未通过。"
    )
    assert report.status == "blocked"


def test_runtime_adapter_readiness_matches_every_frozen_planning_binding() -> None:
    assert _runtime_bindings_wired(("hermes.acp", "hermes.acp"), expected_count=2)
    assert not _runtime_bindings_wired(("http.sync", "http.sync"), expected_count=2)
    assert not _runtime_bindings_wired(("hermes.acp",), expected_count=2)


def test_runtime_readiness_probes_hermes_acp_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setenv("HERMES_API_KEY", "session-only-test-key")
    monkeypatch.setattr(
        "agent_team_os.readiness.shutil.which",
        lambda name: f"/usr/local/bin/{name}",
    )

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(tuple(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_team_os.readiness.subprocess.run", run)

    report = RuntimeReadiness().inspect()

    assert report.status == "ready"
    assert ("hermes", "acp", "--check") in commands
    assert (
        next(check for check in report.checks if check.name == "hermes-acp-protocol").status
        == "ready"
    )


def test_codex_planning_readiness_does_not_require_hermes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    monkeypatch.setattr(
        "agent_team_os.readiness.shutil.which",
        lambda name: None if name == "hermes" else f"/usr/local/bin/{name}",
    )

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(tuple(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_team_os.readiness.subprocess.run", run)

    runtime = RuntimeReadiness(planning_runtime_kind="codex").inspect()
    report = evaluate_knowledge_live_readiness(
        project_id="alpha",
        facts=_ready_codex_facts(),
        flags=FeatureFlags(
            feishu_tenant_sync_v1=True,
            knowledge_hybrid_index_v1=True,
            delivery_knowledge_context_v1=True,
        ),
        framework_revision=DependencyCheck(name="python:acwm-revision", status="ready"),
        runtime=runtime,
    )

    assert runtime.status == "ready"
    assert all("hermes" not in check.name for check in runtime.checks)
    assert ("codex", "login", "status") in commands
    assert report.status == "ready"
    checks = {check.name: check for check in report.checks}
    assert "Codex" in checks["live-provider-bindings"].detail
    assert "Codex" in checks["product-runtime-adapters"].detail


def test_missing_persistent_sync_runtime_cannot_satisfy_live_readiness() -> None:
    report = evaluate_knowledge_live_readiness(
        project_id="alpha",
        facts=_ready_facts().model_copy(update={"product_knowledge_sync_runtime_wired": False}),
        flags=FeatureFlags(
            feishu_tenant_sync_v1=True,
            knowledge_hybrid_index_v1=True,
            delivery_knowledge_context_v1=True,
        ),
        framework_revision=DependencyCheck(name="python:acwm-revision", status="ready"),
        runtime=_runtime_ready(),
    )

    checks = {check.name: check.status for check in report.checks}
    assert checks["knowledge-sync-runtime"] == "blocked"
    assert report.status == "blocked"


def test_cli_persists_blocked_not_run_report_without_a_configured_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "agent_team_os.knowledge_live_readiness.RuntimeReadiness.inspect",
        lambda _self: _runtime_ready(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-team-os",
            "knowledge-live-readiness",
            "--project-id",
            "alpha",
        ],
    )

    with pytest.raises(SystemExit) as exited:
        preview_main()

    assert exited.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert payload["execution_status"] == "not_run"
    reports = tmp_path / "reports" / "readiness"
    assert len(tuple(reports.glob("*.json"))) == 1
    assert len(tuple(reports.glob("*.md"))) == 1


def test_live_readiness_requires_each_workspace_machine_verification_qualification() -> None:
    report = evaluate_knowledge_live_readiness(
        project_id="alpha",
        facts=_ready_facts().model_copy(update={"qualified_verification_workspace_count": 3}),
        flags=FeatureFlags(
            feishu_tenant_sync_v1=True,
            knowledge_hybrid_index_v1=True,
            delivery_knowledge_context_v1=True,
        ),
        framework_revision=DependencyCheck(name="python:acwm-revision", status="ready"),
        runtime=_runtime_ready(),
    )
    assert report.status == "blocked"
    assert report.execution_status == "not_run"
    assert (
        next(c for c in report.checks if c.name == "workspace-verification-profiles").status
        == "blocked"
    )
