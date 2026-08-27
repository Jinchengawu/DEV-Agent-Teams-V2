import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.acwm import ACWMGraphCompiler
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.journey import load_backend_delivery_definition
from agent_team_os.modules.evaluation import (
    EvaluationRunRequest,
    EvaluationService,
    HumanReview,
    SQLiteEvaluationRepository,
)
from agent_team_os.modules.evaluation import application as evaluation_application
from agent_team_os.modules.evidence import EvidenceKind, EvidenceLedger, SQLiteEvidenceRepository
from agent_team_os.modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    SQLitePipelineRepository,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class BindingResolver:
    def snapshot(self, capability_ids: tuple[str, ...]) -> dict[str, dict[str, object]]:
        return {
            capability_id: {
                "instance_id": "deterministic-test",
                "adapter_id": "codex.cli",
                "adapter_version": "1.0.0",
            }
            for capability_id in capability_ids
        }


def _service(tmp_path: Path) -> tuple[EvaluationService, EvidenceLedger]:
    root = Path(__file__).parents[1]
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, root / "migrations").migrate()
    catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=ACWMGraphCompiler(),
        binding_resolver=BindingResolver(),
    )
    catalog.ensure_builtin_pipeline(
        PipelineCreate(
            id="backend-delivery",
            name="Backend",
            definition=load_backend_delivery_definition(root / "config"),
        ),
        actor_id="system",
    )
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))
    return (
        EvaluationService(
            SQLiteEvaluationRepository(database),
            catalog,
            report_dir=tmp_path / "reports",
            project_root=root,
            evidence=evidence,
        ),
        evidence,
    )


def test_offline_smoke_run_is_durable_and_fail_closed(tmp_path: Path) -> None:
    service, evidence = _service(tmp_path)

    completed = service.start(EvaluationRunRequest(profile="smoke"))
    cases = service.cases(completed.id)
    report = service.report(completed.id)

    assert completed.status == "completed"
    assert len(cases) == 10
    assert report.gate_status == "calibrating"
    assert report.proof_scope == "fixture_harness_only"
    assert report.official_benchmark is False
    control = next(item for item in report.dimensions if item.dimension == "control_plane")
    assert control.evaluated == 1
    assert control.passed == 1
    assert "candidate_http_latency" in control.metric_percentiles
    assert all(item.delta_percentage_points in {0.0, None} for item in report.dimensions)
    assert (tmp_path / "reports" / f"{completed.id}.json").exists()
    markdown = (tmp_path / "reports" / f"{completed.id}.md").read_text(encoding="utf-8")
    assert markdown.startswith(f"# 评测报告 {completed.id}")
    assert "| 评测维度 | 已评测/总数 | 通过 | 失败 | 阻塞 |" in markdown
    archived = evidence.list(f"evaluation:{completed.id}")
    assert archived[0].kind == EvidenceKind.EVALUATION_REPORT
    assert archived[0].content_sha256 == report.evidence_sha256


def test_standard_profile_keeps_documented_denominators(tmp_path: Path) -> None:
    service, _evidence = _service(tmp_path)

    completed = service.start(EvaluationRunRequest(profile="standard", seed=20260824))
    cases = service.cases(completed.id)
    counts = {
        dimension: sum(item.dimension == dimension for item in cases)
        for dimension in ("tool_call", "general_agent", "data_generation", "control_plane")
    }

    assert len(cases) == 600
    dataset_case_ids = {case.id for case in service.dataset.cases}
    assert all(item.case_id.split(":", 1)[0] in dataset_case_ids for item in cases)
    assert counts == {
        "tool_call": 300,
        "general_agent": 180,
        "data_generation": 60,
        "control_plane": 60,
    }


def test_live_run_without_runtime_is_blocked_not_passed(tmp_path: Path) -> None:
    service, _evidence = _service(tmp_path)

    blocked = service.start(EvaluationRunRequest(mode="live", profile="live", max_cost_usd=1))
    report = service.report(blocked.id)

    assert blocked.status == "blocked"
    assert report.gate_status == "not_run"
    assert all(item.status == "blocked" for item in service.cases(blocked.id))
    assert "LIVE_EVALUATION_RUNTIME_NOT_CONFIGURED" in report.gate_reasons


def test_tampered_report_fails_hash_verification(tmp_path: Path) -> None:
    service, _evidence = _service(tmp_path)
    run = service.start(EvaluationRunRequest(profile="smoke"))
    with sqlite3.connect(service.repository.database) as connection:
        raw = connection.execute(
            "SELECT report_json FROM evaluation_reports WHERE run_id=?", (run.id,)
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["gate_status"] = "passed"
        connection.execute(
            "UPDATE evaluation_reports SET report_json=? WHERE run_id=?",
            (json.dumps(payload), run.id),
        )

    with pytest.raises(ProductError) as raised:
        service.report(run.id)
    assert raised.value.code == "EVALUATION_REPORT_HASH_INVALID"


def test_run_cancellation_is_idempotent_and_terminal_safe(tmp_path: Path) -> None:
    service, _evidence = _service(tmp_path)
    queued = service.create(EvaluationRunRequest(profile="smoke"))

    cancelled = service.cancel(queued.id)
    assert service.cancel(queued.id) == cancelled
    with pytest.raises(ProductError) as raised:
        service.cancel(service.start(EvaluationRunRequest(profile="smoke")).id)
    assert raised.value.code == "EVALUATION_RUN_TERMINAL"


def test_human_review_import_rejects_unknown_cases(tmp_path: Path) -> None:
    service, _evidence = _service(tmp_path)
    run = service.start(EvaluationRunRequest(profile="smoke"))
    sample = service.human_review_sample(run.id)
    review = HumanReview(
        run_id=run.id,
        case_id=sample[0].case_id,
        reviewer_id="reviewer-1",
        outcome="tie",
        notes_sha256=sha256_json({"notes": "same output"}),
    )

    assert service.import_reviews(run.id, (review,)) == (review,)
    invalid = review.model_copy(update={"id": "review-2", "case_id": "missing"})
    with pytest.raises(ProductError) as raised:
        service.import_reviews(run.id, (invalid,))
    assert raised.value.code == "HUMAN_REVIEW_CASE_INVALID"


def test_evaluation_http_contract_runs_in_background(tmp_path: Path) -> None:
    service, _evidence = _service(tmp_path)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )

    with TestClient(create_app(coordinator, evaluations=service)) as client:
        created = client.post("/v1/evaluation-runs", json={"profile": "smoke", "mode": "offline"})
        run_id = created.json()["id"]
        inspected = client.get(f"/v1/evaluation-runs/{run_id}")
        report = client.get(f"/v1/evaluation-runs/{run_id}/report")
        cases = client.get(f"/v1/evaluation-runs/{run_id}/cases")

    assert created.status_code == 202
    assert inspected.json()["status"] == "completed"
    assert report.status_code == 200
    assert report.json()["official_benchmark"] is False
    assert len(cases.json()) == 10


def test_three_standard_runs_create_immutable_calibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(evaluation_application._PROFILE, "standard", (10, (1, 2)))
    service, _evidence = _service(tmp_path)

    legacy = service.start(EvaluationRunRequest(profile="standard"))
    with sqlite3.connect(service.repository.database) as connection:
        suite_json, report_json = connection.execute(
            """SELECT runs.suite_json,reports.report_json FROM evaluation_runs runs
            JOIN evaluation_reports reports ON reports.run_id=runs.id WHERE runs.id=?""",
            (legacy.id,),
        ).fetchone()
        legacy_suite = json.loads(suite_json)
        legacy_suite["source_sha256"] = "b" * 64
        legacy_report = json.loads(report_json)
        legacy_report["gate_status"] = "failed"
        connection.execute(
            "UPDATE evaluation_runs SET suite_json=? WHERE id=?",
            (json.dumps(legacy_suite), legacy.id),
        )
        connection.execute(
            "UPDATE evaluation_reports SET report_json=? WHERE run_id=?",
            (json.dumps(legacy_report), legacy.id),
        )

    first = service.start(EvaluationRunRequest(profile="standard"))
    second = service.start(EvaluationRunRequest(profile="standard"))
    third = service.start(EvaluationRunRequest(profile="standard"))
    calibrated = service.repository.get_calibration(
        suite_sha256=str(first.suite.source_sha256),
        subject_fingerprint=str(first.candidate.pipeline_fingerprint),
    )
    fourth = service.start(EvaluationRunRequest(profile="standard"))

    assert service.report(first.id).gate_status == "calibrating"
    assert service.report(second.id).gate_status == "calibrating"
    assert service.report(third.id).gate_status == "calibrating"
    assert calibrated.sample_count == 3
    assert "control_plane.p95_latency_ms" in calibrated.metric_medians
    assert service.report(fourth.id).gate_status == "passed"
