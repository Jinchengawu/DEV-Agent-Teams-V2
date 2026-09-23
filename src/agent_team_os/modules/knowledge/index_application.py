from __future__ import annotations

import json
import os
import re
import resource
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from math import ceil, isfinite
from pathlib import Path

from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_bytes, sha256_file, sha256_json
from ...shared.ids import new_id
from ...shared.permissions import Role
from ..artifacts import ContentAddressedArtifactStorage
from .domain import KnowledgeActor
from .index_domain import (
    EmbeddingQualificationRequest,
    EmbeddingQualificationSnapshot,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexCatalog,
    KnowledgeIndexProfileCreate,
    KnowledgeIndexProfileRevision,
    KnowledgeIndexRevision,
    KnowledgeRetrievalHit,
    KnowledgeRetrievalReceipt,
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
    ProjectKnowledgeRetrievalOption,
    RetrievalEvaluationPolicyCreate,
    RetrievalEvaluationPolicyRevision,
    RetrievalEvaluationReport,
    RetrievalEvaluationRunRequest,
    RetrievalPolicyCreate,
    RetrievalPolicyRevision,
    RetrievalScore,
)
from .index_ports import EmbeddingPort, VectorIndexPort, VectorIndexRecord
from .index_repository import SQLiteKnowledgeIndexRepository
from .query_execution import QueryPlanExecutor
from .query_fusion import contribution_audit, contribution_groups
from .query_plan import (
    InputMeasurementPort,
    QueryInputQualification,
    QueryPlan,
    QueryPlanError,
    compile_query_plan,
)
from .query_repository import QueryExecutionIdentity, SQLiteKnowledgeQueryRepository
from .tenant_domain import TenantProviderSnapshotRecord
from .tenant_repository import SQLiteTenantKnowledgeRepository

_CANONICAL_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]*|[0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_EMBEDDING_BATCH_SIZE = 32


class KnowledgeIndexManager:
    def __init__(
        self,
        repository: SQLiteKnowledgeIndexRepository,
        *,
        tenant_repository: SQLiteTenantKnowledgeRepository,
        artifact_storage: ContentAddressedArtifactStorage,
        index_root: Path,
        embedding_port: EmbeddingPort,
        vector_index_port: VectorIndexPort,
        query_measurement: InputMeasurementPort | None = None,
        allow_deterministic_queries: bool = False,
    ) -> None:
        self.repository = repository
        self.tenant_repository = tenant_repository
        self.artifact_storage = artifact_storage
        self.index_root = index_root.resolve()
        self.index_root.mkdir(parents=True, exist_ok=True)
        self.embedding_port = embedding_port
        self.vector_index_port = vector_index_port
        self.query_measurement = query_measurement
        self.allow_deterministic_queries = allow_deterministic_queries
        self.query_repository = SQLiteKnowledgeQueryRepository(repository.database)
        self._verified_index_files: dict[str, tuple[str, int, int]] = {}

    def validate(self, retrieval_policy_revision_id: str, max_context_bytes: int) -> None:
        """Validate one immutable Pipeline knowledge binding without external calls."""
        policy = self.repository.get_retrieval_policy(retrieval_policy_revision_id)
        if policy is None:
            raise ValueError("KNOWLEDGE_RETRIEVAL_POLICY_NOT_FOUND")
        if self.repository.get_evaluation_policy_for_retrieval(policy.id) is None:
            raise ValueError("KNOWLEDGE_RETRIEVAL_EVALUATION_POLICY_MISSING")
        if max_context_bytes > policy.max_context_bytes:
            raise ValueError("KNOWLEDGE_CONTEXT_BUDGET_EXCEEDS_POLICY")

    def catalog(self, actor: KnowledgeActor) -> KnowledgeIndexCatalog:
        self._require_admin(actor)
        return KnowledgeIndexCatalog(
            profiles=self.repository.list_index_profiles(),
            qualifications=self.repository.list_qualifications(),
            retrieval_policies=self.repository.list_retrieval_policies(),
            evaluation_policies=self.repository.list_evaluation_policies(),
            index_revisions=self.repository.list_index_revisions(),
            evaluation_reports=self.repository.list_evaluation_reports(),
        )

    def project_retrieval_options(
        self,
        actor: KnowledgeActor,
        provider_binding_id: str,
    ) -> tuple[ProjectKnowledgeRetrievalOption, ...]:
        del actor
        active_indexes = tuple(
            revision
            for revision in self.repository.list_index_revisions()
            if revision.provider_binding_id == provider_binding_id
            and revision.status == "active"
        )
        policies = self.repository.list_retrieval_policies()
        return tuple(
            sorted(
                (
                    ProjectKnowledgeRetrievalOption(
                        provider_binding_id=provider_binding_id,
                        index_revision_id=index.id,
                        index_profile_revision_id=index.index_profile_revision_id,
                        retrieval_policy_revision_id=policy.id,
                    )
                    for index in active_indexes
                    for policy in policies
                    if policy.index_profile_revision_id == index.index_profile_revision_id
                    and self.repository.get_evaluation_policy_for_retrieval(policy.id)
                    is not None
                ),
                key=lambda item: (
                    item.index_profile_revision_id,
                    item.retrieval_policy_revision_id,
                    item.index_revision_id,
                ),
            )
        )

    def publish_index_profile(
        self, actor: KnowledgeActor, request: KnowledgeIndexProfileCreate
    ) -> KnowledgeIndexProfileRevision:
        self._require_admin(actor)
        now = datetime.now(UTC)
        revision = KnowledgeIndexProfileRevision(
            **request.model_dump(),
            config_sha256=sha256_json(request.model_dump(mode="json")),
            published_by=actor.user_id,
            published_at=now,
        )
        created = self.repository.publish_index_profile(revision)
        if created is None:
            raise _conflict(
                "KNOWLEDGE_INDEX_PROFILE_CONFLICT",
                "Index Profile ID 或 Hash 已发布",
            )
        return created

    def qualify_embedding(
        self, actor: KnowledgeActor, request: EmbeddingQualificationRequest
    ) -> EmbeddingQualificationSnapshot:
        self._require_admin(actor)
        descriptor = self.embedding_port.describe(request.model_name)
        vectors = self.embedding_port.embed(
            ("qualification-probe", "中文资格探针"),
            model_name=request.model_name,
            truncate=False,
        )
        if len(vectors) != 2 or any(not _embedding_vector_is_valid(item) for item in vectors):
            raise _not_ready(
                "KNOWLEDGE_EMBEDDING_QUALIFICATION_FAILED",
                "Embedding Adapter 未返回有限且非零的完整资格向量。",
            )
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise _not_ready(
                "KNOWLEDGE_EMBEDDING_DIMENSION_DRIFT",
                "资格探针返回的向量维度不一致。",
            )
        vector_descriptor = self.vector_index_port.describe()
        if (
            vector_descriptor.engine_name != "sqlite-vec"
            or vector_descriptor.engine_version != "0.1.9"
        ):
            raise _not_ready(
                "KNOWLEDGE_SQLITE_VEC_QUALIFICATION_DRIFT",
                "Vector Index Engine 不是已资格化的 sqlite-vec 0.1.9。",
            )
        payload = {
            "provider_kind": "ollama",
            "model_name": descriptor.model_name,
            "model_digest": descriptor.model_digest,
            "dimension": dimension,
            "adapter_revision": self.embedding_port.adapter_revision,
            "tokenizer_contract": request.tokenizer_contract,
            "vector_normalization": request.vector_normalization,
            "distance_metric": request.distance_metric,
            "sqlite_vec_version": vector_descriptor.engine_version,
            "vector_index_adapter_revision": vector_descriptor.adapter_revision,
        }
        qualification = EmbeddingQualificationSnapshot(
            id=new_id(),
            provider_kind="ollama",
            model_name=descriptor.model_name,
            model_digest=descriptor.model_digest,
            dimension=dimension,
            adapter_revision=self.embedding_port.adapter_revision,
            tokenizer_contract=request.tokenizer_contract,
            vector_normalization=request.vector_normalization,
            distance_metric=request.distance_metric,
            sqlite_vec_version="0.1.9",
            vector_index_adapter_revision=vector_descriptor.adapter_revision,
            qualification_sha256=sha256_json(payload),
            status="qualified",
            qualified_at=datetime.now(UTC),
        )
        return self.repository.create_qualification(qualification)

    def publish_retrieval_policy(
        self, actor: KnowledgeActor, request: RetrievalPolicyCreate
    ) -> RetrievalPolicyRevision:
        self._require_admin(actor)
        self._profile(request.index_profile_revision_id)
        revision = RetrievalPolicyRevision(
            **request.model_dump(),
            policy_sha256=sha256_json(request.model_dump(mode="json")),
            published_by=actor.user_id,
            published_at=datetime.now(UTC),
        )
        created = self.repository.publish_retrieval_policy(revision)
        if created is None:
            raise _conflict(
                "KNOWLEDGE_RETRIEVAL_POLICY_CONFLICT",
                "Retrieval Policy ID 或 Hash 已发布",
            )
        return created

    def publish_evaluation_policy(
        self, actor: KnowledgeActor, request: RetrievalEvaluationPolicyCreate
    ) -> RetrievalEvaluationPolicyRevision:
        self._require_admin(actor)
        policy = self._policy(request.retrieval_policy_revision_id)
        if policy.index_profile_revision_id != request.index_profile_revision_id:
            raise _conflict(
                "KNOWLEDGE_EVALUATION_POLICY_INCOMPATIBLE",
                "Evaluation Policy 与 Retrieval Policy 的 Index Profile 不一致",
            )
        revision = RetrievalEvaluationPolicyRevision(
            **request.model_dump(),
            policy_sha256=sha256_json(request.model_dump(mode="json")),
            published_by=actor.user_id,
            published_at=datetime.now(UTC),
        )
        created = self.repository.publish_evaluation_policy(revision)
        if created is None:
            raise _conflict(
                "KNOWLEDGE_EVALUATION_POLICY_CONFLICT",
                "Retrieval Evaluation Policy ID 或 Hash 已发布",
            )
        return created

    def build(
        self, actor: KnowledgeActor, request: KnowledgeIndexBuildRequest
    ) -> KnowledgeIndexRevision:
        self._require_admin(actor)
        profile = self._profile(request.index_profile_revision_id)
        qualification = self._qualification(request.embedding_qualification_id)
        if profile.embedding_model_name != qualification.model_name:
            raise _conflict(
                "KNOWLEDGE_INDEX_QUALIFICATION_INCOMPATIBLE",
                "Index Profile 与 Embedding Qualification 模型不一致",
            )
        if not self.repository.has_evaluation_policy_for_profile(profile.id):
            raise _not_ready(
                "KNOWLEDGE_RETRIEVAL_EVALUATION_POLICY_MISSING",
                "构建 Hybrid Index 前必须先发布 RetrievalEvaluationPolicyRevision。",
            )
        self._verify_qualification(qualification)
        snapshots = self.tenant_repository.list_active_snapshots(request.provider_binding_id)
        manifest = {
            "binding_id": request.provider_binding_id,
            "profile_sha256": str(profile.config_sha256),
            "qualification_sha256": str(qualification.qualification_sha256),
            "snapshots": [
                {
                    "id": snapshot.id,
                    "source_id": snapshot.source_id,
                    "provider_revision": snapshot.provider_revision,
                    "artifact_sha256": str(snapshot.artifact.sha256),
                }
                for snapshot in snapshots
            ],
        }
        now = datetime.now(UTC)
        building = KnowledgeIndexRevision(
            id=new_id(),
            provider_binding_id=request.provider_binding_id,
            index_profile_revision_id=profile.id,
            embedding_qualification_id=qualification.id,
            input_manifest_sha256=sha256_json(manifest),
            status="building",
            chunk_count=0,
            version=1,
            created_by=actor.user_id,
            created_at=now,
        )
        persisted, created = self.repository.create_index_revision(building)
        if not created:
            return persisted
        try:
            document_count = len(snapshots)
            if document_count > profile.max_documents:
                raise _capacity_exceeded(
                    "KNOWLEDGE_INDEX_DOCUMENT_CAPACITY_EXCEEDED",
                    f"Document 数 {document_count} 超过 Published Profile 上限 "
                    f"{profile.max_documents}。",
                )
            target = self.index_root / f"{building.id}.sqlite"
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{building.id}.", suffix=".sqlite", dir=self.index_root
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                chunk_count = self._write_lexical_index(
                    temporary,
                    self._chunks(snapshots, profile),
                    max_chunks=profile.max_chunks,
                )
                vector_count = self.vector_index_port.build(
                    temporary,
                    self._embedding_batches(
                        self._chunks(snapshots, profile),
                        qualification,
                    ),
                    dimension=qualification.dimension,
                )
                if vector_count != chunk_count:
                    raise RuntimeError("KNOWLEDGE_INDEX_VECTOR_COUNT_MISMATCH")
                self.vector_index_port.verify(
                    temporary,
                    dimension=qualification.dimension,
                    expected_count=chunk_count,
                )
                storage_sha256 = sha256_file(temporary)
                os.replace(temporary, target)
                target.chmod(0o444)
            finally:
                if temporary.exists():
                    temporary.unlink()
            capacity_status = (
                "warning"
                if document_count
                >= ceil(profile.max_documents * profile.capacity_warning_ratio)
                or chunk_count
                >= ceil(profile.max_chunks * profile.capacity_warning_ratio)
                else "normal"
            )
            completed = self.repository.complete_index_build(
                building,
                storage_uri=f"derived-index://{building.id}",
                storage_sha256=str(storage_sha256),
                document_count=document_count,
                chunk_count=chunk_count,
                capacity_status=capacity_status,
            )
            stat = target.stat()
            self._verified_index_files[building.id] = (
                str(storage_sha256),
                stat.st_size,
                stat.st_mtime_ns,
            )
            return completed
        except Exception as error:
            self.repository.fail_index_build(
                building,
                error_code=_build_error_code(error),
            )
            raise

    def evaluate(
        self, actor: KnowledgeActor, request: RetrievalEvaluationRunRequest
    ) -> RetrievalEvaluationReport:
        self._require_admin(actor)
        evaluation_policy = self.repository.get_evaluation_policy(
            request.evaluation_policy_revision_id
        )
        if evaluation_policy is None:
            raise _not_found(
                "KNOWLEDGE_RETRIEVAL_EVALUATION_POLICY_NOT_FOUND",
                "Retrieval Evaluation Policy 不存在",
            )
        index = self.repository.get_index_revision(request.index_revision_id)
        if index is None:
            raise _not_found("KNOWLEDGE_INDEX_NOT_FOUND", "Knowledge Index 不存在")
        if index.status not in {"built", "qualified"}:
            raise _conflict(
                "KNOWLEDGE_INDEX_EVALUATION_STATE_CONFLICT",
                "只有 built 或 qualified 状态的 Index 可以执行评测。",
            )
        if index.index_profile_revision_id != evaluation_policy.index_profile_revision_id:
            raise _conflict(
                "KNOWLEDGE_EVALUATION_POLICY_INCOMPATIBLE",
                "Evaluation Policy 与 Index Revision 的 Index Profile 不一致。",
            )
        if request.target_hardware != evaluation_policy.target_hardware:
            raise _conflict(
                "KNOWLEDGE_EVALUATION_TARGET_HARDWARE_MISMATCH",
                "评测运行环境与已发布的 target_hardware 不一致。",
            )
        dataset_manifest_sha256 = sha256_json(
            [case.model_dump(mode="json") for case in request.cases]
        )
        if dataset_manifest_sha256 != evaluation_policy.dataset_manifest_sha256:
            raise _conflict(
                "KNOWLEDGE_EVALUATION_DATASET_MANIFEST_MISMATCH",
                "评测数据集与已发布的 Dataset Manifest 不一致。",
            )
        retrieval_policy = self._policy(evaluation_policy.retrieval_policy_revision_id)
        if retrieval_policy.index_profile_revision_id != index.index_profile_revision_id:
            raise _conflict(
                "KNOWLEDGE_EVALUATION_POLICY_INCOMPATIBLE",
                "Retrieval Policy 与 Index Revision 的 Index Profile 不一致。",
            )
        assert index.embedding_qualification_id is not None
        qualification = self._qualification(index.embedding_qualification_id)
        self._verify_qualification(qualification)
        indexed_sources = self._indexed_source_ids(index)

        recalls: list[float] = []
        latencies_ms: list[int] = []
        zero_hits = 0
        errors = 0
        for case in request.cases:
            started = time.perf_counter_ns()
            try:
                query_vector = self.embedding_port.embed(
                    (case.query,),
                    model_name=qualification.model_name,
                    truncate=False,
                )
                if len(query_vector) != 1 or len(query_vector[0]) != qualification.dimension:
                    raise RuntimeError("KNOWLEDGE_MODEL_QUALIFICATION_DRIFT")
                if not _embedding_vector_is_valid(query_vector[0]):
                    raise RuntimeError("KNOWLEDGE_EMBEDDING_VECTOR_INVALID")
                hits = self._query_index(
                    index,
                    case.query,
                    query_vector[0],
                    indexed_sources,
                    retrieval_policy,
                )
                hit_sources = {hit.source_id for hit in hits}
                expected_sources = set(case.expected_source_ids)
                recalls.append(len(hit_sources & expected_sources) / len(expected_sources))
                if not hits:
                    zero_hits += 1
            except Exception:
                errors += 1
                recalls.append(0.0)
                zero_hits += 1
            finally:
                elapsed_ns = time.perf_counter_ns() - started
                latencies_ms.append(ceil(elapsed_ns / 1_000_000))

        case_count = len(request.cases)
        recall_at_k = sum(recalls) / case_count
        zero_hit_rate = zero_hits / case_count
        error_rate = errors / case_count
        p95_latency_ms = sorted(latencies_ms)[ceil(0.95 * case_count) - 1]
        peak_rss_bytes = _peak_rss_bytes()
        passed = (
            recall_at_k >= evaluation_policy.recall_at_k_min
            and zero_hit_rate <= evaluation_policy.zero_hit_rate_max
            and error_rate <= evaluation_policy.error_rate_max
            and p95_latency_ms <= evaluation_policy.p95_latency_ms_max
            and peak_rss_bytes <= evaluation_policy.peak_rss_bytes_max
        )
        created_at = datetime.now(UTC)
        report_payload = {
            "evaluation_policy_revision_id": evaluation_policy.id,
            "evaluation_policy_sha256": str(evaluation_policy.policy_sha256),
            "index_revision_id": index.id,
            "index_storage_sha256": str(index.storage_sha256),
            "dataset_manifest_sha256": str(dataset_manifest_sha256),
            "status": "passed" if passed else "failed",
            "metrics": {
                "recall_at_k": recall_at_k,
                "zero_hit_rate": zero_hit_rate,
                "error_rate": error_rate,
                "p95_latency_ms": p95_latency_ms,
                "peak_rss_bytes": peak_rss_bytes,
                "case_count": case_count,
            },
            "target_hardware": request.target_hardware,
            "run_by": actor.user_id,
            "created_at": created_at.isoformat(),
        }
        report_artifact = self.artifact_storage.put_json(
            report_payload,
            media_type=("application/vnd.agent-team-os.retrieval-evaluation-report+json"),
        )
        report = RetrievalEvaluationReport(
            id=new_id(),
            evaluation_policy_revision_id=evaluation_policy.id,
            index_revision_id=index.id,
            dataset_manifest_sha256=dataset_manifest_sha256,
            status="passed" if passed else "failed",
            recall_at_k=recall_at_k,
            zero_hit_rate=zero_hit_rate,
            error_rate=error_rate,
            p95_latency_ms=p95_latency_ms,
            peak_rss_bytes=peak_rss_bytes,
            target_hardware=request.target_hardware,
            case_count=case_count,
            report_artifact=report_artifact,
            run_by=actor.user_id,
            created_at=created_at,
        )
        try:
            persisted, _revision = self.repository.record_evaluation(
                report,
                qualified_at=created_at,
            )
        except RuntimeError as error:
            raise _conflict(str(error), "Knowledge Index 评测状态冲突") from error
        return persisted

    def activate(
        self,
        actor: KnowledgeActor,
        revision_id: str,
        *,
        expected_pointer_version: int | None,
    ) -> KnowledgeIndexRevision:
        self._require_admin(actor)
        try:
            return self.repository.activate_index(
                revision_id,
                expected_pointer_version=expected_pointer_version,
                activated_at=datetime.now(UTC),
            )
        except RuntimeError as error:
            raise _conflict(
                str(error),
                "Knowledge Index Active Pointer 状态或版本冲突",
            ) from error

    def preflight_query(self, request: KnowledgeRetrievalRequest) -> QueryPlan:
        """仅本地编译：预算缺失时不访问 describe/embed，不猜测真实模型容量。"""
        policy = self._policy(request.retrieval_policy_revision_id)
        active = self.repository.get_active_index(
            request.provider_binding_id, policy.index_profile_revision_id
        )
        if active is None or active.embedding_qualification_id is None:
            raise _query_preflight_error("KNOWLEDGE_INDEX_NOT_READY", "查询缺少冻结索引")
        base = self._qualification(active.embedding_qualification_id)
        query_qualification: QueryInputQualification | None = None
        if policy.query_input_qualification_sha256 is not None:
            try:
                query_qualification = self.query_repository.get_qualification(
                    policy.query_input_qualification_sha256
                )
            except KeyError:
                query_qualification = None
            if query_qualification is None:
                raise _query_preflight_error(
                    "KNOWLEDGE_QUERY_INPUT_UNQUALIFIED", "查询输入资格记录不存在"
                )
        try:
            return compile_query_plan(
                request.query, query_qualification, self.query_measurement,
                base_qualification_sha256=base.qualification_sha256,
                model_digest=base.model_digest, embedding_adapter_revision=base.adapter_revision,
                binding_sha256=sha256_json({
                    "index_id": active.id, "storage_sha256": active.storage_sha256,
                    "policy_sha256": policy.policy_sha256,
                    "allowed_sources": sorted(set(request.allowed_source_ids)),
                }),
                allow_deterministic=self.allow_deterministic_queries,
            )
        except QueryPlanError as error:
            failure = _query_preflight_error(
                error.code, "查询输入资格或预算不可执行；请完成本地精确资格核验",
                context={
                    "measured_bytes": len(request.query.encode("utf-8", errors="replace")),
                    "allowed_bytes": (query_qualification.budget.total_bytes
                                      if query_qualification else None),
                },
            )
        raise failure

    def retrieve(
        self, actor: KnowledgeActor, request: KnowledgeRetrievalRequest,
        *, execution_identity: QueryExecutionIdentity | None = None,
        admission: Callable[[], None] | None = None,
    ) -> KnowledgeRetrievalResult:
        audit: list[dict[str, str | int]] = []
        plan = (self.preflight_query(request) if execution_identity is None else
                QueryPlan.model_validate_json(
                    self.query_repository.get_for_execution(execution_identity).plan_json
                ))
        policy = self._policy(request.retrieval_policy_revision_id)
        evaluation_policy = self.repository.get_evaluation_policy_for_retrieval(policy.id)
        if evaluation_policy is None:
            raise _not_ready(
                "KNOWLEDGE_RETRIEVAL_EVALUATION_POLICY_MISSING",
                "Retrieval Policy 尚无 Published Evaluation Policy。",
            )
        active = (self.repository.get_active_index(
            request.provider_binding_id, policy.index_profile_revision_id,
        ) if execution_identity is None else self.repository.get_index_revision(
            execution_identity.index_revision_id or ""
        ))
        allowed_status = {"active"} if execution_identity is None else {"active", "superseded"}
        if active is None or active.status not in allowed_status:
            raise _not_ready(
                "KNOWLEDGE_INDEX_NOT_READY",
                "当前 Binding 与 Index Profile 没有 Active Index。",
            )
        if not self.repository.has_passed_evaluation(evaluation_policy.id, active.id):
            raise _not_ready(
                "KNOWLEDGE_RETRIEVAL_EVALUATION_NOT_PASSED",
                "Active Index 尚未通过当前 Retrieval Policy 的已发布评测。",
            )
        if execution_identity is not None:
            frozen = self.query_repository.get_for_execution(execution_identity)
            self.query_repository.assert_admitted(execution_identity, plan)
            if (
                execution_identity.index_sha256 != active.storage_sha256
                or execution_identity.policy_sha256 != policy.policy_sha256
                or frozen.plan_sha256 != plan.plan_sha256
                or execution_identity.source_ids != tuple(sorted(set(request.allowed_source_ids)))
                or execution_identity.policy_revision_id != policy.id
                or execution_identity.input_sha256 != sha256_bytes(request.query.encode())
                or active.embedding_qualification_id != execution_identity.base_qualification_id
                or sha256_json(policy.model_dump(mode="json", exclude={
                    "policy_sha256", "published_by", "published_at"
                })) != policy.policy_sha256
            ):
                raise _not_ready(
                    "KNOWLEDGE_QUERY_EXECUTION_IDENTITY_CONFLICT",
                    "冻结查询身份已漂移；不得切换新索引或资格继续执行",
                )
            if admission is None:
                raise _not_ready("KNOWLEDGE_QUERY_ADMISSION_MISSING", "缺少逐单元授权核验")
            admission()
            query_qualification = self.query_repository.get_qualification(plan.qualification_sha256)
            base = self._qualification(execution_identity.base_qualification_id or "")
            if (
                base.qualification_sha256 != execution_identity.base_qualification_sha256
                or sha256_json(base.model_dump(mode="json", exclude={
                    "id", "qualification_sha256", "status", "qualified_at"
                })) != base.qualification_sha256
                or base.status != "qualified"
                or query_qualification.base_qualification_sha256 != base.qualification_sha256
                or query_qualification.model_digest != base.model_digest
                or query_qualification.embedding_adapter_revision != base.adapter_revision
                or self.query_measurement is None
                or not self.query_measurement.verify_qualification(query_qualification)
                or (query_qualification.evidence_kind != "live"
                    and not self.allow_deterministic_queries)
            ):
                raise _query_preflight_error(
                    "KNOWLEDGE_QUERY_INPUT_UNQUALIFIED", "冻结资格或计数资产不可验证"
                )
            self._verified_index_files.pop(active.id, None)
            self._index_path(active)
        assert active.embedding_qualification_id is not None
        qualification = self._qualification(active.embedding_qualification_id)
        allowed_sources = tuple(sorted(set(request.allowed_source_ids)))
        query_sha = sha256_bytes(request.query.encode("utf-8"))
        allowed_sha = sha256_json(allowed_sources)
        empty_reason: str | None = None
        hits: tuple[KnowledgeRetrievalHit, ...]
        if not allowed_sources:
            hits = ()
            empty_reason = "approved-scope-empty"
        else:
            provider_verified = False
            def execute_unit(text: str) -> tuple[KnowledgeRetrievalHit, ...]:
                nonlocal provider_verified
                if not provider_verified:
                    self._verify_qualification(qualification)
                    provider_verified = True
                query_vector = self.embedding_port.embed(
                    (text,), model_name=qualification.model_name, truncate=False,
                )
                if len(query_vector) != 1 or len(query_vector[0]) != qualification.dimension:
                    raise _not_ready("KNOWLEDGE_MODEL_QUALIFICATION_DRIFT", "Query 向量维度漂移")
                if not _embedding_vector_is_valid(query_vector[0]):
                    raise _not_ready("KNOWLEDGE_EMBEDDING_VECTOR_INVALID", "Query 向量非法")
                return self._query_index(
                    active, text, query_vector[0], allowed_sources, policy,
                    apply_budget=len(plan.units) == 1,
                )
            if execution_identity is not None:
                if admission is None:
                    raise _not_ready("KNOWLEDGE_QUERY_ADMISSION_MISSING", "缺少逐单元授权核验")
                query_qualification = self.query_repository.get_qualification(
                    plan.qualification_sha256
                )
                budget = query_qualification.budget
                unit_hits = QueryPlanExecutor(
                    self.query_repository, self.artifact_storage,
                    lease_ttl=timedelta(milliseconds=budget.max_duration_ms),
                    max_cache_bytes=budget.max_cache_bytes,
                ).execute(
                    execution_identity, plan, query_qualification, request.query,
                    execute_unit, admission,
                    max_duration_seconds=budget.max_duration_ms / 1000,
                )
            else:
                query_qualification = self.query_repository.get_qualification(
                    plan.qualification_sha256
                )
                budget = query_qualification.budget
                started = time.monotonic()
                cache_bytes = 0
                ephemeral = []
                for unit in plan.units:
                    elapsed = int((time.monotonic() - started) * 1000)
                    if elapsed > budget.max_duration_ms:
                        raise _query_preflight_error(
                            "KNOWLEDGE_QUERY_BUDGET_EXCEEDED", "查询执行时间预算超限",
                            context={"measured": elapsed, "allowed": budget.max_duration_ms},
                        )
                    hits_for_unit = execute_unit(unit.reconstruct(request.query))
                    cache_bytes += len(json.dumps(
                        [hit.model_dump(mode="json") for hit in hits_for_unit],
                        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    ).encode("utf-8"))
                    elapsed = int((time.monotonic() - started) * 1000)
                    if cache_bytes > budget.max_cache_bytes or elapsed > budget.max_duration_ms:
                        raise _query_preflight_error(
                            "KNOWLEDGE_QUERY_BUDGET_EXCEEDED", "查询执行预算超限",
                            context={
                                "measured": (cache_bytes if cache_bytes > budget.max_cache_bytes
                                             else elapsed),
                                "allowed": (budget.max_cache_bytes
                                            if cache_bytes > budget.max_cache_bytes
                                            else budget.max_duration_ms),
                            },
                        )
                    ephemeral.append(hits_for_unit)
                unit_hits = tuple(ephemeral)
            audit = contribution_audit(tuple(unit_hits),
                                       tuple(str(unit.input_sha256) for unit in plan.units))
            hits = (unit_hits[0] if len(unit_hits) == 1
                    else _merge_query_hits(tuple(unit_hits), policy))
            if not hits:
                empty_reason = "no-qualified-hit"
                if policy.empty_result_policy == "fail":
                    raise _not_ready(
                        "KNOWLEDGE_RETRIEVAL_EMPTY",
                        "检索策略不允许空结果。",
                    )
        receipt = KnowledgeRetrievalReceipt(
            id=new_id(),
            project_id=request.project_id,
            provider_binding_id=request.provider_binding_id,
            index_revision_id=active.id,
            retrieval_policy_revision_id=policy.id,
            requested_by=actor.user_id,
            query_sha256=query_sha,
            allowed_source_set_sha256=allowed_sha,
            hit_ids=tuple(hit.chunk_id for hit in hits),
            empty_reason=empty_reason,
            created_at=datetime.now(UTC),
        )
        receipt_artifact = self.artifact_storage.put_json(
            {
                "receipt": receipt.model_dump(mode="json"),
                "hits": [hit.model_dump(mode="json") for hit in hits],
                "query_contributions": audit,
            },
            media_type="application/vnd.agent-team-os.retrieval-receipt+json",
        )
        self.repository.record_retrieval(
            receipt,
            receipt_uri=receipt_artifact.uri,
            receipt_sha256=str(receipt_artifact.sha256),
            hit_count=len(hits),
        )
        return KnowledgeRetrievalResult(receipt=receipt, hits=hits)

    def freeze_query(
        self, request: KnowledgeRetrievalRequest, *, preparation_run_id: str,
        stage_path: str, authorization_epoch_hash: str,
    ) -> QueryExecutionIdentity:
        identity, plan, _ = self.draft_query(
            request, preparation_run_id=preparation_run_id, stage_path=stage_path,
            authorization_epoch_hash=authorization_epoch_hash,
        )
        self.query_repository.freeze_plan(identity, plan=plan)
        return identity

    def draft_query(
        self, request: KnowledgeRetrievalRequest, *, preparation_run_id: str,
        stage_path: str, authorization_epoch_hash: str,
    ) -> tuple[QueryExecutionIdentity, QueryPlan, QueryInputQualification]:
        plan = self.preflight_query(request)
        policy = self._policy(request.retrieval_policy_revision_id)
        active = self.repository.get_active_index(
            request.provider_binding_id, policy.index_profile_revision_id
        )
        assert active is not None and active.storage_sha256 is not None
        identity = QueryExecutionIdentity(
            preparation_run_id=preparation_run_id, stage_path=stage_path,
            binding_id=request.provider_binding_id, input_sha256=plan.source_sha256,
            index_sha256=active.storage_sha256, policy_sha256=policy.policy_sha256,
            qualification_sha256=plan.qualification_sha256,
            budget_sha256=plan.budget_sha256,
            authorization_epoch_hash=Sha256.validate(authorization_epoch_hash),
            index_revision_id=active.id,
            base_qualification_id=active.embedding_qualification_id,
            base_qualification_sha256=self._qualification(
                active.embedding_qualification_id or ""
            ).qualification_sha256,
            source_ids=tuple(sorted(set(request.allowed_source_ids))),
            policy_revision_id=policy.id,
        )
        return identity, plan, self.query_repository.get_qualification(plan.qualification_sha256)

    def _write_lexical_index(
        self,
        path: Path,
        chunks: Iterable[dict[str, str]],
        *,
        max_chunks: int,
    ) -> int:
        count = 0
        with sqlite3.connect(path) as connection:
            connection.executescript(
                """PRAGMA journal_mode=DELETE;
                PRAGMA synchronous=FULL;
                CREATE TABLE chunks(
                    row_id INTEGER PRIMARY KEY,
                    chunk_id TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT,
                    block_anchor TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    lexical_text TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE chunk_fts USING fts5(lexical_text);
                CREATE INDEX chunk_source_idx ON chunks(source_id,row_id);
                """
            )
            for row_id, chunk in enumerate(chunks, 1):
                if row_id > max_chunks:
                    raise _capacity_exceeded(
                        "KNOWLEDGE_INDEX_CHUNK_CAPACITY_EXCEEDED",
                        f"Chunk 数超过 Published Profile 上限 {max_chunks}。",
                    )
                connection.execute(
                    """INSERT INTO chunks(
                    row_id,chunk_id,source_id,snapshot_id,title,source_url,block_anchor,
                    content,content_sha256,lexical_text) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        chunk["chunk_id"],
                        chunk["source_id"],
                        chunk["snapshot_id"],
                        chunk["title"],
                        chunk["source_url"] or None,
                        chunk["block_anchor"],
                        chunk["content"],
                        chunk["content_sha256"],
                        _lexical_text(chunk["content"]),
                    ),
                )
                connection.execute(
                    "INSERT INTO chunk_fts(rowid,lexical_text) VALUES(?,?)",
                    (row_id, _lexical_text(chunk["content"])),
                )
                count = row_id
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError("KNOWLEDGE_INDEX_INTEGRITY_FAILED")
        return count

    def _embedding_batches(
        self,
        chunks: Iterable[dict[str, str]],
        qualification: EmbeddingQualificationSnapshot,
    ) -> Iterable[tuple[VectorIndexRecord, ...]]:
        pending: list[tuple[int, dict[str, str]]] = []
        for row_id, chunk in enumerate(chunks, 1):
            pending.append((row_id, chunk))
            if len(pending) < _EMBEDDING_BATCH_SIZE:
                continue
            yield self._embed_chunk_batch(pending, qualification)
            pending = []
        if pending:
            yield self._embed_chunk_batch(pending, qualification)

    def _embed_chunk_batch(
        self,
        pending: list[tuple[int, dict[str, str]]],
        qualification: EmbeddingQualificationSnapshot,
    ) -> tuple[VectorIndexRecord, ...]:
        texts = tuple(chunk["content"] for _row_id, chunk in pending)
        vectors = self.embedding_port.embed(
            texts,
            model_name=qualification.model_name,
            truncate=False,
        )
        if len(vectors) != len(pending) or any(
            len(vector) != qualification.dimension for vector in vectors
        ):
            raise _not_ready(
                "KNOWLEDGE_EMBEDDING_DIMENSION_DRIFT",
                "构建时 Embedding 数量或维度与资格快照不一致。",
            )
        if any(not _embedding_vector_is_valid(vector) for vector in vectors):
            raise _not_ready(
                "KNOWLEDGE_EMBEDDING_VECTOR_INVALID",
                "构建时 Embedding 包含非有限值或全零向量。",
            )
        batch = tuple(
            VectorIndexRecord(
                row_id=row_id,
                chunk_id=chunk["chunk_id"],
                source_id=chunk["source_id"],
                embedding=vector,
            )
            for (row_id, chunk), vector in zip(pending, vectors, strict=True)
        )
        return batch

    def _indexed_source_ids(self, revision: KnowledgeIndexRevision) -> tuple[str, ...]:
        path = self._index_path(revision)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=1")
            rows = connection.execute(
                "SELECT DISTINCT source_id FROM chunks ORDER BY source_id"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _query_index(
        self,
        revision: KnowledgeIndexRevision,
        query: str,
        query_vector: tuple[float, ...],
        allowed_sources: tuple[str, ...],
        policy: RetrievalPolicyRevision,
        *,
        apply_budget: bool = True,
    ) -> tuple[KnowledgeRetrievalHit, ...]:
        path = self._index_path(revision)
        placeholders = ",".join("?" for _ in allowed_sources)
        lexical_query = " OR ".join(f'"{token}"' for token in _tokens(query))
        lexical_rows: list[sqlite3.Row] = []
        vector_matches = self.vector_index_port.search(
            path,
            query_vector,
            allowed_sources,
            limit=policy.vector_candidates,
        )
        vector_rows: dict[str, sqlite3.Row] = {}
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=1")
            if lexical_query:
                lexical_rows = connection.execute(
                    f"""SELECT chunks.*,bm25(chunk_fts) AS lexical_score
                    FROM chunk_fts JOIN chunks ON chunks.row_id=chunk_fts.rowid
                    WHERE chunk_fts MATCH ? AND chunks.source_id IN ({placeholders})
                    ORDER BY lexical_score,chunk_id LIMIT ?""",  # noqa: S608
                    (lexical_query, *allowed_sources, policy.lexical_candidates),
                ).fetchall()
            if vector_matches:
                chunk_placeholders = ",".join("?" for _ in vector_matches)
                rows = connection.execute(
                    f"SELECT * FROM chunks WHERE chunk_id IN ({chunk_placeholders})",  # noqa: S608
                    tuple(match.chunk_id for match in vector_matches),
                ).fetchall()
                vector_rows = {str(row["chunk_id"]): row for row in rows}
        lexical = {str(row["chunk_id"]): (rank, row) for rank, row in enumerate(lexical_rows, 1)}
        vector = {
            match.chunk_id: (rank, vector_rows[match.chunk_id], match.distance)
            for rank, match in enumerate(vector_matches, 1)
            if match.chunk_id in vector_rows
        }
        ranked: list[tuple[float, str, sqlite3.Row, int | None, int | None]] = []
        for chunk_id in lexical.keys() | vector.keys():
            lexical_item = lexical.get(chunk_id)
            vector_item = vector.get(chunk_id)
            score = 0.0 if lexical_item is None else 1 / (policy.rrf_k + lexical_item[0])
            score += 0.0 if vector_item is None else 1 / (policy.rrf_k + vector_item[0])
            score = round(score, policy.score_precision)
            row = (lexical_item or vector_item)[1]  # type: ignore[index]
            ranked.append(
                (
                    score,
                    chunk_id,
                    row,
                    None if lexical_item is None else lexical_item[0],
                    None if vector_item is None else vector_item[0],
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        hits: list[KnowledgeRetrievalHit] = []
        consumed = 0
        for score, chunk_id, row, lexical_rank, vector_rank in ranked:
            if apply_budget and (score < policy.min_score or len(hits) >= policy.top_k):
                continue
            content = str(row["content"])
            size = len(content.encode("utf-8"))
            if apply_budget and consumed + size > policy.max_context_bytes:
                continue
            consumed += size
            lexical_row = lexical.get(chunk_id)
            vector_row = vector.get(chunk_id)
            hits.append(
                KnowledgeRetrievalHit(
                    citation_id=str(
                        sha256_json(
                            {
                                "index_revision_id": revision.id,
                                "chunk_id": chunk_id,
                                "content_sha256": row["content_sha256"],
                            }
                        )
                    ),
                    chunk_id=chunk_id,
                    source_id=str(row["source_id"]),
                    snapshot_id=str(row["snapshot_id"]),
                    title=str(row["title"]),
                    source_url=(None if row["source_url"] is None else str(row["source_url"])),
                    block_anchor=str(row["block_anchor"]),
                    content=content,
                    content_sha256=row["content_sha256"],
                    score=RetrievalScore(
                        lexical_rank=lexical_rank,
                        lexical_score=(
                            None
                            if lexical_row is None
                            else round(float(lexical_row[1]["lexical_score"]), 8)
                        ),
                        vector_rank=vector_rank,
                        vector_distance=(
                            None
                            if vector_row is None
                            else round(float(vector_row[2]), 8)
                        ),
                        rrf_score=score,
                    ),
                )
            )
        return tuple(hits)

    def _chunks(
        self,
        snapshots: tuple[TenantProviderSnapshotRecord, ...],
        profile: KnowledgeIndexProfileRevision,
    ) -> Iterable[dict[str, str]]:
        nodes = {
            node.source_id: node
            for snapshot in snapshots
            for node in self.tenant_repository.list_binding_nodes(snapshot.binding_id)
            if node.source_id is not None
        }
        for snapshot in snapshots:
            content = self.artifact_storage.get_json(snapshot.artifact)
            title = (
                nodes[snapshot.source_id].title
                if snapshot.source_id in nodes
                else snapshot.source_id
            )
            for anchor, text in _content_blocks(content):
                for ordinal, segment in enumerate(
                    _chunk_segments(
                        text,
                        max_characters=profile.max_chunk_characters,
                        overlap_characters=profile.chunk_overlap_characters,
                    ),
                    1,
                ):
                    content_sha = sha256_bytes(segment.encode("utf-8"))
                    chunk_id = sha256_json(
                        {
                            "snapshot_id": snapshot.id,
                            "block_anchor": anchor,
                            "chunk_ordinal": ordinal,
                            "content_sha256": str(content_sha),
                        }
                    )
                    yield {
                        "chunk_id": str(chunk_id),
                        "source_id": snapshot.source_id,
                        "snapshot_id": snapshot.id,
                        "title": title,
                        "source_url": snapshot.source_url or "",
                        "block_anchor": anchor,
                        "content": segment,
                        "content_sha256": str(content_sha),
                    }

    def _index_path(self, revision: KnowledgeIndexRevision) -> Path:
        if revision.storage_uri != f"derived-index://{revision.id}":
            raise _not_ready(
                "KNOWLEDGE_INDEX_STORAGE_INVALID",
                "Index Storage URI 与 Revision 不一致。",
            )
        path = self.index_root / f"{revision.id}.sqlite"
        if path.is_symlink() or path.parent.is_symlink():
            raise _not_ready("KNOWLEDGE_INDEX_STORAGE_INVALID", "Index 不允许符号链接")
        try:
            stat = path.stat()
        except FileNotFoundError as error:
            raise _not_ready(
                "KNOWLEDGE_INDEX_NOT_READY",
                "Active Index 文件不可用。",
            ) from error
        expected = str(revision.storage_sha256)
        identity = (expected, stat.st_size, stat.st_mtime_ns)
        if self._verified_index_files.get(revision.id) == identity:
            return path
        actual = sha256_file(path)
        verified_stat = path.stat()
        if (
            actual != revision.storage_sha256
            or verified_stat.st_size != stat.st_size
            or verified_stat.st_mtime_ns != stat.st_mtime_ns
        ):
            raise _not_ready(
                "KNOWLEDGE_INDEX_INTEGRITY_FAILED",
                "Active Index 文件哈希校验失败。",
            )
        self._verified_index_files[revision.id] = identity
        return path

    def _verify_qualification(self, qualification: EmbeddingQualificationSnapshot) -> None:
        descriptor = self.embedding_port.describe(qualification.model_name)
        vector_descriptor = self.vector_index_port.describe()
        if (
            descriptor.model_digest != qualification.model_digest
            or descriptor.model_name != qualification.model_name
            or self.embedding_port.adapter_revision != qualification.adapter_revision
            or vector_descriptor.engine_name != "sqlite-vec"
            or vector_descriptor.engine_version != qualification.sqlite_vec_version
            or vector_descriptor.adapter_revision
            != qualification.vector_index_adapter_revision
        ):
            raise _not_ready(
                "KNOWLEDGE_MODEL_QUALIFICATION_DRIFT",
                "Embedding 模型或 Vector Index Adapter 已偏离资格快照。",
            )

    def _profile(self, revision_id: str) -> KnowledgeIndexProfileRevision:
        profile = self.repository.get_index_profile(revision_id)
        if profile is None:
            raise _not_found("KNOWLEDGE_INDEX_PROFILE_NOT_FOUND", "Index Profile 不存在")
        return profile

    def _qualification(self, qualification_id: str) -> EmbeddingQualificationSnapshot:
        qualification = self.repository.get_qualification(qualification_id)
        if qualification is None:
            raise _not_found(
                "KNOWLEDGE_EMBEDDING_QUALIFICATION_NOT_FOUND",
                "Embedding Qualification 不存在",
            )
        return qualification

    def _policy(self, revision_id: str) -> RetrievalPolicyRevision:
        policy = self.repository.get_retrieval_policy(revision_id)
        if policy is None:
            raise _not_found(
                "KNOWLEDGE_RETRIEVAL_POLICY_NOT_FOUND",
                "Retrieval Policy 不存在",
            )
        return policy

    @staticmethod
    def _require_admin(actor: KnowledgeActor) -> None:
        if actor.role != Role.ADMINISTRATOR:
            raise ProductError(
                code="KNOWLEDGE_INDEX_PERMISSION_DENIED",
                title="Knowledge Index 权限不足",
                detail="只有 Administrator 可以发布或激活索引策略。",
                repair="使用管理员账户重试。",
                status_code=403,
            )


def _content_blocks(content: object) -> tuple[tuple[str, str], ...]:
    if isinstance(content, dict):
        blocks = content.get("blocks")
        if isinstance(blocks, list):
            output: list[tuple[str, str]] = []
            for index, block in enumerate(blocks, 1):
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                anchor = block.get("block_id")
                output.append(
                    (
                        str(anchor) if isinstance(anchor, str) and anchor else f"block-{index}",
                        text.strip(),
                    )
                )
            return tuple(output)
        text = content.get("text")
        if isinstance(text, str):
            return tuple(
                (f"paragraph-{index}", paragraph.strip())
                for index, paragraph in enumerate(text.splitlines(), 1)
                if paragraph.strip()
            )
    return ()


def _embedding_vector_is_valid(vector: tuple[float, ...]) -> bool:
    return bool(
        vector
        and all(isfinite(value) for value in vector)
        and any(abs(value) > 1e-12 for value in vector)
    )


def _chunk_segments(
    text: str,
    *,
    max_characters: int,
    overlap_characters: int,
) -> tuple[str, ...]:
    if len(text) <= max_characters:
        return (text,)
    step = max_characters - overlap_characters
    return tuple(
        text[start : start + max_characters]
        for start in range(0, len(text) - overlap_characters, step)
        if text[start : start + max_characters]
    )


def _tokens(value: str) -> tuple[str, ...]:
    canonical = [match.group(0).lower() for match in _CANONICAL_TOKEN.finditer(value)]
    cjk: list[str] = []
    for match in _CJK_RUN.finditer(value):
        run = match.group(0)
        if len(run) == 1:
            cjk.append(run)
        else:
            cjk.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tuple(dict.fromkeys((*canonical, *cjk)))


def _lexical_text(value: str) -> str:
    return " ".join(_tokens(value))


def _peak_rss_bytes() -> int:
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if sys.platform == "darwin" else peak * 1024


def _build_error_code(error: Exception) -> str:
    if isinstance(error, ProductError):
        return error.code
    if isinstance(error, RuntimeError) and str(error):
        return str(error)[:240]
    return "KNOWLEDGE_INDEX_BUILD_FAILED"


def _conflict(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Knowledge Index 冲突",
        detail=detail,
        repair="刷新不可变 Revision 与 Active Pointer 后重试。",
    )


def _not_ready(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Knowledge Retrieval 未就绪",
        detail=detail,
        repair="修复资格、索引或策略后重试。",
        status_code=503,
    )


def _query_preflight_error(
    code: str, detail: str, *, context: dict[str, int | None] | None = None,
) -> ProductError:
    return ProductError(
        code=code, title="查询输入不可执行", detail=detail,
        repair="修复本地输入资格与冻结预算后重新提交。", status_code=422,
        context=context,
    )


def _not_found(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Knowledge Revision 不存在",
        detail=detail,
        repair="刷新 Revision 列表后重试。",
        status_code=404,
    )


def _capacity_exceeded(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Knowledge Index 容量上限已达到",
        detail=detail,
        repair="缩小 Approved Source Scope，或发布并重新评测新的 Index Profile。",
        status_code=409,
    )


def _merge_query_hits(
    units: tuple[tuple[KnowledgeRetrievalHit, ...], ...], policy: RetrievalPolicyRevision
) -> tuple[KnowledgeRetrievalHit, ...]:
    """内部等权 RRF；只在全部单元成功后一次应用最终预算。"""
    scores: dict[tuple[str, str, str], Fraction] = {}
    canonical: dict[tuple[str, str, str], KnowledgeRetrievalHit] = {}
    groups = contribution_groups(units)
    for ordinal, hits in enumerate(units):
        if groups[ordinal][0] != ordinal:
            continue
        seen: set[tuple[str, str, str]] = set()
        for rank, hit in enumerate(hits, 1):
            key = (hit.source_id, hit.snapshot_id, hit.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            previous = canonical.get(key)
            if previous is not None and (
                previous.content_sha256 != hit.content_sha256
                or previous.citation_id != hit.citation_id
            ):
                raise _not_ready("KNOWLEDGE_QUERY_HIT_IDENTITY_DRIFT", "同一命中内容身份漂移")
            canonical[key] = hit
            scores[key] = scores.get(key, Fraction()) + Fraction(1, 60 + rank)
    quantized = {key: round(float(score), policy.score_precision)
                 for key, score in scores.items()}
    ranked = sorted(scores, key=lambda key: (-quantized[key], key))
    output: list[KnowledgeRetrievalHit] = []
    consumed = 0
    for key in ranked:
        hit = canonical[key]
        score = quantized[key]
        size = len(hit.content.encode("utf-8"))
        if score < policy.min_score or len(output) >= policy.top_k:
            continue
        if consumed + size > policy.max_context_bytes:
            continue
        consumed += size
        # 多 query 无单个 lexical/vector rank；原形状不变，不伪报某片排名。
        output.append(hit.model_copy(update={"score": RetrievalScore(rrf_score=score)}))
    return tuple(output)
