from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from ...shared.hashes import Sha256
from ...shared.ids import new_id
from ...shared.verification import VerificationProfileSnapshot
from ..agents import AgentRun
from ..artifacts import ArtifactReference
from .domain import DelegationPolicy


def utc_now() -> datetime:
    return datetime.now(UTC)


WorkcellRunStatus = Literal[
    "planning",
    "delegating",
    "verifying",
    "reviewing",
    "synthesizing",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "interrupted",
]
AttemptStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "interrupted",
]
AttemptPhase = Literal["planning", "delegate", "synthesis", "legacy"]
WorkspaceAccess = Literal[
    "none",
    "workspace_write",
    "candidate_read",
    "artifact_only",
    "legacy",
]


class FrozenSlotBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_key: Literal["main", "delegate_1", "delegate_2", "delegate_3"]
    deployment_id: str
    resolved_provider_binding_hash: Sha256
    deployment_snapshot: dict[str, object]


class WorkcellWorkspaceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_binding_id: str
    kind: Literal["git_repository_v1"]
    adapter_type: Literal["managed-bare-git", "external-git"]
    repository_uri: str
    base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    verification_sha256: Sha256
    verification_profile: VerificationProfileSnapshot | None = None

    @model_serializer(mode="wrap")
    def serialize_legacy_profile(  # type: ignore[no-untyped-def]
        self, handler: SerializerFunctionWrapHandler
    ):
        # 返回注解会让 Pydantic 用任意字典替换模型的输出 Schema，因此保留模型字段推导。
        payload: dict[str, Any] = handler(self)
        if self.verification_profile is None:
            payload.pop("verification_profile", None)
        return payload


class WorkcellExecutionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    team_template_revision_id: str
    team_template_sha256: Sha256
    pipeline_revision_id: str
    pipeline_revision_sha256: Sha256
    stage_path: str
    workcell_key: str
    workspace: WorkcellWorkspaceSnapshot
    delegation_policy: DelegationPolicy
    slot_bindings: tuple[FrozenSlotBinding, ...] = Field(min_length=1, max_length=4)
    slot_method_bindings: dict[Literal["delegate_1", "delegate_2", "delegate_3"], str] = Field(
        default_factory=dict
    )
    slot_purpose_bindings: dict[
        Literal["delegate_1", "delegate_2", "delegate_3"],
        Literal["workspace_write", "artifact", "review"],
    ] = Field(default_factory=dict)
    method_snapshot_sha256: Sha256
    input_artifacts: tuple[ArtifactReference, ...] = ()

    @model_validator(mode="after")
    def slots_are_unique_and_main_is_present(self) -> WorkcellExecutionSnapshot:
        keys = tuple(item.slot_key for item in self.slot_bindings)
        if len(set(keys)) != len(keys):
            raise ValueError("slot bindings must be unique")
        if "main" not in keys:
            raise ValueError("workcell snapshot requires main slot")
        if set(self.slot_method_bindings) != set(self.slot_purpose_bindings):
            raise ValueError("frozen method and purpose slots must match")
        return self


class WorkcellRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    delivery_id: str
    pipeline_run_id: str
    stage_attempt_id: str
    stage_path: str
    loop_iteration: int = Field(ge=1)
    workcell_key: str
    workcell_snapshot: WorkcellExecutionSnapshot
    workcell_snapshot_sha256: Sha256
    status: WorkcellRunStatus
    main_agent_run_id: str | None = None
    version: int = Field(ge=1)
    deadline_at: datetime
    error_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    agent_run_id: str
    phase: AttemptPhase
    ordinal: int = Field(ge=1)
    provider_binding_hash: Sha256
    runtime_identity: str | None = None
    status: AttemptStatus
    error_code: str | None = None
    result_artifact_sha256: Sha256 | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class DelegationAssignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_key: Literal["delegate_1", "delegate_2", "delegate_3"]
    delegate_purpose: Literal["workspace_write", "artifact", "review"]
    workspace_access: WorkspaceAccess
    method_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")
    input_artifacts: tuple[ArtifactReference, ...] = ()

    @model_validator(mode="after")
    def purpose_matches_access(self) -> DelegationAssignment:
        required = {
            "workspace_write": "workspace_write",
            "artifact": "artifact_only",
            "review": "candidate_read",
        }
        if self.workspace_access != required[self.delegate_purpose]:
            raise ValueError("delegate purpose does not match workspace access")
        return self


class DelegationPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    workcell_run_id: str
    main_agent_run_id: str
    assignments: tuple[DelegationAssignment, ...] = Field(max_length=3)
    sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)


class CandidateVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    workcell_run_id: str
    writer_agent_run_id: str
    candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256
    status: Literal["passed", "failed"]
    report: dict[str, object]
    sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)


class CandidateVerificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    writer_agent_run_id: str
    candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256
    status: Literal["passed", "failed"]
    report: dict[str, object]


class BlockingFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1_000)
    evidence_sha256: Sha256


class ReviewArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    workcell_run_id: str
    reviewer_agent_run_id: str
    candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256
    reviewer_binding_hash: Sha256
    blocking_findings: tuple[BlockingFinding, ...] = ()
    artifact_reference: ArtifactReference
    sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)


class ReviewArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_agent_run_id: str
    candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256
    blocking_findings: tuple[BlockingFinding, ...] = ()
    artifact_reference: ArtifactReference


class WorkcellResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    workcell_run_id: str
    candidate_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256 | None = None
    verification_sha256: Sha256
    review_artifact_ids: tuple[str, ...] = ()
    output_artifact_references: tuple[ArtifactReference, ...] = ()
    knowledge_citation_ids: tuple[str, ...] = ()
    sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)


class WorkcellResultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256 | None = None
    verification_sha256: Sha256
    review_artifact_ids: tuple[str, ...] = ()
    output_artifact_references: tuple[ArtifactReference, ...] = ()
    knowledge_citation_ids: tuple[str, ...] = ()


class WorkcellResultValidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    workcell_run_id: str
    status: Literal["passed", "failed"]
    artifact_references: tuple[ArtifactReference, ...] = Field(min_length=1)
    report: dict[str, object]
    sha256: Sha256
    created_at: datetime = Field(default_factory=utc_now)


class WorkcellResultValidationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "failed"]
    artifact_references: tuple[ArtifactReference, ...] = Field(min_length=1)
    report: dict[str, object]


class WorkcellRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: str
    pipeline_run_id: str
    stage_attempt_id: str
    loop_iteration: int = Field(default=1, ge=1)
    snapshot: WorkcellExecutionSnapshot


class WorkcellRunCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class WorkcellRunTree(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workcell_run: WorkcellRun
    delegation_plan: DelegationPlan | None = None
    agent_runs: tuple[AgentRun, ...] = ()
    attempts: tuple[AgentAttempt, ...] = ()
    verification: CandidateVerification | None = None
    result_validation: WorkcellResultValidation | None = None
    reviews: tuple[ReviewArtifact, ...] = ()
    result: WorkcellResult | None = None
