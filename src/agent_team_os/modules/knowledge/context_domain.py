from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...delivery import (
    DeliveryExecutionSnapshot,
    DeliveryKnowledgeContextSnapshot,
    DeliveryKnowledgeContextUnavailableSnapshot,
    KnowledgePreparationInputV1,
)
from ...shared.hashes import Sha256
from ..artifacts import ArtifactReference


class MembershipAuthorizationComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["membership"] = "membership"
    membership_id: str
    version: int = Field(ge=1)


class AdministratorBypassAuthorizationComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["administrator_bypass"] = "administrator_bypass"
    sentinel: Literal["administrator-bypass:no-membership:v1"] = (
        "administrator-bypass:no-membership:v1"
    )
    receipt_id: str
    receipt_sha256: Sha256


AuthorizationAccessComponent = Annotated[
    MembershipAuthorizationComponent | AdministratorBypassAuthorizationComponent,
    Field(discriminator="kind"),
]


class AuthorizationApprovalComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    approval_version: int = Field(ge=1)
    binding_id: str
    binding_authorization_version: int = Field(ge=1)
    approved_source_scope_sha256: Sha256


class AuthorizationConnectionComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_id: str
    authorization_version: int = Field(ge=1)


class KnowledgeAuthorizationStampV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: Literal["best-effort-revoke-v1"] = "best-effort-revoke-v1"
    global_identity_policy_revision: Literal[1] = 1
    project_id: str
    authorized_principal_id: str
    identity_authorization_version: int = Field(ge=1)
    global_role: str
    project_authorization_version: int = Field(ge=1)
    access_component: AuthorizationAccessComponent
    approvals: tuple[AuthorizationApprovalComponent, ...]
    connections: tuple[AuthorizationConnectionComponent, ...]
    authorization_epoch_hash: Sha256


class KnowledgeContextStageResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    preparation_run_id: str
    stage_path: str
    query_sha256: Sha256
    retrieval_policy_revision_id: str
    context: DeliveryKnowledgeContextSnapshot
    created_at: datetime


class KnowledgeContextRuntimeView(BaseModel):
    """Verified, immutable Data Context exposed at one Stage boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage_path: str
    artifact_reference: ArtifactReference
    content: dict[str, object]
    citation_ids: tuple[str, ...]
    authorization_epoch_hash: Sha256


KnowledgeContextPreparationStatus = Literal[
    "queued",
    "leased",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
]


class KnowledgeContextPreparationRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    delivery_id: str
    input_sha256: Sha256
    knowledge_binding_hash: Sha256
    preparation_input: KnowledgePreparationInputV1
    status: KnowledgeContextPreparationStatus
    attempt_count: int = Field(ge=0)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    authorization_stamp: KnowledgeAuthorizationStampV1 | None = None
    authorization_epoch_hash: Sha256 | None = None
    final_snapshot: DeliveryExecutionSnapshot | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeCitationUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    citation_id: str
    stage_paths: tuple[str, ...]
    workcell_run_ids: tuple[str, ...] = ()


class DeliveryKnowledgeContextOverview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: str
    delivery_status: str
    preparation_run: KnowledgeContextPreparationRun | None = None
    contexts: tuple[DeliveryKnowledgeContextSnapshot, ...] = ()
    unavailable: tuple[DeliveryKnowledgeContextUnavailableSnapshot, ...] = ()
    citations: tuple[KnowledgeCitationUsage, ...] = ()
