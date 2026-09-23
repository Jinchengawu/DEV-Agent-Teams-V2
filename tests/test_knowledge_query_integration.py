import sqlite3

import pytest
from query_test_support import install_deterministic_query_qualification
from test_knowledge_hybrid_index import _fixture, _publish_build_contracts

from agent_team_os.modules.knowledge.index_domain import (
    KnowledgeIndexBuildRequest,
    KnowledgeRetrievalRequest,
    RetrievalEvaluationCase,
    RetrievalEvaluationPolicyCreate,
    RetrievalEvaluationRunRequest,
    RetrievalPolicyCreate,
)
from agent_team_os.modules.knowledge.index_ports import EmbeddingFailure
from agent_team_os.modules.knowledge.query_admission import (
    AggregateQueryBudget,
    PreparationQueryBudget,
)
from agent_team_os.modules.knowledge.query_plan import QueryBudget, QueryPlan
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_bytes, sha256_json


def _active(tmp_path, *, include_services=False, cache_limit=1000000):
    actor, binding, embedding, indexes, identity, projects, tenant = _fixture(tmp_path)
    profile, qualification = _publish_build_contracts(
        actor, indexes, profile_id="query-test", max_chunks=20
    )
    q = install_deterministic_query_qualification(
        indexes,
        qualification,
        budget=QueryBudget(
            unit_tokens=16,
            unit_bytes=64,
            max_units=100,
            total_tokens=1000,
            total_bytes=4000,
            overlap_tokens=0,
            overlap_bytes=0,
            max_duration_ms=30000,
            max_cache_bytes=cache_limit,
        ),
    )
    indexes.publish_retrieval_policy(
        actor,
        RetrievalPolicyCreate(
            id="small-query",
            display_name="Deterministic Small Query",
            index_profile_revision_id=profile.id,
            top_k=1,
            query_input_qualification_sha256=q.qualification_sha256,
        ),
    )
    built = indexes.build(
        actor,
        KnowledgeIndexBuildRequest(
            provider_binding_id=binding.id,
            index_profile_revision_id=profile.id,
            embedding_qualification_id=qualification.id,
        ),
    )
    cases = (
        RetrievalEvaluationCase(
            id="scope",
            query="Workspace",
            expected_source_ids=("docx:architecture",),
        ),
    )
    indexes.publish_evaluation_policy(
        actor,
        RetrievalEvaluationPolicyCreate(
            id="query-active-evaluation",
            retrieval_policy_revision_id="small-query",
            index_profile_revision_id=profile.id,
            dataset_manifest_sha256=sha256_json([case.model_dump(mode="json") for case in cases]),
            recall_at_k_min=0,
            zero_hit_rate_max=1,
            error_rate_max=0,
            p95_latency_ms_max=2000,
            peak_rss_bytes_max=1000000000,
            target_hardware="deterministic-test",
        ),
    )
    indexes.evaluate(
        actor,
        RetrievalEvaluationRunRequest(
            evaluation_policy_revision_id="query-active-evaluation",
            index_revision_id=built.id,
            cases=cases,
            target_hardware="deterministic-test",
        ),
    )
    indexes.activate(actor, built.id, expected_pointer_version=None)
    embedding.calls.clear()
    request = KnowledgeRetrievalRequest(
        project_id="rag-project",
        provider_binding_id=binding.id,
        retrieval_policy_revision_id="small-query",
        query="Frontend workspace Backend workspace Apply workspace " * 3,
        allowed_source_ids=("docx:architecture",),
    )
    if include_services:
        return actor, embedding, indexes, request, identity, projects, tenant
    return actor, embedding, indexes, request


def _receipt_count(indexes):
    with sqlite3.connect(indexes.repository.database) as connection:
        return connection.execute("SELECT count(*) FROM knowledge_retrieval_runs").fetchone()[0]


def _freeze(indexes, request, run="prep"):
    identity = indexes.freeze_query(
        request,
        preparation_run_id=run,
        stage_path="requirements",
        authorization_epoch_hash="a" * 64,
    )
    record = indexes.query_repository.get_for_execution(identity)
    plan = QueryPlan.model_validate_json(record.plan_json)
    qualification = indexes.query_repository.get_qualification(plan.qualification_sha256)
    q = qualification.budget
    limit = AggregateQueryBudget(
        units=q.max_units,
        tokens=q.total_tokens,
        utf8_bytes=q.total_bytes,
        duration_ms=q.max_duration_ms,
        cache_bytes=q.max_cache_bytes,
    )
    indexes.query_repository.freeze_preparation(
        ((identity, plan, qualification),),
        budget=PreparationQueryBudget(preparation=limit, stages={"requirements": limit}),
    )
    return identity


def test_missing_query_qualification_stops_before_provider_io(tmp_path):
    _, embedding, indexes, request = _active(tmp_path)
    indexes.query_measurement = None
    with pytest.raises(ProductError) as caught:
        indexes.preflight_query(request)
    assert caught.value.code == "KNOWLEDGE_QUERY_INPUT_UNQUALIFIED"
    assert caught.value.status_code == 422
    assert embedding.calls == []


def test_frozen_replay_never_reads_active_pointer(tmp_path, monkeypatch):
    actor, embedding, indexes, request = _active(tmp_path)
    identity = _freeze(indexes, request)
    result = indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    calls = len(embedding.calls)
    monkeypatch.setattr(
        indexes.repository,
        "get_active_index",
        lambda *_: pytest.fail("resume must not read Active Pointer"),
    )
    replay = indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert replay.hits == result.hits and len(embedding.calls) == calls


def test_partial_resume_uses_frozen_index_and_remaining_units_only(tmp_path, monkeypatch):
    actor, embedding, indexes, request = _active(tmp_path)
    identity = _freeze(indexes, request)
    plan = indexes.preflight_query(request)
    original = embedding.embed
    attempted = 0

    def transient(texts, *, model_name, truncate):
        nonlocal attempted
        attempted += 1
        if attempted == 2:
            raise EmbeddingFailure('KNOWLEDGE_OLLAMA_UNAVAILABLE', 'safe', category='timeout')
        return original(texts, model_name=model_name, truncate=truncate)

    monkeypatch.setattr(embedding, 'embed', transient)
    with pytest.raises(EmbeddingFailure):
        indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert len(indexes.query_repository.unit_receipts(identity.execution_key)) == 1
    monkeypatch.setattr(embedding, 'embed', original)
    monkeypatch.setattr(indexes.repository, 'get_active_index',
                        lambda *_: pytest.fail('no Active lookup on resume'))
    result = indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert result.receipt.index_revision_id == identity.index_revision_id
    assert len(embedding.calls) == len(plan.units)


@pytest.mark.parametrize('damage', ['scope', 'asset', 'policy'])
def test_frozen_identity_invalid_fails_before_provider(tmp_path, monkeypatch, damage):
    actor, embedding, indexes, request = _active(tmp_path)
    identity = _freeze(indexes, request)
    if damage == 'scope':
        request = request.model_copy(update={'allowed_source_ids': ('other',)})
    elif damage == 'asset':
        path = indexes.index_root / f'{identity.index_revision_id}.sqlite'
        path.chmod(0o600)
        path.write_bytes(b'corrupt fixture')
    else:
        policy = indexes.repository.get_retrieval_policy(request.retrieval_policy_revision_id)
        monkeypatch.setattr(indexes.repository, 'get_retrieval_policy',
                            lambda *_: policy.model_copy(update={'top_k': 2}))
    with pytest.raises(ProductError):
        indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert embedding.calls == []


@pytest.mark.parametrize('single', [False, True])
def test_public_retrieval_stops_when_frozen_query_time_budget_is_exceeded(
    tmp_path, monkeypatch, single,
):
    actor, embedding, indexes, request = _active(tmp_path)
    if single:
        request = request.model_copy(update={'query': 'Workspace'})
    receipts = _receipt_count(indexes)
    clock = [0.0]
    monkeypatch.setattr('agent_team_os.modules.knowledge.index_application.time.monotonic',
                        lambda: clock[0])
    original = embedding.embed

    def slow(texts, *, model_name, truncate):
        result = original(texts, model_name=model_name, truncate=truncate)
        clock[0] = 31.0
        return result

    monkeypatch.setattr(embedding, 'embed', slow)
    with pytest.raises(ProductError, match='预算'):
        indexes.retrieve(actor, request)
    assert len(embedding.calls) == 1
    assert _receipt_count(indexes) == receipts


@pytest.mark.parametrize('single', [False, True])
def test_public_cache_limit_publishes_no_partial_receipt(tmp_path, single):
    actor, embedding, indexes, request = _active(tmp_path, cache_limit=1)
    if single:
        request = request.model_copy(update={'query': 'Workspace'})
    receipts = _receipt_count(indexes)
    with pytest.raises(ProductError) as error:
        indexes.retrieve(actor, request)
    assert error.value.status_code == 422
    assert len(embedding.calls) == 1
    assert _receipt_count(indexes) == receipts


def test_unadmitted_plan_stops_before_even_provider_describe(tmp_path, monkeypatch):
    actor, embedding, indexes, request = _active(tmp_path)
    identity = indexes.freeze_query(request, preparation_run_id='not-admitted',
                                    stage_path='requirements', authorization_epoch_hash='a' * 64)
    monkeypatch.setattr(embedding, 'describe',
                        lambda *_: pytest.fail('Provider I/O before admission'))
    with pytest.raises(RuntimeError, match='NOT_ADMITTED'):
        indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert embedding.calls == []


def test_multiunit_scope_topk_full_query_hash_and_cached_replay(tmp_path):
    actor, embedding, indexes, request = _active(tmp_path)
    plan = indexes.preflight_query(request)
    assert len(plan.units) > 1
    identity = _freeze(indexes, request)
    result = indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert len(result.hits) == 1
    assert {hit.source_id for hit in result.hits} == {"docx:architecture"}
    assert result.receipt.query_sha256 == sha256_bytes(request.query.encode())
    assert identity.input_sha256 == result.receipt.query_sha256
    assert len(embedding.calls) == len(plan.units)
    assert all(not truncate for _, truncate in embedding.calls)
    calls = len(embedding.calls)
    replay = indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert replay.hits == result.hits
    assert replay.receipt.query_sha256 == result.receipt.query_sha256
    assert len(embedding.calls) == calls
    assert len(indexes.query_repository.unit_receipts(identity.execution_key)) == len(plan.units)
    denied = request.model_copy(update={"allowed_source_ids": ("docx:other-project",)})
    assert indexes.retrieve(actor, denied).hits == ()


def test_failed_unit_never_publishes_retrieval_or_context(tmp_path, monkeypatch):
    actor, embedding, indexes, request = _active(tmp_path)
    identity = _freeze(indexes, request, "prep-fail")
    original = embedding.embed
    attempts = 0

    def fail_second(texts, *, model_name, truncate):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise EmbeddingFailure(
                "KNOWLEDGE_OLLAMA_REQUEST_FAILED",
                "safe fixture",
                http_status=400,
                category="input_limit",
            )
        return original(texts, model_name=model_name, truncate=truncate)

    monkeypatch.setattr(embedding, "embed", fail_second)
    before = _receipt_count(indexes)
    with pytest.raises(EmbeddingFailure):
        indexes.retrieve(actor, request, execution_identity=identity, admission=lambda: None)
    assert attempts == 2
    assert _receipt_count(indexes) == before
    record = indexes.query_repository.get_for_execution(identity)
    assert record.artifact_sha256 is None and record.final_context_sha256 is None
    assert len(indexes.query_repository.unit_receipts(identity.execution_key)) == 1
    errors = indexes.query_repository.list_safe_errors("prep-fail")
    assert len(errors) == 1 and errors[0].http_status == 400


def test_revoked_admission_stops_before_next_embed(tmp_path):
    actor, embedding, indexes, request = _active(tmp_path)
    identity = _freeze(indexes, request, "prep-revoked")

    def admission():
        if embedding.calls:
            raise RuntimeError("REVOKED")

    before = _receipt_count(indexes)
    with pytest.raises(RuntimeError, match="REVOKED"):
        indexes.retrieve(actor, request, execution_identity=identity, admission=admission)
    assert len(embedding.calls) == 1
    assert _receipt_count(indexes) == before
    assert indexes.query_repository.get_for_execution(identity).artifact_sha256 is None
