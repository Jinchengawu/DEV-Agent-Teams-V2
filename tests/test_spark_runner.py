from __future__ import annotations

from pathlib import Path

import pytest

from agent_team_os.devtools.spark import (
    SPARK_MODEL,
    SparkFailure,
    SparkRunner,
    SparkTask,
    _reports_architecture_block,
    _verification_output_violation,
)


def task(**overrides: object) -> SparkTask:
    payload: dict[str, object] = {
        "id": "SPARK-TEST-001",
        "title": "Add evidence filter",
        "model": SPARK_MODEL,
        "architecture_revision": "ARCH-001",
        "base_revision": "a" * 40,
        "kind": "frontend-component",
        "allowed_paths": ["console/src/features/evidence/**"],
        "forbidden_paths": ["migrations/**"],
        "contracts": ["GET /v1/evidence"],
        "acceptance": ["不生成模拟证据"],
        "verification": ["pnpm test -- evidence"],
    }
    payload.update(overrides)
    return SparkTask.model_validate(payload)


def test_manifest_rejects_model_fallback_and_unsafe_paths() -> None:
    with pytest.raises(ValueError):
        task(model="gpt-5.3-codex")
    with pytest.raises(ValueError):
        task(allowed_paths=["../outside/**"])


def test_scope_gate_rejects_dependency_and_architecture_changes(tmp_path: Path) -> None:
    runner = SparkRunner(tmp_path)
    manifest = task()
    with pytest.raises(SparkFailure) as dependency:
        runner._verify_diff_scope(manifest, ["console/pnpm-lock.yaml"])
    assert dependency.value.code == "SPARK_DEPENDENCY_CHANGE"
    with pytest.raises(SparkFailure) as migration:
        runner._verify_diff_scope(manifest, ["migrations/0009_unsafe.sql"])
    assert migration.value.code == "SPARK_FORBIDDEN_PATH"


def test_scope_gate_accepts_only_manifest_paths(tmp_path: Path) -> None:
    runner = SparkRunner(tmp_path)
    runner._verify_diff_scope(task(), ["console/src/features/evidence/EvidenceFilters.tsx"])


def test_architecture_block_requires_an_explicit_final_status() -> None:
    assert _reports_architecture_block(
        "blocked/ARCHITECTURE_DECISION_REQUIRED\nMissing public contract."
    )
    assert not _reports_architecture_block(
        "No block: blocked/ARCHITECTURE_DECISION_REQUIRED is not needed."
    )


def test_verification_output_rejects_warnings_and_skips() -> None:
    assert _verification_output_violation("3 passed, 1 warning") == "warning"
    assert _verification_output_violation("2 passed, 1 skipped") == "skipped"
    assert _verification_output_violation("3 passed, 0 warnings, 0 skipped") is None


def test_previous_spark_attempt_is_archived_before_retry(tmp_path: Path) -> None:
    run_dir = tmp_path / ".agent-team-os" / "spark-runs" / "SPARK-TEST-001"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text('{"status":"failed"}', encoding="utf-8")
    (run_dir / "verification.json").write_text("[]", encoding="utf-8")

    SparkRunner._archive_previous_attempt(run_dir)

    assert not (run_dir / "result.json").exists()
    assert (run_dir / "attempts" / "001" / "result.json").exists()
    assert (run_dir / "attempts" / "001" / "verification.json").exists()
