from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import agent_team_os.failure_regression as failure_regression
from agent_team_os.failure_regression import (
    FailureRegressionDataset,
    load_failure_regression_dataset,
    replay_failure_regressions,
    run_failure_regression_cli,
    write_failure_regression_report,
)

PROJECT_ROOT = Path(__file__).parents[1]
DATASET_ROOT = (
    PROJECT_ROOT / "evaluation/datasets/delivery-failure-regression/1.0.0"
)


def _copy_dataset(tmp_path: Path) -> Path:
    target = tmp_path / "dataset"
    shutil.copytree(DATASET_ROOT, target)
    return target


def _refresh_hash(dataset: Path, field: str, file_name: str) -> None:
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = hashlib.sha256((dataset / file_name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _semantic_report(dataset: FailureRegressionDataset) -> dict[str, object]:
    report = replay_failure_regressions(dataset, project_root=PROJECT_ROOT).model_dump(mode="json")
    report.pop("created_at")
    for case in report["cases"]:
        case.pop("duration_ms")
    return report


def test_committed_case_pack_replays_all_fifteen_cases(tmp_path: Path) -> None:
    dataset = load_failure_regression_dataset(DATASET_ROOT)

    report = replay_failure_regressions(dataset, project_root=PROJECT_ROOT)
    write_failure_regression_report(report, tmp_path)

    assert dataset.manifest.case_counts == {"workcell_acceptance": 4, "design_contract": 11}
    assert {case.source_kind for case in dataset.cases} == {
        "sanitized_capture",
        "regression_fixture",
        "synthetic_negative",
    }
    assert report.proof_scope == "offline_contract_regression"
    assert report.totals == {
        "total": 15,
        "matched": 15,
        "mismatched": 0,
        "false_rejections": 0,
        "false_acceptances": 0,
        "execution_errors": 0,
        "skipped": 0,
    }
    assert (tmp_path / "report.json").is_file()
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "offline_contract_regression" in markdown
    assert "不证明 Live Agent" in markdown


def test_loader_rejects_tampered_registered_file(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    with (dataset / "cases.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_failure_regression_dataset(dataset)


def test_loader_rejects_duplicate_case_id(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    cases_path = dataset / "cases.jsonl"
    lines = cases_path.read_text(encoding="utf-8").splitlines()
    cases_path.write_text("\n".join([*lines, lines[0]]) + "\n", encoding="utf-8")
    _refresh_hash(dataset, "cases_sha256", "cases.jsonl")

    with pytest.raises(ValueError, match="case ids must be unique"):
        load_failure_regression_dataset(dataset)


def test_loader_rejects_path_escape(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cases_file"] = "../cases.jsonl"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="direct children"):
        load_failure_regression_dataset(dataset)


def test_loader_rejects_symbolic_link(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    cases_path = dataset / "cases.jsonl"
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(cases_path.read_bytes())
    cases_path.unlink()
    cases_path.symlink_to(outside)

    with pytest.raises(ValueError, match="symbolic links"):
        load_failure_regression_dataset(dataset)


def test_loader_rejects_unregistered_file(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    (dataset / "notes.txt").write_text("unregistered", encoding="utf-8")

    with pytest.raises(ValueError, match="files do not match manifest"):
        load_failure_regression_dataset(dataset)


def test_expected_outcome_mismatch_is_reported() -> None:
    dataset = load_failure_regression_dataset(DATASET_ROOT)
    changed = dataset.cases[0].model_copy(
        update={
            "expected_outcome": "rejected",
            "expected_error_code": "WORKCELL_ACCEPTANCE_ASSIGNMENT_INVALID",
        }
    )
    mismatched = dataset.model_copy(update={"cases": (changed,)})

    report = replay_failure_regressions(mismatched, project_root=PROJECT_ROOT)

    assert report.totals["mismatched"] == 1
    assert report.totals["false_acceptances"] == 1


def test_unexpected_exception_is_never_counted_as_an_expected_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = load_failure_regression_dataset(DATASET_ROOT)
    rejected = next(case for case in dataset.cases if case.expected_outcome == "rejected")
    selected = dataset.model_copy(update={"cases": (rejected,)})

    def fail_unexpectedly(*_arguments: object) -> None:
        raise RuntimeError("validator crashed")

    monkeypatch.setattr(failure_regression, "_dispatch", fail_unexpectedly)
    report = replay_failure_regressions(selected, project_root=PROJECT_ROOT)

    assert report.cases[0].actual_outcome == "execution_error"
    assert report.cases[0].actual_error_code == "RuntimeError"
    assert report.cases[0].matched is False
    assert report.totals["execution_errors"] == 1


def test_repeated_replay_is_semantically_stable() -> None:
    dataset = load_failure_regression_dataset(DATASET_ROOT)

    assert _semantic_report(dataset) == _semantic_report(dataset)


def test_cli_exit_codes_zero_one_and_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "success"
    assert (
        run_failure_regression_cli(
            ["--dataset", str(DATASET_ROOT), "--output-dir", str(output)]
        )
        == 0
    )

    monkeypatch.setattr(failure_regression, "_dispatch", lambda *_arguments: None)
    output = tmp_path / "mismatch"
    assert (
        run_failure_regression_cli(
            ["--dataset", str(DATASET_ROOT), "--output-dir", str(output)]
        )
        == 1
    )

    output = tmp_path / "invalid"
    assert (
        run_failure_regression_cli(
            ["--dataset", str(tmp_path / "missing"), "--output-dir", str(output)]
        )
        == 2
    )
