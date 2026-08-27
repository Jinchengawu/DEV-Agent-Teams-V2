from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256, sha256_json
from ...shared.ids import new_id


def utc_now() -> datetime:
    return datetime.now(UTC)


class EvaluationDimension(StrEnum):
    TOOL_CALL = "tool_call"
    GENERAL_AGENT = "general_agent"
    DATA_GENERATION = "data_generation"
    CONTROL_PLANE = "control_plane"


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    version: str
    source: str
    source_sha256: Sha256
    scorer_version: str
    dimensions: tuple[EvaluationDimension, ...]
    required_runtime_features: tuple[str, ...] = ()
    official: bool = False


class SubjectSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pipeline_revision_id: str
    pipeline_fingerprint: Sha256
    binding_model: str
    deployment_snapshots: dict[str, dict[str, object]] = Field(default_factory=dict)
    git_revision: str
    acwm_revision: str


class MetricObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: float
    unit: str
    scope: str = "case"


class Judgment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    judge_identity: str
    rubric_version: str
    outcome: Literal["win", "tie", "loss", "conflict"]
    scores: dict[str, float] = Field(default_factory=dict)
    rationale_sha256: Sha256


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    run_id: str
    case_id: str
    dimension: EvaluationDimension
    category: str
    difficulty: int | None = Field(default=None, ge=1, le=3)
    status: Literal["passed", "failed", "blocked", "unsupported", "cancelled"]
    candidate_score: float | None = None
    baseline_score: float | None = None
    metrics: tuple[MetricObservation, ...] = ()
    judgment: Judgment | None = None
    artifact_sha256: Sha256
    trace_sha256: Sha256 | None = None
    failure_code: str | None = None
    evidence_identity: str
    created_at: datetime = Field(default_factory=utc_now)


class HumanReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    run_id: str
    case_id: str
    reviewer_id: str
    outcome: Literal["win", "tie", "loss"]
    notes_sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    suite: EvaluationSuite
    candidate: SubjectSnapshot
    baseline: SubjectSnapshot
    mode: Literal["offline", "live"]
    profile: Literal["smoke", "standard", "live"]
    seed: int
    concurrency: tuple[int, ...]
    timeout_seconds: int = Field(ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    status: Literal["queued", "running", "completed", "failed", "cancelled", "blocked"] = "queued"
    version: int = Field(default=1, ge=1)
    evidence_identity: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DimensionSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: EvaluationDimension
    total: int
    evaluated: int
    passed: int
    failed: int
    blocked: int
    unsupported: int
    candidate_accuracy: float | None = None
    candidate_accuracy_ci95: tuple[float, float] | None = None
    baseline_accuracy: float | None = None
    delta_percentage_points: float | None = None
    wins: int = 0
    ties: int = 0
    losses: int = 0
    win_rate: float | None = None
    non_loss_rate: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    p99_latency_ms: float | None = None
    baseline_p95_latency_ms: float | None = None
    latency_regression_percent: float | None = None
    metric_percentiles: dict[str, dict[str, float]] = Field(default_factory=dict)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    suite_id: str
    suite_version: str
    mode: Literal["offline", "live"]
    profile: Literal["smoke", "standard", "live"]
    candidate: SubjectSnapshot
    baseline: SubjectSnapshot
    dimensions: tuple[DimensionSummary, ...]
    gate_status: Literal["passed", "failed", "calibrating", "blocked", "not_run"]
    gate_reasons: tuple[str, ...] = ()
    proof_scope: Literal["fixture_harness_only", "live_runtime"]
    official_benchmark: bool = False
    human_review_required: bool = False
    human_agreement: float | None = None
    cohens_kappa: float | None = None
    evidence_sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def no_official_fixture_claim(self) -> EvaluationReport:
        if self.proof_scope == "fixture_harness_only" and self.official_benchmark:
            raise ValueError("deterministic fixtures cannot produce an official score")
        return self


class CalibrationProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    suite_sha256: Sha256
    subject_fingerprint: Sha256
    sample_count: int = Field(ge=3)
    metric_medians: dict[str, float]
    metric_mad: dict[str, float]
    evidence_sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = "backend-delivery"
    candidate_revision: int | None = Field(default=None, ge=1)
    baseline: Literal["active"] = "active"
    suite_id: str = "agent-team-os-mvp"
    mode: Literal["offline", "live"] = "offline"
    profile: Literal["smoke", "standard", "live"] = "standard"
    seed: int = 20260824
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    max_cost_usd: float | None = Field(default=None, ge=0)


class HumanReviewImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviews: tuple[HumanReview, ...]


def evaluation_report_sha256(report: EvaluationReport) -> Sha256:
    return sha256_json(report.model_dump(mode="python", exclude={"evidence_sha256"}))
