from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256
from ...shared.verification import VerificationSnapshot


def utc_now() -> datetime:
    return datetime.now(UTC)


DelegatePurpose = Literal["workspace_write", "artifact", "review"]


class WorkspaceRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["git_repository_v1"]


class DelegationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_children: int = Field(default=3, ge=0, le=3)
    max_concurrency: int = Field(default=2, ge=1, le=2)
    max_writers: int = Field(default=1, ge=0, le=1)
    max_depth: Literal[1] = 1
    wall_clock_budget_seconds: int = Field(default=900, ge=30, le=3600)

    @model_validator(mode="after")
    def limits_are_coherent(self) -> DelegationPolicy:
        if self.max_concurrency > self.max_children:
            raise ValueError("max_concurrency cannot exceed max_children")
        if self.max_writers > self.max_children:
            raise ValueError("max_writers cannot exceed max_children")
        return self


class WorkcellDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workcell_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    name: str = Field(min_length=1, max_length=120)
    responsibility: str = Field(min_length=1, max_length=1_000)
    primary_workspace: WorkspaceRequirement
    delegate_purposes: tuple[DelegatePurpose, ...] = Field(min_length=1, max_length=3)
    delegation_policy: DelegationPolicy = Field(default_factory=DelegationPolicy)

    @model_validator(mode="after")
    def purposes_are_unique_and_policy_compatible(self) -> WorkcellDefinition:
        if len(set(self.delegate_purposes)) != len(self.delegate_purposes):
            raise ValueError("delegate purposes must be unique")
        if "workspace_write" in self.delegate_purposes and self.delegation_policy.max_writers != 1:
            raise ValueError("workspace_write requires exactly one writer capacity")
        if "workspace_write" not in self.delegate_purposes and self.delegation_policy.max_writers:
            raise ValueError("writer capacity requires workspace_write purpose")
        return self


class TopologyNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workcell_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    x: int = Field(ge=0, le=10_000)
    y: int = Field(ge=0, le=10_000)


class TopologyLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_workcell_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    target_workcell_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    label: str = Field(default="artifact", min_length=1, max_length=80)


class TeamTopology(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[TopologyNode, ...]
    links: tuple[TopologyLink, ...] = ()


class TeamTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    latest_revision: int | None = Field(default=None, ge=1)
    version: int = Field(default=1, ge=1)
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamTemplateDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    template_id: str
    name: str
    description: str
    workcells: tuple[WorkcellDefinition, ...]
    topology: TeamTopology
    version: int = Field(default=1, ge=1)
    validation_status: Literal["unknown", "valid", "invalid"] = "unknown"
    validation_errors: tuple[str, ...] = ()
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamTemplateRevision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str
    revision: int = Field(ge=1)
    name: str
    description: str
    workcells: tuple[WorkcellDefinition, ...]
    topology: TeamTopology
    sha256: Sha256
    published_by: str
    published_at: datetime = Field(default_factory=utc_now)

    @property
    def revision_id(self) -> str:
        return f"{self.template_id}:{self.revision}"


class TeamTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    workcells: tuple[WorkcellDefinition, ...] = Field(min_length=1, max_length=32)
    topology: TeamTopology


class TeamTemplateDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    workcells: tuple[WorkcellDefinition, ...] | None = Field(
        default=None, min_length=1, max_length=32
    )
    topology: TeamTopology | None = None


class TeamTemplateWithDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template: TeamTemplate
    draft: TeamTemplateDraft


class TeamTemplateVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


WorkspaceAdapterType = Literal["managed-bare-git", "external-git"]
WorkspaceBindingStatus = Literal["pending", "ready", "failed"]
ProjectTeamBindingStatus = Literal["provisioning", "active", "legacy_projected"]


class ProjectTeamBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    template_id: str
    template_revision: int = Field(ge=1)
    template_sha256: Sha256
    status: ProjectTeamBindingStatus
    version: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def revision_id(self) -> str:
        return f"{self.template_id}:{self.template_revision}"


class WorkspaceBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    kind: Literal["git_repository_v1"]
    adapter_type: WorkspaceAdapterType
    repository_uri: str = Field(min_length=1, max_length=500)
    credential_reference: str | None = Field(default=None, max_length=500)
    status: WorkspaceBindingStatus
    verification_sha256: Sha256 | None = None
    verification: dict[str, object] = Field(default_factory=dict)
    verification_profile_id: str | None = None
    verification_profile: VerificationSnapshot | None = None
    verification_profile_error_code: str | None = None
    error_code: str | None = None
    version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectWorkcellBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    workcell_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    workspace_binding_id: str
    version: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkspaceBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workcell_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    kind: Literal["git_repository_v1"]
    adapter_type: WorkspaceAdapterType
    repository_uri: str = Field(min_length=1, max_length=500)
    credential_reference: str | None = Field(default=None, max_length=500)
    verification_profile_id: str | None = None

    @model_validator(mode="after")
    def credential_matches_adapter(self) -> WorkspaceBindingCreate:
        if self.adapter_type == "managed-bare-git" and self.credential_reference is not None:
            raise ValueError("managed-bare-git does not accept credential_reference")
        return self


class WorkspaceBindingVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class WorkspaceVerificationProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    verification_profile_id: str


class TeamActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class WorkspaceBindingAssignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workcell_binding: ProjectWorkcellBinding
    workspace_binding: WorkspaceBinding


class ProjectWorkcellTopology(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    project_status: str
    team_binding: ProjectTeamBinding
    team_revision: TeamTemplateRevision
    workcell_bindings: tuple[ProjectWorkcellBinding, ...] = ()
    workspace_bindings: tuple[WorkspaceBinding, ...] = ()
