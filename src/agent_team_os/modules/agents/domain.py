from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...shared.hashes import Sha256


def utc_now() -> datetime:
    return datetime.now(UTC)


class AgentInstructions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template_ref: str | None = None
    custom_text: str = Field(default="", max_length=20_000)
    variables_schema: str | None = None
    examples: tuple[dict[str, object], ...] = ()

    @field_validator("template_ref")
    @classmethod
    def template_is_a_reference(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("prompt://"):
            raise ValueError("template_ref must use prompt://")
        return value

    @field_validator("variables_schema")
    @classmethod
    def variables_are_a_schema_reference(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("schema://"):
            raise ValueError("variables_schema must use schema://")
        return value


class AgentCapabilityRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    version: str = Field(min_length=1, max_length=80)

    @field_validator("version")
    @classmethod
    def version_is_declarative(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9A-Za-z.*<>=,!~^+-]+", value):
            raise ValueError("capability version contains unsupported characters")
        return value


class AgentExtensionRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    kind: Literal["skill", "plugin", "mcp"]
    version: str = Field(min_length=1, max_length=80)
    optional: bool = False


class AgentPolicyReferences(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_policy_ref: str
    resource_policy_ref: str
    approval_policy_ref: str
    memory_policy_ref: str
    delegation_policy_ref: str

    @field_validator("*")
    @classmethod
    def policy_is_a_reference(cls, value: str) -> str:
        if not value.startswith("policy://"):
            raise ValueError("policy references must use policy://")
        return value


class AgentProfileSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    tags: tuple[str, ...] = ()
    instructions: AgentInstructions
    capabilities: tuple[AgentCapabilityRequirement, ...]
    policies: AgentPolicyReferences
    isolation_preference: Literal["shared", "dedicated"] = "shared"
    extensions: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def has_unique_capabilities_and_tags(self) -> AgentProfileSpec:
        capability_ids = [item.id for item in self.capabilities]
        if not capability_ids:
            raise ValueError("at least one capability is required")
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability ids must be unique")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags must be unique")
        extension_ids = [item.id for item in self.extension_requirements]
        if len(extension_ids) != len(set(extension_ids)):
            raise ValueError("runtime extension ids must be unique")
        return self

    @property
    def extension_requirements(self) -> tuple[AgentExtensionRequirement, ...]:
        raw = self.extensions.get("runtime_extensions", ())
        if not isinstance(raw, (list, tuple)):
            raise ValueError("extensions.runtime_extensions must be a list")
        return tuple(AgentExtensionRequirement.model_validate(item) for item in raw)


class AgentProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    description: str
    tags: tuple[str, ...]
    latest_revision: int | None = None
    version: int = Field(ge=1)
    created_by: str
    created_at: datetime
    updated_at: datetime


class AgentProfileDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    spec: AgentProfileSpec
    version: int = Field(ge=1)
    validation_status: Literal["unknown", "valid", "invalid"] = "unknown"
    validation_errors: tuple[str, ...] = ()
    updated_by: str
    updated_at: datetime


class AgentProfileRevision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    revision: int = Field(ge=1)
    spec: AgentProfileSpec
    canonical_json: str
    sha256: Sha256
    published_by: str
    published_at: datetime


class AgentProfileWithDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: AgentProfile
    draft: AgentProfileDraft


class AgentProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: AgentProfileSpec


class AgentProfileDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    spec: AgentProfileSpec


class AgentProfileVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class AgentSpecImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["json", "yaml"]
    content: str = Field(min_length=1, max_length=200_000)


class AgentSpecExport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal["json", "yaml"]
    content: str
    canonical_json: str
    sha256: Sha256
