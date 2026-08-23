from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class Pipeline(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    active_revision: int | None = Field(default=None, ge=1)
    version: int = Field(default=1, ge=1)
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PipelineDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    pipeline_id: str
    name: str = Field(min_length=1, max_length=120)
    definition: dict[str, object]
    layout: dict[str, object] = Field(default_factory=dict)
    input_schema: dict[str, object] = Field(default_factory=dict)
    agent_assignments: dict[str, str] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    validation_status: Literal["unknown", "valid", "invalid"] = "unknown"
    validation_errors: tuple[str, ...] = ()
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PipelineRevision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pipeline_id: str
    revision: int = Field(ge=1)
    definition: dict[str, object]
    compiled_graph: dict[str, object]
    binding_snapshot: dict[str, dict[str, object]]
    binding_model: Literal["legacy-v0", "provider-v1"] = "legacy-v0"
    resolved_provider_bindings: dict[str, dict[str, object]] = Field(default_factory=dict)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_by: str
    published_at: datetime = Field(default_factory=utc_now)


class GraphCompilation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    graph: dict[str, object]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_ids: tuple[str, ...]


class PipelineRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    delivery_id: str
    pipeline_revision_id: str
    graph_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str
    version: int = Field(ge=1)
    snapshot: dict[str, object]
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PipelineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    definition: dict[str, object]
    layout: dict[str, object] = Field(default_factory=dict)
    input_schema: dict[str, object] = Field(default_factory=dict)
    agent_assignments: dict[str, str] = Field(default_factory=dict)


class PipelineDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    definition: dict[str, object] | None = None
    layout: dict[str, object] | None = None
    input_schema: dict[str, object] | None = None
    agent_assignments: dict[str, str] | None = None


class PipelineWithDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pipeline: Pipeline
    draft: PipelineDraft
