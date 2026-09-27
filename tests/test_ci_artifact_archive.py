"""CI Artifact 的原始 ZIP 与独立 Bundle 复核合同。"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from audit_ci_bundle_artifact import audit_bundle  # noqa: E402
from test_ci_bundle_artifact import _bundle  # noqa: E402
from verify_ci_artifact_archive import (  # noqa: E402
    ArtifactRejected,
    fetch_and_verify_artifact,
    main,
    prepare_bundle,
    validate_trusted_push_event,
    verify_artifact_archive,
)

REVISION = "a" * 40
RUN_ID = 12345
ARTIFACT_ID = 67890
ARTIFACT_NAME = f"agent-team-os-b0-{REVISION}"
TRUSTED_REF = "refs/heads/codex/method-pack-s1-candidate-20260926"
TRUSTED_REPOSITORY = "Jinchengawu/DEV-Agent-Teams-V2"
TRUSTED_BEFORE = "f26ae975965773a5572a753b801c2e90e2e1f820"
TRUSTED_MESSAGE = "ci(b0): one-shot artifact 2026-09-28 8d4e71a2"


def _trusted_push(tmp_path: Path) -> tuple[Path, dict[str, str], dict[str, object]]:
    path = tmp_path / "push-event.json"
    event: dict[str, object] = {
        "ref": TRUSTED_REF,
        "before": TRUSTED_BEFORE,
        "after": REVISION,
        "created": False,
        "forced": False,
        "repository": {"full_name": TRUSTED_REPOSITORY},
        "head_commit": {"id": REVISION, "message": TRUSTED_MESSAGE},
    }
    path.write_text(json.dumps(event), encoding="utf-8")
    runner = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": TRUSTED_REF,
        "GITHUB_REPOSITORY": TRUSTED_REPOSITORY,
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": REVISION,
    }
    return path, runner, event


def test_prepare_event_guard_accepts_exact_candidate_push(tmp_path: Path) -> None:
    path, runner, _event = _trusted_push(tmp_path)
    validate_trusted_push_event(path, runner, REVISION)


@pytest.mark.parametrize(
    ("location", "key", "value"),
    (
        ("event", "ref", "refs/heads/CODEX/method-pack-s1-candidate-20260926"),
        ("event", "ref", "refs/heads/main"),
        ("event", "before", "0" * 40),
        ("event", "after", "b" * 40),
        ("event", "created", True),
        ("event", "forced", True),
        ("event", "head_commit", None),
        ("head_commit", "id", "b" * 40),
        ("head_commit", "message", TRUSTED_MESSAGE.upper()),
        ("repository", "full_name", TRUSTED_REPOSITORY.lower()),
        ("runner", "GITHUB_EVENT_NAME", "pull_request"),
        ("runner", "GITHUB_REF", "refs/heads/main"),
        ("runner", "GITHUB_REPOSITORY", TRUSTED_REPOSITORY.lower()),
        ("runner", "GITHUB_RUN_ATTEMPT", "2"),
        ("runner", "GITHUB_SHA", "b" * 40),
    ),
)
def test_prepare_event_guard_rejects_other_contexts(
    tmp_path: Path, location: str, key: str, value: object
) -> None:
    path, runner, event = _trusted_push(tmp_path)
    if location == "runner":
        runner[key] = str(value)
    elif location == "event":
        event[key] = value
    else:
        nested = event[location]
        assert isinstance(nested, dict)
        nested[key] = value
    path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ArtifactRejected, match="CI_ARTIFACT_EVENT_UNTRUSTED"):
        validate_trusted_push_event(path, runner, REVISION)


def test_prepare_cli_rejects_event_without_leaking_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path, runner, event = _trusted_push(tmp_path)
    sentinel = "SENTINEL_EVENT_CONTENT_MUST_NOT_LOG"
    head_commit = event["head_commit"]
    assert isinstance(head_commit, dict)
    head_commit["message"] = sentinel
    path.write_text(json.dumps(event), encoding="utf-8")
    for key, value in runner.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    github_output = tmp_path / "github-output"
    monkeypatch.setattr(sys, "argv", [
        "verify_ci_artifact_archive.py", "prepare",
        "--bundle-parent", str(tmp_path / "missing-bundle"),
        "--receipt", str(tmp_path / "missing-receipt"),
        "--expected-revision", REVISION,
        "--github-output", str(github_output),
    ])
    assert main() == 1
    output = capsys.readouterr()
    assert output.out.strip() == "ci_artifact=rejected; code=CI_ARTIFACT_EVENT_UNTRUSTED"
    assert output.err == ""
    assert sentinel not in output.out
    assert not github_output.exists()


def _archive_bundle(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    bundle = _bundle(tmp_path)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(bundle).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps({
            "id": ARTIFACT_ID,
            "name": ARTIFACT_NAME,
            "digest": f"sha256:{digest}",
            "size_in_bytes": archive.stat().st_size,
            "expired": False,
            "workflow_run": {"id": RUN_ID, "head_sha": REVISION},
        }),
        encoding="utf-8",
    )
    return archive, metadata, digest, bundle


def _verify(tmp_path: Path, archive: Path, metadata: Path, digest: str, bundle: Path) -> None:
    manifest_sha = hashlib.sha256((bundle / "delivery-manifest.json").read_bytes()).hexdigest()
    verified = verify_artifact_archive(
        archive=archive,
        metadata=metadata,
        output_root=tmp_path / "downloaded",
        artifact_id=ARTIFACT_ID,
        artifact_name=ARTIFACT_NAME,
        upload_digest=digest,
        expected_manifest_sha256=manifest_sha,
        expected_revision=REVISION,
        run_id=RUN_ID,
    )
    assert verified["manifest_sha256"] == manifest_sha
    assert verified["revision"] == REVISION


def test_original_zip_digest_and_downloaded_manifest_pass(tmp_path: Path) -> None:
    archive, metadata, digest, bundle = _archive_bundle(tmp_path)
    _verify(tmp_path, archive, metadata, digest, bundle)


@pytest.mark.parametrize(
    "field", ("upload_digest", "rest_digest", "run_id", "head_sha", "artifact_name")
)
def test_archive_identity_mismatch_is_rejected(tmp_path: Path, field: str) -> None:
    archive, metadata, digest, bundle = _archive_bundle(tmp_path)
    if field == "upload_digest":
        digest = "0" * 64
    else:
        record = json.loads(metadata.read_text(encoding="utf-8"))
        if field == "rest_digest":
            record["digest"] = "sha256:" + "0" * 64
        elif field == "run_id":
            record["workflow_run"]["id"] += 1
        elif field == "head_sha":
            record["workflow_run"]["head_sha"] = "b" * 40
        else:
            record["name"] = "different"
        metadata.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ArtifactRejected):
        _verify(tmp_path, archive, metadata, digest, bundle)


@pytest.mark.parametrize("entry", ("../outside.txt", "/absolute.txt", "config/./alias.txt"))
def test_zip_unsafe_path_is_rejected_without_escape(tmp_path: Path, entry: str) -> None:
    archive, metadata, _digest, bundle = _archive_bundle(tmp_path)
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr(entry, b"bad")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    record = json.loads(metadata.read_text(encoding="utf-8"))
    record["digest"] = f"sha256:{digest}"
    record["size_in_bytes"] = archive.stat().st_size
    metadata.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ArtifactRejected):
        _verify(tmp_path, archive, metadata, digest, bundle)
    assert not (tmp_path / "outside.txt").exists()


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    archive, metadata, _digest, bundle = _archive_bundle(tmp_path)
    link = zipfile.ZipInfo("config/escape")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr(link, "../../outside")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    record = json.loads(metadata.read_text(encoding="utf-8"))
    record["digest"] = f"sha256:{digest}"
    record["size_in_bytes"] = archive.stat().st_size
    metadata.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ArtifactRejected):
        _verify(tmp_path, archive, metadata, digest, bundle)


def test_archive_digest_can_match_while_manifest_content_still_fails(
    tmp_path: Path,
) -> None:
    archive, metadata, _digest, bundle = _archive_bundle(tmp_path)
    replaced = tmp_path / "changed.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(replaced, "w") as output:
        for entry in original.infolist():
            payload = original.read(entry)
            if entry.filename == "config/capabilities.yaml":
                payload = b"changed\n"
            output.writestr(entry, payload)
    replaced.replace(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    record = json.loads(metadata.read_text(encoding="utf-8"))
    record["digest"] = f"sha256:{digest}"
    record["size_in_bytes"] = archive.stat().st_size
    metadata.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ArtifactRejected):
        _verify(tmp_path, archive, metadata, digest, bundle)


def test_zip_declared_inflated_limit_is_rejected(tmp_path: Path) -> None:
    archive, metadata, _digest, bundle = _archive_bundle(tmp_path)
    with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("console/dist/assets/oversized.js", b"x" * (8 * 1024 * 1024 + 1))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    record = json.loads(metadata.read_text(encoding="utf-8"))
    record["digest"] = f"sha256:{digest}"
    record["size_in_bytes"] = archive.stat().st_size
    metadata.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ArtifactRejected):
        _verify(tmp_path, archive, metadata, digest, bundle)


def test_manifest_sha_and_revision_fail_closed(tmp_path: Path) -> None:
    archive, metadata, digest, bundle = _archive_bundle(tmp_path)
    with pytest.raises(ArtifactRejected):
        verify_artifact_archive(
            archive=archive,
            metadata=metadata,
            output_root=tmp_path / "downloaded",
            artifact_id=ARTIFACT_ID,
            artifact_name=ARTIFACT_NAME,
            upload_digest=digest,
            expected_manifest_sha256="0" * 64,
            expected_revision=REVISION,
            run_id=RUN_ID,
        )
    with pytest.raises(ArtifactRejected):
        verify_artifact_archive(
            archive=archive,
            metadata=metadata,
            output_root=tmp_path / "downloaded-revision",
            artifact_id=ARTIFACT_ID,
            artifact_name=ARTIFACT_NAME,
            upload_digest=digest,
            expected_manifest_sha256=hashlib.sha256(
                (bundle / "delivery-manifest.json").read_bytes()
            ).hexdigest(),
            expected_revision="b" * 40,
            run_id=RUN_ID,
        )


def test_prepare_rejects_receipt_mismatch_and_hidden_bundle_file(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    parent = bundle.parent
    result = audit_bundle(bundle, REVISION)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "schema": "agent-team-os-b0-static-candidate-v1",
        "scope": "ci_static_integrity_only_not_s1_or_release",
        "bundle": result,
    }), encoding="utf-8")
    path, manifest_sha = prepare_bundle(parent, receipt, REVISION)
    assert path == bundle
    assert manifest_sha == result["manifest_sha256"]
    result["manifest_sha256"] = "0" * 64
    receipt.write_text(json.dumps({
        "schema": "agent-team-os-b0-static-candidate-v1",
        "scope": "ci_static_integrity_only_not_s1_or_release",
        "bundle": result,
    }), encoding="utf-8")
    with pytest.raises(ArtifactRejected):
        prepare_bundle(parent, receipt, REVISION)
    result["manifest_sha256"] = manifest_sha
    receipt.write_text(json.dumps({
        "schema": "agent-team-os-b0-static-candidate-v1",
        "scope": "ci_static_integrity_only_not_s1_or_release",
        "bundle": result,
    }), encoding="utf-8")
    (bundle / "console/dist/.hidden").write_text("secret-ish\n", encoding="utf-8")
    with pytest.raises(ArtifactRejected):
        prepare_bundle(parent, receipt, REVISION)


class _HTTPResponse:
    def __init__(self, status: int, payload: bytes = b"", location: str | None = None):
        self.status = status
        self.headers = {"Location": location} if location else {}
        self._payload = io.BytesIO(payload)

    def __enter__(self) -> _HTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self._payload.close()

    def read(self, size: int = -1) -> bytes:
        return self._payload.read(size)


def test_fetch_uses_same_run_rest_and_never_forwards_token_to_signed_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, metadata, digest, bundle = _archive_bundle(tmp_path)
    seen: list[tuple[str, str | None]] = []
    signed_url = "https://artifact-cache.actions.githubusercontent.com/private?signature=redacted"

    def fake_open(request: object) -> _HTTPResponse:
        from urllib.request import Request

        assert isinstance(request, Request)
        authorization = request.get_header("Authorization")
        seen.append((request.full_url, authorization))
        if request.full_url.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return _HTTPResponse(200, metadata.read_bytes())
        if request.full_url.endswith(f"/artifacts/{ARTIFACT_ID}/zip"):
            return _HTTPResponse(302, location=signed_url)
        assert request.full_url == signed_url
        return _HTTPResponse(200, archive.read_bytes())

    monkeypatch.setattr("verify_ci_artifact_archive._open_url", fake_open)
    result = fetch_and_verify_artifact(
        work_root=tmp_path,
        artifact_id=ARTIFACT_ID,
        artifact_name=ARTIFACT_NAME,
        upload_digest=digest,
        expected_manifest_sha256=hashlib.sha256(
            (bundle / "delivery-manifest.json").read_bytes()
        ).hexdigest(),
        expected_revision=REVISION,
        run_id=RUN_ID,
        repository="owner/repo",
        token="secret-test-token",
    )
    assert result["archive_sha256"] == digest
    assert len(seen) == 3
    assert seen[0][1] == seen[1][1] == "Bearer secret-test-token"
    assert seen[2] == (signed_url, None)


@pytest.mark.parametrize(
    "bad_location", (
        "http://example.com/a", "https://user:pass@example.com/a", "file:///x",
        "https://127.0.0.1/a", "https://artifact.local/a", "https://example.com:444/a",
        "https://example.com/a\nX-Test: leaked", "https://example.com/a",
    )
)
def test_fetch_rejects_unsafe_signed_url_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_location: str
) -> None:
    archive, metadata, digest, bundle = _archive_bundle(tmp_path)
    calls = 0

    def fake_open(request: object) -> _HTTPResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _HTTPResponse(200, metadata.read_bytes())
        if calls == 2:
            return _HTTPResponse(302, location=bad_location)
        pytest.fail("unsafe Location must not be fetched")

    monkeypatch.setattr("verify_ci_artifact_archive._open_url", fake_open)
    with pytest.raises(ArtifactRejected):
        fetch_and_verify_artifact(
            work_root=tmp_path,
            artifact_id=ARTIFACT_ID,
            artifact_name=ARTIFACT_NAME,
            upload_digest=digest,
            expected_manifest_sha256=hashlib.sha256(
                (bundle / "delivery-manifest.json").read_bytes()
            ).hexdigest(),
            expected_revision=REVISION,
            run_id=RUN_ID,
            repository="owner/repo",
            token="secret-test-token",
        )
    assert calls == 2


def test_fetch_rejects_wrong_run_metadata_before_zip_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _archive, metadata, digest, bundle = _archive_bundle(tmp_path)
    record = json.loads(metadata.read_text(encoding="utf-8"))
    record["workflow_run"]["id"] = RUN_ID + 1
    calls = 0

    def fake_open(_request: object) -> _HTTPResponse:
        nonlocal calls
        calls += 1
        if calls > 1:
            pytest.fail("wrong-run artifact must not fetch a signed URL")
        return _HTTPResponse(200, json.dumps(record).encode())

    monkeypatch.setattr("verify_ci_artifact_archive._open_url", fake_open)
    with pytest.raises(ArtifactRejected, match="CI_ARTIFACT_METADATA_IDENTITY"):
        fetch_and_verify_artifact(
            work_root=tmp_path,
            artifact_id=ARTIFACT_ID,
            artifact_name=ARTIFACT_NAME,
            upload_digest=digest,
            expected_manifest_sha256=hashlib.sha256(
                (bundle / "delivery-manifest.json").read_bytes()
            ).hexdigest(),
            expected_revision=REVISION,
            run_id=RUN_ID,
            repository="owner/repo",
            token="secret-test-token",
        )
    assert calls == 1
