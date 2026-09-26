import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from test_knowledge_query_execution import setup

from agent_team_os.modules.knowledge.query_admission import (
    AggregateQueryBudget,
    PreparationQueryBudget,
)
from agent_team_os.modules.knowledge.query_execution import QueryPlanExecutor
from agent_team_os.shared.hashes import Sha256


def test_multibinding_aggregate_rejects_before_freezing_any_plan(tmp_path: Path) -> None:
    q, _, plan, identity, repo, _ = setup(tmp_path, admitted=False)
    second = identity.model_copy(update={"binding_id": "second"})
    limit = AggregateQueryBudget(
        units=3, tokens=100, utf8_bytes=100, duration_ms=60000, cache_bytes=20000
    )
    budget = PreparationQueryBudget(preparation=limit, stages={"stage": limit})
    with pytest.raises(ValueError, match="AGGREGATE_BUDGET"):
        repo.freeze_preparation(((identity, plan, q), (second, plan, q)), budget=budget)
    assert repo.get_stage_plan("run", "stage", "b") is None
    assert repo.get_stage_plan("run", "stage", "second") is None


@pytest.mark.parametrize("scope", ["stage", "preparation"])
@pytest.mark.parametrize("multiple_stages", [False, True])
@pytest.mark.parametrize(
    "resource", ["units", "tokens", "utf8_bytes", "duration_ms", "cache_bytes"]
)
def test_all_aggregate_resources_are_atomic(
    tmp_path: Path, scope: str, multiple_stages: bool, resource: str
) -> None:
    q, _, plan, identity, repo, _ = setup(tmp_path, admitted=False)
    other_stage = "second-stage" if multiple_stages else "stage"
    second = identity.model_copy(update={"binding_id": "second", "stage_path": other_stage})
    large = dict(units=100, tokens=1000, utf8_bytes=1000, duration_ms=100000, cache_bytes=100000)
    unit_values = dict(units=2, tokens=4, utf8_bytes=4, duration_ms=30000, cache_bytes=10000)
    measured = unit_values[resource] * (1 if scope == "stage" and multiple_stages else 2)
    lower = AggregateQueryBudget(**(large | {resource: measured - 1}))
    upper = AggregateQueryBudget(**large)
    stages = {stage: upper for stage in {"stage", other_stage}}
    if scope == "stage":
        stages["stage"] = lower
    budget = PreparationQueryBudget(
        preparation=lower if scope == "preparation" else upper, stages=stages
    )
    with pytest.raises(ValueError, match="AGGREGATE_BUDGET"):
        repo.freeze_preparation(((identity, plan, q), (second, plan, q)), budget=budget)
    with sqlite3.connect(repo.database) as connection:
        assert connection.execute("SELECT count(*) FROM knowledge_query_plans").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM knowledge_query_admissions").fetchone()[0] == 0
        )


def test_freeze_conflict_rolls_back_all_plans_and_success_is_immutable(tmp_path: Path) -> None:
    q, _, plan, identity, repo, _ = setup(tmp_path, admitted=False)
    second = identity.model_copy(update={"binding_id": "second", "input_sha256": Sha256("f" * 64)})
    upper = AggregateQueryBudget(
        units=4, tokens=8, utf8_bytes=8, duration_ms=60000, cache_bytes=20000
    )
    budget = PreparationQueryBudget(preparation=upper, stages={"stage": upper})
    with pytest.raises(RuntimeError, match="PLAN_IDENTITY"):
        repo.freeze_preparation(((identity, plan, q), (second, plan, q)), budget=budget)
    assert repo.get_stage_plan("run", "stage", "b") is None
    second = identity.model_copy(update={"binding_id": "second"})
    entries = ((identity, plan, q), (second, plan, q))
    sha = repo.freeze_preparation(entries, budget=budget)
    assert repo.freeze_preparation(entries, budget=budget) == sha
    assert repo.freeze_preparation(tuple(reversed(entries)), budget=budget) == sha
    assert repo.assert_admitted(identity, plan).totals["units"] == 4
    changed = budget.model_copy(update={"preparation": upper.model_copy(update={"units": 5})})
    with pytest.raises(RuntimeError, match="IMMUTABLE"):
        repo.freeze_preparation(entries, budget=changed)


def test_unadmitted_plan_never_calls_provider(tmp_path: Path) -> None:
    q, query, plan, identity, repo, storage = setup(tmp_path, admitted=False)
    repo.freeze_plan(identity, plan=plan)
    executor = QueryPlanExecutor(
        repo, storage, lease_ttl=timedelta(seconds=30), max_cache_bytes=10000
    )
    calls = []
    with pytest.raises(RuntimeError, match="NOT_ADMITTED"):
        executor.execute(
            identity,
            plan,
            q,
            query,
            lambda text: calls.append(text),
            lambda: None,
            max_duration_seconds=30,
        )
    assert calls == []
