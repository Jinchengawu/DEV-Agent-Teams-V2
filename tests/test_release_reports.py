from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch, raises

from agent_team_os.readiness import imported_acwm_revision
from agent_team_os.release import (
    GateReport,
    _enqueue_gate_delivery,
    _live_gate_codex_config,
    _live_gate_planning_timeout_seconds,
    _report,
    combined_gate_status,
    latest_reports,
)


def test_acwm_revision_uses_the_imported_source_checkout(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    checkout = tmp_path / "acwm-checkout"
    package = checkout / "src" / "acwm"
    package.mkdir(parents=True)
    (checkout / ".git").mkdir()
    module = SimpleNamespace(__file__=str(package / "__init__.py"))
    monkeypatch.setattr("agent_team_os.readiness.import_module", lambda _name: module)
    monkeypatch.setattr(
        "agent_team_os.readiness.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="f" * 40 + "\n"
        ),
    )

    assert imported_acwm_revision() == "f" * 40


def test_acwm_revision_does_not_mistake_enclosing_application_repo(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    application = tmp_path / "application"
    package = application / ".venv" / "site-packages" / "acwm"
    package.mkdir(parents=True)
    (application / ".git").mkdir()
    module = SimpleNamespace(__file__=str(package / "__init__.py"))

    class Distribution:
        @staticmethod
        def read_text(_name: str) -> str:
            return '{"vcs_info":{"commit_id":"' + "c" * 40 + '"}}'

    monkeypatch.setattr("agent_team_os.readiness.import_module", lambda _name: module)
    monkeypatch.setattr(
        "agent_team_os.readiness.importlib.metadata.distribution",
        lambda _name: Distribution(),
    )
    monkeypatch.setattr(
        "agent_team_os.readiness.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("enclosing repository must not be attested as ACWM")
        ),
    )

    assert imported_acwm_revision() == "c" * 40


def test_clean_same_revision_reports_are_publishable() -> None:
    now = datetime.now(UTC)
    deterministic = _gate_report("deterministic", now)
    live = _gate_report("live", now)

    combined = combined_gate_status(
        {"deterministic": deterministic, "live": live}, now=now
    )

    assert combined.status == "passed"
    assert combined.code == "RELEASE_GATE_PASSED"


def test_live_gate_gives_each_real_codex_turn_a_five_minute_budget() -> None:
    planning = _live_gate_codex_config("read-only")
    execution = _live_gate_codex_config("workspace-write")

    assert planning.timeout_seconds == 300
    assert execution.timeout_seconds == 300
    assert planning.sandbox == "read-only"
    assert execution.sandbox == "workspace-write"


def test_live_gate_budget_covers_both_serial_planning_turns() -> None:
    assert _live_gate_planning_timeout_seconds() == 630


def test_planning_gate_timeout_cancels_and_quiesces_the_delivery() -> None:
    class StalledCoordinator:
        def __init__(self) -> None:
            self.delivery = SimpleNamespace(
                id="stalled-delivery",
                status="planning",
                version=3,
                error_code=None,
            )
            self.cancelled: tuple[str, int] | None = None

        def enqueue(self, **_values: object) -> SimpleNamespace:
            return self.delivery

        def get(self, _delivery_id: str) -> SimpleNamespace:
            return self.delivery

        async def cancel_and_wait(
            self, delivery_id: str, *, expected_version: int
        ) -> SimpleNamespace:
            self.cancelled = (delivery_id, expected_version)
            self.delivery.status = "cancelled"
            return self.delivery

    async def scenario() -> None:
        coordinator = StalledCoordinator()
        revision = SimpleNamespace(
            pipeline_id="backend-delivery",
            revision=1,
            binding_snapshot={},
            fingerprint="a" * 64,
        )

        with raises(TimeoutError, match="Pipeline planning gate timed out"):
            await _enqueue_gate_delivery(  # type: ignore[arg-type]
                coordinator,  # type: ignore[arg-type]
                revision,  # type: ignore[arg-type]
                workspace_id="backend-demo",
                user_request="stalled",
                timeout_seconds=0,
            )

        assert coordinator.cancelled == ("stalled-delivery", 3)
        assert coordinator.delivery.status == "cancelled"

    asyncio.run(scenario())


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


def test_combined_gate_rejects_pipeline_revision_or_graph_mismatch() -> None:
    now = datetime.now(UTC)
    deterministic = _gate_report("deterministic", now)
    live = _gate_report("live", now)
    different_revision = _rehash(
        live.model_copy(update={"pipeline_revision_id": "backend-delivery:3"})
    )
    different_graph = _rehash(
        live.model_copy(update={"pipeline_fingerprint": "f" * 64})
    )

    revision_status = combined_gate_status(
        {"deterministic": deterministic, "live": different_revision}, now=now
    )
    graph_status = combined_gate_status(
        {"deterministic": deterministic, "live": different_graph}, now=now
    )

    assert revision_status.code == "RELEASE_GATE_PIPELINE_MISMATCH"
    assert graph_status.code == "RELEASE_GATE_PIPELINE_MISMATCH"


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


def test_combined_gate_rejects_missing_completed_pipeline_run() -> None:
    now = datetime.now(UTC)
    deterministic = _gate_report("deterministic", now).model_copy(
        update={"pipeline_run_status": None}
    )
    deterministic = deterministic.model_copy(
        update={"evidence_sha256": _report_evidence_for(deterministic)}
    )

    combined = combined_gate_status(
        {"deterministic": deterministic, "live": _gate_report("live", now)},
        now=now,
    )

    assert combined.code == "RELEASE_GATE_DELIVERY_EVIDENCE_INCOMPLETE"


def test_combined_gate_requires_multi_pipeline_browser_evidence() -> None:
    now = datetime.now(UTC)
    deterministic = _gate_report("deterministic", now)
    incomplete = deterministic.model_copy(
        update={
            "browser_multi_pipeline_e2e": False,
            "browser_verified_evidence_count": 0,
            "browser_candidate_matches_main": False,
        }
    )
    incomplete = incomplete.model_copy(
        update={"evidence_sha256": _report_evidence_for(incomplete)}
    )

    combined = combined_gate_status(
        {"deterministic": incomplete, "live": _gate_report("live", now)},
        now=now,
    )

    assert combined.code == "RELEASE_GATE_BROWSER_EVIDENCE_INCOMPLETE"


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
        pipeline_revision_id="backend-delivery:2",
        pipeline_fingerprint="e" * 64,
        pipeline_run_id="pipeline-run-1",
        pipeline_run_status="completed",
        candidate_revision="c" * 40,
        diff_sha256="d" * 64,
        verification_exit_code=0,
        browser_e2e=deterministic,
        browser_restart_recovery=(
            deterministic
            if browser_restart_recovery is None
            else browser_restart_recovery
        ),
        browser_multi_pipeline_e2e=deterministic,
        browser_verified_evidence_count=7 if deterministic else 0,
        browser_candidate_matches_main=deterministic,
    )


def _report_evidence_for(report: GateReport) -> str:
    from agent_team_os.release import _report_evidence_sha256

    return _report_evidence_sha256(report)


def _rehash(report: GateReport) -> GateReport:
    return report.model_copy(update={"evidence_sha256": _report_evidence_for(report)})
