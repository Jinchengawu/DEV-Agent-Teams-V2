from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_team_os.handoff_evidence import (
    BROWSER_BASE_CHECKS,
    BROWSER_R2_CHECKS,
    LIVE_R2_REQUIRED_CHECKS,
    CoreBrowserRunReceipt,
    HandoffEvidenceIndex,
    build_handoff_evidence_index,
    write_core_browser_receipt,
)
from agent_team_os.knowledge_context_contract import KNOWLEDGE_CONTEXT_STAGE_PATHS
from agent_team_os.modules.releases.acceptance_domain import (
    ReleaseAcceptanceCheckV2,
    ReleaseAcceptanceReportV2,
)
from agent_team_os.release import GateReport, _report_evidence_sha256

PRODUCT_SHA = "a" * 40
ACWM_SHA = "b" * 40
BUILD_SHA = "c" * 64


def browser_receipt(*, r2: bool = True) -> CoreBrowserRunReceipt:
    paths = KNOWLEDGE_CONTEXT_STAGE_PATHS
    run_ids = {path: f"browser-run-{number}" for number, path in enumerate(paths[2:])}
    return CoreBrowserRunReceipt.create(
        scenario=("agent-workcell-knowledge-delivery-v1" if r2 else "agent-workcell-delivery-v1"),
        product_revision=PRODUCT_SHA,
        product_worktree_clean=True,
        acwm_revision=ACWM_SHA,
        project_id="browser-project",
        delivery_id="browser-delivery",
        pipeline_revision_id="browser-pipeline:1",
        pipeline_revision_sha256="d" * 64,
        build_identity_sha256=BUILD_SHA,
        execution_snapshot_sha256="e" * 64,
        console_bundle_sha256="8" * 64,
        planning_identity="deterministic-test",
        execution_identity=None,
        evidence_identity="deterministic-test",
        runtime_identity="deterministic-model-boundary",
        started_at=datetime(2026, 9, 5, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 5, 1, 5, tzinfo=UTC),
        checks_passed=BROWSER_BASE_CHECKS + (BROWSER_R2_CHECKS if r2 else ()),
        knowledge_scope=(
            {
                "required_stage_paths": paths,
                "contexts": [
                    {
                        "stage_path": path,
                        "artifact_sha256": "f" * 64,
                        "citation_ids": [f"citation-{index}"],
                        "authorization_epoch_hash": "9" * 64,
                    }
                    for index, path in enumerate(paths)
                ],
                "context_count": len(paths),
                "workcell_run_ids": run_ids,
                "qa_preparation_run_id": run_ids["qa-preparation-repair/qa-preparation"],
            }
            if r2
            else None
        ),
    )


def deterministic_report() -> GateReport:
    report = GateReport(
        kind="deterministic",
        status="passed",
        fail=0,
        warn=0,
        skipped=0,
        created_at=datetime(2026, 9, 5, 1, 0, tzinfo=UTC),
        dev_revision=PRODUCT_SHA,
        acwm_revision=ACWM_SHA,
        planning_identity="deterministic-test",
        execution_identity="deterministic-model-boundary",
        pipeline_revision_id="baseline:2",
        pipeline_fingerprint="1" * 64,
        pipeline_run_id="baseline-run",
        pipeline_run_status="completed",
        candidate_revision="e" * 40,
        diff_sha256="2" * 64,
        verification_exit_code=0,
        evidence_sha256="3" * 64,
        browser_e2e=True,
        browser_restart_recovery=True,
        browser_multi_pipeline_e2e=True,
        browser_verified_evidence_count=7,
        browser_candidate_matches_main=True,
    )
    return report.model_copy(update={"evidence_sha256": _report_evidence_sha256(report)})


def live_report(*, codes: tuple[str, ...] | None = None) -> ReleaseAcceptanceReportV2:
    return ReleaseAcceptanceReportV2.create(
        project_id="live-project",
        delivery_id="live-delivery",
        product_revision=PRODUCT_SHA,
        acwm_revision=ACWM_SHA,
        pipeline_revision_id="r2-live:3",
        build_identity_sha256=BUILD_SHA,
        knowledge_context_set_sha256="1" * 64,
        workcell_evidence_sha256="2" * 64,
        release_bundle_sha256="3" * 64,
        release_manifest_sha256="4" * 64,
        checks=tuple(
            ReleaseAcceptanceCheckV2(
                code=code, status="passed", detail="现有断言通过。", evidence_sha256="5" * 64
            )
            for code in (codes or (*LIVE_R2_REQUIRED_CHECKS, "CODEX_PLANNING_ATTEMPTS_VERIFIED"))
        ),
    )


def write_sources(tmp_path: Path, *, r2: bool = True) -> dict[str, Path]:
    sources = {
        name: tmp_path / f"{name}.json"
        for name in ("core_browser", "deterministic_gate", "live_release")
    }
    write_core_browser_receipt(sources["core_browser"], browser_receipt(r2=r2))
    sources["deterministic_gate"].write_text(deterministic_report().model_dump_json())
    sources["live_release"].write_text(live_report().model_dump_json())
    return sources


def check(sources: dict[str, Path]) -> HandoffEvidenceIndex:
    return build_handoff_evidence_index(
        product_revision=PRODUCT_SHA, acwm_revision=ACWM_SHA, sources=sources
    )


def test_native_three_tracks_keep_actual_scope_and_only_check_references(tmp_path: Path) -> None:
    sources = write_sources(tmp_path)
    before = {key: path.read_bytes() for key, path in sources.items()}
    result = check(sources)
    assert result.reference_check == "consistent"
    assert result.target == "four-repo-r2-alpha"
    assert "status" not in result.model_dump()
    assert result.sources[1].native_hash_field == "evidence_sha256"
    assert result.sources[2].native_hash_field == "report_sha256"
    assert result.sources[0].identity["delivery_id"] != result.sources[2].identity["delivery_id"]
    assert before == {key: path.read_bytes() for key, path in sources.items()}


def test_basic_browser_is_incomplete_for_r2_target(tmp_path: Path) -> None:
    result = check(write_sources(tmp_path, r2=False))
    assert result.reference_check == "incomplete"
    assert "BROWSER_R2_SCOPE_MISSING" in {issue.code for issue in result.issues}


@pytest.mark.parametrize("missing", ["core_browser", "deterministic_gate", "live_release"])
def test_missing_track_is_not_acceptance(tmp_path: Path, missing: str) -> None:
    sources = write_sources(tmp_path)
    del sources[missing]
    assert check(sources).reference_check == "incomplete"


@pytest.mark.parametrize(
    "role,field,value",
    [
        ("core_browser", "product_revision", "f" * 40),
        ("core_browser", "product_worktree_clean", False),
        ("core_browser", "evidence_identity", "live"),
        ("core_browser", "warn", 1),
        ("deterministic_gate", "skipped", 1),
        ("deterministic_gate", "kind", "live"),
        ("live_release", "fail", 1),
        ("live_release", "knowledge_context_set_sha256", None),
    ],
)
def test_source_tampering_never_becomes_consistent(
    tmp_path: Path,
    role: str,
    field: str,
    value: object,
) -> None:
    sources = write_sources(tmp_path)
    payload = json.loads(sources[role].read_text())
    payload[field] = value
    sources[role].write_text(json.dumps(payload))
    assert check(sources).reference_check == "invalid"


def test_correctly_hashed_live_with_incomplete_checks_is_rejected(tmp_path: Path) -> None:
    sources = write_sources(tmp_path)
    sources["live_release"].write_text(
        live_report(codes=("ACCEPTANCE_SUBJECT_VERIFIED",)).model_dump_json()
    )
    assert check(sources).reference_check == "invalid"


@pytest.mark.parametrize(
    "schema", ["live-delivery-browser-checkpoint-v1", "knowledge-live-ready-v1"]
)
def test_checkpoints_and_readiness_cannot_supply_browser_assertions(
    tmp_path: Path,
    schema: str,
) -> None:
    sources = write_sources(tmp_path)
    sources["core_browser"].write_text(json.dumps({"schema_version": schema, "status": "passed"}))
    assert check(sources).reference_check == "invalid"


def test_r2_receipt_does_not_accept_missing_or_unknown_context_path() -> None:
    payload = browser_receipt().model_dump(mode="json", exclude={"receipt_sha256"})
    payload["knowledge_scope"]["contexts"][0]["stage_path"] = "unknown-stage"
    with pytest.raises(ValueError):
        CoreBrowserRunReceipt.create(**payload)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("product_revision", "f" * 40, "SOURCE_REVISION_MISMATCH"),
        ("acwm_revision", "e" * 40, "SOURCE_REVISION_MISMATCH"),
        ("build_identity_sha256", "7" * 64, "BUILD_IDENTITY_MISMATCH"),
    ],
)
def test_valid_native_hash_cannot_hide_wrong_target_identity(
    tmp_path: Path,
    field: str,
    value: str,
    expected: str,
) -> None:
    sources = write_sources(tmp_path)
    payload = browser_receipt().model_dump(mode="json", exclude={"receipt_sha256"})
    payload[field] = value
    write_core_browser_receipt(sources["core_browser"], CoreBrowserRunReceipt.create(**payload))
    result = check(sources)
    assert result.reference_check == "invalid"
    assert expected in {issue.code for issue in result.issues}


def test_unknown_and_omitted_browser_assertions_are_not_receipts() -> None:
    payload = browser_receipt().model_dump(mode="json", exclude={"receipt_sha256"})
    payload["checks_passed"][0] = "EXIT_ZERO_OBSERVED"
    with pytest.raises(ValueError):
        CoreBrowserRunReceipt.create(**payload)


def test_live_keeps_hermes_binding_identity_without_relabeling(tmp_path: Path) -> None:
    sources = write_sources(tmp_path)
    sources["live_release"].write_text(
        live_report(
            codes=(*LIVE_R2_REQUIRED_CHECKS, "HERMES_PLANNING_ATTEMPTS_VERIFIED")
        ).model_dump_json()
    )
    result = check(sources)
    assert result.reference_check == "consistent"
    assert result.sources[2].identity["planning_adapter_verified"] == "hermes.acp"
    assert "planning_identity" not in result.sources[2].identity


@pytest.mark.parametrize("include_live", [False, True])
def test_index_cli_only_writes_ignored_report_and_nonzero_for_missing_track(
    tmp_path: Path,
    include_live: bool,
) -> None:
    project = tmp_path / "product"
    project.mkdir()
    sources = write_sources(tmp_path)
    output = project / ".agent-team-os/reports/handoff.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_delivery_handoff.py",
            "--project-root",
            str(project),
            "--product-revision",
            PRODUCT_SHA,
            "--acwm-revision",
            ACWM_SHA,
            "--core-browser",
            str(sources["core_browser"]),
            "--deterministic-gate",
            str(sources["deterministic_gate"]),
            "--output",
            str(output),
        ]
        + (["--live-release", str(sources["live_release"])] if include_live else []),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == (0 if include_live else 2), result.stderr
    assert json.loads(output.read_text())["reference_check"] == (
        "consistent" if include_live else "incomplete"
    )


def test_index_cli_cannot_overwrite_tracked_product_file(tmp_path: Path) -> None:
    source = tmp_path / "release-plan.md"
    source.write_text("现有产品文档")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_delivery_handoff.py",
            "--project-root",
            str(tmp_path),
            "--product-revision",
            PRODUCT_SHA,
            "--acwm-revision",
            ACWM_SHA,
            "--output",
            str(source),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert source.read_text() == "现有产品文档"
