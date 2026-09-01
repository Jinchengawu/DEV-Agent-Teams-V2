from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256
from ...shared.ids import new_id


def utc_now() -> datetime:
    return datetime.now(UTC)


class WorkspaceCandidateV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    delivery_id: str
    project_id: str
    workcell_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    workspace_binding_id: str
    repository_uri: str
    adapter_type: Literal["managed-bare-git", "external-git"]
    base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256
    candidate_branch: str
    verification_sha256: Sha256
    review_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=3)
    evidence_sha256: Sha256
    status: Literal["verified"] = "verified"
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def candidate_identity_is_coherent(self) -> WorkspaceCandidateV2:
        expected = f"agent-team-os/{self.delivery_id}/{self.workcell_key}"
        if self.candidate_branch != expected:
            raise ValueError("candidate branch does not match delivery/workcell identity")
        if self.base_revision == self.candidate_revision:
            raise ValueError("candidate must differ from base")
        if len(set(self.review_artifact_ids)) != len(self.review_artifact_ids):
            raise ValueError("review artifact ids must be unique")
        return self


class WorkspaceCandidateV2Create(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: str
    project_id: str
    workcell_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    workspace_binding_id: str
    repository_uri: str
    adapter_type: Literal["managed-bare-git", "external-git"]
    base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256
    verification_sha256: Sha256
    review_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=3)


class GitHubPRReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    provider: Literal["github"] = "github"
    pull_request_id: int = Field(ge=1)
    url: str = Field(pattern=r"^https://github\.com/")
    base_branch: Literal["main"] = "main"
    head_branch: str
    head_candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    state: Literal["open", "draft", "closed", "merged"]
    receipt_sha256: Sha256
    observed_at: datetime = Field(default_factory=utc_now)


class GitHubPRReceiptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pull_request_id: int = Field(ge=1)
    url: str = Field(pattern=r"^https://github\.com/")
    base_branch: Literal["main"] = "main"
    head_branch: str
    head_candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    state: Literal["open", "draft", "closed", "merged"]


class ReleaseBundleV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: str
    project_id: str
    pipeline_revision_id: str
    release_contract_snapshot: tuple[str, ...] = Field(min_length=1)
    candidates: tuple[WorkspaceCandidateV2, ...] = Field(min_length=1)
    bundle_sha256: Sha256
    status: Literal["verified"] = "verified"
    verified_at: datetime = Field(default_factory=utc_now)


class RemoteApplyReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: str
    ordinal: int = Field(ge=0)
    candidate_id: str
    workcell_key: str
    repository_uri: str
    before_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    after_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    recovered: bool = False
    receipt_sha256: Sha256
    applied_at: datetime = Field(default_factory=utc_now)


class ReleaseApplyAttemptV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: str
    project_id: str
    bundle_sha256: Sha256
    status: Literal["applying", "needs_attention", "completed"]
    error_code: str | None = None
    version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ReleaseManifestV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    delivery_id: str
    pipeline_revision_id: str
    bundle_sha256: Sha256
    repositories: tuple[RemoteApplyReceipt, ...]
    manifest_sha256: Sha256
    status: Literal["active"] = "active"
    activated_at: datetime = Field(default_factory=utc_now)


class ReleaseHealthV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    status: Literal["healthy", "release_drifted"]
    delivery_id: str | None = None
    bundle_sha256: Sha256 | None = None
    error_code: str | None = None
    version: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=utc_now)


class ExternalReleaseView(BaseModel):
    """Observable projection for PR, partial Apply receipts and Manifest evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: str
    project_id: str | None = None
    candidates: tuple[WorkspaceCandidateV2, ...] = ()
    pull_requests: tuple[GitHubPRReceipt, ...] = ()
    bundle: ReleaseBundleV2 | None = None
    apply_attempt: ReleaseApplyAttemptV2 | None = None
    remote_apply_receipts: tuple[RemoteApplyReceipt, ...] = ()
    manifest: ReleaseManifestV2 | None = None
