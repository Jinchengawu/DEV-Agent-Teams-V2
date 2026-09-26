"""Install only fully qualified locked Method Packs into an isolated Store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .delivery_bundle import (
    BundleBuildError,
    PinnedRegularFile,
    verify_delivery_bundle_with_method_lock,
)
from .infrastructure.method_pack_registry import LockedMethodPackArchiveFetcher
from .modules.extensions import (
    ContentAddressedMethodPackStore,
    FrozenMethodPackSet,
    MethodPackInstall,
)
from .product_root import resolve_product_root
from .readiness import snapshot_delivery_build_identity


@dataclass(frozen=True)
class _SourceLockSnapshot:
    content: bytes
    identity: tuple[int, int]


def _read_source_lock(path: Path) -> _SourceLockSnapshot:
    if path.is_symlink():
        raise RuntimeError("METHOD_PACK_LOCK_INVALID")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("METHOD_PACK_LOCK_INVALID")
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
            raise RuntimeError("METHOD_PACK_LOCK_CHANGED")
        return _SourceLockSnapshot(b"".join(chunks), (before.st_dev, before.st_ino))
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None, *, source_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-team-os-method-packs")
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--store", type=Path)
    arguments = parser.parse_args(argv)
    with ExitStack() as lock_lifetime:
        return _install_with_pinned_lock(arguments, source_root, lock_lifetime)


def _install_with_pinned_lock(
    arguments: argparse.Namespace, source_root: Path | None, lock_lifetime: ExitStack
) -> int:

    product_root = source_root.resolve() if source_root is not None else resolve_product_root()
    canonical_lock = product_root / "config" / "method-packs-v050.json"
    lock_file = arguments.lock or canonical_lock
    if lock_file.is_symlink() or not lock_file.is_file():
        raise RuntimeError("METHOD_PACK_LOCK_INVALID")
    lock_file = Path(os.path.abspath(lock_file.expanduser()))
    ContentAddressedMethodPackStore._check_path_components(  # noqa: SLF001
        lock_file.parent, require_private=False
    )
    bundle_mode = (product_root / "delivery-manifest.json").is_file()
    manifest_hash: str | None = None
    product_revision: str | None = None
    if bundle_mode:
        if lock_file != canonical_lock:
            raise RuntimeError("METHOD_PACK_BUNDLE_LOCK_OVERRIDE_DENIED")
        ContentAddressedMethodPackStore._check_path_components(  # noqa: SLF001
            product_root, require_private=False
        )
        manifest_pin = lock_lifetime.enter_context(
            PinnedRegularFile(product_root / "delivery-manifest.json")
        )
        lock_pin = lock_lifetime.enter_context(PinnedRegularFile(lock_file))
        verified = verify_delivery_bundle_with_method_lock(
            product_root, pinned_manifest=manifest_pin, pinned_lock=lock_pin
        )
        if (
            manifest_pin.identity != verified.manifest_identity
            or hashlib.sha256(manifest_pin.content).hexdigest() != verified.manifest_sha256
            or lock_pin.identity != verified.lock_identity
            or lock_pin.content != verified.lock_bytes
        ):
            raise RuntimeError("METHOD_PACK_BUNDLE_LOCK_CHANGED")
        manifest_pin.assert_stable()
        lock_pin.assert_stable()
        manifest_hash = verified.manifest_sha256
        frozen_lock = verified.lock_bytes
        frozen_identity = verified.lock_identity
        identity = snapshot_delivery_build_identity(product_root)
        if not identity.product_worktree_clean or identity.framework_dependency_status != "ready":
            raise RuntimeError("METHOD_PACK_BUILD_IDENTITY_NOT_READY")
        if identity.product_revision != verified.product_revision:
            raise RuntimeError("METHOD_PACK_BUILD_IDENTITY_CHANGED")
        product_revision = identity.product_revision
    else:
        lock_pin = lock_lifetime.enter_context(PinnedRegularFile(lock_file))
        source_lock = _read_source_lock(lock_file)
        if source_lock.content != lock_pin.content or source_lock.identity != lock_pin.identity:
            raise RuntimeError("METHOD_PACK_LOCK_CHANGED")
        frozen_lock = source_lock.content
        frozen_identity = source_lock.identity

    def assert_lock_stable() -> None:
        if bundle_mode:
            try:
                manifest_pin.assert_stable()
                lock_pin.assert_stable()
            except BundleBuildError as error:
                raise RuntimeError("METHOD_PACK_BUNDLE_LOCK_CHANGED") from error
            latest = verify_delivery_bundle_with_method_lock(
                product_root, pinned_manifest=manifest_pin, pinned_lock=lock_pin
            )
            if (
                latest.manifest_sha256 != manifest_hash
                or latest.manifest_identity != verified.manifest_identity
                or latest.lock_bytes != frozen_lock
                or latest.lock_identity != frozen_identity
                or latest.product_revision != product_revision
            ):
                raise RuntimeError("METHOD_PACK_BUNDLE_LOCK_CHANGED")
            try:
                manifest_pin.assert_stable()
                lock_pin.assert_stable()
            except BundleBuildError as error:
                raise RuntimeError("METHOD_PACK_BUNDLE_LOCK_CHANGED") from error
        else:
            try:
                lock_pin.assert_stable()
            except BundleBuildError as error:
                raise RuntimeError("METHOD_PACK_LOCK_CHANGED") from error
            latest_source = _read_source_lock(lock_file)
            if latest_source.content != frozen_lock or latest_source.identity != frozen_identity:
                raise RuntimeError("METHOD_PACK_LOCK_CHANGED")
            try:
                lock_pin.assert_stable()
            except BundleBuildError as error:
                raise RuntimeError("METHOD_PACK_LOCK_CHANGED") from error

    configuration = json.loads(frozen_lock)
    raw_packages = configuration.get("packages")
    if (
        configuration.get("policy_version") != "method-pack-store-v1"
        or not isinstance(raw_packages, list)
        or not raw_packages
    ):
        raise RuntimeError("METHOD_PACK_LOCK_PACKAGES_MISSING")
    if bundle_mode and len(raw_packages) != 2:
        raise RuntimeError("METHOD_PACK_BUNDLE_LOCK_SET_INVALID")

    explicit_data_root = "AGENT_TEAM_OS_DATA_DIR" in os.environ
    data_root = Path(
        os.environ.get("AGENT_TEAM_OS_DATA_DIR", str(product_root / ".agent-team-os"))
    ).expanduser()
    data_root = Path(os.path.abspath(data_root))
    if not data_root.exists() and not data_root.is_symlink():
        ContentAddressedMethodPackStore._check_path_components(
            data_root.parent, require_private=False
        )
        parent_before = data_root.parent.lstat()
        data_root.mkdir(mode=0o700)
        ContentAddressedMethodPackStore._check_path_components(data_root, require_private=False)
        parent_after = data_root.parent.lstat()
        if (parent_before.st_dev, parent_before.st_ino) != (
            parent_after.st_dev,
            parent_after.st_ino,
        ):
            raise RuntimeError("METHOD_PACK_DATA_ROOT_REPLACED")
    ContentAddressedMethodPackStore._check_path_components(data_root, require_private=False)
    if (
        data_root.is_symlink()
        or data_root.stat().st_uid != os.getuid()
        or data_root.stat().st_mode & (0o077 if explicit_data_root else 0o022)
    ):
        raise RuntimeError("METHOD_PACK_DATA_ROOT_UNSAFE")
    store_root = Path(os.path.abspath((arguments.store or data_root / "method-packs").expanduser()))
    anchor = (
        data_root
        if store_root.is_relative_to(data_root) and data_root.stat().st_mode & 0o077 == 0
        else None
    )

    fetcher = LockedMethodPackArchiveFetcher()
    installations: list[tuple[MethodPackInstall, bytes, str, str]] = []
    assert_lock_stable()
    for raw in raw_packages:
        if not isinstance(raw, dict) or not isinstance(raw.get("install"), dict):
            raise RuntimeError("METHOD_PACK_LOCK_PACKAGE_INVALID")
        request = MethodPackInstall.model_validate(raw["install"])
        content = raw.get("expected_content_sha256")
        qualification = raw.get("expected_qualification_sha256")
        if not isinstance(content, str) or not isinstance(qualification, str):
            raise RuntimeError("METHOD_PACK_LOCK_QUALIFICATION_MISSING")
        archive = fetcher.fetch(request, request.tarball_uri)
        installations.append((request, archive, content, qualification))

    assert_lock_stable()
    store = ContentAddressedMethodPackStore(store_root, security_anchor=anchor)
    snapshots = store.install_locked_batch(tuple(installations), lock_file, lock_bytes=frozen_lock)
    frozen_set = FrozenMethodPackSet(lock_file, store, lock_bytes=frozen_lock).snapshot()
    assert_lock_stable()
    installed = [
        {
            "package_name": snapshot.package_name,
            "package_version": snapshot.package_version,
            "official_url": snapshot.tarball_uri,
            "archive_sha256": snapshot.archive_sha256,
            "content_sha256": snapshot.content_sha256,
            "qualification_sha256": snapshot.qualification_sha256,
            "store_uri": snapshot.store_uri,
        }
        for snapshot in snapshots
    ]
    print(
        json.dumps(
            {
                "status": "ready" if bundle_mode else "source-qualified",
                "product_revision": product_revision,
                "bundle_manifest_sha256": manifest_hash,
                "lock_sha256": hashlib.sha256(frozen_lock).hexdigest(),
                "transport": {"proxy": "disabled", "redirects": 0},
                "store": str(store_root),
                "method_pack_set_sha256": frozen_set.qualification_sha256,
                "method_entries": sorted(frozen_set.method_entries),
                "packages": installed,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
