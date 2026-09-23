"""纯确定性计数夹具，不代表真实 tokenizer 或 Provider 容量资格。"""
from __future__ import annotations

import pytest

from agent_team_os.modules.knowledge.query_plan import (
    InputMeasurement,
    QueryBudget,
    QueryInputQualification,
    QueryPlan,
    QueryPlanError,
    check_query_execution_budget,
    compile_query_plan,
)
from agent_team_os.shared.hashes import sha256_bytes

H = sha256_bytes(b"deterministic-test-only")


class FixtureMeasurement:
    def verify_qualification(self, qualification: QueryInputQualification) -> bool:
        return qualification.evidence_kind == "deterministic"

    def measure(self, text: str) -> InputMeasurement:
        return InputMeasurement(tokens=len(text), utf8_bytes=len(text.encode()), identity_sha256=H)


def qualification(**budget: int) -> QueryInputQualification:
    return QueryInputQualification(
        status="qualified", evidence_kind="deterministic", base_qualification_sha256=H,
        model_digest="fixture-model", embedding_adapter_revision="fixture-adapter",
        measurement_identity_sha256=H, tokenizer_assets_sha256=H,
        tokenizer_contract="fixture-codepoints-including-special-tokens",
        capacity_evidence_sha256=H, qualification_receipt_sha256=H,
        budget=QueryBudget(**({"unit_tokens": 8, "unit_bytes": 32, "max_units": 100,
                              "total_tokens": 1000, "total_bytes": 4000,
                              "max_duration_ms": 30000, "max_cache_bytes": 100000,
                              "overlap_tokens": 0, "overlap_bytes": 0} | budget)),
    )


def compile_fixture(text: str, q: QueryInputQualification | None = None,
                    measurement: FixtureMeasurement | None = None) -> QueryPlan:
    return compile_query_plan(
        text, q or qualification(), measurement or FixtureMeasurement(),
        base_qualification_sha256=H, model_digest="fixture-model",
        embedding_adapter_revision="fixture-adapter", binding_sha256=H,
        allow_deterministic=True,
    )


@pytest.mark.parametrize("text", ["short", "中文🙂e\u0301" * 20, "x" * 80, " \n\t" * 30])
def test_lossless_stable_coverage(text: str) -> None:
    plan = compile_fixture(text)
    assert "".join(text[u.primary_start:u.end] for u in plan.units) == text
    assert plan.plan_sha256 == compile_fixture(text).plan_sha256
    for unit in plan.units:
        assert unit.reconstruct(text) == text[unit.start:unit.end]
        assert unit.tokens <= 8 and unit.utf8_bytes <= 32
    assert text not in plan.model_dump_json()


def test_short_query_unchanged() -> None:
    plan = compile_fixture("你好")
    assert len(plan.units) == 1
    assert plan.units[0].reconstruct("你好") == "你好"


def test_non_monotonic_token_counts_are_not_binary_searched() -> None:
    class NonMonotonic(FixtureMeasurement):
        def measure(self, text: str) -> InputMeasurement:
            return InputMeasurement(tokens=1 if len(text) in (1, 7) else 100,
                                    utf8_bytes=len(text.encode()), identity_sha256=H)
    plan = compile_fixture("abcdefghij", measurement=NonMonotonic())
    assert plan.units[0].end == 7


def test_no_fixture_qualification_in_live_path() -> None:
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_INPUT_UNQUALIFIED"):
        compile_query_plan("x", qualification(), FixtureMeasurement(),
                           base_qualification_sha256=H, model_digest="fixture-model",
                           embedding_adapter_revision="fixture-adapter", binding_sha256=H)


def test_arbitrary_hashes_are_not_capacity_proof() -> None:
    class Unverified(FixtureMeasurement):
        def verify_qualification(self, qualification: QueryInputQualification) -> bool:
            return False
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_INPUT_UNQUALIFIED"):
        compile_fixture("x", measurement=Unverified())


def test_total_budget_rejects_without_partial_plan() -> None:
    q = qualification()
    q = q.model_copy(update={"budget": q.budget.model_copy(update={"max_units": 1})})
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_BUDGET_EXCEEDED"):
        compile_fixture("x" * 30, q)


def test_identity_drift_and_bad_byte_count_fail_closed() -> None:
    class Drift(FixtureMeasurement):
        def measure(self, text: str) -> InputMeasurement:
            return InputMeasurement(tokens=1, utf8_bytes=999, identity_sha256=H)
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_MEASUREMENT_INVALID"):
        compile_fixture("x", measurement=Drift())


def test_unrepresentable_codepoint_and_tampered_source_fail() -> None:
    q = qualification()
    q = q.model_copy(update={"budget": q.budget.model_copy(update={"unit_bytes": 1})})
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_CAPACITY_INSUFFICIENT"):
        compile_fixture("🙂", q)
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_SOURCE_MISMATCH"):
        compile_fixture("abc").units[0].reconstruct("abd")


@pytest.mark.parametrize("field", ["total_tokens", "total_bytes"])
def test_each_aggregate_budget_is_enforced(field: str) -> None:
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_BUDGET_EXCEEDED"):
        compile_fixture("abcdefghij", qualification(**{field: 9}))


def test_overlaps_never_replace_primary_coverage() -> None:
    text = "abcdef\n\nghijkl\n\nmnopqr"
    q = qualification(unit_tokens=16, overlap_tokens=2, overlap_bytes=4)
    plan = compile_fixture(text, q)
    assert "".join(text[u.primary_start:u.end] for u in plan.units) == text
    assert any(u.start < u.primary_start for u in plan.units)
    for unit in plan.units:
        assert unit.primary_start - unit.start <= 2
        assert unit.tokens <= 16


def test_qualification_and_binding_identity_frozen_in_hash() -> None:
    q = qualification()
    altered = q.model_copy(update={"budget": qualification(unit_tokens=9).budget})
    assert compile_fixture("abc", q).plan_sha256 != compile_fixture("abc", altered).plan_sha256
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_INPUT_UNQUALIFIED"):
        compile_fixture("abc", q.model_copy(update={"model_digest": "other"}))


def test_missing_qualification_prevents_even_local_measurement() -> None:
    class MustNotRun(FixtureMeasurement):
        def verify_qualification(self, qualification: QueryInputQualification) -> bool:
            pytest.fail("unqualified inputs must be rejected first")
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_INPUT_UNQUALIFIED"):
        compile_query_plan("x", None, MustNotRun(), base_qualification_sha256=H,
                           model_digest="fixture-model",
                           embedding_adapter_revision="fixture-adapter",
                           binding_sha256=H)


def test_total_byte_overflow_stops_before_tokenizer_work() -> None:
    class NoMeasurement(FixtureMeasurement):
        def measure(self, text: str) -> InputMeasurement:
            pytest.fail("oversized source must fail before tokenizer work")
    with pytest.raises(QueryPlanError, match="KNOWLEDGE_QUERY_BUDGET_EXCEEDED"):
        compile_fixture("x" * 1001, qualification(total_bytes=1000), NoMeasurement())


def test_plan_deserialization_rejects_coverage_gaps() -> None:
    data = compile_fixture("abcdefghij").model_dump(mode="json")
    data["units"][1]["primary_start"] += 1
    with pytest.raises(ValueError, match="invalid query coverage"):
        QueryPlan.model_validate(data)


def test_twenty_thousand_codepoints_have_lossless_primary_coverage() -> None:
    text = "中🙂e\u0301" * 5000
    q = qualification(unit_tokens=2000, unit_bytes=8000, max_units=20,
                      total_tokens=20000, total_bytes=100000)
    plan = compile_fixture(text, q)
    assert "".join(text[u.primary_start:u.end] for u in plan.units).encode() == text.encode()


def test_errors_never_echo_input_or_adapter_error() -> None:
    class Broken(FixtureMeasurement):
        def measure(self, text: str) -> InputMeasurement:
            raise RuntimeError("secret query=" + text)
    with pytest.raises(QueryPlanError) as error:
        compile_fixture("confidential", measurement=Broken())
    assert str(error.value) == "KNOWLEDGE_QUERY_MEASUREMENT_INVALID"
    assert error.value.__suppress_context__


def test_execution_budget_requires_explicit_limits_and_freezes_them() -> None:
    budget = qualification().budget
    data = budget.model_dump()
    for field in ("max_duration_ms", "max_cache_bytes"):
        missing = {key: value for key, value in data.items() if key != field}
        with pytest.raises(ValueError):
            QueryBudget.model_validate(missing)
        changed = QueryBudget.model_validate(data | {field: data[field] + 1})
        assert changed.budget_sha256 != budget.budget_sha256


@pytest.mark.parametrize("elapsed,cache,code", [
    (30001, 0, "KNOWLEDGE_QUERY_TIME_BUDGET_EXCEEDED"),
    (0, 100001, "KNOWLEDGE_QUERY_CACHE_BUDGET_EXCEEDED"),
    (-1, 0, "KNOWLEDGE_QUERY_EXECUTION_BUDGET_INVALID"),
    (0, -1, "KNOWLEDGE_QUERY_EXECUTION_BUDGET_INVALID"),
])
def test_execution_budget_rejects_overflow_and_invalid_accounting(
    elapsed: int, cache: int, code: str,
) -> None:
    with pytest.raises(QueryPlanError, match=code):
        check_query_execution_budget(qualification().budget, elapsed_ms=elapsed, cache_bytes=cache)


def test_execution_budget_accepts_exact_limits() -> None:
    check_query_execution_budget(qualification().budget, elapsed_ms=30000, cache_bytes=100000)
