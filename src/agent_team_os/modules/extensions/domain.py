from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...shared.hashes import Sha256

ExtensionKind = Literal["skill", "plugin", "mcp"]


class RuntimeExtensionInstall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    kind: ExtensionKind
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=40)
    source_uri: str = Field(min_length=1, max_length=500)
    revision_sha256: Sha256
    requested_permissions: tuple[str, ...] = ()

    @field_validator("source_uri")
    @classmethod
    def source_matches_kind(cls, value: str, info: object) -> str:
        data = getattr(info, "data", {})
        kind = data.get("kind") if isinstance(data, dict) else None
        if kind and not value.startswith(f"{kind}://"):
            raise ValueError("Extension source URI must match its kind")
        return value


class RuntimeExtensionRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", min_length=1, max_length=120)
    kind: ExtensionKind
    version: str = Field(min_length=1, max_length=80)
    optional: bool = False


class RuntimeExtensionVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class RuntimeExtension(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    kind: ExtensionKind
    version_label: str
    source_uri: str
    revision_sha256: Sha256
    requested_permissions: tuple[str, ...]
    status: Literal["installed", "qualified", "failed", "disabled"]
    qualification_sha256: Sha256 | None = None
    qualification_errors: tuple[str, ...] = ()
    version: int = Field(ge=1)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
