from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_team_os.infrastructure.database.migration import MigrationRunner
from agent_team_os.modules.artifacts.storage import ContentAddressedArtifactStorage
from agent_team_os.modules.knowledge.index_ports import EmbeddingFailure
from agent_team_os.modules.knowledge.query_admission import (
    AggregateQueryBudget,
    PreparationQueryBudget,
)
from agent_team_os.modules.knowledge.query_execution import QueryPlanExecutor
from agent_team_os.modules.knowledge.query_plan import (
    InputMeasurement,
    QueryBudget,
    QueryInputQualification,
    compile_query_plan,
)
from agent_team_os.modules.knowledge.query_repository import (
    QueryExecutionIdentity,
    SQLiteKnowledgeQueryRepository,
)
from agent_team_os.shared.hashes import sha256_bytes

H = sha256_bytes(b"deterministic fixture")


class FixtureMeasurement:
    def verify_qualification(self, qualification: QueryInputQualification) -> bool:
        return qualification.evidence_kind == "deterministic"

    def measure(self, text: str) -> InputMeasurement:
        return InputMeasurement(tokens=len(text), utf8_bytes=len(text.encode()), identity_sha256=H)


def setup(tmp_path: Path, *, admitted: bool = True):
    qualification = QueryInputQualification(
        status="qualified",
        evidence_kind="deterministic",
        base_qualification_sha256=H,
        model_digest="fixture",
        embedding_adapter_revision="fixture",
        measurement_identity_sha256=H,
        tokenizer_assets_sha256=H,
        tokenizer_contract="fixture",
        capacity_evidence_sha256=H,
        qualification_receipt_sha256=H,
        budget=QueryBudget(
            unit_tokens=2,
            unit_bytes=8,
            max_units=4,
            total_tokens=8,
            total_bytes=32,
            max_duration_ms=30000,
            max_cache_bytes=10000,
            overlap_tokens=0,
            overlap_bytes=0,
        ),
    )
    query = "abcd"
    plan = compile_query_plan(
        query,
        qualification,
        FixtureMeasurement(),
        base_qualification_sha256=H,
        model_digest="fixture",
        embedding_adapter_revision="fixture",
        binding_sha256=H,
        allow_deterministic=True,
    )
    identity = QueryExecutionIdentity(
        preparation_run_id="run",
        stage_path="stage",
        binding_id="b",
        input_sha256=plan.source_sha256,
        index_sha256=H,
        policy_sha256=H,
        qualification_sha256=qualification.qualification_sha256,
        budget_sha256=plan.budget_sha256,
        authorization_epoch_hash=H,
        index_revision_id="index",
        base_qualification_id="base",
        base_qualification_sha256=H,
        source_ids=("source",),
        policy_revision_id="policy",
    )
    database = tmp_path / "fixture.sqlite"
    MigrationRunner(database, Path("migrations")).migrate()
    repo = SQLiteKnowledgeQueryRepository(database)
    storage = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    if admitted:
        limit = AggregateQueryBudget(
            units=4, tokens=8, utf8_bytes=32, duration_ms=30000, cache_bytes=10000
        )
        repo.freeze_preparation(
            ((identity, plan, qualification),),
            budget=PreparationQueryBudget(preparation=limit, stages={"stage": limit}),
        )
    return qualification, query, plan, identity, repo, storage


def test_cached_completion_rechecks_authorization_and_integrity(tmp_path: Path) -> None:
    q, query, plan, identity, repo, storage = setup(tmp_path)
    calls = []
    executor = QueryPlanExecutor(
        repo, storage, lease_ttl=timedelta(seconds=30), max_cache_bytes=10000
    )

    def unit(text):
        calls.append(text)
        return ()

    assert executor.execute(
        identity, plan, q, query, unit, lambda: None, max_duration_seconds=30
    ) == ((), ())
    executor.execute(identity, plan, q, query, unit, lambda: None, max_duration_seconds=30)
    assert calls == ["ab", "cd"]

    def revoked():
        raise RuntimeError("REVOKED")

    with pytest.raises(RuntimeError, match="REVOKED"):
        executor.execute(identity, plan, q, query, unit, revoked, max_duration_seconds=30)
    digest = repo.unit_receipts(identity.execution_key)[0]
    (storage.root / "sha256" / digest[:2] / digest).write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="HASH_MISMATCH"):
        executor.execute(identity, plan, q, query, unit, lambda: None, max_duration_seconds=30)


@pytest.mark.parametrize("category", ["input_limit", "rate_limited", "provider_error"])
def test_partial_failure_safe_error_never_completes(tmp_path: Path, category: str) -> None:
    q, query, plan, identity, repo, storage = setup(tmp_path)
    executor = QueryPlanExecutor(
        repo, storage, lease_ttl=timedelta(seconds=30), max_cache_bytes=10000
    )

    def unit(text):
        if text == "cd":
            raise EmbeddingFailure("unsafe", "SECRET_SENTINEL", http_status=400, category=category)
        return ()

    with pytest.raises(EmbeddingFailure) as caught:
        executor.execute(identity, plan, q, query, unit, lambda: None, max_duration_seconds=30)
    assert "SECRET_SENTINEL" not in str(caught.value)
    assert repo.get_for_execution(identity).artifact_sha256 is None
    assert len(repo.unit_receipts(identity.execution_key)) == 1
    assert repo.list_safe_errors("run")[0].category == category
    assert caught.value.code == (
        "KNOWLEDGE_QUERY_INPUT_LIMIT"
        if category == "input_limit"
        else "KNOWLEDGE_QUERY_PROVIDER_FAILED"
    )
    assert "SECRET_SENTINEL" not in repo.list_safe_errors("run")[0].model_dump_json()


@pytest.mark.parametrize("category", ["timeout", "transport"])
def test_transient_restart_reuses_completed_units(tmp_path: Path, category: str) -> None:
    q, query, plan, identity, repo, storage = setup(tmp_path)
    now = datetime.now(UTC)
    executor = QueryPlanExecutor(
        repo, storage, lease_ttl=timedelta(seconds=1), max_cache_bytes=10000, clock=lambda: now
    )

    def interrupted(text):
        if text == "cd":
            raise EmbeddingFailure("UNAVAILABLE", "unsafe-sentinel", category=category)
        return ()

    with pytest.raises(EmbeddingFailure) as failure:
        executor.execute(
            identity, plan, q, query, interrupted, lambda: None, max_duration_seconds=30
        )
    assert failure.value.code == "KNOWLEDGE_OLLAMA_UNAVAILABLE"
    assert failure.value.category == category
    assert "unsafe-sentinel" not in str(failure.value)
    assert repo.list_safe_errors("run") == ()
    recovered = QueryPlanExecutor(
        repo,
        storage,
        lease_ttl=timedelta(seconds=5),
        max_cache_bytes=10000,
        clock=lambda: now,
    )
    calls = []

    def unit(text):
        calls.append(text)
        return ()

    recovered.execute(identity, plan, q, query, unit, lambda: None, max_duration_seconds=30)
    assert calls == ["cd"]


def test_restart_cannot_reset_elapsed_budget(tmp_path: Path) -> None:
    q, query, plan, identity, repo, storage = setup(tmp_path)
    now = datetime.now(UTC)
    executor = QueryPlanExecutor(
        repo, storage, lease_ttl=timedelta(seconds=1), max_cache_bytes=10000, clock=lambda: now
    )

    def interrupted(text):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        executor.execute(
            identity, plan, q, query, interrupted, lambda: None, max_duration_seconds=1
        )
    recovered = QueryPlanExecutor(
        repo,
        storage,
        lease_ttl=timedelta(seconds=1),
        max_cache_bytes=10000,
        clock=lambda: now + timedelta(seconds=2),
    )
    calls = []
    with pytest.raises(RuntimeError, match="TIME_BUDGET_EXCEEDED"):
        recovered.execute(
            identity,
            plan,
            q,
            query,
            lambda text: calls.append(text),
            lambda: None,
            max_duration_seconds=1,
        )
    assert calls == []
    assert repo.elapsed_ms(identity.execution_key, now=now + timedelta(seconds=2)) == 1000


def test_cache_budget_counts_all_units_and_manifest(tmp_path: Path) -> None:
    q, query, plan, identity, repo, storage = setup(tmp_path)
    executor = QueryPlanExecutor(
        repo, storage, lease_ttl=timedelta(seconds=30), max_cache_bytes=400
    )
    with pytest.raises(RuntimeError, match="CACHE_BUDGET_EXCEEDED"):
        executor.execute(
            identity, plan, q, query, lambda text: (), lambda: None, max_duration_seconds=30
        )
    assert repo.get_for_execution(identity).artifact_sha256 is None
    assert len(repo.unit_receipts(identity.execution_key)) == 2
    error = repo.list_safe_errors("run")[0]
    assert error.category == "budget"
    assert error.measured_cache_bytes > error.allowed_cache_bytes


def test_time_budget_persists_safe_metadata_with_valid_fence(tmp_path: Path, monkeypatch) -> None:
    q, query, plan, identity, repo, storage = setup(tmp_path)
    tick = [0.0]
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "agent_team_os.modules.knowledge.query_execution.time.monotonic", lambda: tick[0]
    )
    executor = QueryPlanExecutor(
        repo,
        storage,
        lease_ttl=timedelta(seconds=30),
        max_cache_bytes=10000,
        clock=lambda: now + timedelta(seconds=tick[0]),
    )

    def unit(text):
        tick[0] = 0.002
        return ()

    with pytest.raises(RuntimeError, match="TIME_BUDGET_EXCEEDED"):
        executor.execute(identity, plan, q, query, unit, lambda: None, max_duration_seconds=0.001)
    error = repo.list_safe_errors("run")[0]
    assert error.category == "budget"
    assert error.measured_duration_ms == 2
    assert error.allowed_duration_ms == 1
    assert repo.get_for_execution(identity).artifact_sha256 is None
    assert repo.elapsed_ms(identity.execution_key, now=now + timedelta(seconds=2)) == 2
