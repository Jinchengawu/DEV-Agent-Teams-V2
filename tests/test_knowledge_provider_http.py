from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from agent_team_os.modules.knowledge import (
    KnowledgeActor,
    KnowledgeProviderKind,
    ProviderBinding,
    ProviderBindingCreate,
    ProviderNode,
    ProviderNodeKind,
    ProviderSyncResult,
    ProviderSyncRun,
    ProviderSyncStatus,
    create_provider_knowledge_router,
)
from agent_team_os.shared.permissions import Role


class StubProviderKnowledgeService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.binding = _binding()

    def create_binding(
        self, actor: KnowledgeActor, request: ProviderBindingCreate
    ) -> ProviderBinding:
        self.calls.append(("create", actor, request))
        return self.binding

    def list_bindings(self, actor: KnowledgeActor) -> tuple[ProviderBinding, ...]:
        self.calls.append(("list", actor))
        return (self.binding,)

    def list_nodes(
        self, actor: KnowledgeActor, binding_id: str
    ) -> tuple[ProviderNode, ...]:
        self.calls.append(("nodes", actor, binding_id))
        return (
            ProviderNode(
                external_id="node-1",
                external_space_id="space-1",
                source_id="docx:document-1",
                title="交付手册",
                kind=ProviderNodeKind.DOCUMENT,
                provider_revision="revision-1",
            ),
        )

    def sync(
        self, actor: KnowledgeActor, binding_id: str, source_id: str
    ) -> ProviderSyncResult:
        self.calls.append(("sync", actor, binding_id, source_id))
        now = datetime(2026, 8, 23, tzinfo=UTC)
        return ProviderSyncResult(
            run=ProviderSyncRun(
                id="sync-1",
                binding_id=binding_id,
                source_id=source_id,
                status=ProviderSyncStatus.FAILED,
                error_code="FEISHU_USER_AUTHORIZATION_MISSING",
                started_at=now,
                completed_at=now,
            )
        )


def _binding() -> ProviderBinding:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    return ProviderBinding(
        id="binding-1",
        provider_kind=KnowledgeProviderKind.FEISHU,
        display_name="飞书交付知识",
        external_space_id="space-1",
        credential_ref="env:FEISHU_APP_SECRET",
        enabled=True,
        version=1,
        created_by="admin-1",
        created_at=now,
        updated_at=now,
    )


def _actor(_request: Request) -> KnowledgeActor:
    return KnowledgeActor(user_id="admin-1", role=Role.ADMINISTRATOR)


def _app(service: StubProviderKnowledgeService) -> FastAPI:
    app = FastAPI()
    app.include_router(create_provider_knowledge_router(service, _actor, _actor))
    return app


@pytest.mark.anyio
async def test_provider_binding_http_contract_uses_actor_and_redacts_no_secrets() -> None:
    service = StubProviderKnowledgeService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/knowledge/provider-bindings",
            json={
                "provider_kind": "feishu",
                "display_name": "飞书交付知识",
                "external_space_id": "space-1",
                "credential_ref": "env:FEISHU_APP_SECRET",
            },
        )
        listed = await client.get("/v1/knowledge/provider-bindings")
        nodes = await client.get(
            "/v1/knowledge/provider-bindings/binding-1/nodes"
        )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert nodes.status_code == 200
    assert listed.json()[0]["credential_ref"] == "env:FEISHU_APP_SECRET"
    assert service.calls[0][0] == "create"
    assert nodes.json()[0]["source_id"] == "docx:document-1"
    assert service.calls[1][0] == "list"
    assert service.calls[2][0] == "nodes"


@pytest.mark.anyio
async def test_provider_sync_http_returns_audited_failure_instead_of_success() -> None:
    service = StubProviderKnowledgeService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/knowledge/provider-bindings/binding-1/sync",
            json={"source_id": "docx:document-1"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["status"] == "failed"
    assert payload["run"]["error_code"] == "FEISHU_USER_AUTHORIZATION_MISSING"
    assert payload["snapshot"] is None
    assert service.calls == [
        (
            "sync",
            KnowledgeActor(user_id="admin-1", role=Role.ADMINISTRATOR),
            "binding-1",
            "docx:document-1",
        )
    ]
