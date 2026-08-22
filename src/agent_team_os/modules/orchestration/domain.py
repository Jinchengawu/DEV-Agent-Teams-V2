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
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_by: str
    published_at: datetime = Field(default_factory=utc_now)


class GraphCompilation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    graph: dict[str, object]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_ids: tuple[str, ...]


class PipelineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    definition: dict[str, object]
    layout: dict[str, object] = Field(default_factory=dict)
    input_schema: dict[str, object] = Field(default_factory=dict)


class PipelineWithDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pipeline: Pipeline
    draft: PipelineDraft
