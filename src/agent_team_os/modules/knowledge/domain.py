from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ...shared.hashes import Sha256
from ...shared.permissions import Role


class WikiAccess(StrEnum):
    NONE = "none"
    READ = "read"
    COMMENT = "comment"
    EDIT = "edit"
    ADMIN = "admin"


class SpaceKind(StrEnum):
    PROJECT_DOCUMENTS = "project-documents"
    CUSTOM = "custom"
    LEGACY_ARCHIVE = "legacy-archive"


class KnowledgeLifecycleStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DocumentKind(StrEnum):
    PRODUCT_REQUIREMENT = "product-requirement"
    DELIVERY_PLAN = "delivery-plan"
    DESIGN_SPEC = "design-spec"
    FRONTEND_TECHNICAL = "frontend-technical"
    BACKEND_API = "backend-api"
    TEST_PLAN = "test-plan"
    TEST_REPORT = "test-report"
    PROJECT_GENERAL = "project-general"


class RevisionProducerKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    LEGACY = "legacy"
    LEGACY_MIGRATION = "legacy-migration"


class RevisionProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    producer_kind: RevisionProducerKind
    producer_id: str = Field(min_length=1)
    agent_run_id: str | None = None
    binding_site: str | None = None
    contract_id: str | None = None
    artifact_id: str | None = None
    artifact_key: str | None = None
    runtime_identity: str | None = None
    source_artifact_sha256: Sha256 | None = None


class AssetReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(pattern=r"^(external-link|provider-snapshot|project-artifact)$")
    url: str | None = None
    provider_snapshot_id: str | None = None
    project_artifact_id: str | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> AssetReference:
        if self.kind == "external-link":
            if self.url is None:
                raise ValueError("external-link requires url")
            parsed = urlsplit(self.url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("external-link only supports HTTPS")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("external-link cannot contain credentials")
            credential_markers = (
                "token",
                "secret",
                "password",
                "auth",
                "credential",
                "signature",
                "api_key",
                "apikey",
            )
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
                normalized = key.lower().replace("-", "_")
                if any(marker in normalized for marker in credential_markers):
                    raise ValueError("external-link cannot contain credential parameters")
            if self.provider_snapshot_id is not None or self.project_artifact_id is not None:
                raise ValueError("external-link cannot contain another asset identifier")
            return self
        if self.kind == "provider-snapshot":
            if self.provider_snapshot_id is None:
                raise ValueError("provider-snapshot requires provider_snapshot_id")
            if self.url is not None or self.project_artifact_id is not None:
                raise ValueError("provider-snapshot cannot contain another asset identifier")
            return self
        if self.project_artifact_id is None:
            raise ValueError("project-artifact requires project_artifact_id")
        if self.url is not None or self.provider_snapshot_id is not None:
            raise ValueError("project-artifact cannot contain another asset identifier")
        return self


class KnowledgeActor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    role: Role


class Space(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    scope_kind: str = "project"
    project_id: str | None = "legacy-default"
    space_kind: SpaceKind = SpaceKind.CUSTOM
    lifecycle_status: KnowledgeLifecycleStatus = KnowledgeLifecycleStatus.ACTIVE
    name: str
    description: str
    version: int = Field(ge=1)
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class SpaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    scope_kind: str = Field(default="project", pattern=r"^(project|global)$")
    project_id: str | None = "legacy-default"

    @model_validator(mode="after")
    def validate_scope(self) -> SpaceCreate:
        if self.scope_kind == "project" and self.project_id is None:
            raise ValueError("project knowledge space requires project_id")
        if self.scope_kind == "global" and self.project_id is not None:
            raise ValueError("global knowledge space cannot reference a project")
        return self


class Document(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    space_id: str
    parent_id: str | None = None
    title: str
    current_revision: int = Field(ge=1)
    version: int = Field(ge=1)
    source_kind: str
    source_id: str | None = None
    document_kind: DocumentKind = DocumentKind.PROJECT_GENERAL
    role_key: str | None = None
    delivery_id: str | None = None
    lifecycle_status: KnowledgeLifecycleStatus = KnowledgeLifecycleStatus.ACTIVE
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class Revision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    revision: int = Field(ge=1)
    content: JsonValue
    search_text: str
    content_sha256: Sha256
    provenance: RevisionProvenance = Field(
        default_factory=lambda: RevisionProvenance(
            producer_kind=RevisionProducerKind.LEGACY,
            producer_id="legacy-system",
        )
    )
    asset_references: tuple[AssetReference, ...] = ()
    created_by: str | None
    created_at: datetime


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str
    parent_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    document_kind: DocumentKind = DocumentKind.PROJECT_GENERAL
    role_key: str | None = Field(default=None, min_length=1, max_length=120)
    delivery_id: str | None = Field(default=None, min_length=1, max_length=160)
    content: JsonValue
    asset_references: tuple[AssetReference, ...] = ()


class KnowledgeDerivation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    project_id: str
    target_space_id: str
    source_kind: str
    source_id: str
    source_revision: str
    source_sha256: Sha256
    created_by: str
    created_at: datetime


class KnowledgeDerivationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    source_kind: str = Field(pattern=r"^(evidence|provider-snapshot)$")
    source_id: str = Field(min_length=1)
    expected_source_sha256: Sha256
    target_space_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)


class KnowledgeDerivationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document: Document
    derivation: KnowledgeDerivation
    created: bool


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    parent_id: str | None = None
    content: JsonValue | None = None
    asset_references: tuple[AssetReference, ...] | None = None


class RevisionRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class Comment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    document_id: str
    parent_id: str | None = None
    body: str
    author_id: str
    resolved: bool
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: str | None = None
    body: str = Field(min_length=1, max_length=10_000)


class CommentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    body: str | None = Field(default=None, min_length=1, max_length=10_000)
    resolved: bool | None = None


class PermissionGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_kind: str
    resource_id: str
    user_id: str
    access: WikiAccess


class SystemKnowledgeArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1, max_length=320)
    title: str = Field(min_length=1, max_length=240)
    content: JsonValue
