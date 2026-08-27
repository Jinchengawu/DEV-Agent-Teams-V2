import json
import shutil
from pathlib import Path

import pytest

from agent_team_os.modules.evaluation import (
    EvaluationDatasetCase,
    default_dataset_dir,
    load_evaluation_dataset,
)
from agent_team_os.shared.errors import ProductError


def test_versioned_dataset_manifest_schema_and_live_boundary_are_stable() -> None:
    root = Path(__file__).parents[1]
    dataset = load_evaluation_dataset(default_dataset_dir(root))

    assert dataset.manifest.version == "1.3.0"
    assert dataset.manifest.official is False
    assert len(dataset.cases) == 10
    assert {key.value: value for key, value in dataset.manifest.case_counts.items()} == {
        "tool_call": 5,
        "general_agent": 3,
        "data_generation": 1,
        "control_plane": 1,
    }
    assert all("actual" not in case for case in dataset.execution_cases("live"))
    assert all("actual_calls" not in case for case in dataset.execution_cases("live"))
    assert any("actual_calls" in case for case in dataset.execution_cases("offline"))


def test_dataset_tampering_fails_closed(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    copied = tmp_path / "dataset"
    shutil.copytree(default_dataset_dir(root), copied)
    cases = copied / "cases.jsonl"
    cases.write_text(cases.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ProductError) as raised:
        load_evaluation_dataset(copied)
    assert raised.value.code == "EVALUATION_DATASET_HASH_INVALID"


def test_dataset_case_contract_rejects_unknown_shape() -> None:
    payload = {
        "id": "agent.invalid",
        "dimension": "general_agent",
        "category": "reasoning",
        "difficulty": 1,
        "input": {"prompt": "answer"},
        "expected": {"value": "ok"},
        "scoring": {"answer_type": "unsupported"},
        "fixture_output": {"value": "ok"},
        "unexpected": True,
    }

    with pytest.raises(ValueError):
        EvaluationDatasetCase.model_validate(payload)


def test_readme_evaluation_snapshot_matches_machine_summary() -> None:
    root = Path(__file__).parents[1]
    summary = json.loads(
        (root / "docs/evaluation/results/2026-08-24-offline-standard.json").read_text(
            encoding="utf-8"
        )
    )
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert f"suite {summary['suite_version']}" in readme
    assert summary["evidence_sha256"] in readme
    for dimension in summary["dimensions"].values():
        assert f"{dimension['evaluated']}/{dimension['total']}" in readme
    http = summary["control_plane_metrics_ms"]["candidate_http_latency"]
    graph = summary["control_plane_metrics_ms"]["candidate_graph_total_latency"]
    assert f"{http['p95']:.2f} ms" in readme
    assert f"{graph['p95']:.2f} ms" in readme


def test_evaluation_docs_and_engineering_prompt_are_chinese_first() -> None:
    root = Path(__file__).parents[1]
    default_documents = (
        root / "docs/evaluation/METHODOLOGY.md",
        root / "docs/evaluation/results/2026-08-24-offline-standard.md",
        root / "evaluation/datasets/agent-team-os-mvp/1.3.0/README.md",
    )

    for document in default_documents:
        content = document.read_text(encoding="utf-8")
        assert "默认中文版" in content
        assert document.with_name(f"{document.stem}.en{document.suffix}").exists()
    instructions = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "文档产出中文优先" in instructions
    assert "代码注释、CLI/API 描述和功能介绍默认使用简体中文" in instructions
    assert (root / "README.en.md").exists()
