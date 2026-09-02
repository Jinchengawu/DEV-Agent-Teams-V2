from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_team_os.knowledge_live_readiness import (
    KnowledgeLiveReadinessCheck,
    KnowledgeLiveReadinessReport,
)
from agent_team_os.modules.releases.acceptance_domain import (
    ReleaseAcceptanceCheckV2,
    ReleaseAcceptanceReportV2,
)
from agent_team_os.preview import main as preview_main


def _check(code: str, *, passed: bool = True) -> ReleaseAcceptanceCheckV2:
    return ReleaseAcceptanceCheckV2(
        code=code,
        status="passed" if passed else "failed",
        detail="已验证不可变交付事实。",
        evidence_sha256=("a" if passed else "b") * 64,
    )


def test_release_acceptance_report_is_content_addressed_and_zero_tolerance() -> None:
    report = ReleaseAcceptanceReportV2.create(
        project_id="alpha",
        delivery_id="delivery-live-1",
        checks=(
            _check("BUILD_IDENTITY_VERIFIED"),
            _check("REMOTE_MAIN_VERIFIED", passed=False),
        ),
        product_revision="1" * 40,
        acwm_revision="2" * 40,
        pipeline_revision_id="agent-workcell-delivery:2",
        build_identity_sha256="3" * 64,
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert report.status == "failed"
    assert (report.fail, report.warn, report.skipped) == (1, 0, 0)
    assert len(report.report_sha256) == 64


def test_release_acceptance_report_detects_tampering() -> None:
    report = ReleaseAcceptanceReportV2.create(
        project_id="alpha",
        delivery_id="delivery-live-1",
        checks=(_check("BUILD_IDENTITY_VERIFIED"),),
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    payload = json.loads(report.model_dump_json())
    payload["checks"][0]["detail"] = "tampered"

    with pytest.raises(ValidationError, match="content hash mismatch"):
        ReleaseAcceptanceReportV2.model_validate(payload)


def test_release_acceptance_report_rejects_duplicate_check_codes() -> None:
    with pytest.raises(ValidationError, match="check codes must be unique"):
        ReleaseAcceptanceReportV2.create(
            project_id="alpha",
            delivery_id="delivery-live-1",
            checks=(
                _check("BUILD_IDENTITY_VERIFIED"),
                _check("BUILD_IDENTITY_VERIFIED"),
            ),
        )


def test_live_gate_stops_at_readiness_and_does_not_create_acceptance_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocked = KnowledgeLiveReadinessReport(
        project_id="alpha",
        status="blocked",
        checks=(
            KnowledgeLiveReadinessCheck(
                name="external-prerequisites",
                status="blocked",
                detail="Live 前置条件尚未满足。",
                repair="配置真实凭据和四仓。",
            ),
        ),
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "agent_team_os.preview.inspect_knowledge_live_readiness",
        lambda **_kwargs: blocked,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-team-os",
            "knowledge-live-gate",
            "--project-id",
            "alpha",
            "--delivery-id",
            "delivery-live-1",
        ],
    )

    with pytest.raises(SystemExit) as exited:
        preview_main()

    assert exited.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert payload["execution_status"] == "not_run"
    assert not (tmp_path / "reports" / "release-v2").exists()


def test_live_gate_persists_sanitized_acceptance_report_after_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ready = KnowledgeLiveReadinessReport(
        project_id="alpha",
        status="ready",
        checks=(
            KnowledgeLiveReadinessCheck(
                name="external-prerequisites",
                status="ready",
                detail="真实前置条件已验证。",
            ),
        ),
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    acceptance = ReleaseAcceptanceReportV2.create(
        project_id="alpha",
        delivery_id="delivery-live-1",
        checks=(_check("ALL_LIVE_EVIDENCE_VERIFIED"),),
        product_revision="1" * 40,
        acwm_revision="2" * 40,
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )

    class Verifier:
        def verify(self, **_kwargs: object) -> ReleaseAcceptanceReportV2:
            return acceptance

    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "agent_team_os.preview.inspect_knowledge_live_readiness",
        lambda **_kwargs: ready,
    )
    monkeypatch.setattr(
        "agent_team_os.preview._build_release_acceptance_verifier",
        lambda **_kwargs: Verifier(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-team-os",
            "knowledge-live-gate",
            "--project-id",
            "alpha",
            "--delivery-id",
            "delivery-live-1",
        ],
    )

    with pytest.raises(SystemExit) as exited:
        preview_main()

    assert exited.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"
    report_dir = tmp_path / "reports" / "release-v2"
    reports = tuple(report_dir.glob("*.json"))
    assert len(reports) == 1
    persisted = reports[0].read_text(encoding="utf-8")
    assert acceptance.report_sha256 in persisted
    assert all(
        forbidden not in persisted
        for forbidden in (
            "repository_uri",
            "credential_reference",
            "knowledge_body",
            "model_raw_response",
            "session-only-secret",
        )
    )
