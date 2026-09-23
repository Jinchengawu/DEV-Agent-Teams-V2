"""合成精确词项 oracle；真实检索语义和产品 fixture 签认仍待验收。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team_os.modules.knowledge.query_plan import (
    InputMeasurement,
    QueryBudget,
    QueryInputQualification,
    QueryPlan,
    compile_query_plan,
)
from agent_team_os.shared.hashes import sha256_bytes

DATASET = json.loads(
    (Path(__file__).parent / "fixtures/knowledge_query_semantics.json").read_text()
)
H = sha256_bytes(b"synthetic-semantics-fixture-not-model-qualification")
TERMS = {
    "accept-start": "AC_START",
    "accept-middle": "AC_MIDDLE",
    "accept-tail": "AC_TAIL",
    "deny": "DENY_UNLESS",
    "exception": "EXCEPT_APPROVED",
    "prerequisite": "REQUIRES_FIRST",
    "second-condition": "AND_SECOND",
    "revoked-source": "AC_START",
    "opposite-clause": "ALLOW_UNCONDITIONALLY",
    "irrelevant": "UNRELATED_TOPIC",
}


class SyntheticMeasurement:
    """明示 codepoint fixture；不得用作生产 tokenizer。"""

    def verify_qualification(self, qualification: QueryInputQualification) -> bool:
        return qualification.evidence_kind == "deterministic"

    def measure(self, text: str) -> InputMeasurement:
        return InputMeasurement(tokens=len(text), utf8_bytes=len(text.encode()), identity_sha256=H)


def _compile(query: str) -> QueryPlan:
    qualification = QueryInputQualification(
        status="qualified", evidence_kind="deterministic", base_qualification_sha256=H,
        model_digest="synthetic-not-bge", embedding_adapter_revision="synthetic-v1",
        measurement_identity_sha256=H, tokenizer_assets_sha256=H,
        tokenizer_contract="synthetic-codepoint-identity", capacity_evidence_sha256=H,
        qualification_receipt_sha256=H,
        budget=QueryBudget(unit_tokens=512, unit_bytes=2048, max_units=300,
                           total_tokens=40000, total_bytes=160000,
                           max_duration_ms=30000, max_cache_bytes=1000000,
                           overlap_tokens=64, overlap_bytes=256),
    )
    return compile_query_plan(
        query, qualification, SyntheticMeasurement(), base_qualification_sha256=H,
        model_digest="synthetic-not-bge", embedding_adapter_revision="synthetic-v1",
        binding_sha256=H, allow_deterministic=True,
    )


def _source(language: str, characters: int) -> str:
    filler = {"ascii": "background ", "cjk": "背景内容和说明",
              "mixed": "背景metadata ", "unicode": "🙂e\u0301背景"}[language]
    left = "Project: AC_START DENY_UNLESS REQUIRES_FIRST\n\n"
    middle = "\n\nStage: AC_MIDDLE EXCEPT_APPROVED\n\n"
    right = "\n\nAC_TAIL AND_SECOND AC_START"
    padding = characters - len(left + middle + right)
    assert padding > 0
    padded = (filler * (padding // len(filler) + 1))[:padding]
    return left + padded[:padding // 2] + middle + padded[padding // 2:] + right


def _recall(units: list[str]) -> set[str]:
    # 先过滤来源再生成候选。该 oracle 只证明冻结标识符的召回，不理解自然语言。
    authorized = {key: term for key, term in TERMS.items() if key != "revoked-source"}
    return {key for unit in units for key, term in authorized.items() if term in unit}


@pytest.mark.parametrize("case", DATASET["cases"], ids=lambda case: case["id"])
def test_structural_coverage_is_separate_from_required_citation_recall(
    case: dict[str, str | int],
) -> None:
    query = _source(str(case["language"]), int(case["characters"]))
    plan = _compile(query)
    assert len(query) == case["characters"]
    # 结构不变量：这项通过本身不证明引用/语义正确。
    assert "".join(query[u.primary_start:u.end] for u in plan.units) == query
    transmitted = [unit.reconstruct(query) for unit in plan.units]
    found = _recall(transmitted)
    assert set(DATASET["required_citations"]) <= found
    assert not set(DATASET["forbidden_citations"]) & found
    for relation in DATASET["relations"]:
        assert set(relation["citations"]) <= found
    assert _recall(list(reversed(transmitted))) == found
    if int(case["characters"]) == 20000:
        # 依赖与目标位于不同片；聚合不能只选首片或末片。
        assert not any("REQUIRES_FIRST" in unit and "AC_TAIL" in unit for unit in transmitted)
    else:
        assert found == _recall([query])  # 合成短输入 golden，不代表真实模型 golden。


def test_complete_character_coverage_does_not_imply_successful_recall() -> None:
    query = _source("cjk", 220)
    plan = _compile(query)
    assert "".join(query[u.primary_start:u.end] for u in plan.units) == query
    # 即使 offset 覆盖全部字符，错误检索器仍会遗漏 Acceptance；必须独立断言。
    broken_retrieval: set[str] = set()
    assert not set(DATASET["required_citations"]) <= broken_retrieval


def test_zero_hits_are_not_fabricated_and_fixture_provenance_is_explicit() -> None:
    query = "没有匹配的查询🙂"
    assert _recall([u.reconstruct(query) for u in _compile(query).units]) == set()
    assert DATASET["product_review"] == "pending"
    assert DATASET["evidence_kind"] == "deterministic-exact-term-oracle"
