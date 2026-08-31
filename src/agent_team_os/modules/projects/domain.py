from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256
from ...shared.repositories import RepositoryRole, RepositorySnapshot


def utc_now() -> datetime:
    return datetime.now(UTC)


ProjectLifecycle = Literal["provisioning", "active", "provision_failed", "archived"]
WorkspaceStatus = Literal["provisioning", "ready", "failed"]


class Project(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    lifecycle_status: ProjectLifecycle
    version: int = Field(ge=1)
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectWorkspace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    workspace_id: str
    seed_revision: str | None = None
    repository_ref: str
    status: WorkspaceStatus
    provision_attempt: int = Field(ge=1)
    error_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectRepository(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    role: RepositoryRole
    workspace_ref: str
    repository_ref: str
    seed_revision: str | None = None
    status: WorkspaceStatus
    provision_attempt: int = Field(ge=1)
    error_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def snapshot(self) -> RepositorySnapshot:
        if self.status != "ready" or self.seed_revision is None:
            raise ValueError("repository is not ready")
        return RepositorySnapshot(
            role=self.role,
            workspace_ref=self.workspace_ref,
            repository_ref=self.repository_ref,
            seed_revision=self.seed_revision,
        )


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    default_pipeline_revision_id: str
    deployment_ids: tuple[str, ...] = ()
    team_template_revision_id: str | None = Field(default=None, min_length=3, max_length=180)
    repository_mode: Literal["backend", "fullstack"] = Field(
        default="backend",
        json_schema_extra={"deprecated": True},
    )

    @model_validator(mode="after")
    def legacy_repository_mode_is_not_mixed_with_workcells(self) -> ProjectCreate:
        if self.team_template_revision_id is not None and self.repository_mode != "backend":
            raise ValueError(
                "repository_mode cannot be combined with team_template_revision_id"
            )
        return self


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)


class ProjectPipelineBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    pipeline_id: str
    pipeline_revision: int = Field(ge=1)
    enabled: bool
    is_default: bool
    version: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def revision_id(self) -> str:
        return f"{self.pipeline_id}:{self.pipeline_revision}"


class ProjectBindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int | None = Field(default=None, ge=1)
    pipeline_revision_id: str
    enabled: bool = True
    is_default: bool = False


class ProjectDeploymentAccess(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    deployment_id: str
    enabled: bool
    version: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectDeploymentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str
    enabled: bool = True
    expected_version: int | None = Field(default=None, ge=1)


class ProjectKnowledgeSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    binding_id: str
    source_scope: str = "*"
    enabled: bool
    version: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectKnowledgeSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding_id: str = Field(min_length=1)
    source_scope: str = Field(default="*", min_length=1)
    enabled: bool = True
    expected_version: int | None = Field(default=None, ge=1)


class ProjectExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    project_version: int
    workspace_id: str
    repository_ref: str
    pipeline_revision_id: str
    deployment_ids: tuple[str, ...]
    repositories: tuple[RepositorySnapshot, ...] = ()
    repository_set_sha256: Sha256 | None = None


class ProjectDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project: Project
    workspace: ProjectWorkspace
    pipeline_bindings: tuple[ProjectPipelineBinding, ...] = ()
    deployment_access: tuple[ProjectDeploymentAccess, ...] = ()
    knowledge_sources: tuple[ProjectKnowledgeSource, ...] = ()
    repositories: tuple[ProjectRepository, ...] = ()
    active_delivery_id: str | None = None
