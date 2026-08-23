from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from ...shared.hashes import Sha256, sha256_json

CREDENTIAL_REFERENCE = re.compile(r"(?:env:[A-Z][A-Z0-9_]*|keychain:[A-Za-z0-9._-]+)")


def _credential_reference(value: str) -> str:
    if CREDENTIAL_REFERENCE.fullmatch(value) is None:
        raise ValueError("凭据必须使用 env: 或 keychain: 引用")
    return value


class KnowledgeProviderKind(StrEnum):
    FEISHU = "feishu"


class ProviderNodeKind(StrEnum):
    FOLDER = "folder"
    DOCUMENT = "document"


class ProviderSyncStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class ProviderActor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    product_user_id: str = Field(min_length=1)
    provider_user_id: str = Field(min_length=1)


class ProviderBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_kind: KnowledgeProviderKind
    display_name: str = Field(min_length=1, max_length=120)
    external_space_id: str = Field(min_length=1, max_length=240)
    credential_ref: str = Field(min_length=1, max_length=240)

    _credential_must_be_a_reference = field_validator("credential_ref")(
        _credential_reference
    )


class ProviderBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    provider_kind: KnowledgeProviderKind
    display_name: str
    external_space_id: str
    credential_ref: str
    enabled: bool
    version: int = Field(ge=1)
    created_by: str
    created_at: datetime
    updated_at: datetime

    _credential_must_be_a_reference = field_validator("credential_ref")(
        _credential_reference
    )


class ProviderSpace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str
    title: str


class ProviderNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str
    external_space_id: str
    parent_external_id: str | None = None
    source_id: str | None = None
    title: str
    kind: ProviderNodeKind
    provider_revision: str | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def document_must_reference_fetchable_source(self) -> ProviderNode:
        if self.kind == ProviderNodeKind.DOCUMENT and self.source_id is None:
            raise ValueError("文档节点必须关联可抓取的内容源")
        return self


class ProviderSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    provider_revision: str
    content_type: str
    normalized_content: JsonValue
    normalized_text: str
    content_sha256: Sha256
    source_url: str | None = None
    fetched_at: datetime

    @model_validator(mode="after")
    def hash_must_match_normalized_content(self) -> ProviderSnapshot:
        actual = sha256_json(self.normalized_content)
        if actual != self.content_sha256:
            raise ValueError("内容哈希与标准化快照不一致")
        return self


class ProviderSnapshotRecord(ProviderSnapshot):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    binding_id: str
    fetched_by_product_user_id: str
    fetched_by_provider_user_id: str


class ProviderSyncRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    binding_id: str
    source_id: str
    status: ProviderSyncStatus
    provider_revision: str | None = None
    snapshot_id: str | None = None
    snapshot_sha256: Sha256 | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def terminal_state_must_have_consistent_evidence(self) -> ProviderSyncRun:
        if self.status == ProviderSyncStatus.SUCCEEDED and (
            self.snapshot_sha256 is None
            or self.snapshot_id is None
            or self.provider_revision is None
            or self.error_code is not None
        ):
            raise ValueError("同步成功必须关联 Provider Revision 和快照哈希")
        if self.status in {ProviderSyncStatus.FAILED, ProviderSyncStatus.UNAVAILABLE} and not (
            self.error_code
        ):
            raise ValueError("同步失败或不可用必须提供稳定错误码")
        if self.status in {
            ProviderSyncStatus.SUCCEEDED,
            ProviderSyncStatus.FAILED,
            ProviderSyncStatus.UNAVAILABLE,
        } and self.completed_at is None:
            raise ValueError("终态同步必须记录完成时间")
        if self.status != ProviderSyncStatus.QUEUED and self.started_at is None:
            raise ValueError("已启动同步必须记录开始时间")
        return self


class ProviderSyncResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: ProviderSyncRun
    snapshot: ProviderSnapshotRecord | None = None

    @model_validator(mode="after")
    def successful_result_must_include_snapshot(self) -> ProviderSyncResult:
        if (self.run.status == ProviderSyncStatus.SUCCEEDED) != (self.snapshot is not None):
            raise ValueError("同步成功状态与快照必须一致")
        return self
