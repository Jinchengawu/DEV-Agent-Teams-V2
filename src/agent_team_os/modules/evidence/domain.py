from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256


class EvidenceKind(StrEnum):
    JOURNEY = "journey"
    REQUIREMENT = "requirement"
    TASK = "task"
    PLAN_GATE = "plan-gate"
    DESIGN_GATE = "design-gate"
    CANDIDATE = "candidate"
    DIFF = "diff"
    VERIFICATION = "verification"
    CANDIDATE_GATE = "candidate-gate"
    APPLY_RECEIPT = "apply-receipt"
    RELEASE_BUNDLE = "release-bundle"
    RELEASE_MANIFEST = "release-manifest"
    EVALUATION_REPORT = "evaluation-report"


class EvidenceStatus(StrEnum):
    VERIFIED = "verified"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str = "legacy-default"
    delivery_id: str
    kind: EvidenceKind
    source_kind: str
    source_id: str
    producer_identity: str
    content_sha256: Sha256 | None = None
    status: EvidenceStatus
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    verified_at: datetime | None = None
    verification_error: str | None = None

    @model_validator(mode="after")
    def verified_requires_hash(self) -> EvidenceRecord:
        if self.status == EvidenceStatus.VERIFIED and self.content_sha256 is None:
            raise ValueError("verified evidence requires a non-zero SHA-256")
        return self


class EvidenceVerificationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    evidence_id: str
    status: EvidenceStatus
    error: str | None = None
    verified_at: datetime
