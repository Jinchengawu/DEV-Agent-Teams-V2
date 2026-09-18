import sys
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from agent_team_os.infrastructure.acwm import PipelineBindingResolutionError
from agent_team_os.preview import (
    CodexPreviewReadiness,
    _ensure_builtin_pipeline_for_preview,
    _inspect_method_pack_store,
    main,
)
from agent_team_os.readiness import DependencyCheck, ReadinessReport


def test_demo_checks_framework_lock_before_building_preview_app(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = ReadinessReport(
        status="not_ready",
        checks=(
            DependencyCheck(
                name="python:acwm-revision",
                status="failed",
                repair="安装锁定 ACWM Revision。",
            ),
        ),
    )
    monkeypatch.setattr(sys, "argv", ["agent-team-os", "demo"])
    monkeypatch.setattr(
        "agent_team_os.preview.CodexPreviewReadiness.inspect", lambda _self: report
    )
    monkeypatch.setattr(
        "agent_team_os.preview.build_preview_app",
        lambda: (_ for _ in ()).throw(
            AssertionError("Preview App must not be built before readiness")
        ),
    )

    with pytest.raises(SystemExit) as stopped:
        main()

    assert stopped.value.code == 2
    assert '"name": "python:acwm-revision"' in capsys.readouterr().out


def test_preview_stays_available_when_builtin_pipeline_binding_needs_repair(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class StaleBindingCatalog:
        def ensure_builtin_pipeline(self, _request: object, *, actor_id: str) -> None:
            assert actor_id == "system"
            raise PipelineBindingResolutionError(
                "Capability codex-backend binding is stale"
            )

    result = _ensure_builtin_pipeline_for_preview(StaleBindingCatalog(), object())

    assert result is None
    assert "codex-backend binding is stale" in caplog.text


def test_preview_readiness_blocks_when_locked_method_packs_are_not_installed(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[1]

    check = _inspect_method_pack_store(
        root / "config" / "method-packs-v050.json",
        tmp_path / "empty-method-store",
    )

    assert check.name == "method-packs:bmad-tea-v050"
    assert check.status == "missing"
    assert check.repair is not None and "install_method_packs.py" in check.repair


def test_preview_readiness_preserves_codex_model_compatibility_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = ReadinessReport(
        status="not_ready",
        checks=(
            DependencyCheck(name="python:acwm", status="ready"),
            DependencyCheck(name="python:agentscope", status="ready"),
            DependencyCheck(
                name="codex-cli-version",
                status="failed",
                repair="Install Codex CLI >= 0.153.4.",
            ),
            DependencyCheck(name="codex-login", status="ready"),
        ),
    )
    monkeypatch.setattr(
        "agent_team_os.preview.RuntimeReadiness.inspect", lambda _self: runtime
    )

    report = CodexPreviewReadiness().inspect()

    check = next(item for item in report.checks if item.name == "codex-cli-version")
    assert check.status == "failed"
    assert report.status == "not_ready"
