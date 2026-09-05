"""产品控制的机器验证方案与不可变工具链资格契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .hashes import Sha256


class VerificationProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    revision: int = Field(ge=1)
    name: str
    commands: tuple[tuple[str, ...], ...] = Field(min_length=1)
    timeout_seconds: int = Field(ge=1, le=300)
    environment: dict[str, str]
    result_contract: Literal["python-unittest-count-v1", "node-tap-count-v1"]
    tool_names: tuple[Literal["python", "node"], ...]


class VerificationToolIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["python", "node"]
    executable: str
    version: str
    executable_sha256: Sha256


class VerificationProfileSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: VerificationProfile
    profile_sha256: Sha256
    tools: tuple[VerificationToolIdentity, ...] = Field(min_length=1)
    qualification_sha256: Sha256


class VerificationProfileV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["verification-profile-v2"] = "verification-profile-v2"
    id: str
    revision: int = Field(ge=1)
    name: str
    workcell_key: Literal["design", "frontend", "backend", "qa"]
    commands: tuple[tuple[str, ...], ...] = Field(min_length=1)
    timeout_seconds: int = Field(ge=1, le=300)
    environment: dict[str, str]
    result_contract: Literal["fullstack-verification-v2"] = "fullstack-verification-v2"
    tool_names: tuple[Literal["python", "node"], ...]
    config_paths: tuple[str, ...]
    dependency_names: tuple[str, ...]
    input_contracts: tuple[str, ...] = ()
    output_contract: str | None = None


class VerificationDependencyIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    root: str
    content_sha256: Sha256


class VerificationFileIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: Sha256


class VerificationQualificationV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["verification-qualification-v2"] = "verification-qualification-v2"
    profile: VerificationProfileV2
    profile_sha256: Sha256
    tools: tuple[VerificationToolIdentity, ...]
    dependencies: tuple[VerificationDependencyIdentity, ...]
    workspace_files: tuple[VerificationFileIdentity, ...]
    qualification_sha256: Sha256


VerificationProfileLike = VerificationProfile | VerificationProfileV2
VerificationSnapshot = VerificationProfileSnapshot | VerificationQualificationV2
