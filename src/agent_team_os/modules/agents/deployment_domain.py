from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...shared.hashes import Sha256
from .domain import AgentCapabilityRequirement


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProviderManifestView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    revision: str
    fingerprint: Sha256
    runtime_types: tuple[str, ...]
    capabilities: tuple[dict[str, str], ...]
    workflow_modes: tuple[str, ...]
    required_features: tuple[str, ...]
    input_contracts: tuple[dict[str, object], ...] = ()
    output_contracts: tuple[dict[str, object], ...] = ()
    permission_requirements: tuple[str, ...] = ()


class AgentDeployment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    profile_id: str
    profile_revision: int = Field(ge=1)
    profile_sha256: Sha256
    capability_requirements: tuple[AgentCapabilityRequirement, ...]
    instance_id: str
    instance_version: int = Field(ge=1)
    adapter_id: str
    adapter_version: str
    provider_id: str
    provider_revision: str
    provider_fingerprint: Sha256
    isolation_mode: Literal["shared", "dedicated"]
    policy_snapshot: dict[str, str]
    extension_snapshot: tuple[dict[str, object], ...] = ()
    qualification_status: Literal["unknown", "qualified", "failed"] = "unknown"
    qualification_errors: tuple[str, ...] = ()
    enabled: bool = False
    version: int = Field(default=1, ge=1)
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentDeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    profile_id: str
    profile_revision: int = Field(ge=1)
    instance_id: str
    provider_id: str


class AgentDeploymentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    profile_id: str | None = None
    profile_revision: int | None = Field(default=None, ge=1)
    instance_id: str | None = None
    provider_id: str | None = None


class AgentDeploymentVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
