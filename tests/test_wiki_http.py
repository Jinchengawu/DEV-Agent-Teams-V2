from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from agent_team_os.modules.knowledge import (
    Comment,
    CommentCreate,
    CommentPatch,
    Document,
    DocumentCreate,
    DocumentPatch,
    KnowledgeActor,
    PermissionGrant,
    Revision,
    Space,
    SpaceCreate,
    WikiAccess,
    create_wiki_router,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.ids import new_id
from agent_team_os.shared.permissions import Role


def _dt(value: int) -> datetime:
    return datetime(2026, 8, 22, 12, 0, 0, value)


class DummyWikiService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.fail_on_search = False

    def list_spaces(
        self,
        actor: KnowledgeActor,
        project_id: str | None = None,
        include_global: bool = True,
        include_archived: bool = False,
    ) -> tuple[Space, ...]:
        self.calls.append(
            ("list_spaces", actor, project_id, include_global, include_archived)
        )
        return (
            Space(
                id="space-id",
                name="技术总览",
                description="wiki",
                version=1,
                created_by="user-admin",
                created_at=_dt(1),
                updated_at=_dt(2),
            ),
        )

    def create_space(self, actor: KnowledgeActor, request: SpaceCreate) -> Space:
        self.calls.append(("create_space", actor, request))
        return Space(
            id="space-new",
            name=request.name,
            description=request.description,
            version=1,
            created_by=actor.user_id,
            created_at=_dt(3),
            updated_at=_dt(3),
        )

    def list_documents(
        self,
        actor: KnowledgeActor,
        space_id: str | None = None,
        document_kind: object | None = None,
        role_key: str | None = None,
        delivery_id: str | None = None,
        source_kind: str | None = None,
        include_archived: bool = False,
    ) -> tuple[Document, ...]:
        self.calls.append(
            (
                "list_documents",
                actor,
                space_id,
                document_kind,
                role_key,
                delivery_id,
                source_kind,
                include_archived,
            )
        )
        return (
            Document(
                id="doc-id",
                space_id=space_id or "space-id",
                parent_id=None,
                title="交付文档",
                current_revision=2,
                version=1,
                source_kind="manual",
                created_by=actor.user_id,
                created_at=_dt(4),
                updated_at=_dt(5),
            ),
        )

    def create_document(self, actor: KnowledgeActor, request: DocumentCreate) -> Document:
        self.calls.append(("create_document", actor, request))
        return Document(
            id="doc-new",
            space_id=request.space_id,
            parent_id=request.parent_id,
            title=request.title,
            current_revision=1,
            version=1,
            source_kind="manual",
            created_by=actor.user_id,
            created_at=_dt(6),
            updated_at=_dt(6),
        )

    def get_document(self, actor: KnowledgeActor, document_id: str) -> Document:
        self.calls.append(("get_document", actor, document_id))
        return Document(
            id=document_id,
            space_id="space-id",
            parent_id=None,
            title="交付文档",
            current_revision=1,
            version=1,
            source_kind="manual",
            created_by=actor.user_id,
            created_at=_dt(7),
            updated_at=_dt(8),
        )

    def patch_document(
        self, actor: KnowledgeActor, document_id: str, request: DocumentPatch
    ) -> Document:
        self.calls.append(("patch_document", actor, document_id, request))
        return Document(
            id=document_id,
            space_id="space-id",
            parent_id=None,
            title=request.title or "交付文档",
            current_revision=2,
            version=request.expected_version + 1,
            source_kind="manual",
            created_by=actor.user_id,
            created_at=_dt(7),
            updated_at=_dt(9),
        )

    def list_revisions(self, actor: KnowledgeActor, document_id: str) -> tuple[Revision, ...]:
        self.calls.append(("list_revisions", actor, document_id))
        return (
            Revision(
                document_id=document_id,
                revision=1,
                content={"version": "first"},
                search_text="",
                content_sha256="a" * 64,
                created_by="user-admin",
                created_at=_dt(10),
            ),
            Revision(
                document_id=document_id,
                revision=2,
                content={"version": "second"},
                search_text="",
                content_sha256="b" * 64,
                created_by="user-admin",
                created_at=_dt(11),
            ),
        )

    def get_revision(self, actor: KnowledgeActor, document_id: str, revision: int) -> Revision:
        self.calls.append(("get_revision", actor, document_id, revision))
        return Revision(
            document_id=document_id,
            revision=revision,
            content={"version": revision},
            search_text="",
            content_sha256="c" * 64,
            created_by="user-admin",
            created_at=_dt(12),
        )

    def restore_revision(
        self, actor: KnowledgeActor, document_id: str, revision: int, expected_version: int
    ) -> Document:
        self.calls.append(
            ("restore_revision", actor, document_id, revision, expected_version)
        )
        return Document(
            id=document_id,
            space_id="space-id",
            parent_id=None,
            title="交付文档",
            current_revision=3,
            version=expected_version + 1,
            source_kind="manual",
            created_by=actor.user_id,
            created_at=_dt(7),
            updated_at=_dt(13),
        )

    def list_comments(self, actor: KnowledgeActor, document_id: str) -> tuple[Comment, ...]:
        self.calls.append(("list_comments", actor, document_id))
        return (
            Comment(
                id="comment-id",
                document_id=document_id,
                parent_id=None,
                body="评审意见",
                author_id=actor.user_id,
                resolved=False,
                version=1,
                created_at=_dt(14),
                updated_at=_dt(15),
            ),
        )

    def add_comment(
        self, actor: KnowledgeActor, document_id: str, request: CommentCreate
    ) -> Comment:
        self.calls.append(("add_comment", actor, document_id, request))
        return Comment(
            id="comment-new",
            document_id=document_id,
            parent_id=request.parent_id,
            body=request.body,
            author_id=actor.user_id,
            resolved=False,
            version=1,
            created_at=_dt(16),
            updated_at=_dt(16),
        )

    def patch_comment(
        self, actor: KnowledgeActor, comment_id: str, request: CommentPatch
    ) -> Comment:
        self.calls.append(("patch_comment", actor, comment_id, request))
        return Comment(
            id=comment_id,
            document_id="doc-id",
            parent_id=None,
            body=request.body or "评审意见",
            author_id=actor.user_id,
            resolved=bool(request.resolved),
            version=request.expected_version + 1,
            created_at=_dt(14),
            updated_at=_dt(17),
        )

    def put_permission(self, actor: KnowledgeActor, grant: PermissionGrant) -> PermissionGrant:
        self.calls.append(("put_permission", actor, grant))
        return grant

    def search(self, actor: KnowledgeActor, query: str) -> tuple[Document, ...]:
        if self.fail_on_search:
            raise ProductError(
                code="WIKI_SEARCH_FAILED",
                title="检索失败",
                detail="检索索引不可用。",
                repair="稍后重试。",
                status_code=503,
            )
        self.calls.append(("search", actor, query))
        return self.list_documents(actor, None)


class ActorResolver:
    def __init__(self, actor: KnowledgeActor) -> None:
        self.actor = actor
        self.calls: list[Request] = []

    def __call__(self, request: Request) -> KnowledgeActor:
        self.calls.append(request)
        return self.actor


def _app(
    service: DummyWikiService,
    read_actor: ActorResolver,
    mutation_actor: ActorResolver,
) -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_wiki_router(service, read_actor=read_actor, mutation_actor=mutation_actor)
    )

    @app.exception_handler(ProductError)
    async def _product_error_handler(
        _request: Request, error: ProductError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error.problem(new_id()).model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    return app


@pytest.mark.anyio
async def test_wiki_http_router_routes_and_actor_separation() -> None:
    service = DummyWikiService()
    read_actor = ActorResolver(
        KnowledgeActor(user_id="read-user", role=Role.VIEWER)
    )
    mutation_actor = ActorResolver(
        KnowledgeActor(user_id="mutation-user", role=Role.ADMINISTRATOR)
    )
    app = _app(service, read_actor, mutation_actor)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/wiki/spaces")
        assert response.status_code == 200
        assert len(response.json()) == 1

        response = await client.post(
            "/v1/wiki/spaces",
            json={"name": "研发路线", "description": "路线图"},
        )
        assert response.status_code == 201
        assert response.json()["name"] == "研发路线"

        response = await client.get("/v1/wiki/documents")
        assert response.status_code == 200

        response = await client.get("/v1/wiki/documents?space_id=space-id")
        assert response.status_code == 200

        response = await client.post(
            "/v1/wiki/documents",
            json={
                "space_id": "space-id",
                "title": "交付指标",
                "content": {"kind": "doc", "text": "稳定交付"},
            },
        )
        assert response.status_code == 201

        response = await client.get("/v1/wiki/documents/doc-id")
        assert response.status_code == 200

        response = await client.patch(
            "/v1/wiki/documents/doc-id",
            json={"expected_version": 1, "title": "交付指标（修订）"},
        )
        assert response.status_code == 200

        response = await client.get("/v1/wiki/documents/doc-id/revisions")
        assert response.status_code == 200

        response = await client.get("/v1/wiki/documents/doc-id/revisions/2")
        assert response.status_code == 200

        response = await client.post(
            "/v1/wiki/documents/doc-id/revisions/1/restore",
            json={"expected_version": 2},
        )
        assert response.status_code == 200

        response = await client.get("/v1/wiki/documents/doc-id/comments")
        assert response.status_code == 200

        response = await client.post(
            "/v1/wiki/documents/doc-id/comments",
            json={"parent_id": None, "body": "这个段落可优化"},
        )
        assert response.status_code == 201

        response = await client.patch(
            "/v1/wiki/comments/comment-id",
            json={"expected_version": 1, "resolved": True},
        )
        assert response.status_code == 200

        response = await client.put(
            "/v1/wiki/permissions",
            json={
                "resource_kind": "wiki-document",
                "resource_id": "doc-id",
                "user_id": "reader",
                "access": WikiAccess.READ,
            },
        )
        assert response.status_code == 200

        response = await client.get("/v1/wiki/search?q=交付")
        assert response.status_code == 200

    assert read_actor.calls != []
    assert mutation_actor.calls != []
    assert len(read_actor.calls) == 8
    assert len(mutation_actor.calls) == 7

    assert service.calls[0][0] == "list_spaces"
    assert service.calls[0][1] == read_actor.actor
    assert service.calls[1][0] == "create_space"
    assert service.calls[1][1] == mutation_actor.actor
    assert service.calls[2][0] == "list_documents"
    assert service.calls[2][1] == read_actor.actor
    assert service.calls[3][0] == "list_documents"
    assert service.calls[3][1] == read_actor.actor
    assert service.calls[4][0] == "create_document"
    assert service.calls[4][1] == mutation_actor.actor
    assert service.calls[5][0] == "get_document"
    assert service.calls[5][1] == read_actor.actor
    assert service.calls[6][0] == "patch_document"
    assert service.calls[6][1] == mutation_actor.actor
    assert service.calls[7][0] == "list_revisions"
    assert service.calls[7][1] == read_actor.actor
    assert service.calls[8][0] == "get_revision"
    assert service.calls[8][1] == read_actor.actor
    assert service.calls[9][0] == "restore_revision"
    assert service.calls[9][1] == mutation_actor.actor
    assert service.calls[10][0] == "list_comments"
    assert service.calls[10][1] == read_actor.actor
    assert service.calls[11][0] == "add_comment"
    assert service.calls[11][1] == mutation_actor.actor
    assert service.calls[12][0] == "patch_comment"
    assert service.calls[12][1] == mutation_actor.actor
    assert service.calls[13][0] == "put_permission"
    assert service.calls[13][1] == mutation_actor.actor
    assert service.calls[14][0] == "search"
    assert service.calls[14][1] == read_actor.actor

    assert service.calls[2][2] is None
    assert service.calls[3][2] == "space-id"
    assert isinstance(service.calls[1][2], SpaceCreate)
    assert isinstance(service.calls[4][2], DocumentCreate)


@pytest.mark.anyio
async def test_wiki_http_router_propagates_product_error_as_problem_json() -> None:
    service = DummyWikiService()
    service.fail_on_search = True
    read_actor = ActorResolver(KnowledgeActor(user_id="viewer", role=Role.VIEWER))
    mutation_actor = ActorResolver(KnowledgeActor(user_id="admin", role=Role.ADMINISTRATOR))
    app = _app(service, read_actor, mutation_actor)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/wiki/search?q=检索失败")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    payload = response.json()
    assert payload["code"] == "WIKI_SEARCH_FAILED"
    assert payload["trace_id"]
