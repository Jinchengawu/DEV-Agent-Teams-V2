from __future__ import annotations

import urllib.request

import pytest

import agent_team_os.infrastructure.method_pack_registry as registry
from agent_team_os.infrastructure.method_pack_registry import (
    LockedMethodPackArchiveFetcher,
    _RejectRedirect,
)
from agent_team_os.modules.extensions import MethodEntry, MethodPackInstall
from agent_team_os.shared.errors import ProductError

URL = "https://registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz"


def _request(url: str = URL, *, limit: int = 100) -> MethodPackInstall:
    return MethodPackInstall(
        package_name="bmad-method",
        package_version="6.11.0",
        tarball_uri=url,
        registry_integrity="sha512-" + "A" * 88,
        archive_sha256="a" * 64,
        method_entries=(MethodEntry(method_id="bmad", source_path="src/bmad"),),
        max_unpacked_bytes=limit,
    )


class _Response:
    def __init__(self, content: bytes, *, url: str = URL, status: int = 200) -> None:
        self.content = content
        self.url = url
        self.status = status
        self.offset = 0
        self.read_timeouts = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self) -> str:
        return self.url

    def read(self, size: int) -> bytes:
        result = self.content[self.offset : self.offset + size]
        self.offset += len(result)
        return result

    def set_read_timeout(self, seconds: float) -> None:
        self.read_timeouts.append(seconds)


class _Opener:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request.full_url, timeout))
        return self.response


def test_official_exact_url_returns_bounded_bytes() -> None:
    opener = _Opener(_Response(b"archive"))
    fetcher = LockedMethodPackArchiveFetcher(_opener=opener)
    assert fetcher.fetch(_request(), URL) == b"archive"
    assert opener.calls[0][0] == URL
    assert 0 < opener.calls[0][1] <= 60
    assert opener.response.read_timeouts and all(
        0 < item <= 60 for item in opener.response.read_timeouts
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/bmad-method-6.11.0.tgz",
        URL + "?token=x",
        "https://registry.npmjs.org:443/bmad-method/-/bmad-method-6.11.0.tgz",
        "https://user@registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz",
    ],
)
def test_non_exact_url_never_reaches_transport(url: str) -> None:
    opener = _Opener(_Response(b"archive"))
    with pytest.raises(ProductError):
        LockedMethodPackArchiveFetcher(_opener=opener).fetch(_request(url), url)
    assert not opener.calls


@pytest.mark.parametrize(
    "response",
    [_Response(b"ok", status=302), _Response(b"ok", url="https://evil.example/")],
)
def test_redirect_or_final_url_drift_is_rejected(response: _Response) -> None:
    with pytest.raises(ProductError):
        LockedMethodPackArchiveFetcher(_opener=_Opener(response)).fetch(_request(), URL)


def test_redirect_handler_rejects_before_following() -> None:
    with pytest.raises(ProductError):
        _RejectRedirect().redirect_request(None, None, 302, "move", {}, "https://evil.example/")


def test_transport_has_no_proxy_and_validates_tls(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    opener = LockedMethodPackArchiveFetcher()._opener
    proxies = [
        handler for handler in opener.handlers if isinstance(handler, urllib.request.ProxyHandler)
    ]
    assert not proxies or (len(proxies) == 1 and proxies[0].proxies == {})
    https = [
        handler for handler in opener.handlers if isinstance(handler, urllib.request.HTTPSHandler)
    ]
    assert len(https) == 1 and https[0]._context.check_hostname


def test_download_limit_is_not_looser_than_request_limit() -> None:
    opener = _Opener(_Response(b"too large"))
    with pytest.raises(ProductError):
        LockedMethodPackArchiveFetcher(_opener=opener).fetch(_request(limit=2), URL)


def test_slow_read_that_crosses_deadline_is_not_accepted(monkeypatch) -> None:
    clock = iter([0.0, 0.0, 0.0, 0.0, 0.0, 61.0])
    monkeypatch.setattr(registry.time, "monotonic", lambda: next(clock))
    with pytest.raises(ProductError):
        LockedMethodPackArchiveFetcher(_opener=_Opener(_Response(b"late"))).fetch(_request(), URL)
