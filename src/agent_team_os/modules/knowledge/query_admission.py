"""首个 Provider I/O 前的确定性聚合预算；不选择模型或猜测容量。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...shared.hashes import Sha256, sha256_json
from .query_plan import QueryInputQualification, QueryPlan


class AggregateQueryBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    units: int = Field(gt=0)
    tokens: int = Field(gt=0)
    utf8_bytes: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    cache_bytes: int = Field(gt=0)


class PreparationQueryBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    preparation: AggregateQueryBudget
    stages: dict[str, AggregateQueryBudget] = Field(min_length=1)


class QueryAdmissionError(ValueError):
    def __init__(self, *, scope: str, resource: str, measured: int, allowed: int) -> None:
        self.code = "KNOWLEDGE_QUERY_AGGREGATE_BUDGET_EXCEEDED"
        self.safe_context = {
            "scope": scope,
            "resource": resource,
            "measured": measured,
            "allowed": allowed,
        }
        super().__init__(self.code)


class QueryAdmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    budget: PreparationQueryBudget
    plans: dict[str, Sha256]
    totals: dict[str, int]
    stage_totals: dict[str, dict[str, int]]

    @property
    def admission_sha256(self) -> Sha256:
        return sha256_json(self.model_dump(mode="json"))


def admit_query_plans(
    entries: tuple[tuple[str, str, QueryPlan, QueryInputQualification], ...],
    budget: PreparationQueryBudget,
) -> QueryAdmission:
    """time/cache按每个Query的冻结最大值预留，因此每Query执行上限之和有界。"""
    budget = PreparationQueryBudget.model_validate_json(budget.model_dump_json())
    if not entries or {entry[0] for entry in entries} != set(budget.stages):
        raise ValueError("KNOWLEDGE_QUERY_ADMISSION_STAGE_SET_MISMATCH")
    totals = dict.fromkeys(("units", "tokens", "utf8_bytes", "duration_ms", "cache_bytes"), 0)
    stages = {stage: totals.copy() for stage in budget.stages}
    plans: dict[str, Sha256] = {}
    for stage, key, plan, qualification in entries:
        plan = QueryPlan.model_validate_json(plan.model_dump_json())
        if key in plans or plan.qualification_sha256 != qualification.qualification_sha256:
            raise ValueError("KNOWLEDGE_QUERY_ADMISSION_IDENTITY_CONFLICT")
        if plan.budget_sha256 != qualification.budget.budget_sha256:
            raise ValueError("KNOWLEDGE_QUERY_ADMISSION_IDENTITY_CONFLICT")
        q = qualification.budget
        if (
            len(plan.units) > q.max_units
            or plan.total_tokens > q.total_tokens
            or plan.total_bytes > q.total_bytes
            or any(
                unit.tokens > q.unit_tokens or unit.utf8_bytes > q.unit_bytes for unit in plan.units
            )
        ):
            raise ValueError("KNOWLEDGE_QUERY_ADMISSION_QUERY_BUDGET_EXCEEDED")
        plans[key] = plan.plan_sha256
        values = {
            "units": len(plan.units),
            "tokens": plan.total_tokens,
            "utf8_bytes": plan.total_bytes,
            "duration_ms": qualification.budget.max_duration_ms,
            "cache_bytes": qualification.budget.max_cache_bytes,
        }
        for resource, value in values.items():
            totals[resource] += value
            stages[stage][resource] += value
    for stage, values in stages.items():
        for resource, measured in values.items():
            allowed = getattr(budget.stages[stage], resource)
            if measured > allowed:
                raise QueryAdmissionError(
                    scope="stage", resource=resource, measured=measured, allowed=allowed
                )
    for resource, measured in totals.items():
        allowed = getattr(budget.preparation, resource)
        if measured > allowed:
            raise QueryAdmissionError(
                scope="preparation", resource=resource, measured=measured, allowed=allowed
            )
    return QueryAdmission(budget=budget, plans=plans, totals=totals, stage_totals=stages)
