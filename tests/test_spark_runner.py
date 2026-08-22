from __future__ import annotations

from pathlib import Path

import pytest

from agent_team_os.devtools.spark import SPARK_MODEL, SparkFailure, SparkRunner, SparkTask


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
