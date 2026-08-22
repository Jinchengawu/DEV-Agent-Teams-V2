from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_team_os.release import (
    GateReport,
    _report,
    combined_gate_status,
    latest_reports,
)


def test_clean_same_revision_reports_are_publishable() -> None:
    now = datetime.now(UTC)
    deterministic = _gate_report("deterministic", now)
    live = _gate_report("live", now)

    combined = combined_gate_status(
        {"deterministic": deterministic, "live": live}, now=now
    )

    assert combined.status == "passed"
    assert combined.code == "RELEASE_GATE_PASSED"


def test_combined_gate_rejects_revision_mismatch_and_tampered_evidence() -> None:
    now = datetime.now(UTC)
    deterministic = _gate_report("deterministic", now)
    live = _gate_report("live", now)

    mismatch = combined_gate_status(
        {
            "deterministic": deterministic,
            "live": live.model_copy(update={"dev_revision": "f" * 40}),
        },
        now=now,
    )
    tampered = combined_gate_status(
        {
            "deterministic": deterministic.model_copy(update={"diff_sha256": "e" * 64}),
            "live": live,
        },
        now=now,
    )

    assert mismatch.code == "RELEASE_GATE_REVISION_MISMATCH"
    assert tampered.code == "RELEASE_GATE_EVIDENCE_HASH_INVALID"


def test_combined_gate_rejects_expired_or_untrusted_identity_reports() -> None:
    now = datetime.now(UTC)
    expired = _gate_report("deterministic", now - timedelta(hours=25))
    live = _gate_report("live", now)
    wrong_live = _gate_report("live", now, execution_identity="deterministic-test")
    missing_restart = _gate_report(
        "deterministic", now, browser_restart_recovery=False
    )

    assert (
        combined_gate_status({"deterministic": expired, "live": live}, now=now).code
        == "RELEASE_GATE_REPORT_EXPIRED"
    )
    assert (
        combined_gate_status(
            {"deterministic": _gate_report("deterministic", now), "live": wrong_live},
            now=now,
        ).code
        == "RELEASE_GATE_LIVE_IDENTITY_INVALID"
    )
    assert (
        combined_gate_status(
            {"deterministic": missing_restart, "live": live}, now=now
        ).code
        == "RELEASE_GATE_DETERMINISTIC_IDENTITY_INVALID"
    )


def test_latest_reports_do_not_fall_back_past_a_corrupt_new_report(tmp_path: Path) -> None:
    older = _gate_report("deterministic", datetime.now(UTC))
    (tmp_path / "20260822T010000Z-deterministic.json").write_text(
        older.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / "20260822T020000Z-deterministic.json").write_text(
        "{not-json", encoding="utf-8"
    )

    latest = latest_reports(tmp_path)

    assert latest["deterministic"] is None


def _gate_report(
    kind: str,
    created_at: datetime,
    *,
    execution_identity: str | None = None,
    browser_restart_recovery: bool | None = None,
) -> GateReport:
    deterministic = kind == "deterministic"
    return _report(
        kind=kind,
        created_at=created_at,
        dev_revision="a" * 40,
        acwm_revision="b" * 40,
        planning_identity=(
            "deterministic-test" if deterministic else "codex-simulated-hermes"
        ),
        execution_identity=execution_identity
        or ("deterministic-model-boundary" if deterministic else "codex-cli"),
        candidate_revision="c" * 40,
        diff_sha256="d" * 64,
        verification_exit_code=0,
        browser_e2e=deterministic,
        browser_restart_recovery=(
            deterministic
            if browser_restart_recovery is None
            else browser_restart_recovery
        ),
    )
