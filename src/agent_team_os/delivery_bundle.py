"""Build and verify the local Agent-Team-OS delivery bundle."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .product_root import resolve_product_root
from .version import __version__

PRODUCT_VERSION = __version__
MANIFEST_NAME = "delivery-manifest.json"
MANIFEST_SCHEMA = "agent-team-os-delivery-bundle-v1"
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

    destination = output_root.resolve() / f"agent-team-os-{PRODUCT_VERSION}"
    if destination.exists():
        raise BundleBuildError(f"输出目录已存在，拒绝覆盖：{destination}")
    destination.mkdir(parents=True)
    try:
        _copy_regular_file(wheel, destination / "backend" / wheel.name)
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
    root = bundle_root.resolve()
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink():
        raise BundleBuildError("Delivery Manifest 不得是符号链接。")
    try:
        raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleBuildError("Delivery Manifest 不可读或非法。") from error
    if not isinstance(raw, dict) or raw.get("schema") != MANIFEST_SCHEMA:
        raise BundleBuildError("Delivery Manifest schema 不受支持。")
    if raw.get("product_version") != PRODUCT_VERSION:
        raise BundleBuildError("Delivery Manifest 产品版本不一致。")
    entries = raw.get("files")
    if not isinstance(entries, list):
        raise BundleBuildError("Delivery Manifest files 必须是列表。")
    expected: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BundleBuildError("Delivery Manifest 文件条目非法。")
        relative = entry.get("path")
        if not isinstance(relative, str) or relative in expected:
            raise BundleBuildError("Delivery Manifest 文件路径非法或重复。")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise BundleBuildError(f"Delivery Bundle 文件路径非法：{relative}")
        if path.stat().st_size != entry.get("size") or _sha256(path) != entry.get("sha256"):
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
    return {
        "status": "verified",
        "manifest_sha256": _sha256(manifest_path),
        "product_version": PRODUCT_VERSION,
    }


def _validate_inputs(root: Path) -> None:
    required = [root / "config" / name for name in _CONFIG_ALLOW_LIST]
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
