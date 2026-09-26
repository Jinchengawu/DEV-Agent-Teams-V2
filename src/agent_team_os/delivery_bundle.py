"""Build and verify the local Agent-Team-OS delivery bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .product_root import resolve_product_root
from .version import __version__

PRODUCT_VERSION = __version__
MANIFEST_NAME = "delivery-manifest.json"
MANIFEST_SCHEMA = "agent-team-os-delivery-bundle-v2"
_CONFIG_ALLOW_LIST = (
    "capabilities.yaml",
    "framework-lock.json",
    "journeys.yaml",
    "method-packs-v050.json",
)
_EVALUATION_DATASET = Path("evaluation/datasets/agent-team-os-mvp/1.3.0")
_EVALUATION_ALLOW_LIST = (
    "README.en.md",
    "README.md",
    "cases.jsonl",
    "manifest.json",
    "schema.json",
)
_FORBIDDEN_NAMES = {".env", "auth.json", "credentials.json", "id_rsa", "id_ed25519"}
_FORBIDDEN_SUFFIXES = {".db", ".key", ".log", ".pem", ".sqlite", ".sqlite3"}
_FORBIDDEN_PARTS = {".agent-team-os", "__pycache__", "node_modules"}


class BundleBuildError(RuntimeError):
    """Delivery bundle input or integrity is invalid."""


@dataclass(frozen=True)
class BundleBuildResult:
    bundle_root: Path
    manifest_path: Path
    manifest_sha256: str


@dataclass(frozen=True)
class VerifiedBundleMethodLock:
    manifest_sha256: str
    manifest_identity: tuple[int, int]
    product_revision: str
    lock_bytes: bytes
    lock_sha256: str
    lock_identity: tuple[int, int]


def build_delivery_bundle(
    *,
    project_root: Path,
    output_root: Path,
    wheel: Path,
    git_revision: str,
    worktree_clean: bool,
) -> BundleBuildResult:
    root = project_root.resolve()
    wheel = wheel.resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", git_revision):
        raise BundleBuildError("Git Revision 必须是 40 位小写十六进制 SHA。")
    expected_wheel_token = f"-{PRODUCT_VERSION}-"
    if not wheel.is_file() or expected_wheel_token not in wheel.name or wheel.suffix != ".whl":
        raise BundleBuildError(f"后端 wheel 必须与产品版本 {PRODUCT_VERSION} 一致。")
    _validate_inputs(root)
    _verify_package_files(root / "src" / "agent_team_os", wheel)

    destination = output_root.resolve() / f"agent-team-os-{PRODUCT_VERSION}"
    if destination.exists():
        raise BundleBuildError(f"输出目录已存在，拒绝覆盖：{destination}")
    destination.mkdir(parents=True)
    try:
        _copy_regular_file(wheel, destination / "backend" / wheel.name)
        for name in ("pyproject.toml", "uv.lock"):
            _copy_regular_file(root / name, destination / name)
        for name in _CONFIG_ALLOW_LIST:
            _copy_regular_file(root / "config" / name, destination / "config" / name)
        for source in sorted((root / "migrations").glob("*.sql")):
            _copy_regular_file(source, destination / "migrations" / source.name)
        _copy_tree(root / "console" / "dist", destination / "console" / "dist")
        for name in _EVALUATION_ALLOW_LIST:
            _copy_regular_file(
                root / _EVALUATION_DATASET / name,
                destination / _EVALUATION_DATASET / name,
            )

        files = [_manifest_entry(destination, path) for path in _payload_files(destination)]
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "product_version": PRODUCT_VERSION,
            "git_revision": git_revision,
            "source_worktree_clean": worktree_clean,
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "evidence_scope": {
                "local_bundle": "built" if worktree_clean else "development_only",
                "deterministic_gate": "not_run",
                "live_gate": "not_run",
                "release_approval": "not_authorized",
                "apply_read_back": "not_authorized",
            },
            "files": files,
        }
        manifest_path = destination / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        verified = verify_delivery_bundle(destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return BundleBuildResult(
        bundle_root=destination,
        manifest_path=manifest_path,
        manifest_sha256=str(verified["manifest_sha256"]),
    )


def verify_delivery_bundle(bundle_root: Path) -> dict[str, str]:
    result, _locked = _verify_delivery_bundle(bundle_root, capture_method_lock=False)
    return result


def verify_delivery_bundle_with_method_lock(bundle_root: Path) -> VerifiedBundleMethodLock:
    """Return Method Lock bytes attested by the same Manifest verification pass."""
    result, locked = _verify_delivery_bundle(bundle_root, capture_method_lock=True)
    if locked is None:
        raise BundleBuildError("Delivery Bundle 缺少已验 Method Pack Lock。")
    raw, manifest_identity, lock_bytes, lock_identity = locked
    revision = raw.get("git_revision")
    if not isinstance(revision, str):
        raise BundleBuildError("Delivery Bundle Product Revision 缺失。")
    return VerifiedBundleMethodLock(
        manifest_sha256=result["manifest_sha256"],
        manifest_identity=manifest_identity,
        product_revision=revision,
        lock_bytes=lock_bytes,
        lock_sha256=hashlib.sha256(lock_bytes).hexdigest(),
        lock_identity=lock_identity,
    )


def _verify_delivery_bundle(
    bundle_root: Path, *, capture_method_lock: bool
) -> tuple[
    dict[str, str],
    tuple[dict[str, Any], tuple[int, int], bytes, tuple[int, int]] | None,
]:
    root = bundle_root.resolve()
    manifest_path = root / MANIFEST_NAME
    try:
        manifest_bytes, manifest_identity = _read_pinned_regular(manifest_path)
        raw: Any = json.loads(manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleBuildError("Delivery Manifest 不可读或非法。") from error
    if not isinstance(raw, dict) or raw.get("schema") not in {
        MANIFEST_SCHEMA, "agent-team-os-delivery-bundle-v1",
    }:
        raise BundleBuildError("Delivery Manifest schema 不受支持。")
    if raw.get("product_version") != PRODUCT_VERSION:
        raise BundleBuildError("Delivery Manifest 产品版本不一致。")
    entries = raw.get("files")
    if not isinstance(entries, list):
        raise BundleBuildError("Delivery Manifest files 必须是列表。")
    expected: set[str] = set()
    method_lock_bytes: bytes | None = None
    method_lock_identity: tuple[int, int] | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            raise BundleBuildError("Delivery Manifest 文件条目非法。")
        relative = entry.get("path")
        if not isinstance(relative, str) or relative in expected:
            raise BundleBuildError("Delivery Manifest 文件路径非法或重复。")
        raw_path = Path(relative)
        if raw_path.is_absolute() or ".." in raw_path.parts:
            raise BundleBuildError(f"Delivery Bundle 文件路径非法：{relative}")
        unresolved = root / relative
        if any(part.is_symlink() for part in (unresolved, *unresolved.parents)
               if part != root and part.is_relative_to(root)):
            raise BundleBuildError(f"Delivery Bundle 不接受符号链接：{relative}")
        path = unresolved.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise BundleBuildError(f"Delivery Bundle 文件路径非法：{relative}")
        if capture_method_lock and relative == "config/method-packs-v050.json":
            method_lock_bytes, method_lock_identity = _read_pinned_regular(unresolved)
            actual_size = len(method_lock_bytes)
            actual_sha256 = hashlib.sha256(method_lock_bytes).hexdigest()
        else:
            actual_size = path.stat().st_size
            actual_sha256 = _sha256(path)
        if actual_size != entry.get("size") or actual_sha256 != entry.get("sha256"):
            raise BundleBuildError(f"Delivery Bundle SHA-256 或大小不一致：{relative}")
        expected.add(relative)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME
    }
    if expected != actual:
        raise BundleBuildError("Delivery Bundle 实际文件与 Manifest 不一致。")
    resolve_product_root(root)
    result = {
        "status": "verified",
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "product_version": PRODUCT_VERSION,
    }
    if not capture_method_lock:
        return result, None
    if method_lock_bytes is None or method_lock_identity is None:
        raise BundleBuildError("Delivery Bundle Manifest 缺少 Method Pack Lock。")
    return result, (raw, manifest_identity, method_lock_bytes, method_lock_identity)


def _read_pinned_regular(path: Path) -> tuple[bytes, tuple[int, int]]:
    """Read one no-follow file and reject inode/content drift during the read."""
    if path.is_symlink():
        raise BundleBuildError(f"Delivery Bundle 不接受符号链接：{path.name}")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise BundleBuildError(f"Delivery Bundle 文件类型非法：{path.name}")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 65_536):
                chunks.append(chunk)
            after = os.fstat(descriptor)
            current = path.lstat()
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise BundleBuildError(f"Delivery Bundle 文件读取期间漂移：{path.name}")
            return b"".join(chunks), (before.st_dev, before.st_ino)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise BundleBuildError(f"Delivery Bundle 文件不可读：{path.name}") from error


def verified_bundle_revision(bundle_root: Path) -> str:
    """校验受信分发包及实际加载代码；完整性校验不等于发布签名认证。"""
    root = bundle_root.resolve()
    verify_delivery_bundle(root)
    raw = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    if raw.get("schema") != MANIFEST_SCHEMA:
        raise BundleBuildError("旧版 Bundle 不支持运行身份；请从冻结候选重新构建 v2 Bundle。")
    revision = raw.get("git_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise BundleBuildError("Delivery Bundle Git Revision 非法。")
    if raw.get("source_worktree_clean") is not True:
        raise BundleBuildError("Delivery Bundle 必须来自干净候选。")
    if not all((root / name).is_file() for name in ("pyproject.toml", "uv.lock")):
        raise BundleBuildError("Delivery Bundle 缺少构建依赖锁。")
    wheels = list((root / "backend").glob("*.whl"))
    if len(wheels) != 1:
        raise BundleBuildError("Delivery Bundle 必须包含唯一后端 wheel。")
    _verify_package_files(_loaded_package_root(), wheels[0])
    return revision


def _loaded_package_root() -> Path:
    return Path(__file__).parent


def _verify_package_files(package: Path, wheel: Path) -> None:
    """匹配完整 package 文件集合与字节，拒绝源码混装、额外代码和链接。"""
    if not package.is_dir() or package.is_symlink():
        raise BundleBuildError("后端 package 路径非法。")
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = [name for name in archive.namelist()
                     if name.startswith("agent_team_os/") and not name.endswith("/")]
            if not names or len(names) != len(set(names)):
                raise BundleBuildError("后端 wheel package 为空或重复。")
            expected = {name.removeprefix("agent_team_os/"): archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile) as error:
        raise BundleBuildError("后端 wheel 不可读。") from error
    actual = {}
    for path in package.rglob("*"):
        relative = path.relative_to(package)
        if path.is_symlink():
            raise BundleBuildError("后端 package 不接受符号链接。")
        if "__pycache__" in relative.parts:
            continue
        if path.is_file():
            actual[relative.as_posix()] = path.read_bytes()
    if actual != expected:
        raise BundleBuildError("后端 package 与 Bundle wheel 不一致。")


def _validate_inputs(root: Path) -> None:
    required = [root / "config" / name for name in _CONFIG_ALLOW_LIST]
    required.extend(root / name for name in ("pyproject.toml", "uv.lock"))
    required.extend((root / "migrations", root / "console" / "dist" / "index.html"))
    required.extend(root / _EVALUATION_DATASET / name for name in _EVALUATION_ALLOW_LIST)
    missing = [path.relative_to(root).as_posix() for path in required if not path.exists()]
    if missing:
        raise BundleBuildError(f"Delivery Bundle 缺少必需资源：{', '.join(missing)}")
    if not any((root / "migrations").glob("*.sql")):
        raise BundleBuildError("Delivery Bundle 缺少 migrations/*.sql。")
    for included_root in (
        root / "config",
        root / "migrations",
        root / "console" / "dist",
        root / _EVALUATION_DATASET,
    ):
        for path in included_root.rglob("*"):
            relative = path.relative_to(root)
            if path.is_symlink():
                raise BundleBuildError(f"敏感或运行态路径不得进入 Bundle：{relative}")
            if path.is_file() and _is_forbidden(relative):
                raise BundleBuildError(f"敏感或运行态路径不得进入 Bundle：{relative}")


def _is_forbidden(relative: Path) -> bool:
    lowered = {part.lower() for part in relative.parts}
    return bool(
        lowered & _FORBIDDEN_PARTS
        or relative.name.lower() in _FORBIDDEN_NAMES
        or relative.suffix.lower() in _FORBIDDEN_SUFFIXES
    )


def _copy_tree(source_root: Path, destination_root: Path) -> None:
    for source in sorted(source_root.rglob("*")):
        if source.is_dir():
            continue
        _copy_regular_file(source, destination_root / source.relative_to(source_root))


def _copy_regular_file(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise BundleBuildError(f"Bundle 只接受普通文件：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _payload_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and path.name != MANIFEST_NAME)


def _manifest_entry(root: Path, path: Path) -> dict[str, str | int]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
