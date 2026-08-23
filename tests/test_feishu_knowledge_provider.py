from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from agent_team_os.infrastructure.feishu import FeishuKnowledgeProvider
from agent_team_os.modules.knowledge import (
    KnowledgeProviderKind,
    ProviderActor,
    ProviderBinding,
    ProviderFailure,
    ProviderNodeKind,
)
from agent_team_os.shared.hashes import sha256_json


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 23, 3, 0, tzinfo=UTC)


class TokenResolver:
    def __init__(self, token: str = "user-access-token") -> None:
        self.token = token
        self.calls: list[tuple[str, str]] = []

    def resolve_user_access_token(
        self, binding: ProviderBinding, actor: ProviderActor
    ) -> str:
        self.calls.append((binding.id, actor.provider_user_id))
        return self.token


def _binding() -> ProviderBinding:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    return ProviderBinding(
        id="binding-feishu",
        provider_kind=KnowledgeProviderKind.FEISHU,
        display_name="飞书知识",
        external_space_id="space-1",
        credential_ref="env:FEISHU_APP_SECRET",
        enabled=True,
        version=1,
        created_by="admin-1",
        created_at=now,
        updated_at=now,
    )


def _actor() -> ProviderActor:
    return ProviderActor(
        product_user_id="editor-1",
        provider_user_id="feishu-editor-1",
    )


def test_feishu_adapter_uses_user_token_and_normalizes_spaces_nodes_snapshot() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer user-access-token"
        path = request.url.path
        if path == "/open-apis/wiki/v2/spaces":
            if request.url.params.get("page_token") == "next-space":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "items": [{"space_id": "space-2", "name": "产品知识"}],
                            "has_more": False,
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"space_id": "space-1", "name": "交付知识"}],
                        "has_more": True,
                        "page_token": "next-space",
                    },
                },
            )
        if path == "/open-apis/wiki/v2/spaces/space-1/nodes":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "space_id": "space-1",
                                "obj_type": "docx",
                                "obj_token": "doc-token",
                                "node_token": "node-token",
                                "parent_node_token": "root-node",
                                "title": "发布手册",
                                "obj_edit_time": 1787418000,
                                "url": "https://example.invalid/wiki/doc-token",
                            }
                        ],
                        "has_more": False,
                    },
                },
            )
        if path == "/open-apis/wiki/v2/spaces/get_node":
            assert request.url.params["token"] == "doc-token"
            assert request.url.params["obj_type"] == "docx"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "node": {
                            "node_token": "node-token",
                            "obj_edit_time": 1787418000,
                            "url": "https://example.invalid/wiki/doc-token",
                        }
                    },
                },
            )
        if path == "/open-apis/docx/v1/documents/doc-token/raw_content":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"content": "# 发布手册\n真实内容"}},
            )
        return httpx.Response(404, json={"code": 404, "msg": "not found"})

    token_resolver = TokenResolver()
    client = httpx.Client(
        base_url="https://open.feishu.cn",
        transport=httpx.MockTransport(handler),
    )
    provider = FeishuKnowledgeProvider(
        _binding(), token_resolver, client=client, clock=FixedClock()
    )

    assert [space.external_id for space in provider.list_spaces(_actor())] == [
        "space-1",
        "space-2",
    ]
    nodes = provider.list_nodes(_actor(), "space-1")
    assert nodes[0].external_id == "node-token"
    assert nodes[0].parent_external_id == "root-node"
    assert nodes[0].source_id == "docx:doc-token"
    assert nodes[0].kind == ProviderNodeKind.DOCUMENT
    assert nodes[0].provider_revision == "1787418000"
    assert nodes[0].source_id is not None
    snapshot = provider.fetch_snapshot(_actor(), nodes[0].source_id)
    assert snapshot.provider_revision == "1787418000"
    assert snapshot.normalized_text == "# 发布手册\n真实内容"
    assert snapshot.content_sha256 == sha256_json(snapshot.normalized_content)
    assert snapshot.fetched_at == FixedClock().now()
    assert len(token_resolver.calls) == len(requests)


@pytest.mark.parametrize(
    ("response", "code", "unavailable"),
    [
        (httpx.Response(403, json={"code": 99991663}), "FEISHU_PERMISSION_REVOKED", True),
        (httpx.Response(429, json={"code": 99991400}), "FEISHU_RATE_LIMITED", False),
        (httpx.Response(503, json={"code": 1}), "FEISHU_UNAVAILABLE", True),
        (httpx.Response(200, text="not-json"), "FEISHU_RESPONSE_INVALID", False),
    ],
)
def test_feishu_adapter_maps_failures_without_leaking_token(
    response: httpx.Response, code: str, unavailable: bool
) -> None:
    client = httpx.Client(
        base_url="https://open.feishu.cn",
        transport=httpx.MockTransport(lambda _request: response),
    )
    provider = FeishuKnowledgeProvider(_binding(), TokenResolver(), client=client)

    with pytest.raises(ProviderFailure) as failure:
        provider.list_spaces(_actor())

    assert failure.value.code == code
    assert failure.value.unavailable is unavailable
    assert "user-access-token" not in failure.value.detail


def test_feishu_adapter_rejects_unsupported_object_before_network() -> None:
    client = httpx.Client(
        base_url="https://open.feishu.cn",
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("unsupported source must not call Feishu")
        ),
    )
    provider = FeishuKnowledgeProvider(_binding(), TokenResolver(), client=client)

    with pytest.raises(ProviderFailure) as failure:
        provider.fetch_snapshot(_actor(), "sheet:sheet-token")

    assert failure.value.code == "FEISHU_SOURCE_TYPE_UNSUPPORTED"
