import sys

import pytest
from pytest import MonkeyPatch

from agent_team_os.infrastructure.acwm import PipelineBindingResolutionError
from agent_team_os.preview import _ensure_builtin_pipeline_for_preview, main
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
