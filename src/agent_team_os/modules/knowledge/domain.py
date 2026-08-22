from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...shared.hashes import Sha256
from ...shared.permissions import Role


class WikiAccess(StrEnum):
    NONE = "none"
    READ = "read"
    COMMENT = "comment"
    EDIT = "edit"
    ADMIN = "admin"


class KnowledgeActor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    role: Role


class Space(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    description: str
    version: int = Field(ge=1)
    created_by: str
    created_at: datetime
    updated_at: datetime


class SpaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)


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
    created_by: str
    created_at: datetime
    updated_at: datetime


class Revision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    revision: int = Field(ge=1)
    content: JsonValue
    search_text: str
    content_sha256: Sha256
    created_by: str
    created_at: datetime


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str
    parent_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    content: JsonValue


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    parent_id: str | None = None
    content: JsonValue | None = None


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
