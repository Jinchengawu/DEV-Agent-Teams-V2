from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic import JsonValue

from ...modules.knowledge.provider_domain import (
    ProviderActor,
    ProviderBinding,
    ProviderNode,
    ProviderNodeKind,
    ProviderSnapshot,
    ProviderSpace,
)
from ...modules.knowledge.provider_ports import KnowledgeProvider, ProviderFailure
from ...shared.clock import Clock, SystemClock
from ...shared.hashes import sha256_json


class FeishuAccessTokenResolver(Protocol):
    def resolve_user_access_token(
        self, binding: ProviderBinding, actor: ProviderActor
    ) -> str: ...


class FeishuKnowledgeProviderResolver:
    def __init__(
        self,
        token_resolver: FeishuAccessTokenResolver,
        *,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.token_resolver = token_resolver
        self.client = client or httpx.Client(
            base_url="https://open.feishu.cn",
            timeout=httpx.Timeout(20),
        )
        self.clock = clock

    def resolve(self, binding: ProviderBinding) -> KnowledgeProvider:
        return FeishuKnowledgeProvider(
            binding,
            self.token_resolver,
            client=self.client,
            clock=self.clock,
        )

    def close(self) -> None:
        self.client.close()


class FeishuKnowledgeProvider:
    def __init__(
        self,
        binding: ProviderBinding,
        token_resolver: FeishuAccessTokenResolver,
        *,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.binding = binding
        self.token_resolver = token_resolver
        self.client = client or httpx.Client(
            base_url="https://open.feishu.cn",
            timeout=httpx.Timeout(20),
        )
        self.clock = clock or SystemClock()

    def list_spaces(self, actor: ProviderActor) -> tuple[ProviderSpace, ...]:
        items = self._list_pages(actor, "/open-apis/wiki/v2/spaces")
        spaces: list[ProviderSpace] = []
        for item in items:
            external_id = _required_text(item, "space_id")
            spaces.append(
                ProviderSpace(
                    external_id=external_id,
                    title=_required_text(item, "name"),
                )
            )
        return tuple(spaces)

    def list_nodes(
        self, actor: ProviderActor, external_space_id: str
    ) -> tuple[ProviderNode, ...]:
        path = f"/open-apis/wiki/v2/spaces/{quote(external_space_id, safe='')}/nodes"
        items = self._list_pages(actor, path)
        nodes: list[ProviderNode] = []
        for item in items:
            object_type = _required_text(item, "obj_type")
            object_token = _required_text(item, "obj_token")
            node_token = _required_text(item, "node_token")
            edited_at = _optional_timestamp(item.get("obj_edit_time"))
            nodes.append(
                ProviderNode(
                    external_id=node_token,
                    external_space_id=str(item.get("space_id") or external_space_id),
                    parent_external_id=_optional_text(item.get("parent_node_token")),
                    source_id=(
                        None
                        if object_type == "folder"
                        else f"{object_type}:{object_token}"
                    ),
                    title=_required_text(item, "title"),
                    kind=(
                        ProviderNodeKind.FOLDER
                        if object_type == "folder"
                        else ProviderNodeKind.DOCUMENT
                    ),
                    provider_revision=(
                        str(item["obj_edit_time"])
                        if item.get("obj_edit_time") is not None
                        else None
                    ),
                    updated_at=edited_at,
                )
            )
        return tuple(nodes)

    def fetch_snapshot(self, actor: ProviderActor, source_id: str) -> ProviderSnapshot:
        object_type, separator, object_token = source_id.partition(":")
        if separator != ":" or not object_token:
            raise ProviderFailure(
                "FEISHU_SOURCE_ID_INVALID",
                "Feishu source ID must contain an object type and token",
            )
        if object_type != "docx":
            raise ProviderFailure(
                "FEISHU_SOURCE_TYPE_UNSUPPORTED",
                f"Feishu object type {object_type} is not supported by this release",
            )
        metadata = self._request_json(
            actor,
            "/open-apis/wiki/v2/spaces/get_node",
            params={"token": object_token, "obj_type": object_type},
        )
        node = _required_mapping(metadata, "node")
        response = self._request_json(
            actor,
            f"/open-apis/docx/v1/documents/{quote(object_token, safe='')}/raw_content",
        )
        content = _required_text(response, "content")
        normalized: JsonValue = {"type": "feishu-docx-raw", "text": content}
        revision = str(
            node.get("obj_edit_time")
            or node.get("node_create_time")
            or _required_text(node, "node_token")
        )
        return ProviderSnapshot(
            source_id=source_id,
            provider_revision=revision,
            content_type="text/plain; charset=utf-8",
            normalized_content=normalized,
            normalized_text=content,
            content_sha256=sha256_json(normalized),
            source_url=_optional_text(node.get("url")),
            fetched_at=self.clock.now(),
        )

    def _list_pages(
        self, actor: ProviderActor, path: str
    ) -> tuple[Mapping[str, object], ...]:
        page_token: str | None = None
        items: list[Mapping[str, object]] = []
        for _page in range(100):
            params: dict[str, str | int | float | bool | None] = {"page_size": 50}
            if page_token is not None:
                params["page_token"] = page_token
            data = self._request_json(actor, path, params=params)
            raw_items = data.get("items", [])
            if not isinstance(raw_items, list):
                raise ProviderFailure(
                    "FEISHU_RESPONSE_INVALID", "Feishu items field is not a list"
                )
            for item in raw_items:
                if not isinstance(item, Mapping):
                    raise ProviderFailure(
                        "FEISHU_RESPONSE_INVALID", "Feishu item is not an object"
                    )
                items.append(item)
            if not data.get("has_more"):
                return tuple(items)
            page_token = _required_text(data, "page_token")
        raise ProviderFailure(
            "FEISHU_PAGINATION_LIMIT", "Feishu pagination exceeded the safety limit"
        )

    def _request_json(
        self,
        actor: ProviderActor,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
    ) -> Mapping[str, object]:
        access_token = self.token_resolver.resolve_user_access_token(self.binding, actor)
        if not access_token:
            raise ProviderFailure(
                "FEISHU_USER_AUTHORIZATION_MISSING",
                "Feishu user authorization is missing",
                unavailable=True,
            )
        try:
            response = self.client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.TimeoutException as error:
            raise ProviderFailure(
                "FEISHU_TIMEOUT", "Feishu request timed out", unavailable=True
            ) from error
        except httpx.HTTPError as error:
            raise ProviderFailure(
                "FEISHU_UNAVAILABLE", "Feishu request failed", unavailable=True
            ) from error
        if response.status_code in {401, 403}:
            raise ProviderFailure(
                "FEISHU_PERMISSION_REVOKED",
                "Feishu rejected the user authorization",
                unavailable=True,
            )
        if response.status_code == 429:
            raise ProviderFailure("FEISHU_RATE_LIMITED", "Feishu rate limit exceeded")
        if response.status_code >= 500:
            raise ProviderFailure(
                "FEISHU_UNAVAILABLE", "Feishu service is unavailable", unavailable=True
            )
        if response.status_code >= 400:
            raise ProviderFailure("FEISHU_REQUEST_REJECTED", "Feishu rejected the request")
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderFailure(
                "FEISHU_RESPONSE_INVALID", "Feishu returned invalid JSON"
            ) from error
        if not isinstance(payload, Mapping):
            raise ProviderFailure("FEISHU_RESPONSE_INVALID", "Feishu response is not an object")
        code = payload.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise ProviderFailure(
                "FEISHU_RESPONSE_INVALID", "Feishu response code is not an integer"
            )
        if code != 0:
            raise ProviderFailure(
                "FEISHU_API_ERROR",
                f"Feishu API returned code {code}",
            )
        return _required_mapping(payload, "data")


def _required_mapping(
    value: Mapping[str, object], field: str
) -> Mapping[str, object]:
    result = value.get(field)
    if not isinstance(result, Mapping):
        raise ProviderFailure(
            "FEISHU_RESPONSE_INVALID", f"Feishu field {field} is not an object"
        )
    return result


def _required_text(value: Mapping[str, object], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ProviderFailure(
            "FEISHU_RESPONSE_INVALID", f"Feishu field {field} is missing"
        )
    return result


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_timestamp(value: object) -> datetime | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC)
