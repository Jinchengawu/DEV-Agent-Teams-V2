"""Locked npm archive transport; it cannot publish Method Pack qualification."""

from __future__ import annotations

import ssl
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from ..modules.extensions.method_packs import MethodPackInstall
from ..shared.errors import ProductError

_OFFICIAL_URLS = frozenset(
    {
        "https://registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz",
        "https://registry.npmjs.org/bmad-method-test-architecture-enterprise/-/bmad-method-test-architecture-enterprise-1.23.4.tgz",
    }
)
_MAX_ARCHIVE_BYTES = 100_000_000
_MAX_BATCH_BYTES = 200_000_000


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise _registry_error(
            "METHOD_PACK_DOWNLOAD_REDIRECT", "Method Pack Registry 不允许重定向。"
        )


class LockedMethodPackArchiveFetcher:
    def __init__(self, *, _opener: Any | None = None) -> None:
        self._opener = _opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            _RejectRedirect(),
        )
        self._batch_bytes = 0
        self._started = time.monotonic()

    def fetch(self, request: MethodPackInstall, exact_locked_url: str) -> bytes:
        url = request.tarball_uri
        parsed = urlsplit(url)
        if (
            url != exact_locked_url
            or url not in _OFFICIAL_URLS
            or parsed.scheme != "https"
            or parsed.hostname != "registry.npmjs.org"
            or parsed.netloc != "registry.npmjs.org"
            or parsed.query
            or parsed.fragment
        ):
            raise _registry_error(
                "METHOD_PACK_DOWNLOAD_URL_INVALID", "Method Pack URL 不属于冻结的官方归档。"
            )
        request_url = urllib.request.Request(url, method="GET")
        started = time.monotonic()
        deadline = min(started + 60, self._started + 120)
        limit = min(request.max_unpacked_bytes, _MAX_ARCHIVE_BYTES)
        chunks: list[bytes] = []
        size = 0
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _registry_error("METHOD_PACK_DOWNLOAD_TIMEOUT", "Method Pack 下载超时。")
            with self._opener.open(request_url, timeout=remaining) as response:
                if response.geturl() != url or response.status != 200:
                    raise _registry_error(
                        "METHOD_PACK_DOWNLOAD_RESPONSE_INVALID", "Registry 最终 URL 或状态不符。"
                    )
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _registry_error(
                            "METHOD_PACK_DOWNLOAD_TIMEOUT", "Method Pack 下载超时。"
                        )
                    _set_response_timeout(response, remaining)
                    chunk = response.read(min(65_536, limit - size + 1))
                    if time.monotonic() > deadline:
                        raise _registry_error(
                            "METHOD_PACK_DOWNLOAD_TIMEOUT", "Method Pack 下载超时。"
                        )
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit or self._batch_bytes + size > _MAX_BATCH_BYTES:
                        raise _registry_error(
                            "METHOD_PACK_DOWNLOAD_SIZE_LIMIT", "Method Pack 下载超过字节上限。"
                        )
                    chunks.append(chunk)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise _registry_error(
                "METHOD_PACK_DOWNLOAD_FAILED", "Method Pack Registry 请求失败。"
            ) from error
        self._batch_bytes += size
        return b"".join(chunks)


def _set_response_timeout(response: Any, remaining: float) -> None:
    # HTTPResponse wraps an SSLSocket here. Tests provide the same operation
    # directly; unknown transports fail closed instead of silently exceeding
    # the total deadline during one blocking read.
    socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    setter = getattr(socket, "settimeout", None) or getattr(response, "set_read_timeout", None)
    if setter is None:
        raise _registry_error(
            "METHOD_PACK_DOWNLOAD_TIMEOUT_UNSUPPORTED", "Registry 连接无法约束读取时限。"
        )
    setter(remaining)


def _registry_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Method Pack 来源检查失败",
        detail=detail,
        repair="检查冻结的官方 URL、TLS 和网络边界；不得回退代理、镜像或缓存。",
        status_code=409,
    )
