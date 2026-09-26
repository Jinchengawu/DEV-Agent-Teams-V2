"""审计 B0 CI 静态 Bundle；不授予构建来源或 Release 资格。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from agent_team_os.delivery_bundle import (
    MANIFEST_NAME,
    PinnedRegularFile,
    verify_delivery_bundle_with_method_lock,
)

MAX_MANIFEST_FILES = 200
MAX_WHEEL_MEMBERS = 400
MAX_INFLATED_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
EXPECTED_SKIP_CLASSNAMES = frozenset({
    "tests.integration.test_live_codex_simulated_planning",
    "integration.test_live_codex_simulated_planning",
})
EXPECTED_SKIP_NAME = "test_real_codex_can_simulate_bounded_planning_without_workspace_changes"
EXPECTED_SKIP_REASON = "set AGENT_TEAM_OS_LIVE_CODEX=1 to call the real Codex CLI"

_FORBIDDEN_PARTS = {".agent-team-os", ".git", ".venv", "__pycache__", "node_modules"}
_FORBIDDEN_NAMES = {"auth.json", "credentials.json", "id_rsa", "id_ed25519"}
_FORBIDDEN_SUFFIXES = {".db", ".key", ".log", ".pem", ".sqlite", ".sqlite3"}
_NESTED_ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".bz2", ".xz", ".jar", ".whl"}
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:sk-(?:proj|svcacct)-[A-Za-z0-9_-]{20,}|"
               rb"gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
               rb"AKIA[0-9A-Z]{16})\b"),
    re.compile(rb"(?i)\bAuthorization\s*[:=]\s*['\"]?Bearer\s+[A-Za-z0-9._-]{24,}"),
    re.compile(
        rb"(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|token|password)\b"
        rb"['\"]?\s*[:=]\s*(['\"])(?!<|\$\{|changeme\b|example\b|placeholder\b)"
        rb"[A-Za-z0-9_./+=-]{24,}\1"
    ),
)
_YAML_BARE_SECRET = re.compile(
    rb"(?im)^[ \t]*(?:api[_-]?key|client[_-]?secret|access[_-]?token|token|password)"
    rb"[ \t]*:[ \t]*(?!<|changeme\b|example\b|placeholder\b)"
    rb"[A-Za-z0-9_+/=-]{24,}[ \t]*(?:#.*)?$"
)


class AuditRejected(RuntimeError):
    """只携带固定错误码，绝不携带被审计内容。"""


def _reject(code: str) -> None:
    raise AuditRejected(code)


def _safe_path(name: str, *, wheel_member: bool = False) -> PurePosixPath:
    if (
        not name or "\\" in name or ":" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        _reject("B0_PATH_INVALID")
    _scan_text(name.encode("utf-8"))
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        _reject("B0_PATH_INVALID")
    lowered = tuple(part.lower() for part in path.parts)
    if any(part in _FORBIDDEN_PARTS or part.startswith(".env") for part in lowered):
        _reject("B0_PATH_FORBIDDEN")
    if path.name.lower() in _FORBIDDEN_NAMES or path.suffix.lower() in _FORBIDDEN_SUFFIXES:
        _reject("B0_PATH_FORBIDDEN")
    if not wheel_member and not (
        name in {"pyproject.toml", "uv.lock"}
        or name.startswith(("config/", "migrations/", "console/dist/", "evaluation/"))
        or (name.startswith("backend/") and name.endswith(".whl"))
    ):
        _reject("B0_PATH_CATEGORY")
    return path


def _scan_text(payload: bytes, name: str = "") -> None:
    if any(byte < 9 or 13 < byte < 32 for byte in payload):
        _reject("B0_UNKNOWN_BINARY")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        _reject("B0_UNKNOWN_BINARY")
    if any(pattern.search(payload) for pattern in _SECRET_PATTERNS) or (
        PurePosixPath(name).suffix.lower() in {".yaml", ".yml"}
        and _YAML_BARE_SECRET.search(payload)
    ):
        _reject("B0_SECRET_VALUE")


def _nested_archive(payload: bytes, name: str) -> bool:
    return (
        PurePosixPath(name).suffix.lower() in _NESTED_ARCHIVE_SUFFIXES
        or payload.startswith((b"PK\x03\x04", b"\x1f\x8b", b"BZh", b"\xfd7zXZ"))
        or payload[257:262] == b"ustar"
    )


def _scan_wheel(wheel: Path) -> tuple[int, int]:
    inflated = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(wheel) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_WHEEL_MEMBERS:
                _reject("B0_WHEEL_MEMBER_LIMIT")
            for member in members:
                name = member.filename.removesuffix("/")
                _safe_path(name, wheel_member=True)
                if name.casefold() in seen:
                    _reject("B0_WHEEL_DUPLICATE")
                seen.add(name.casefold())
                mode = stat.S_IFMT(member.external_attr >> 16)
                if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    _reject("B0_WHEEL_SPECIAL_FILE")
                if member.is_dir():
                    if mode not in {0, stat.S_IFDIR}:
                        _reject("B0_WHEEL_SPECIAL_FILE")
                    continue
                if mode == stat.S_IFDIR or member.file_size > MAX_FILE_BYTES:
                    _reject("B0_WHEEL_MEMBER_LIMIT")
                inflated += member.file_size
                if inflated > MAX_INFLATED_BYTES:
                    _reject("B0_INFLATED_LIMIT")
                with archive.open(member, "r") as source:
                    payload = source.read(MAX_FILE_BYTES + 1)
                    overrun = bool(source.read(1))
                if overrun or len(payload) != member.file_size or _nested_archive(payload, name):
                    _reject("B0_WHEEL_CONTENT")
                _scan_text(payload, name)
    except AuditRejected:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        _reject("B0_WHEEL_INVALID")
    return len(members), inflated


def _lock_signature(path: Path) -> tuple[int, int, int, int, int, int]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
        _reject("B0_METHOD_LOCK_LIMIT")
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_mode,
    )


def audit_bundle(bundle_root: Path, expected_revision: str) -> dict[str, Any]:
    """消费权威 Verifier 的同次 Manifest，另作有限安全审计。"""
    root = bundle_root.absolute()
    if root.is_symlink() or not re.fullmatch(r"[0-9a-f]{40}", expected_revision):
        _reject("B0_IDENTITY_INVALID")
    manifest_path = root / MANIFEST_NAME
    lock_path = root / "config/method-packs-v050.json"
    try:
        if manifest_path.is_symlink() or manifest_path.stat().st_size > MAX_RECEIPT_BYTES:
            _reject("B0_MANIFEST_LIMIT")
        # 仅为读前预检。恶意并发替换仍可能造成 pin 内部读取放大；
        # 不得将其视为硬资源隔离保证。
        initial_lock = _lock_signature(lock_path)
        with PinnedRegularFile(manifest_path) as manifest_pin, PinnedRegularFile(
            lock_path
        ) as lock_pin:
            if len(manifest_pin.content) > MAX_RECEIPT_BYTES:
                _reject("B0_MANIFEST_LIMIT")
            if (
                len(lock_pin.content) > MAX_FILE_BYTES
                or lock_pin.identity != initial_lock[:2]
                or len(lock_pin.content) != initial_lock[2]
                or _lock_signature(lock_path) != initial_lock
            ):
                _reject("B0_METHOD_LOCK_DRIFT")
            lock_pin.assert_stable()
            manifest = json.loads(manifest_pin.content)
            if not isinstance(manifest, dict):
                _reject("B0_MANIFEST_INVALID")
            entries = manifest.get("files")
            if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_MANIFEST_FILES:
                _reject("B0_MANIFEST_LIMIT")
            preflight_total = 0
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    _reject("B0_MANIFEST_INVALID")
                _safe_path(entry["path"])
                size = entry.get("size")
                if not isinstance(size, int) or size < 0 or size > MAX_FILE_BYTES:
                    _reject("B0_FILE_LIMIT")
                target = root / entry["path"]
                if target.is_symlink() or not target.is_file():
                    _reject("B0_PATH_INVALID")
                actual_size = target.stat().st_size
                if actual_size != size or actual_size > MAX_FILE_BYTES:
                    _reject("B0_FILE_LIMIT")
                preflight_total += actual_size
            if preflight_total > MAX_INFLATED_BYTES:
                _reject("B0_INFLATED_LIMIT")
            verified = verify_delivery_bundle_with_method_lock(
                root, pinned_manifest=manifest_pin, pinned_lock=lock_pin
            )
            if (
                manifest.get("schema") != "agent-team-os-delivery-bundle-v2"
                or manifest.get("source_worktree_clean") is not True
                or verified.product_revision != expected_revision
            ):
                _reject("B0_IDENTITY_INVALID")
            nonwheel_bytes = 0
            wheel_count = 0
            wheel_members = 0
            wheel_inflated = 0
            for entry in entries:
                relative = entry["path"]
                target = root / relative
                if target.is_symlink() or not target.is_file():
                    _reject("B0_PATH_INVALID")
                if relative.startswith("backend/"):
                    wheel_count += 1
                    wheel_members, wheel_inflated = _scan_wheel(target)
                else:
                    payload = target.read_bytes()
                    if len(payload) != entry["size"] or _nested_archive(payload, relative):
                        _reject("B0_FILE_CONTENT")
                    _scan_text(payload, relative)
                    nonwheel_bytes += len(payload)
                if nonwheel_bytes + wheel_inflated > MAX_INFLATED_BYTES:
                    _reject("B0_INFLATED_LIMIT")
            if wheel_count != 1:
                _reject("B0_WHEEL_COUNT")
            manifest_pin.assert_stable()
            lock_pin.assert_stable()
            if _lock_signature(lock_path) != initial_lock:
                _reject("B0_METHOD_LOCK_DRIFT")
            repeated = verify_delivery_bundle_with_method_lock(
                root, pinned_manifest=manifest_pin, pinned_lock=lock_pin
            )
            if repeated.manifest_sha256 != verified.manifest_sha256:
                _reject("B0_IDENTITY_DRIFT")
            return {
                "manifest_sha256": verified.manifest_sha256,
                "revision": expected_revision,
                "manifest_files": len(entries),
                "wheel_members": wheel_members,
                "inflated_bytes": nonwheel_bytes + wheel_inflated,
            }
    except AuditRejected:
        raise
    except Exception:
        _reject("B0_BUNDLE_VERIFICATION_FAILED")


def confirm_expected_skip(junit_xml: Path) -> dict[str, int | str]:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(junit_xml, flags)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                _reject("B0_PYTEST_RECEIPT_INVALID")
            if metadata.st_size > 4 * 1024 * 1024:
                _reject("B0_PYTEST_RECEIPT_LIMIT")
            payload = source.read(4 * 1024 * 1024 + 1)
        if len(payload) > 4 * 1024 * 1024:
            _reject("B0_PYTEST_RECEIPT_LIMIT")
        root = ET.fromstring(payload)
        cases = root.findall(".//testcase")
        skipped = [(case, case.find("skipped")) for case in cases]
        skipped = [(case, marker) for case, marker in skipped if marker is not None]
        if len(skipped) != 1 or any(
            case.find("failure") is not None or case.find("error") is not None
            for case in cases
        ):
            _reject("B0_PYTEST_OUTCOME")
        case, marker = skipped[0]
        assert marker is not None
        classname = case.get("classname", "")
        reason = marker.get("message", "") + (marker.text or "")
        if (
            classname not in EXPECTED_SKIP_CLASSNAMES
            or case.get("name") != EXPECTED_SKIP_NAME
            or EXPECTED_SKIP_REASON not in reason
        ):
            _reject("B0_PYTEST_SKIP_UNKNOWN")
        return {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "testcases": len(cases),
            "skipped": len(skipped),
        }
    except AuditRejected:
        raise
    except (OSError, ET.ParseError):
        _reject("B0_PYTEST_RECEIPT_INVALID")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_root", type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--pytest-junit", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    try:
        pytest_junit = confirm_expected_skip(args.pytest_junit)
        result = audit_bundle(args.bundle_root, args.expected_revision)
        project_root = args.project_root.absolute()
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain=v1"], cwd=project_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        if revision != args.expected_revision or dirty:
            _reject("B0_SOURCE_IDENTITY")
        dist = project_root / "dist"
        wheels = list(dist.glob("dev_agent_teams_v2-*.whl"))
        sdists = list(dist.glob("dev_agent_teams_v2-*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            _reject("B0_BUILD_OUTPUT_COUNT")
        bundled_wheels = list((args.bundle_root / "backend").glob("*.whl"))
        if len(bundled_wheels) != 1 or _sha256(wheels[0]) != _sha256(bundled_wheels[0]):
            _reject("B0_BUILD_OUTPUT_DRIFT")
        uv_version = subprocess.run(
            ["uv", "--version"], check=True, capture_output=True, text=True,
        ).stdout.strip()
        if not re.fullmatch(r"uv [0-9]+\.[0-9]+\.[0-9]+(?: .{1,80})?", uv_version):
            _reject("B0_UV_IDENTITY")
        receipt = {
            "schema": "agent-team-os-b0-static-candidate-v1",
            "scope": "ci_static_integrity_only_not_s1_or_release",
            "build_backend": "Unknown: isolated Hatchling and transitive source not attested",
            "uv_version": uv_version,
            "pytest_expected_skip": EXPECTED_SKIP_NAME,
            "pytest_junit": pytest_junit,
            "bundle": result,
            "wheel_sha256": _sha256(wheels[0]),
            "sdist_sha256": _sha256(sdists[0]),
            "builder_sha256": _sha256(project_root / "scripts/build_delivery_bundle.py"),
            "bundle_module_sha256": _sha256(
                project_root / "src/agent_team_os/delivery_bundle.py"
            ),
            "verifier_cli_sha256": _sha256(project_root / "scripts/verify_delivery_bundle.py"),
            "pyproject_sha256": _sha256(project_root / "pyproject.toml"),
            "uv_lock_sha256": _sha256(project_root / "uv.lock"),
        }
        encoded = (json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n").encode()
        if len(encoded) > MAX_RECEIPT_BYTES:
            _reject("B0_RECEIPT_LIMIT")
        descriptor = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
        print(
            "b0_audit=passed; scope=ci_static_integrity_only; "
            f"revision={revision}; manifest_sha256={result['manifest_sha256']}; "
            f"wheel_sha256={receipt['wheel_sha256']}; "
            f"pytest_junit_sha256={pytest_junit['sha256']}; "
            f"pytest_testcases={pytest_junit['testcases']}; "
            f"receipt_sha256={_sha256(args.receipt)}"
        )
        return 0
    except AuditRejected as error:
        print(f"b0_audit=rejected; code={error}")
        return 1
    except Exception:
        print("b0_audit=rejected; code=B0_UNEXPECTED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
