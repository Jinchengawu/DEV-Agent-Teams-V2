"""仅校验候选 Dataset 的身份、可判定期望与预算；不执行语义验收。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team_os.shared.hashes import sha256_bytes, sha256_json

DATASET = json.loads((Path(__file__).parent / "fixtures/query_semantic_candidate.json").read_text())


def reconstruct(case):
    recipe = case["query_recipe"]
    start, middle, end = (recipe[key] for key in ("start", "middle", "end"))
    padding = recipe["goal_characters"] - len(start + middle + end)
    assert padding >= 0
    filler = (recipe["filler"] * (padding // len(recipe["filler"]) + 1))[:padding]
    goal = start + filler[:padding // 2] + middle + filler[padding // 2:] + end
    return goal, recipe["metadata"] + goal


@pytest.mark.parametrize("case", DATASET["cases"], ids=lambda case: case["id"])
def test_candidate_hashes_sources_expectations_and_context_fit(case):
    goal, query = reconstruct(case)
    assert len(goal) == case["query_recipe"]["goal_characters"]
    assert len(query) == case["query_characters"]
    assert sha256_bytes(goal.encode()) == case["goal_sha256"]
    assert sha256_bytes(query.encode()) == case["query_sha256"]
    assert sha256_json(DATASET["sources"]) == case["source_fixture_sha256"]
    sources = {source["id"]: source for source in DATASET["sources"]}
    authorized = {tuple(citation) for citation in case["authorized_citations"]}
    required = set(case["required_citations"])
    assert not required.intersection(case["forbidden_citations"])
    for key in required:
        source = sources[key]
        assert source["authorized"]
        assert tuple(source["canonical_citation"]) in authorized
        assert sha256_bytes(source["text"].encode()) == source["source_fixture_sha256"]
    source_bytes = sum(len(sources[key]["text"].encode()) for key in required)
    assert source_bytes < case["context_budget_bytes"]
    for relation in case["relations"]:
        assert set(relation["sources"]) <= required
    for decision in case["expected_decisions"]:
        facts = decision["facts"]
        # 冻结真值表内部一致性，不是从模型输出提取关系，更不是召回得分。
        expected = bool(facts["tests_pass"] and facts["approval_current"]
                        and not facts["security_failed"] and facts["artifact_hash_matches"])
        assert expected == decision["release"]


def test_candidate_matrix_and_unapproved_evidence_boundary():
    assert DATASET["product_review"] == "pending"
    assert DATASET["evidence_scope"] == "candidate-only-not-executed-semantic-acceptance"
    assert sha256_json(DATASET["sources"]) == DATASET["source_manifest_sha256"]
    matrix = {(case["language"], case["length_band"]) for case in DATASET["cases"]}
    assert {(language, band) for language in ("ascii", "cjk", "mixed", "emoji-combining")
            for band in ("short", "boundary", "maximum")} <= matrix
    assert len({case["id"] for case in DATASET["cases"]}) == len(DATASET["cases"])
    assert {"zero-hit", "revoked-source", "long-metadata", "failure-400-structural"} <= {
        case["id"] for case in DATASET["cases"]
    }
    ascii_case = next(case for case in DATASET["cases"] if case["id"] == "ascii-maximum")
    assert reconstruct(ascii_case)[1].isascii()


def test_candidate_duplicate_clause_oracle_is_comparable_and_explicit():
    cases = {case["id"]: case for case in DATASET["cases"]}
    control = cases["duplicate-control"]
    repeated = cases["duplicate-clauses"]
    oracle = repeated["duplicate_oracle"]
    assert oracle["control_case_id"] == control["id"]
    assert repeated["required_citations"] == control["required_citations"]
    assert repeated["forbidden_citations"] == control["forbidden_citations"]
    assert oracle["expected_final_occurrences"] == {
        citation: 1 for citation in repeated["required_citations"]
    }
    assert oracle["expected_final_order"] == control["expected_final_order"]
    assert oracle["scores_equal_to_control"] is True
    assert oracle["no_required_citation_displaced"] is True
    assert len(oracle["repeated_unit_rankings"]) > len(oracle["control_unit_rankings"])
    assert len({tuple(rank) for rank in oracle["repeated_unit_rankings"]}) == 1
    assert reconstruct(repeated)[1].count(oracle["repeated_clause"]) == 4
    assert reconstruct(control)[1].count(oracle["repeated_clause"]) == 1


def test_candidate_has_actual_long_stage_fields_and_preserves_project_only_case():
    cases = {case["id"]: case for case in DATASET["cases"]}
    combined = cases["long-project-stage-metadata"]
    fields = combined["metadata_fields"]
    assert len(fields["project_description"]) >= 4000
    assert len(fields["stage_path"]) >= 1000
    assert len(fields["stage_responsibility"]) >= 4000
    metadata = combined["query_recipe"]["metadata"]
    assert all(value in metadata for value in fields.values())
    assert combined["context_budget_bytes"] == 8192
    assert combined["required_citations"] == cases["long-metadata"]["required_citations"]
    assert combined["relations"] == cases["long-metadata"]["relations"]
