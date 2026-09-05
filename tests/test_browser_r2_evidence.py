from __future__ import annotations

import copy
from pathlib import Path
from urllib.parse import unquote

import pytest

from agent_team_os.knowledge_context_contract import KNOWLEDGE_CONTEXT_STAGE_PATHS
from agent_team_os.shared.hashes import sha256_json


@pytest.fixture
def evidence(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    import browser_feishu_knowledge_e2e as script

    epoch = "a" * 64
    paths = sorted(KNOWLEDGE_CONTEXT_STAGE_PATHS)
    artifacts = {path: {"stage_path": path} for path in paths}
    contexts = {
        path: {
            "stage_path": path,
            "citation_ids": [f"citation-{path}"],
            "authorization_epoch_hash": epoch,
            "trust_class": "external-collaborative",
            "artifact_reference": {"sha256": sha256_json(artifacts[path])},
        }
        for path in paths
    }
    snapshot = {
        "knowledge_context_bindings": {path: {"required": True} for path in paths},
        "knowledge_contexts": contexts,
        "knowledge_authorization_stamp": {"authorization_epoch_hash": epoch},
        "snapshot_sha256": "b" * 64,
    }
    delivery = {
        "status": "completed",
        "delivery_execution_snapshot": snapshot,
        "requirements": {"knowledge_citation_ids": contexts["requirements"]["citation_ids"]},
        "task": {"knowledge_citation_ids": contexts["tasking"]["citation_ids"]},
    }
    overview = {
        "preparation_run": {
            "status": "succeeded",
            "authorization_epoch_hash": epoch,
            "final_snapshot": copy.deepcopy(snapshot),
        },
        "contexts": list(contexts.values()),
        "unavailable": [],
    }
    trees = [
        {
            "workcell_run": {"id": f"run-{path}", "stage_path": path, "status": "succeeded"},
            "result": {
                "knowledge_citation_ids": contexts[path]["citation_ids"],
                "candidate_sha": None,
                "output_artifact_references": [{"sha256": "c" * 64}],
            },
            "verification": None,
            "result_validation": {"status": "passed"},
        }
        for path in paths
        if "/" in path
    ]
    fetched_artifacts = []

    def get(_context, _url, method, path):
        assert method == "GET"
        if "artifact?stage_path=" in path:
            stage = unquote(path.split("stage_path=", 1)[1])
            fetched_artifacts.append(stage)
            return artifacts[stage]
        if path.endswith("/knowledge-context"):
            return overview
        if path.endswith("/workcell-runs"):
            return trees
        assert path == "/v1/deliveries/delivery-1"
        return delivery

    monkeypatch.setattr(script, "_request_json", get)
    return script, delivery, overview, trees, artifacts, fetched_artifacts


def test_r2_scope_requires_seven_actual_artifact_reads_and_artifact_only_qa(evidence):
    script, _, _, trees, _, fetched = evidence
    scope = script._verify_gate_c_evidence(None, "http://127.0.0.1", "delivery-1")
    assert set(fetched) == set(KNOWLEDGE_CONTEXT_STAGE_PATHS)
    assert len(scope["workcell_run_ids"]) == 5
    assert scope["qa_preparation_run_id"] == next(
        tree["workcell_run"]["id"]
        for tree in trees
        if tree["workcell_run"]["stage_path"] == "qa-preparation-repair/qa-preparation"
    )


@pytest.mark.parametrize(
    "mismatch",
    ["optional", "missing", "artifact", "epoch", "citation", "qa_candidate", "qa_validation"],
)
def test_r2_scope_rejects_incomplete_or_unrelated_evidence(evidence, mismatch):
    script, delivery, overview, trees, artifacts, _ = evidence
    qa = next(tree for tree in trees if "qa-preparation" in tree["workcell_run"]["stage_path"])
    if mismatch == "optional":
        delivery["delivery_execution_snapshot"]["knowledge_context_bindings"]["requirements"][
            "required"
        ] = False
    elif mismatch == "missing":
        overview["contexts"].pop()
    elif mismatch == "artifact":
        artifacts["requirements"] = {"unrelated": True}
    elif mismatch == "epoch":
        overview["preparation_run"]["authorization_epoch_hash"] = "d" * 64
    elif mismatch == "citation":
        qa["result"]["knowledge_citation_ids"] = ["unfrozen-citation"]
    elif mismatch == "qa_candidate":
        qa["result"]["candidate_sha"] = "e" * 40
    else:
        qa["result_validation"]["status"] = "failed"
    with pytest.raises(AssertionError):
        script._verify_gate_c_evidence(None, "http://127.0.0.1", "delivery-1")
