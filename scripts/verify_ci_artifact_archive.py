"""复核 CI 静态 Bundle 的上传范围与独立下载归档身份。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from audit_ci_bundle_artifact import audit_bundle

from agent_team_os.delivery_bundle import PRODUCT_VERSION

MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_EXTRACTED_BYTES = 16 * 1024 * 1024 + 64 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 400
HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SIGNED_ARTIFACT_HOST_SUFFIXES = (".actions.githubusercontent.com", ".blob.core.windows.net")
TRUSTED_REPOSITORY = "Jinchengawu/DEV-Agent-Teams-V2"
TRUSTED_REF = "refs/heads/codex/method-pack-s1-candidate-20260926"
TRUSTED_BEFORE = "f26ae975965773a5572a753b801c2e90e2e1f820"
TRUSTED_COMMIT_MESSAGE = "ci(b0): one-shot artifact 2026-09-28 8d4e71a2"


class ArtifactRejected(RuntimeError):
    """失败关闭错误；CLI 只输出固定代码，不泄露归档内容。"""


def _reject(code: str) -> NoReturn:
    raise ArtifactRejected(code)


def _regular_bytes(path: Path, limit: int) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
                _reject("CI_ARTIFACT_FILE_LIMIT")
            payload = source.read(limit + 1)
        if len(payload) > limit:
            _reject("CI_ARTIFACT_FILE_LIMIT")
        return payload
    except ArtifactRejected:
        raise
    except OSError:
        _reject("CI_ARTIFACT_FILE_INVALID")


def _hex(value: Any, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        _reject(code)
    return value


def _walk_plain_files(root: Path) -> None:
    """上传 Action 的路径输入只能看到已审计的普通文件。"""
    pending = [root]
    while pending:
        current = pending.pop()
        for entry in current.iterdir():
            relative = entry.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                _reject("CI_ARTIFACT_HIDDEN_PATH")
            mode = entry.lstat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(entry)
            elif not stat.S_ISREG(mode):
                _reject("CI_ARTIFACT_SPECIAL_FILE")


def validate_trusted_push_event(
    event_path: Path, runner: Mapping[str, str], expected_revision: str
) -> None:
    """在任何上传准备之前，对 GitHub 表达式的大小写宽松比较作严格补核。"""
    if (
        not event_path.is_absolute()
        or not HEX_40.fullmatch(expected_revision)
        or runner.get("GITHUB_EVENT_NAME") != "push"
        or runner.get("GITHUB_REF") != TRUSTED_REF
        or runner.get("GITHUB_REPOSITORY") != TRUSTED_REPOSITORY
        or runner.get("GITHUB_RUN_ATTEMPT") != "1"
        or runner.get("GITHUB_SHA") != expected_revision
    ):
        _reject("CI_ARTIFACT_EVENT_UNTRUSTED")
    try:
        event = json.loads(_regular_bytes(event_path, 2 * 1024 * 1024))
    except (ArtifactRejected, UnicodeDecodeError, json.JSONDecodeError):
        _reject("CI_ARTIFACT_EVENT_UNTRUSTED")
    if not isinstance(event, dict):
        _reject("CI_ARTIFACT_EVENT_UNTRUSTED")
    repository = event.get("repository")
    head_commit = event.get("head_commit")
    if (
        not isinstance(repository, dict)
        or not isinstance(head_commit, dict)
        or repository.get("full_name") != TRUSTED_REPOSITORY
        or event.get("ref") != TRUSTED_REF
        or event.get("before") != TRUSTED_BEFORE
        or event.get("after") != expected_revision
        or event.get("created") is not False
        or event.get("forced") is not False
        or head_commit.get("id") != expected_revision
        or head_commit.get("message") != TRUSTED_COMMIT_MESSAGE
    ):
        _reject("CI_ARTIFACT_EVENT_UNTRUSTED")


def prepare_bundle(
    bundle_parent: Path, receipt_path: Path, expected_revision: str
) -> tuple[Path, str]:
    """B0 成功之后，再确认唯一上传目录与同次审计身份。"""
    _hex(expected_revision, HEX_40, "CI_ARTIFACT_REVISION")
    parent = bundle_parent.absolute()
    if parent.is_symlink() or not parent.is_dir():
        _reject("CI_ARTIFACT_PARENT")
    children = list(parent.iterdir())
    if len(children) != 1:
        _reject("CI_ARTIFACT_ROOT_COUNT")
    bundle = children[0]
    if (
        bundle.name != f"agent-team-os-{PRODUCT_VERSION}"
        or bundle.is_symlink()
        or not bundle.is_dir()
    ):
        _reject("CI_ARTIFACT_ROOT")
    try:
        receipt = json.loads(_regular_bytes(receipt_path, 64 * 1024))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject("CI_ARTIFACT_RECEIPT")
    if not isinstance(receipt, dict) or (
        receipt.get("schema") != "agent-team-os-b0-static-candidate-v1"
        or receipt.get("scope") != "ci_static_integrity_only_not_s1_or_release"
    ):
        _reject("CI_ARTIFACT_RECEIPT")
    audited_receipt = receipt.get("bundle")
    if not isinstance(audited_receipt, dict):
        _reject("CI_ARTIFACT_RECEIPT")
    manifest_sha = _hex(
        audited_receipt.get("manifest_sha256"), HEX_64, "CI_ARTIFACT_MANIFEST_SHA"
    )
    if audited_receipt.get("revision") != expected_revision:
        _reject("CI_ARTIFACT_RECEIPT_REVISION")
    _walk_plain_files(bundle)
    try:
        current = audit_bundle(bundle, expected_revision)
    except Exception:
        _reject("CI_ARTIFACT_B0_RECHECK")
    if current != audited_receipt:
        _reject("CI_ARTIFACT_AUDIT_DRIFT")
    return bundle, manifest_sha


def _safe_zip_name(name: str) -> PurePosixPath:
    if (
        not name or name.startswith("/") or "\\" in name or ":" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        _reject("CI_ARTIFACT_ZIP_PATH")
    parts = name.removesuffix("/").split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        _reject("CI_ARTIFACT_ZIP_PATH")
    return PurePosixPath(*parts)


def _extract_bounded_zip(archive: Path, output_root: Path) -> None:
    if output_root.exists() or output_root.is_symlink():
        _reject("CI_ARTIFACT_OUTPUT_EXISTS")
    try:
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            if not entries or len(entries) > MAX_ENTRIES:
                _reject("CI_ARTIFACT_ZIP_COUNT")
            names: set[str] = set()
            total = 0
            for entry in entries:
                relative = _safe_zip_name(entry.filename)
                key = str(relative).casefold()
                if key in names or entry.flag_bits & 1:
                    _reject("CI_ARTIFACT_ZIP_DUPLICATE_OR_ENCRYPTED")
                names.add(key)
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if entry.is_dir():
                    if kind not in {0, stat.S_IFDIR}:
                        _reject("CI_ARTIFACT_ZIP_SPECIAL_FILE")
                    continue
                if kind not in {0, stat.S_IFREG} or entry.file_size > MAX_FILE_BYTES:
                    _reject("CI_ARTIFACT_ZIP_SPECIAL_FILE")
                total += entry.file_size
                if total > MAX_EXTRACTED_BYTES:
                    _reject("CI_ARTIFACT_ZIP_INFLATED_LIMIT")
            output_root.mkdir(parents=True)
            for entry in entries:
                if entry.is_dir():
                    continue
                destination = output_root.joinpath(*_safe_zip_name(entry.filename).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry, "r") as input_file, destination.open("xb") as output_file:
                    payload = input_file.read(MAX_FILE_BYTES + 1)
                    if len(payload) != entry.file_size or len(payload) > MAX_FILE_BYTES:
                        _reject("CI_ARTIFACT_ZIP_CONTENT")
                    output_file.write(payload)
    except ArtifactRejected:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        _reject("CI_ARTIFACT_ZIP_INVALID")


def _read_metadata(metadata: Path) -> dict[str, Any]:
    try:
        record = json.loads(_regular_bytes(metadata, 128 * 1024))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject("CI_ARTIFACT_METADATA")
    if not isinstance(record, dict) or not isinstance(record.get("workflow_run"), dict):
        _reject("CI_ARTIFACT_METADATA")
    return record


def _check_metadata_identity(
    record: dict[str, Any], *, artifact_id: int, artifact_name: str,
    upload_digest: str, expected_revision: str, run_id: int,
) -> None:
    if (
        record.get("id") != artifact_id
        or record.get("name") != artifact_name
        or record.get("expired") is not False
        or record["workflow_run"].get("id") != run_id
        or record["workflow_run"].get("head_sha") != expected_revision
        or record.get("digest") != f"sha256:{upload_digest}"
        or type(record.get("size_in_bytes")) is not int
        or not 0 < record["size_in_bytes"] <= MAX_ARCHIVE_BYTES
    ):
        _reject("CI_ARTIFACT_METADATA_IDENTITY")


def verify_artifact_archive(
    *,
    archive: Path,
    metadata: Path,
    output_root: Path,
    artifact_id: int,
    artifact_name: str,
    upload_digest: str,
    expected_manifest_sha256: str,
    expected_revision: str,
    run_id: int,
) -> dict[str, str]:
    """核同 run 原始 ZIP，再复核独立解压后的 Bundle。"""
    _hex(upload_digest, HEX_64, "CI_ARTIFACT_DIGEST")
    _hex(expected_manifest_sha256, HEX_64, "CI_ARTIFACT_MANIFEST_SHA")
    _hex(expected_revision, HEX_40, "CI_ARTIFACT_REVISION")
    if artifact_id < 1 or run_id < 1 or artifact_name != f"agent-team-os-b0-{expected_revision}":
        _reject("CI_ARTIFACT_IDENTITY")
    record = _read_metadata(metadata)
    _check_metadata_identity(
        record, artifact_id=artifact_id, artifact_name=artifact_name,
        upload_digest=upload_digest, expected_revision=expected_revision, run_id=run_id,
    )
    archive_bytes = _regular_bytes(archive, MAX_ARCHIVE_BYTES)
    if record.get("size_in_bytes") != len(archive_bytes):
        _reject("CI_ARTIFACT_ARCHIVE_SIZE")
    if hashlib.sha256(archive_bytes).hexdigest() != upload_digest:
        _reject("CI_ARTIFACT_ARCHIVE_DIGEST")
    _extract_bounded_zip(archive, output_root)
    try:
        checked = audit_bundle(output_root, expected_revision)
    except Exception:
        _reject("CI_ARTIFACT_BUNDLE_VERIFICATION")
    if checked["manifest_sha256"] != expected_manifest_sha256:
        _reject("CI_ARTIFACT_MANIFEST_DRIFT")
    return {
        "archive_sha256": upload_digest,
        "manifest_sha256": expected_manifest_sha256,
        "revision": expected_revision,
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _open_url(request: urllib.request.Request) -> Any:
    """API 的 Bearer 不得随 302 转发至签名 URL；禁止环境代理。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        return opener.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        if error.code == 302:
            return error
        _reject("CI_ARTIFACT_HTTP_STATUS")
    except (urllib.error.URLError, OSError, TimeoutError):
        _reject("CI_ARTIFACT_HTTP_FAILURE")


def _download_response(response: Any, destination: Path, limit: int) -> None:
    if response.status != 200:
        _reject("CI_ARTIFACT_HTTP_STATUS")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            total = 0
            while True:
                block = response.read(min(1024 * 1024, limit - total + 1))
                if not block:
                    break
                total += len(block)
                if total > limit:
                    _reject("CI_ARTIFACT_DOWNLOAD_LIMIT")
                output.write(block)
    except ArtifactRejected:
        raise
    except OSError:
        _reject("CI_ARTIFACT_DOWNLOAD_FAILURE")


def fetch_and_verify_artifact(
    *, work_root: Path, artifact_id: int, artifact_name: str,
    upload_digest: str, expected_manifest_sha256: str,
    expected_revision: str, run_id: int, repository: str, token: str,
) -> dict[str, str]:
    """只从当前 Artifact ID 的 GitHub REST 回读原始 ZIP；不输出 token 或签名 URL。"""
    _hex(upload_digest, HEX_64, "CI_ARTIFACT_DIGEST")
    _hex(expected_manifest_sha256, HEX_64, "CI_ARTIFACT_MANIFEST_SHA")
    _hex(expected_revision, HEX_40, "CI_ARTIFACT_REVISION")
    if (
        artifact_id < 1 or run_id < 1 or not REPOSITORY.fullmatch(repository)
        or not token or "\n" in token or "\r" in token
        or artifact_name != f"agent-team-os-b0-{expected_revision}"
        or work_root.is_symlink() or not work_root.is_dir()
    ):
        _reject("CI_ARTIFACT_FETCH_INPUT")
    api_root = f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}"
    api_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-team-os-b0-readback",
    }
    with tempfile.TemporaryDirectory(prefix="agent-team-os-b0-", dir=work_root) as temporary:
        private_root = Path(temporary)
        metadata_path = private_root / "metadata.json"
        with _open_url(urllib.request.Request(api_root, headers=api_headers)) as response:
            _download_response(response, metadata_path, 128 * 1024)
        record = _read_metadata(metadata_path)
        _check_metadata_identity(
            record, artifact_id=artifact_id, artifact_name=artifact_name,
            upload_digest=upload_digest, expected_revision=expected_revision, run_id=run_id,
        )
        with _open_url(urllib.request.Request(f"{api_root}/zip", headers=api_headers)) as response:
            if response.status != 302:
                _reject("CI_ARTIFACT_REDIRECT_STATUS")
            location = response.headers.get("Location")
        if not isinstance(location, str):
            _reject("CI_ARTIFACT_REDIRECT_LOCATION")
        if any(ord(character) <= 32 or ord(character) == 127 for character in location):
            _reject("CI_ARTIFACT_REDIRECT_LOCATION")
        try:
            parsed = urllib.parse.urlsplit(location)
            port = parsed.port
        except ValueError:
            _reject("CI_ARTIFACT_REDIRECT_LOCATION")
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not host.endswith(SIGNED_ARTIFACT_HOST_SUFFIXES)
            or parsed.username or parsed.password or port not in {None, 443}
            or parsed.fragment
        ):
            _reject("CI_ARTIFACT_REDIRECT_LOCATION")
        archive_path = private_root / "artifact.zip"
        # 签名 URL 的请求不得携带 GitHub Token；HTTP 层不自动跟随其后续重定向。
        signed_request = urllib.request.Request(
            location, headers={"User-Agent": "agent-team-os-b0-readback"}
        )
        with _open_url(signed_request) as response:
            _download_response(response, archive_path, MAX_ARCHIVE_BYTES)
        return verify_artifact_archive(
            archive=archive_path, metadata=metadata_path,
            output_root=private_root / "extracted", artifact_id=artifact_id,
            artifact_name=artifact_name, upload_digest=upload_digest,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_revision=expected_revision, run_id=run_id,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    prepare = modes.add_parser("prepare")
    prepare.add_argument("--bundle-parent", required=True, type=Path)
    prepare.add_argument("--receipt", required=True, type=Path)
    prepare.add_argument("--expected-revision", required=True)
    prepare.add_argument("--github-output", required=True, type=Path)
    verify = modes.add_parser("verify")
    verify.add_argument("--archive", required=True, type=Path)
    verify.add_argument("--metadata", required=True, type=Path)
    verify.add_argument("--output-root", required=True, type=Path)
    verify.add_argument("--artifact-id", required=True, type=int)
    verify.add_argument("--artifact-name", required=True)
    verify.add_argument("--upload-digest", required=True)
    verify.add_argument("--expected-manifest-sha256", required=True)
    verify.add_argument("--expected-revision", required=True)
    verify.add_argument("--run-id", required=True, type=int)
    fetch = modes.add_parser("fetch-verify")
    fetch.add_argument("--work-root", required=True, type=Path)
    fetch.add_argument("--artifact-id", required=True, type=int)
    fetch.add_argument("--artifact-name", required=True)
    fetch.add_argument("--upload-digest", required=True)
    fetch.add_argument("--expected-manifest-sha256", required=True)
    fetch.add_argument("--expected-revision", required=True)
    fetch.add_argument("--run-id", required=True, type=int)
    fetch.add_argument("--repository", required=True)
    args = parser.parse_args()
    try:
        if args.mode == "prepare":
            validate_trusted_push_event(
                Path(os.environ.get("GITHUB_EVENT_PATH", "")),
                os.environ,
                args.expected_revision,
            )
            bundle, manifest_sha = prepare_bundle(
                args.bundle_parent, args.receipt, args.expected_revision
            )
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(f"bundle_path={bundle}\nmanifest_sha256={manifest_sha}\n")
            print(f"ci_artifact_prepare=passed; manifest_sha256={manifest_sha}")
        elif args.mode == "verify":
            result = verify_artifact_archive(
                archive=args.archive,
                metadata=args.metadata,
                output_root=args.output_root,
                artifact_id=args.artifact_id,
                artifact_name=args.artifact_name,
                upload_digest=args.upload_digest,
                expected_manifest_sha256=args.expected_manifest_sha256,
                expected_revision=args.expected_revision,
                run_id=args.run_id,
            )
        else:
            result = fetch_and_verify_artifact(
                work_root=args.work_root,
                artifact_id=args.artifact_id,
                artifact_name=args.artifact_name,
                upload_digest=args.upload_digest,
                expected_manifest_sha256=args.expected_manifest_sha256,
                expected_revision=args.expected_revision,
                run_id=args.run_id,
                repository=args.repository,
                token=os.environ.get("GITHUB_TOKEN", ""),
            )
        if args.mode != "prepare":
            print(
                "ci_artifact_verify=passed; "
                f"archive_sha256={result['archive_sha256']}; "
                f"manifest_sha256={result['manifest_sha256']}; "
                f"revision={result['revision']}; "
                "scope=ci_static_integrity_only"
            )
        return 0
    except ArtifactRejected as error:
        print(f"ci_artifact=rejected; code={error}")
        return 1
    except Exception:
        print("ci_artifact=rejected; code=CI_ARTIFACT_UNEXPECTED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
