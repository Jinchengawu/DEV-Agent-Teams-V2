from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic import JsonValue

from ...modules.knowledge.provider_domain import (
    ProviderNode,
    ProviderNodeKind,
    ProviderSnapshot,
    ProviderSpace,
)
from ...modules.knowledge.provider_ports import ProviderFailure
from ...modules.knowledge.tenant_domain import TenantConnection
from ...shared.clock import Clock, SystemClock
from ...shared.hashes import sha256_json


class SecretReferenceResolver(Protocol):
    def resolve(self, reference: str) -> str: ...


class EnvironmentSecretResolver:
    def resolve(self, reference: str) -> str:
        prefix = "env://" if reference.startswith("env://") else "env:"
        if not reference.startswith(prefix):
            raise ProviderFailure(
                "KNOWLEDGE_CREDENTIAL_REFERENCE_UNSUPPORTED",
                "Credential reference is not an environment reference",
                unavailable=True,
            )
        name = reference.removeprefix(prefix)
        value = os.environ.get(name)
        if not value:
            raise ProviderFailure(
                "KNOWLEDGE_CREDENTIAL_REFERENCE_UNRESOLVED",
                "Credential reference cannot be resolved",
                unavailable=True,
            )
        return value


class SystemSecretReferenceResolver:
    """Resolve explicit env/keychain references without persisting their values."""

    def __init__(self) -> None:
        self.environment = EnvironmentSecretResolver()

    def resolve(self, reference: str) -> str:
        if reference.startswith("env:"):
            return self.environment.resolve(reference)
        prefix = "keychain://" if reference.startswith("keychain://") else "keychain:"
        if not reference.startswith(prefix):
            raise ProviderFailure(
                "KNOWLEDGE_CREDENTIAL_REFERENCE_UNSUPPORTED",
                "Credential reference scheme is unsupported",
                unavailable=True,
            )
        service = reference.removeprefix(prefix)
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-w", "-s", service],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ProviderFailure(
                "KNOWLEDGE_CREDENTIAL_REFERENCE_UNRESOLVED",
                "Credential reference cannot be resolved",
                unavailable=True,
            ) from error
        value = result.stdout.rstrip("\n")
        if not value:
            raise ProviderFailure(
                "KNOWLEDGE_CREDENTIAL_REFERENCE_UNRESOLVED",
                "Credential reference cannot be resolved",
                unavailable=True,
            )
        return value


class FeishuTenantKnowledgeProvider:
    def __init__(
        self,
        connection: TenantConnection,
        secrets: SecretReferenceResolver,
        *,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.connection = connection
        self.secrets = secrets
        self.client = client or httpx.Client(
            base_url="https://open.feishu.cn",
            timeout=httpx.Timeout(20),
        )
        self.clock = clock or SystemClock()
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None

    def list_spaces(self) -> tuple[ProviderSpace, ...]:
        items = self._list_pages("/open-apis/wiki/v2/spaces")
        return tuple(
            ProviderSpace(
                external_id=_required_text(item, "space_id"),
                title=_required_text(item, "name"),
            )
            for item in items
        )

    def list_nodes(self, external_space_id: str) -> tuple[ProviderNode, ...]:
        path = f"/open-apis/wiki/v2/spaces/{quote(external_space_id, safe='')}/nodes"
        items = self._list_pages(path)
        nodes: list[ProviderNode] = []
        for item in items:
            object_type = _required_text(item, "obj_type")
            object_token = _required_text(item, "obj_token")
            nodes.append(
                ProviderNode(
                    external_id=_required_text(item, "node_token"),
                    external_space_id=str(item.get("space_id") or external_space_id),
                    parent_external_id=_optional_text(item.get("parent_node_token")),
                    source_id=(
                        None if object_type == "folder" else f"{object_type}:{object_token}"
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
                    updated_at=_optional_timestamp(item.get("obj_edit_time")),
                )
            )
        return tuple(nodes)

    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
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
            "/open-apis/wiki/v2/spaces/get_node",
            params={"token": object_token, "obj_type": object_type},
            source_scope=True,
        )
        node = _required_mapping(metadata, "node")
        response = self._request_json(
            f"/open-apis/docx/v1/documents/{quote(object_token, safe='')}/raw_content",
            source_scope=True,
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

    def _list_pages(self, path: str) -> tuple[Mapping[str, object], ...]:
        page_token: str | None = None
        items: list[Mapping[str, object]] = []
        for _page in range(100):
            params: dict[str, str | int | float | bool | None] = {"page_size": 50}
            if page_token is not None:
                params["page_token"] = page_token
            data = self._request_json(path, params=params)
            raw_items = data.get("items", [])
            if not isinstance(raw_items, list):
                raise ProviderFailure("FEISHU_RESPONSE_INVALID", "Feishu items field is not a list")
            for item in raw_items:
                if not isinstance(item, Mapping):
                    raise ProviderFailure("FEISHU_RESPONSE_INVALID", "Feishu item is not an object")
                items.append(item)
            if not data.get("has_more"):
                return tuple(items)
            page_token = _required_text(data, "page_token")
        raise ProviderFailure(
            "FEISHU_PAGINATION_LIMIT", "Feishu pagination exceeded the safety limit"
        )

    def _request_json(
        self,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        source_scope: bool = False,
    ) -> Mapping[str, object]:
        response = self._get(path, params=params)
        if response.status_code == 401:
            self._access_token = None
            self._access_token_expires_at = None
            response = self._get(path, params=params)
        return _decode_api_response(response, source_scope=source_scope)

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None,
    ) -> httpx.Response:
        token = self._resolve_tenant_access_token()
        try:
            return self.client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TimeoutException as error:
            raise ProviderFailure(
                "FEISHU_TIMEOUT", "Feishu request timed out", unavailable=True
            ) from error
        except httpx.HTTPError as error:
            raise ProviderFailure(
                "FEISHU_UNAVAILABLE", "Feishu request failed", unavailable=True
            ) from error

    def _resolve_tenant_access_token(self) -> str:
        now = self.clock.now()
        if (
            self._access_token is not None
            and self._access_token_expires_at is not None
            and self._access_token_expires_at > now
        ):
            return self._access_token
        app_id = self.secrets.resolve(self.connection.app_id_ref)
        app_secret = self.secrets.resolve(self.connection.app_secret_ref)
        try:
            response = self.client.post(
                "/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
        except httpx.TimeoutException as error:
            raise ProviderFailure(
                "FEISHU_TIMEOUT", "Feishu token request timed out", unavailable=True
            ) from error
        except httpx.HTTPError as error:
            raise ProviderFailure(
                "FEISHU_UNAVAILABLE", "Feishu token request failed", unavailable=True
            ) from error
        payload = _decode_payload(response)
        code = payload.get("code")
        if code != 0:
            raise ProviderFailure(
                "FEISHU_TENANT_AUTH_FAILED",
                "Feishu rejected the tenant app credentials",
                unavailable=True,
            )
        token = payload.get("tenant_access_token")
        expires = payload.get("expire")
        if not isinstance(token, str) or not token or not isinstance(expires, int):
            raise ProviderFailure("FEISHU_RESPONSE_INVALID", "Feishu token response is invalid")
        self._access_token = token
        self._access_token_expires_at = now + timedelta(seconds=max(expires - 60, 1))
        return token


class FeishuTenantKnowledgeProviderResolver:
    def __init__(
        self,
        secrets: SecretReferenceResolver | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.secrets = secrets or SystemSecretReferenceResolver()
        self.client = client

    def resolve(self, connection: TenantConnection) -> FeishuTenantKnowledgeProvider:
        return FeishuTenantKnowledgeProvider(
            connection,
            self.secrets,
            client=self.client,
        )


def _decode_api_response(
    response: httpx.Response, *, source_scope: bool = False
) -> Mapping[str, object]:
    payload = _decode_payload(response, source_scope=source_scope)
    code = payload.get("code")
    if code != 0:
        if response.status_code == 401:
            raise ProviderFailure(
                "FEISHU_PERMISSION_REVOKED",
                "Feishu rejected the tenant app authorization",
                unavailable=True,
            )
        if response.status_code == 403:
            raise ProviderFailure(
                (
                    "FEISHU_SOURCE_PERMISSION_REVOKED"
                    if source_scope
                    else "FEISHU_PERMISSION_REVOKED"
                ),
                "Feishu rejected the requested authorization scope",
                unavailable=True,
            )
        if response.status_code == 429:
            raise ProviderFailure(
                "FEISHU_RATE_LIMITED",
                "Feishu rate limit exceeded",
                retry_after_seconds=_retry_after_seconds(response),
            )
        if response.status_code >= 500:
            raise ProviderFailure(
                "FEISHU_UNAVAILABLE", "Feishu service is unavailable", unavailable=True
            )
        raise ProviderFailure("FEISHU_API_ERROR", "Feishu API request failed")
    return _required_mapping(payload, "data")


def _decode_payload(
    response: httpx.Response, *, source_scope: bool = False
) -> Mapping[str, object]:
    if response.status_code == 401:
        raise ProviderFailure(
            "FEISHU_PERMISSION_REVOKED",
            "Feishu rejected the tenant app authorization",
            unavailable=True,
        )
    if response.status_code == 403:
        raise ProviderFailure(
            ("FEISHU_SOURCE_PERMISSION_REVOKED" if source_scope else "FEISHU_PERMISSION_REVOKED"),
            "Feishu rejected the requested authorization scope",
            unavailable=True,
        )
    if response.status_code == 404 and source_scope:
        raise ProviderFailure(
            "FEISHU_SOURCE_NOT_FOUND",
            "Feishu source no longer exists",
        )
    if response.status_code == 429:
        raise ProviderFailure(
            "FEISHU_RATE_LIMITED",
            "Feishu rate limit exceeded",
            retry_after_seconds=_retry_after_seconds(response),
        )
    if response.status_code >= 500:
        raise ProviderFailure(
            "FEISHU_UNAVAILABLE", "Feishu service is unavailable", unavailable=True
        )
    if response.status_code >= 400:
        raise ProviderFailure("FEISHU_REQUEST_REJECTED", "Feishu rejected the request")
    try:
        payload = response.json()
    except ValueError as error:
        raise ProviderFailure("FEISHU_RESPONSE_INVALID", "Feishu returned invalid JSON") from error
    if not isinstance(payload, Mapping):
        raise ProviderFailure("FEISHU_RESPONSE_INVALID", "Feishu response is not an object")
    return payload


def _required_mapping(value: Mapping[str, object], field: str) -> Mapping[str, object]:
    result = value.get(field)
    if not isinstance(result, Mapping):
        raise ProviderFailure("FEISHU_RESPONSE_INVALID", f"Feishu field {field} is not an object")
    return result


def _required_text(value: Mapping[str, object], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ProviderFailure("FEISHU_RESPONSE_INVALID", f"Feishu field {field} is missing")
    return result


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_timestamp(value: object) -> datetime | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
