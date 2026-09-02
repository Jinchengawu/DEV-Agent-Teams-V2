from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256
from ..artifacts import ArtifactReference


class EmbeddingModelDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_name: str = Field(min_length=1, max_length=240)
    model_digest: str = Field(min_length=8, max_length=240)


class EmbeddingQualificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(min_length=1, max_length=240)
    tokenizer_contract: str = "ollama-embed-input-v1"
    vector_normalization: Literal["none", "l2"] = "none"
    distance_metric: Literal["cosine", "l2"] = "cosine"


class EmbeddingQualificationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    provider_kind: Literal["ollama"] = "ollama"
    model_name: str
    model_digest: str
    dimension: int = Field(gt=0)
    adapter_revision: str
    tokenizer_contract: str
    vector_normalization: Literal["none", "l2"]
    distance_metric: Literal["cosine", "l2"]
    sqlite_vec_version: Literal["0.1.9"] = "0.1.9"
    vector_index_adapter_revision: str
    qualification_sha256: Sha256
    status: Literal["qualified", "drifted"]
    qualified_at: datetime


class KnowledgeIndexProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=180)
    display_name: str = Field(min_length=1, max_length=120)
    embedding_model_name: str = Field(min_length=1, max_length=240)
    normalizer_id: Literal["feishu-block-normalizer-v1"] = "feishu-block-normalizer-v1"
    chunker_id: Literal["block-aware-chunker-v1"] = "block-aware-chunker-v1"
    lexical_analyzer_id: Literal["cjk-bigram-canonical-v1"] = "cjk-bigram-canonical-v1"
    index_schema: Literal["sqlite-vec-hybrid-v1"] = "sqlite-vec-hybrid-v1"
    max_chunk_characters: int = Field(default=1_200, ge=100, le=20_000)
    chunk_overlap_characters: int = Field(default=150, ge=0, le=5_000)
    max_documents: int = Field(default=5_000, ge=1, le=100_000)
    max_chunks: int = Field(default=100_000, ge=1, le=1_000_000)
    capacity_warning_ratio: float = Field(default=0.8, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def overlap_is_smaller_than_chunk(self) -> KnowledgeIndexProfileCreate:
        if self.chunk_overlap_characters >= self.max_chunk_characters:
            raise ValueError("chunk overlap must be smaller than max chunk characters")
        return self


class KnowledgeIndexProfileRevision(KnowledgeIndexProfileCreate):
    model_config = ConfigDict(frozen=True, extra="forbid")

    config_sha256: Sha256
    published_by: str
    published_at: datetime


class RetrievalPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=180)
    display_name: str = Field(min_length=1, max_length=120)
    index_profile_revision_id: str = Field(min_length=1, max_length=180)
    query_normalizer_id: Literal["cjk-bigram-canonical-v1"] = "cjk-bigram-canonical-v1"
    lexical_candidates: int = Field(default=20, ge=1, le=500)
    vector_candidates: int = Field(default=20, ge=1, le=500)
    top_k: int = Field(default=8, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=10_000)
    score_precision: int = Field(default=8, ge=0, le=12)
    min_score: float = Field(default=0.0, ge=0.0)
    max_context_bytes: int = Field(default=65_536, ge=1, le=2_000_000)
    empty_result_policy: Literal["allow-empty", "fail"] = "allow-empty"


class RetrievalPolicyRevision(RetrievalPolicyCreate):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_sha256: Sha256
    published_by: str
    published_at: datetime


class RetrievalEvaluationPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=180)
    retrieval_policy_revision_id: str = Field(min_length=1, max_length=180)
    index_profile_revision_id: str = Field(min_length=1, max_length=180)
    dataset_manifest_sha256: Sha256
    recall_at_k_min: float = Field(ge=0.0, le=1.0)
    zero_hit_rate_max: float = Field(ge=0.0, le=1.0)
    error_rate_max: float = Field(ge=0.0, le=1.0)
    p95_latency_ms_max: int = Field(gt=0)
    peak_rss_bytes_max: int = Field(gt=0)
    target_hardware: str = Field(min_length=1, max_length=500)


class RetrievalEvaluationPolicyRevision(RetrievalEvaluationPolicyCreate):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_sha256: Sha256
    published_by: str
    published_at: datetime


class RetrievalEvaluationCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=180)
    query: str = Field(min_length=1, max_length=10_000)
    expected_source_ids: tuple[str, ...] = Field(min_length=1)


class RetrievalEvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_policy_revision_id: str = Field(min_length=1, max_length=180)
    index_revision_id: str = Field(min_length=1, max_length=180)
    cases: tuple[RetrievalEvaluationCase, ...] = Field(min_length=1, max_length=10_000)
    target_hardware: str = Field(min_length=1, max_length=500)


class RetrievalEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    evaluation_policy_revision_id: str
    index_revision_id: str
    dataset_manifest_sha256: Sha256
    status: Literal["passed", "failed"]
    recall_at_k: float = Field(ge=0.0, le=1.0)
    zero_hit_rate: float = Field(ge=0.0, le=1.0)
    error_rate: float = Field(ge=0.0, le=1.0)
    p95_latency_ms: int = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    target_hardware: str
    case_count: int = Field(gt=0)
    report_artifact: ArtifactReference
    run_by: str
    created_at: datetime


class KnowledgeIndexBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_binding_id: str = Field(min_length=1, max_length=180)
    index_profile_revision_id: str = Field(min_length=1, max_length=180)
    embedding_qualification_id: str = Field(min_length=1, max_length=180)


KnowledgeIndexStatus = Literal[
    "building",
    "built",
    "qualified",
    "active",
    "stale",
    "superseded",
    "failed",
]


class KnowledgeIndexRevision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    provider_binding_id: str
    index_profile_revision_id: str
    embedding_qualification_id: str | None = None
    input_manifest_sha256: Sha256
    status: KnowledgeIndexStatus
    storage_uri: str | None = None
    storage_sha256: Sha256 | None = None
    document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(ge=0)
    capacity_status: Literal["normal", "warning"] = "normal"
    version: int = Field(ge=1)
    created_by: str
    created_at: datetime
    qualified_at: datetime | None = None
    activated_at: datetime | None = None
    error_code: str | None = None
    evaluation_report_uri: str | None = None
    evaluation_report_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def usable_revision_has_storage_evidence(self) -> KnowledgeIndexRevision:
        if self.status in {"built", "qualified", "active", "stale", "superseded"} and (
            self.storage_uri is None or self.storage_sha256 is None
        ):
            raise ValueError("built index revision requires immutable storage evidence")
        if self.status in {"qualified", "active", "stale", "superseded"} and (
            self.qualified_at is None
            or self.evaluation_report_uri is None
            or self.evaluation_report_sha256 is None
        ):
            raise ValueError("qualified index revision requires evaluation evidence")
        return self


class KnowledgeIndexCatalog(BaseModel):
    """Read model for the operator console; all entries remain immutable revisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profiles: tuple[KnowledgeIndexProfileRevision, ...] = ()
    qualifications: tuple[EmbeddingQualificationSnapshot, ...] = ()
    retrieval_policies: tuple[RetrievalPolicyRevision, ...] = ()
    evaluation_policies: tuple[RetrievalEvaluationPolicyRevision, ...] = ()
    index_revisions: tuple[KnowledgeIndexRevision, ...] = ()
    evaluation_reports: tuple[RetrievalEvaluationReport, ...] = ()


class ProjectKnowledgeRetrievalOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_binding_id: str
    index_revision_id: str
    index_profile_revision_id: str
    retrieval_policy_revision_id: str


class KnowledgeRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=120)
    provider_binding_id: str = Field(min_length=1, max_length=180)
    retrieval_policy_revision_id: str = Field(min_length=1, max_length=180)
    query: str = Field(min_length=1, max_length=10_000)
    allowed_source_ids: tuple[str, ...]


class RetrievalScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lexical_rank: int | None = Field(default=None, ge=1)
    lexical_score: float | None = None
    vector_rank: int | None = Field(default=None, ge=1)
    vector_distance: float | None = Field(default=None, ge=0.0)
    rrf_score: float = Field(ge=0.0)


class KnowledgeRetrievalHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    citation_id: str
    chunk_id: str
    source_id: str
    snapshot_id: str
    title: str
    source_url: str | None = None
    block_anchor: str
    content: str
    content_sha256: Sha256
    score: RetrievalScore


class KnowledgeRetrievalReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    provider_binding_id: str
    index_revision_id: str
    retrieval_policy_revision_id: str
    requested_by: str
    query_sha256: Sha256
    allowed_source_set_sha256: Sha256
    hit_ids: tuple[str, ...]
    empty_reason: str | None = None
    created_at: datetime


class KnowledgeRetrievalResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt: KnowledgeRetrievalReceipt
    hits: tuple[KnowledgeRetrievalHit, ...]
