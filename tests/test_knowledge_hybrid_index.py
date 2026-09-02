from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.knowledge import SQLiteVectorIndexAdapter
from agent_team_os.infrastructure.ollama import OllamaEmbeddingAdapter
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
)
from agent_team_os.modules.knowledge import (
    EmbeddingModelDescriptor,
    EmbeddingQualificationRequest,
    KnowledgeActor,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexManager,
    KnowledgeIndexProfileCreate,
    KnowledgeRetrievalRequest,
    KnowledgeSyncJobRequest,
    ProviderNode,
    ProviderNodeKind,
    ProviderSnapshot,
    ProviderSpace,
    RetrievalEvaluationCase,
    RetrievalEvaluationPolicyCreate,
    RetrievalEvaluationRunRequest,
    RetrievalPolicyCreate,
    SQLiteKnowledgeIndexRepository,
    SQLiteTenantKnowledgeRepository,
    TenantConnectionCreate,
    TenantKnowledgeManager,
    TenantProviderBindingCreate,
)
from agent_team_os.modules.knowledge.index_application import _chunk_segments
from agent_team_os.modules.projects import (
    ProjectCatalog,
    ProjectCreate,
    ProjectKnowledgeSourceApprovalUpdate,
    SQLiteProjectRepository,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.permissions import Role
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService

ADMIN_PASSWORD = "secure-admin-2026"


class DeterministicWorkspaceProvisioner:
    def provision(self, repository_ref: str) -> str:
        return f"seed:{repository_ref}"

    def reset(self, repository_ref: str) -> str:
        return f"reset:{repository_ref}"

    def revision(self, repository_ref: str) -> str:
        return f"head:{repository_ref}"


class OneDocumentProvider:
    def __init__(self, block_texts: tuple[str, ...] | None = None) -> None:
        self.block_texts = block_texts or (
            "Frontend 和 Backend 使用独立 Git Workspace，不共享仓库。",
            "REMOTE_MAIN_APPLY_NOT_ALLOWED 必须 Fail Closed。",
        )

    def list_spaces(self) -> tuple[ProviderSpace, ...]:
        return (ProviderSpace(external_id="space-1", title="研发"),)

    def list_nodes(self, external_space_id: str) -> tuple[ProviderNode, ...]:
        return (
            ProviderNode(
                external_id="node-1",
                external_space_id=external_space_id,
                source_id="docx:architecture",
                title="架构规范",
                kind=ProviderNodeKind.DOCUMENT,
                provider_revision="rev-1",
            ),
        )

    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
        normalized = {
            "type": "feishu-docx-blocks-v1",
            "blocks": [
                {
                    "block_id": f"block-{index}",
                    "text": text,
                }
                for index, text in enumerate(self.block_texts, 1)
            ],
        }
        text = "\n".join(block["text"] for block in normalized["blocks"])
        return ProviderSnapshot(
            source_id=source_id,
            provider_revision="rev-1",
            content_type="application/json",
            normalized_content=normalized,
            normalized_text=text,
            content_sha256=sha256_json(normalized),
            source_url="https://example.invalid/wiki/architecture",
            fetched_at=datetime(2026, 9, 2, tzinfo=UTC),
        )


class StaticResolver:
    def __init__(self, provider: OneDocumentProvider) -> None:
        self.provider = provider

    def resolve(self, _connection):  # type: ignore[no-untyped-def]
        return self.provider


class DeterministicEmbeddingPort:
    adapter_revision = "deterministic-embedding-v1"

    def __init__(self) -> None:
        self.digest = "sha256:" + "1" * 64
        self.calls: list[tuple[tuple[str, ...], bool]] = []
        self.dimension_drift = False

    def describe(self, model_name: str) -> EmbeddingModelDescriptor:
        return EmbeddingModelDescriptor(
            model_name=model_name,
            model_digest=self.digest,
        )

    def embed(
        self, texts: tuple[str, ...], *, model_name: str, truncate: bool
    ) -> tuple[tuple[float, ...], ...]:
        self.calls.append((texts, truncate))
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                (
                    0.01 + float("workspace" in lowered or "仓库" in text),
                    float("frontend" in lowered),
                    float("backend" in lowered),
                    float("apply" in lowered),
                    *((1.0,) if self.dimension_drift else ()),
                )
            )
        return tuple(vectors)


class ZeroEmbeddingPort(DeterministicEmbeddingPort):
    def embed(
        self, texts: tuple[str, ...], *, model_name: str, truncate: bool
    ) -> tuple[tuple[float, ...], ...]:
        del model_name, truncate
        return tuple((0.0, 0.0, 0.0, 0.0) for _text in texts)


def _fixture(
    tmp_path: Path,
    *,
    block_texts: tuple[str, ...] | None = None,
):  # type: ignore[no-untyped-def]
    database = tmp_path / "hybrid.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    admin = identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    actor = KnowledgeActor(user_id=admin.id, role=Role.ADMINISTRATOR)
    projects = ProjectCatalog(
        SQLiteProjectRepository(database),
        DeterministicWorkspaceProvisioner(),
    )
    projects.create(
        ProjectCreate(
            id="rag-project",
            name="RAG Project",
            default_pipeline_revision_id="backend-delivery:1",
        ),
        admin.id,
    )
    artifacts = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    provider = OneDocumentProvider(block_texts)
    tenant = TenantKnowledgeManager(
        SQLiteTenantKnowledgeRepository(database),
        provider_resolver=StaticResolver(provider),
        artifact_storage=artifacts,
    )
    connection = tenant.create_connection(
        actor,
        TenantConnectionCreate(
            provider_kind="feishu",
            display_name="研发知识",
            app_id_ref="env:FEISHU_APP_ID",
            app_secret_ref="env:FEISHU_APP_SECRET",
        ),
    )
    connection = tenant.diagnose_connection(actor, connection.id)
    assert connection.status == "ready"
    binding = tenant.create_binding(
        actor,
        TenantProviderBindingCreate(
            connection_id=connection.id,
            display_name="研发 Wiki",
            external_space_id="space-1",
        ),
    )
    projects.configure_knowledge_binding_validator(tenant.require_binding)
    projects.put_knowledge_source_approval(
        "rag-project",
        binding.id,
        request=ProjectKnowledgeSourceApprovalUpdate(enabled=True, rag_enabled=True),
        actor_id=admin.id,
    )
    tenant.request_sync(
        actor,
        "rag-project",
        request=KnowledgeSyncJobRequest(
            binding_id=binding.id,
            source_id="docx:architecture",
            idempotency_key="initial-sync",
        ),
    )
    embeddings = DeterministicEmbeddingPort()
    indexes = KnowledgeIndexManager(
        SQLiteKnowledgeIndexRepository(database),
        tenant_repository=tenant.repository,
        artifact_storage=artifacts,
        index_root=tmp_path / "indexes",
        embedding_port=embeddings,
        vector_index_port=SQLiteVectorIndexAdapter(),
    )
    return actor, binding, embeddings, indexes, identity, projects, tenant


def _publish_build_contracts(
    actor: KnowledgeActor,
    indexes: KnowledgeIndexManager,
    *,
    profile_id: str,
    max_chunks: int,
    warning_ratio: float = 0.8,
):  # type: ignore[no-untyped-def]
    profile = indexes.publish_index_profile(
        actor,
        KnowledgeIndexProfileCreate(
            id=profile_id,
            display_name=profile_id,
            embedding_model_name="bge-m3",
            max_documents=2,
            max_chunks=max_chunks,
            capacity_warning_ratio=warning_ratio,
        ),
    )
    qualification = indexes.qualify_embedding(
        actor,
        EmbeddingQualificationRequest(model_name="bge-m3"),
    )
    policy = indexes.publish_retrieval_policy(
        actor,
        RetrievalPolicyCreate(
            id=f"{profile_id}-policy",
            display_name=f"{profile_id} policy",
            index_profile_revision_id=profile.id,
        ),
    )
    indexes.publish_evaluation_policy(
        actor,
        RetrievalEvaluationPolicyCreate(
            id=f"{profile_id}-evaluation",
            retrieval_policy_revision_id=policy.id,
            index_profile_revision_id=profile.id,
            dataset_manifest_sha256="1" * 64,
            recall_at_k_min=0.0,
            zero_hit_rate_max=1.0,
            error_rate_max=1.0,
            p95_latency_ms_max=2_000,
            peak_rss_bytes_max=1_000_000_000,
            target_hardware="deterministic-test",
        ),
    )
    return profile, qualification


def test_block_aware_chunker_caps_and_overlaps_long_blocks() -> None:
    text = "".join(str(index % 10) for index in range(2_500))

    segments = _chunk_segments(text, max_characters=1_200, overlap_characters=150)

    assert tuple(len(segment) for segment in segments) == (1_200, 1_200, 400)
    assert segments[0][-150:] == segments[1][:150]
    assert segments[1][-150:] == segments[2][:150]


def test_embedding_qualification_rejects_zero_vectors(tmp_path: Path) -> None:
    actor, _binding, _embeddings, indexes, *_ = _fixture(tmp_path)
    indexes.embedding_port = ZeroEmbeddingPort()

    with pytest.raises(ProductError) as error:
        indexes.qualify_embedding(
            actor,
            EmbeddingQualificationRequest(model_name="bge-m3"),
        )

    assert error.value.code == "KNOWLEDGE_EMBEDDING_QUALIFICATION_FAILED"


def test_index_build_batches_embeddings_and_reports_capacity_warning(
    tmp_path: Path,
) -> None:
    actor, binding, embeddings, indexes, *_ = _fixture(
        tmp_path,
        block_texts=tuple(f"容量基准 Chunk {index}" for index in range(33)),
    )
    profile, qualification = _publish_build_contracts(
        actor,
        indexes,
        profile_id="capacity-warning-profile",
        max_chunks=40,
    )

    built = indexes.build(
        actor,
        KnowledgeIndexBuildRequest(
            provider_binding_id=binding.id,
            index_profile_revision_id=profile.id,
            embedding_qualification_id=qualification.id,
        ),
    )

    assert built.chunk_count == 33
    assert built.document_count == 1
    assert built.capacity_status == "warning"
    build_calls = [texts for texts, _truncate in embeddings.calls if len(texts) != 2]
    assert tuple(len(texts) for texts in build_calls) == (32, 1)


def test_index_build_fails_closed_at_published_chunk_capacity(tmp_path: Path) -> None:
    actor, binding, _embeddings, indexes, *_ = _fixture(tmp_path)
    profile, qualification = _publish_build_contracts(
        actor,
        indexes,
        profile_id="capacity-limit-profile",
        max_chunks=1,
    )

    with pytest.raises(ProductError) as error:
        indexes.build(
            actor,
            KnowledgeIndexBuildRequest(
                provider_binding_id=binding.id,
                index_profile_revision_id=profile.id,
                embedding_qualification_id=qualification.id,
            ),
        )

    assert error.value.code == "KNOWLEDGE_INDEX_CHUNK_CAPACITY_EXCEEDED"
    failed = indexes.repository.list_index_revisions()[0]
    assert failed.status == "failed"
    assert failed.error_code == "KNOWLEDGE_INDEX_CHUNK_CAPACITY_EXCEEDED"


def test_hybrid_index_is_immutable_qualified_and_scope_filtered(tmp_path: Path) -> None:
    actor, binding, embeddings, indexes, *_ = _fixture(tmp_path)
    profile = indexes.publish_index_profile(
        actor,
        KnowledgeIndexProfileCreate(
            id="cjk-hybrid-v1",
            display_name="CJK Hybrid v1",
            embedding_model_name="bge-m3",
        ),
    )
    qualification = indexes.qualify_embedding(
        actor,
        EmbeddingQualificationRequest(model_name="bge-m3"),
    )
    policy = indexes.publish_retrieval_policy(
        actor,
        RetrievalPolicyCreate(
            id="retrieval-v1",
            display_name="Retrieval v1",
            index_profile_revision_id=profile.id,
            lexical_candidates=20,
            vector_candidates=20,
            top_k=4,
            rrf_k=60,
            score_precision=8,
            max_context_bytes=16_384,
        ),
    )
    cases = (
        RetrievalEvaluationCase(
            id="workspace-isolation",
            query="前后端为什么不能共享 workspace？",
            expected_source_ids=("docx:architecture",),
        ),
    )
    evaluation_policy = indexes.publish_evaluation_policy(
        actor,
        RetrievalEvaluationPolicyCreate(
            id="retrieval-eval-v1",
            retrieval_policy_revision_id=policy.id,
            index_profile_revision_id=profile.id,
            dataset_manifest_sha256=sha256_json([item.model_dump(mode="json") for item in cases]),
            recall_at_k_min=0.5,
            zero_hit_rate_max=0.5,
            error_rate_max=0.0,
            p95_latency_ms_max=2_000,
            peak_rss_bytes_max=1_000_000_000,
            target_hardware="deterministic-test",
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
    assert built.status == "built"
    with pytest.raises(ProductError) as not_evaluated:
        indexes.activate(actor, built.id, expected_pointer_version=None)
    assert not_evaluated.value.code == "KNOWLEDGE_INDEX_NOT_QUALIFIED"
    evaluation = indexes.evaluate(
        actor,
        RetrievalEvaluationRunRequest(
            evaluation_policy_revision_id=evaluation_policy.id,
            index_revision_id=built.id,
            cases=cases,
            target_hardware="deterministic-test",
        ),
    )
    qualified = indexes.repository.get_index_revision(built.id)
    assert qualified is not None
    active = indexes.activate(actor, qualified.id, expected_pointer_version=None)

    result = indexes.retrieve(
        actor,
        KnowledgeRetrievalRequest(
            project_id="rag-project",
            provider_binding_id=binding.id,
            retrieval_policy_revision_id=policy.id,
            query="前后端为什么不能共享 workspace？",
            allowed_source_ids=("docx:architecture",),
        ),
    )
    denied = indexes.retrieve(
        actor,
        KnowledgeRetrievalRequest(
            project_id="rag-project",
            provider_binding_id=binding.id,
            retrieval_policy_revision_id=policy.id,
            query="前后端为什么不能共享 workspace？",
            allowed_source_ids=(),
        ),
    )

    assert evaluation.status == "passed"
    assert qualified.status == "qualified"
    assert qualified.evaluation_report_sha256 == evaluation.report_artifact.sha256
    assert active.status == "active"
    assert active.storage_sha256 is not None
    assert active.chunk_count == 2
    assert result.hits
    assert result.hits[0].source_id == "docx:architecture"
    assert "Workspace" in result.hits[0].content
    assert result.receipt.allowed_source_set_sha256
    assert denied.hits == ()
    assert denied.receipt.empty_reason == "approved-scope-empty"
    assert embeddings.calls
    assert all(truncate is False for _texts, truncate in embeddings.calls)


def test_model_digest_drift_fails_closed_at_retrieval(tmp_path: Path) -> None:
    actor, binding, embeddings, indexes, *_ = _fixture(tmp_path)
    profile = indexes.publish_index_profile(
        actor,
        KnowledgeIndexProfileCreate(
            id="drift-profile",
            display_name="Drift Profile",
            embedding_model_name="bge-m3",
        ),
    )
    qualification = indexes.qualify_embedding(
        actor, EmbeddingQualificationRequest(model_name="bge-m3")
    )
    policy = indexes.publish_retrieval_policy(
        actor,
        RetrievalPolicyCreate(
            id="drift-policy",
            display_name="Drift Policy",
            index_profile_revision_id=profile.id,
        ),
    )
    cases = (
        RetrievalEvaluationCase(
            id="drift-workspace",
            query="workspace",
            expected_source_ids=("docx:architecture",),
        ),
    )
    evaluation_policy = indexes.publish_evaluation_policy(
        actor,
        RetrievalEvaluationPolicyCreate(
            id="drift-evaluation",
            retrieval_policy_revision_id=policy.id,
            index_profile_revision_id=profile.id,
            dataset_manifest_sha256=sha256_json([item.model_dump(mode="json") for item in cases]),
            recall_at_k_min=0.0,
            zero_hit_rate_max=1.0,
            error_rate_max=0.0,
            p95_latency_ms_max=2_000,
            peak_rss_bytes_max=1_000_000_000,
            target_hardware="deterministic-test",
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
    indexes.evaluate(
        actor,
        RetrievalEvaluationRunRequest(
            evaluation_policy_revision_id=evaluation_policy.id,
            index_revision_id=built.id,
            cases=cases,
            target_hardware="deterministic-test",
        ),
    )
    indexes.activate(actor, built.id, expected_pointer_version=None)
    embeddings.digest = "sha256:" + "9" * 64

    with pytest.raises(ProductError) as error:
        indexes.retrieve(
            actor,
            KnowledgeRetrievalRequest(
                project_id="rag-project",
                provider_binding_id=binding.id,
                retrieval_policy_revision_id=policy.id,
                query="workspace",
                allowed_source_ids=("docx:architecture",),
            ),
        )

    assert error.value.code == "KNOWLEDGE_MODEL_QUALIFICATION_DRIFT"


def test_ollama_adapter_uses_exact_digest_and_forbids_truncation() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "bge-m3:latest",
                            "digest": "sha256:" + "a" * 64,
                        }
                    ]
                },
            )
        assert request.url.path == "/api/embed"
        assert request.read() == (
            b'{"model":"bge-m3:latest","input":["alpha","beta"],"truncate":false}'
        )
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0], [0.0, 1.0]]})

    adapter = OllamaEmbeddingAdapter(
        client=httpx.Client(
            base_url="http://127.0.0.1:11434",
            transport=httpx.MockTransport(handler),
        )
    )

    descriptor = adapter.describe("bge-m3:latest")
    vectors = adapter.embed(
        ("alpha", "beta"),
        model_name="bge-m3:latest",
        truncate=False,
    )

    assert descriptor.model_digest == "sha256:" + "a" * 64
    assert vectors == ((1.0, 0.0), (0.0, 1.0))
    assert [request.url.path for request in requests] == ["/api/tags", "/api/embed"]


def test_ollama_default_client_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_team_os.infrastructure.ollama import embedding as embedding_module

    client_options: dict[str, object] = {}

    class ClientSpy:
        def __init__(self, **options: object) -> None:
            client_options.update(options)

    monkeypatch.setattr(embedding_module.httpx, "Client", ClientSpy)

    OllamaEmbeddingAdapter()

    assert client_options["base_url"] == "http://127.0.0.1:11434"
    assert client_options["trust_env"] is False


@pytest.mark.anyio
async def test_retrieval_api_compiles_scope_server_side(tmp_path: Path) -> None:
    actor, binding, _embeddings, indexes, identity, projects, tenant = _fixture(tmp_path)
    profile = indexes.publish_index_profile(
        actor,
        KnowledgeIndexProfileCreate(
            id="api-profile",
            display_name="API Profile",
            embedding_model_name="bge-m3",
        ),
    )
    qualification = indexes.qualify_embedding(
        actor, EmbeddingQualificationRequest(model_name="bge-m3")
    )
    policy = indexes.publish_retrieval_policy(
        actor,
        RetrievalPolicyCreate(
            id="api-policy",
            display_name="API Policy",
            index_profile_revision_id=profile.id,
        ),
    )
    cases = (
        RetrievalEvaluationCase(
            id="api-workspace",
            query="workspace",
            expected_source_ids=("docx:architecture",),
        ),
    )
    evaluation_policy = indexes.publish_evaluation_policy(
        actor,
        RetrievalEvaluationPolicyCreate(
            id="api-evaluation",
            retrieval_policy_revision_id=policy.id,
            index_profile_revision_id=profile.id,
            dataset_manifest_sha256=sha256_json([item.model_dump(mode="json") for item in cases]),
            recall_at_k_min=0.0,
            zero_hit_rate_max=1.0,
            error_rate_max=0.0,
            p95_latency_ms_max=2_000,
            peak_rss_bytes_max=1_000_000_000,
            target_hardware="deterministic-test",
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
    app = create_app(
        DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            resolved_journey_sha256="a" * 64,
        ),
        identity=identity,
        projects=projects,
        tenant_knowledge=tenant,
        knowledge_indexes=indexes,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/auth/login",
            headers={"Origin": "http://test"},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        headers = {
            "Origin": "http://test",
            "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
        }
        evaluation = await client.post(
            "/v1/knowledge/retrieval-evaluation-runs",
            headers=headers,
            json={
                "evaluation_policy_revision_id": evaluation_policy.id,
                "index_revision_id": built.id,
                "cases": [item.model_dump(mode="json") for item in cases],
                "target_hardware": "deterministic-test",
            },
        )
        activation = await client.post(
            f"/v1/knowledge/index-revisions/{built.id}/activate",
            headers=headers,
            json={"expected_pointer_version": None},
        )
        catalog = await client.get("/v1/knowledge/index-catalog")
        options = await client.get(
            "/v1/projects/rag-project/knowledge-retrieval-options",
            params={"provider_binding_id": binding.id},
        )
        injected_scope = await client.post(
            "/v1/projects/rag-project/knowledge-retrieval-preview",
            headers=headers,
            json={
                "provider_binding_id": binding.id,
                "retrieval_policy_revision_id": policy.id,
                "query": "workspace",
                "allowed_source_ids": ["docx:other-project"],
            },
        )
        result = await client.post(
            "/v1/projects/rag-project/knowledge-retrieval-preview",
            headers=headers,
            json={
                "provider_binding_id": binding.id,
                "retrieval_policy_revision_id": policy.id,
                "query": "workspace",
            },
        )

    assert evaluation.status_code == 201
    assert evaluation.json()["status"] == "passed"
    assert activation.status_code == 200
    assert catalog.status_code == 200
    assert [item["id"] for item in catalog.json()["profiles"]] == [profile.id]
    assert [item["id"] for item in catalog.json()["qualifications"]] == [qualification.id]
    assert [item["id"] for item in catalog.json()["retrieval_policies"]] == [policy.id]
    assert [item["id"] for item in catalog.json()["evaluation_policies"]] == [
        evaluation_policy.id
    ]
    assert [item["id"] for item in catalog.json()["index_revisions"]] == [built.id]
    assert [item["id"] for item in catalog.json()["evaluation_reports"]] == [
        evaluation.json()["id"]
    ]
    assert options.status_code == 200
    assert options.json() == [
        {
            "provider_binding_id": binding.id,
            "index_revision_id": built.id,
            "index_profile_revision_id": profile.id,
            "retrieval_policy_revision_id": policy.id,
        }
    ]
    assert injected_scope.status_code == 422
    assert result.status_code == 200
    assert result.json()["hits"][0]["source_id"] == "docx:architecture"


def test_failed_evaluation_is_persisted_and_cannot_be_activated(tmp_path: Path) -> None:
    actor, binding, _embeddings, indexes, *_ = _fixture(tmp_path)
    profile = indexes.publish_index_profile(
        actor,
        KnowledgeIndexProfileCreate(
            id="failed-eval-profile",
            display_name="Failed Evaluation Profile",
            embedding_model_name="bge-m3",
        ),
    )
    qualification = indexes.qualify_embedding(
        actor, EmbeddingQualificationRequest(model_name="bge-m3")
    )
    policy = indexes.publish_retrieval_policy(
        actor,
        RetrievalPolicyCreate(
            id="failed-eval-policy",
            display_name="Failed Evaluation Policy",
            index_profile_revision_id=profile.id,
        ),
    )
    cases = (
        RetrievalEvaluationCase(
            id="missing-source",
            query="workspace",
            expected_source_ids=("docx:not-present",),
        ),
    )
    evaluation_policy = indexes.publish_evaluation_policy(
        actor,
        RetrievalEvaluationPolicyCreate(
            id="failed-eval-gate",
            retrieval_policy_revision_id=policy.id,
            index_profile_revision_id=profile.id,
            dataset_manifest_sha256=sha256_json([item.model_dump(mode="json") for item in cases]),
            recall_at_k_min=1.0,
            zero_hit_rate_max=1.0,
            error_rate_max=0.0,
            p95_latency_ms_max=2_000,
            peak_rss_bytes_max=1_000_000_000,
            target_hardware="deterministic-test",
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

    report = indexes.evaluate(
        actor,
        RetrievalEvaluationRunRequest(
            evaluation_policy_revision_id=evaluation_policy.id,
            index_revision_id=built.id,
            cases=cases,
            target_hardware="deterministic-test",
        ),
    )
    failed = indexes.repository.get_index_revision(built.id)

    assert report.status == "failed"
    assert report.recall_at_k == 0.0
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "KNOWLEDGE_RETRIEVAL_EVALUATION_FAILED"
    with pytest.raises(ProductError) as activation:
        indexes.activate(actor, built.id, expected_pointer_version=None)
    assert activation.value.code == "KNOWLEDGE_INDEX_NOT_QUALIFIED"


def test_index_build_failure_is_persisted(tmp_path: Path) -> None:
    actor, binding, embeddings, indexes, *_ = _fixture(tmp_path)
    profile = indexes.publish_index_profile(
        actor,
        KnowledgeIndexProfileCreate(
            id="failed-build-profile",
            display_name="Failed Build Profile",
            embedding_model_name="bge-m3",
        ),
    )
    qualification = indexes.qualify_embedding(
        actor, EmbeddingQualificationRequest(model_name="bge-m3")
    )
    policy = indexes.publish_retrieval_policy(
        actor,
        RetrievalPolicyCreate(
            id="failed-build-policy",
            display_name="Failed Build Policy",
            index_profile_revision_id=profile.id,
        ),
    )
    cases = (
        RetrievalEvaluationCase(
            id="failed-build-case",
            query="workspace",
            expected_source_ids=("docx:architecture",),
        ),
    )
    indexes.publish_evaluation_policy(
        actor,
        RetrievalEvaluationPolicyCreate(
            id="failed-build-evaluation",
            retrieval_policy_revision_id=policy.id,
            index_profile_revision_id=profile.id,
            dataset_manifest_sha256=sha256_json([item.model_dump(mode="json") for item in cases]),
            recall_at_k_min=0.0,
            zero_hit_rate_max=1.0,
            error_rate_max=1.0,
            p95_latency_ms_max=2_000,
            peak_rss_bytes_max=1_000_000_000,
            target_hardware="deterministic-test",
        ),
    )
    embeddings.dimension_drift = True

    with pytest.raises(ProductError) as error:
        indexes.build(
            actor,
            KnowledgeIndexBuildRequest(
                provider_binding_id=binding.id,
                index_profile_revision_id=profile.id,
                embedding_qualification_id=qualification.id,
            ),
        )

    with indexes.repository._connect() as connection:
        row = connection.execute(
            """SELECT status,error_code FROM knowledge_index_revisions
            WHERE provider_binding_id=? AND index_profile_revision_id=?""",
            (binding.id, profile.id),
        ).fetchone()
    assert error.value.code == "KNOWLEDGE_EMBEDDING_DIMENSION_DRIFT"
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_code"] == "KNOWLEDGE_EMBEDDING_DIMENSION_DRIFT"
