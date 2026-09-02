from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...modules.artifacts import ArtifactReference
from ...shared.hashes import Sha256
from .provider_domain import KnowledgeProviderKind

_SECRET_REFERENCE = re.compile(r"(?:env:(?://)?[A-Z][A-Z0-9_]*|keychain:(?://)?[A-Za-z0-9._-]+)")


def _secret_reference(value: str) -> str:
    if _SECRET_REFERENCE.fullmatch(value) is None:
        raise ValueError("凭据必须使用 env:/env:// 或 keychain:/keychain:// 引用")
    return value


TenantConnectionStatus = Literal["unverified", "ready", "degraded", "disabled"]


class TenantConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_kind: KnowledgeProviderKind
    display_name: str = Field(min_length=1, max_length=120)
    app_id_ref: str = Field(min_length=1, max_length=240)
    app_secret_ref: str = Field(min_length=1, max_length=240)

    _app_id_is_reference = field_validator("app_id_ref")(_secret_reference)
    _app_secret_is_reference = field_validator("app_secret_ref")(_secret_reference)


class TenantConnection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    provider_kind: KnowledgeProviderKind
    display_name: str
    access_model: Literal["tenant-service-principal-v1"] = "tenant-service-principal-v1"
    app_id_ref: str
    app_secret_ref: str
    status: TenantConnectionStatus
    authorization_version: int = Field(default=1, ge=1)
    version: int = Field(ge=1)
    created_by: str
    created_at: datetime
    updated_at: datetime
    last_diagnosed_at: datetime | None = None
    last_error_code: str | None = None

    _app_id_is_reference = field_validator("app_id_ref")(_secret_reference)
    _app_secret_is_reference = field_validator("app_secret_ref")(_secret_reference)


class TenantProviderBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(min_length=1, max_length=180)
    display_name: str = Field(min_length=1, max_length=120)
    external_space_id: str = Field(min_length=1, max_length=240)
    root_node_token: str | None = Field(default=None, min_length=1, max_length=240)
    replaces_binding_id: str | None = Field(default=None, min_length=1, max_length=180)


class TenantProviderBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    connection_id: str
    display_name: str
    external_space_id: str
    root_node_token: str | None = None
    status: Literal["ready", "degraded", "disabled"]
    authorization_version: int = Field(default=1, ge=1)
    version: int = Field(ge=1)
    replaces_binding_id: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    last_permission_probe_at: datetime | None = None
    last_error_code: str | None = None


KnowledgeSyncJobStatus = Literal[
    "queued",
    "leased",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
]


class KnowledgeSyncJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: str = Field(min_length=1, max_length=180)
    source_id: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=240)


class TenantProviderSnapshotRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    binding_id: str
    source_id: str
    provider_revision: str
    content_type: str
    artifact: ArtifactReference
    normalized_text_sha256: Sha256
    source_url: str | None = None
    fetched_by_product_user_id: str
    fetched_at: datetime


class KnowledgeSyncJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    binding_id: str
    source_id: str
    idempotency_key: str
    status: KnowledgeSyncJobStatus
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    retry_at: datetime | None = None
    provider_revision: str | None = None
    snapshot_id: str | None = None
    snapshot_sha256: Sha256 | None = None
    error_code: str | None = None
    requested_by: str
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
