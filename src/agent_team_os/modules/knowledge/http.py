from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from .application import WikiService
from .domain import (
    Comment,
    CommentCreate,
    CommentPatch,
    Document,
    DocumentCreate,
    DocumentPatch,
    KnowledgeActor,
    PermissionGrant,
    Revision,
    RevisionRestoreRequest,
    Space,
    SpaceCreate,
)


def create_wiki_router(
    service: WikiService,
    read_actor: Callable[[Request], KnowledgeActor],
    mutation_actor: Callable[[Request], KnowledgeActor],
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/wiki/spaces", response_model=tuple[Space, ...])
    def list_spaces(request: Request) -> tuple[Space, ...]:
        actor = read_actor(request)
        return service.list_spaces(actor)

    @router.post("/v1/wiki/spaces", response_model=Space, status_code=201)
    def create_space(request_body: SpaceCreate, request: Request) -> Space:
        actor = mutation_actor(request)
        return service.create_space(actor, request_body)

    @router.get("/v1/wiki/documents", response_model=tuple[Document, ...])
    def list_documents(
        request: Request,
        space_id: str | None = None,
    ) -> tuple[Document, ...]:
        actor = read_actor(request)
        return service.list_documents(actor, space_id)

    @router.post("/v1/wiki/documents", response_model=Document, status_code=201)
    def create_document(request_body: DocumentCreate, request: Request) -> Document:
        actor = mutation_actor(request)
        return service.create_document(actor, request_body)

    @router.get(
        "/v1/wiki/documents/{document_id}/revisions/{revision}",
        response_model=Revision,
    )
    def get_revision(
        document_id: str, revision: int, request: Request
    ) -> Revision:
        actor = read_actor(request)
        return service.get_revision(actor, document_id, revision)

    @router.post(
        "/v1/wiki/documents/{document_id}/revisions/{revision}/restore",
        response_model=Document,
    )
    def restore_revision(
        document_id: str,
        revision: int,
        request_body: RevisionRestoreRequest,
        request: Request,
    ) -> Document:
        actor = mutation_actor(request)
        return service.restore_revision(
            actor, document_id, revision, request_body.expected_version
        )

    @router.get("/v1/wiki/documents/{document_id}/revisions", response_model=tuple[Revision, ...])
    def list_revisions(document_id: str, request: Request) -> tuple[Revision, ...]:
        actor = read_actor(request)
        return service.list_revisions(actor, document_id)

    @router.get("/v1/wiki/documents/{document_id}", response_model=Document)
    def get_document(document_id: str, request: Request) -> Document:
        actor = read_actor(request)
        return service.get_document(actor, document_id)

    @router.patch("/v1/wiki/documents/{document_id}", response_model=Document)
    def patch_document(
        document_id: str, request_body: DocumentPatch, request: Request
    ) -> Document:
        actor = mutation_actor(request)
        return service.patch_document(actor, document_id, request_body)

    @router.get(
        "/v1/wiki/documents/{document_id}/comments",
        response_model=tuple[Comment, ...],
    )
    def list_comments(document_id: str, request: Request) -> tuple[Comment, ...]:
        actor = read_actor(request)
        return service.list_comments(actor, document_id)

    @router.post(
        "/v1/wiki/documents/{document_id}/comments",
        response_model=Comment,
        status_code=201,
    )
    def create_comment(
        document_id: str, request_body: CommentCreate, request: Request
    ) -> Comment:
        actor = mutation_actor(request)
        return service.add_comment(actor, document_id, request_body)

    @router.patch("/v1/wiki/comments/{comment_id}", response_model=Comment)
    def patch_comment(
        comment_id: str, request_body: CommentPatch, request: Request
    ) -> Comment:
        actor = mutation_actor(request)
        return service.patch_comment(actor, comment_id, request_body)

    @router.put("/v1/wiki/permissions", response_model=PermissionGrant)
    def put_permission(request_body: PermissionGrant, request: Request) -> PermissionGrant:
        actor = mutation_actor(request)
        return service.put_permission(actor, request_body)

    @router.get("/v1/wiki/search", response_model=tuple[Document, ...])
    def search(q: str, request: Request) -> tuple[Document, ...]:
        actor = read_actor(request)
        return service.search(actor, q)

    return router
