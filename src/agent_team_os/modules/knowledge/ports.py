from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from .domain import (
    Comment,
    Document,
    KnowledgeDerivation,
    PermissionGrant,
    Revision,
    Space,
    WikiAccess,
)


class CompareAndSwapResult(StrEnum):
    UPDATED = "updated"
    VERSION_CONFLICT = "version-conflict"
    NOT_FOUND = "not-found"


class WikiRepository(Protocol):
    def create_space(self, space: Space) -> Space: ...

    def reconcile_project_space(self, space: Space) -> Space: ...

    def ensure_system_space(self, space: Space) -> Space: ...

    def get_space(self, space_id: str) -> Space | None: ...

    def list_spaces(self) -> tuple[Space, ...]: ...

    def create_document(self, document: Document, revision: Revision) -> Document: ...

    def create_derived_document(
        self,
        document: Document,
        revision: Revision,
        derivation: KnowledgeDerivation,
    ) -> tuple[Document, KnowledgeDerivation, bool]: ...

    def ensure_system_document(self, document: Document, revision: Revision) -> Document: ...

    def get_document(self, document_id: str) -> Document | None: ...

    def get_document_by_source(self, source_kind: str, source_id: str) -> Document | None: ...

    def list_documents(self, space_id: str | None = None) -> tuple[Document, ...]: ...

    def get_revision(self, document_id: str, revision: int) -> Revision | None: ...

    def list_revisions(self, document_id: str) -> tuple[Revision, ...]: ...

    def compare_and_swap_document(
        self, expected_version: int, document: Document, revision: Revision
    ) -> CompareAndSwapResult: ...

    def search_document_ids(self, query: str) -> tuple[str, ...]: ...

    def create_comment(self, comment: Comment) -> Comment: ...

    def get_comment(self, comment_id: str) -> Comment | None: ...

    def list_comments(self, document_id: str) -> tuple[Comment, ...]: ...

    def compare_and_swap_comment(
        self, expected_version: int, comment: Comment
    ) -> CompareAndSwapResult: ...

    def get_permission(
        self, resource_kind: str, resource_id: str, user_id: str
    ) -> WikiAccess | None: ...

    def put_permission(self, grant: PermissionGrant) -> PermissionGrant: ...
