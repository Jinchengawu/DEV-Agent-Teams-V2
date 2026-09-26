from __future__ import annotations

import base64
import binascii
import ctypes
import fcntl
import hashlib
import hmac
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...delivery import DeliveryMethodSnapshot
from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_bytes, sha256_json


class MethodEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    method_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=120)
    source_path: str = Field(min_length=1, max_length=500)

    @field_validator("source_path")
    @classmethod
    def source_path_is_relative(cls, value: str) -> str:
        normalized = _safe_relative_path(value)
        if normalized == "package" or normalized.startswith("package/"):
            raise ValueError("source_path is relative to the package root")
        return normalized


class MethodPackInstall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    package_name: str = Field(pattern=r"^(?:@[a-z0-9._-]+/)?[a-z0-9._-]+$", max_length=214)
    package_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=40)
    tarball_uri: str = Field(pattern=r"^https://", max_length=500)
    registry_integrity: str = Field(pattern=r"^sha512-[A-Za-z0-9+/]+={0,2}$", max_length=128)
    archive_sha256: Sha256
    method_entries: tuple[MethodEntry, ...] = Field(min_length=1, max_length=32)
    max_file_count: int = Field(default=10_000, ge=1, le=50_000)
    max_unpacked_bytes: int = Field(default=100_000_000, ge=1, le=500_000_000)

    @model_validator(mode="after")
    def method_ids_are_unique(self) -> MethodPackInstall:
        ids = tuple(item.method_id for item in self.method_entries)
        if len(set(ids)) != len(ids):
            raise ValueError("method entry ids must be unique")
        return self


class MethodPackFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: Sha256
    size_bytes: int = Field(ge=0)


class MethodPackSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    package_name: str
    package_version: str
    tarball_uri: str
    registry_integrity: str
    archive_sha256: Sha256
    content_sha256: Sha256
    qualification_sha256: Sha256
    store_uri: str
    method_entries: tuple[MethodEntry, ...]
    files: tuple[MethodPackFile, ...]
    policy_version: str = "method-pack-store-v1"

    @model_validator(mode="after")
    def store_uri_matches_content(self) -> MethodPackSnapshot:
        if self.store_uri != f"method-pack://sha256/{self.content_sha256}":
            raise ValueError("method pack URI must match the content hash")
        return self


class RuntimeMethodOverlay(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    root: Path
    codex_home: Path
    environment: dict[str, str]
    package_snapshots: tuple[MethodPackSnapshot, ...]


@dataclass(frozen=True)
class PreparedPack:
    snapshot: MethodPackSnapshot
    files: dict[str, bytes]


_STORE_MUTEX_GUARD = threading.Lock()
_STORE_MUTEXES: dict[str, threading.Lock] = {}


def _store_mutex(root: Path) -> threading.Lock:
    with _STORE_MUTEX_GUARD:
        return _STORE_MUTEXES.setdefault(str(root), threading.Lock())


class ContentAddressedMethodPackStore:
    """Verify npm archives and expose selected skills through an ephemeral Codex home."""

    def __init__(self, root: Path, *, security_anchor: Path | None = None) -> None:
        # resolve() would hide a symlink in an operator-supplied Store path.
        self.root = Path(os.path.abspath(root.expanduser()))
        self._security_anchor = (
            Path(os.path.abspath(security_anchor.expanduser()))
            if security_anchor is not None
            else None
        )
        self.objects = self.root / "objects" / "sha256"
        self._check_root_path(create=False)
        self._check_pending()

    def _check_pending(self) -> None:
        pending = self.root / ".install-in-progress"
        if pending.exists() or pending.is_symlink():
            raise _method_pack_error(
                "METHOD_PACK_STORE_PENDING", "Method Pack Store 存在未完成的安装。"
            )

    def _check_root_path(self, *, create: bool) -> bool:
        self._check_security_anchor()
        try:
            root_metadata = self.root.lstat()
        except FileNotFoundError:
            self._check_absent_root_ancestors()
            if not create:
                return False
            # Only a Writer may create one Store root beneath an existing safe parent.
            parent = self.root.parent
            self._check_path_components(parent, require_private=False)
            root_metadata = self._create_root_under_parent(parent)
        self._check_path_components(self.root)
        if root_metadata.st_uid != os.getuid() or stat.S_IMODE(root_metadata.st_mode) & 0o077:
            raise _method_pack_error(
                "METHOD_PACK_STORE_PATH_UNSAFE", "Method Pack Store 根必须是当前用户的私有目录。"
            )
        return True

    def _create_root_under_parent(self, parent: Path) -> os.stat_result:
        parent_fd = os.open(
            parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        )
        child_fd = -1
        created = False
        created_inode: tuple[int, int] | None = None
        try:
            parent_opened = os.fstat(parent_fd)
            parent_named = parent.lstat()
            parent_identity = (parent_opened.st_dev, parent_opened.st_ino)
            if parent_identity != (parent_named.st_dev, parent_named.st_ino):
                raise _method_pack_error(
                    "METHOD_PACK_STORE_PATH_INVALID", "Store 父目录身份漂移。"
                )
            try:
                os.mkdir(self.root.name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                # Another Writer may have won; join only after checking the same inode.
                pass
            else:
                created = True
            child_fd = os.open(
                self.root.name,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            child_opened = os.fstat(child_fd)
            child_named = os.stat(self.root.name, dir_fd=parent_fd, follow_symlinks=False)
            parent_after = parent.lstat()
            parent_fd_after = os.fstat(parent_fd)
            if (child_opened.st_dev, child_opened.st_ino) != (
                child_named.st_dev,
                child_named.st_ino,
            ):
                raise _method_pack_error(
                    "METHOD_PACK_STORE_PATH_INVALID", "Store 创建期间根目录被替换。"
                )
            if created:
                # mkdir(0700) may be narrowed by umask. Never chmod an opened
                # child before its ownership is established; a concurrent path
                # replacement could make that fd belong to someone else's root.
                if (
                    child_opened.st_uid != os.getuid()
                    or stat.S_IMODE(child_opened.st_mode) != 0o700
                    or stat.S_IMODE(child_named.st_mode) != 0o700
                    or os.listdir(child_fd)
                ):
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_PATH_UNSAFE",
                        "Store 首次创建后的身份、权限或空目录状态不可确认。",
                    )
                if parent_identity != (parent_fd_after.st_dev, parent_fd_after.st_ino):
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_PATH_INVALID", "Store 父目录身份漂移。"
                    )
                created_inode = (child_opened.st_dev, child_opened.st_ino)
            if (
                parent_identity != (parent_after.st_dev, parent_after.st_ino)
                or (child_opened.st_dev, child_opened.st_ino)
                != (child_named.st_dev, child_named.st_ino)
            ):
                raise _method_pack_error(
                    "METHOD_PACK_STORE_PATH_INVALID", "Store 创建期间父路径被替换。"
                )
            self._check_path_components(self.root)
            root_named = self.root.lstat()
            if (root_named.st_dev, root_named.st_ino) != (
                child_opened.st_dev,
                child_opened.st_ino,
            ):
                raise _method_pack_error(
                    "METHOD_PACK_STORE_PATH_INVALID", "Store 创建期间根目录被替换。"
                )
            return root_named
        except BaseException as error:
            if created:
                if child_fd >= 0:
                    os.close(child_fd)
                    child_fd = -1
                if not self._remove_new_empty_root(parent_fd, created_inode):
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_RECOVERY_REQUIRED",
                        "Store 首次创建失败且本批空根无法安全清理；需单独授权恢复。",
                    ) from error
            raise
        finally:
            if child_fd >= 0:
                os.close(child_fd)
            os.close(parent_fd)

    def _remove_new_empty_root(
        self, parent_fd: int, created_inode: tuple[int, int] | None
    ) -> bool:
        if created_inode is None:
            return False
        try:
            current = os.stat(self.root.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or (
                current.st_dev,
                current.st_ino,
            ) != created_inode:
                return False
            os.rmdir(self.root.name, dir_fd=parent_fd)
        except OSError:
            return False
        return True

    def _check_absent_root_ancestors(self) -> None:
        current = self.root.parent
        missing = [self.root]
        while True:
            try:
                current.lstat()
                break
            except FileNotFoundError:
                if current == current.parent:
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_PATH_INVALID", "Store 缺少可验证的父目录。"
                    ) from None
                missing.append(current)
                current = current.parent
        self._check_path_components(current, require_private=False)
        for path in missing:
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise _method_pack_error(
                "METHOD_PACK_STORE_PATH_INVALID", "Store 缺根检查期间路径发生变化。"
            )

    def _check_security_anchor(self) -> None:
        if self._security_anchor is not None:
            anchor = self._security_anchor
            try:
                self.root.relative_to(anchor)
            except ValueError as error:
                raise _method_pack_error(
                    "METHOD_PACK_STORE_PATH_UNSAFE", "Store 不在指定 Data Root 内。"
                ) from error
            self._check_path_components(anchor, require_private=False)
            metadata = anchor.lstat()
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise _method_pack_error(
                    "METHOD_PACK_STORE_PATH_UNSAFE", "Data Root 必须是当前用户的私有目录。"
                )

    @staticmethod
    def _check_path_components(path: Path, *, require_private: bool = True) -> None:
        directory = os.open(
            path.anchor, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        )
        private_anchor_seen = False
        try:
            for part in path.parts[1:]:
                child = -1
                try:
                    child = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory,
                    )
                    metadata = os.fstat(child)
                    entry = os.stat(part, dir_fd=directory, follow_symlinks=False)
                except OSError as error:
                    if child >= 0:
                        os.close(child)
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_PATH_INVALID", "Method Pack Store 路径不可验证。"
                    ) from error
                if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != (
                    entry.st_dev,
                    entry.st_ino,
                ):
                    os.close(child)
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_PATH_INVALID", "Method Pack Store 路径被替换。"
                    )
                mode = stat.S_IMODE(metadata.st_mode)
                if metadata.st_uid == os.getuid():
                    if mode & 0o022:
                        os.close(child)
                        raise _method_pack_error(
                            "METHOD_PACK_STORE_PATH_UNSAFE",
                            "Method Pack Store 路径可被其他用户修改。",
                        )
                    if mode & 0o077 == 0:
                        private_anchor_seen = True
                elif metadata.st_uid != 0 or (mode & 0o022 and not (mode & stat.S_ISVTX)):
                    os.close(child)
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_PATH_UNSAFE", "Method Pack Store 祖先目录不可信。"
                    )
                os.close(directory)
                directory = child
            final = os.stat(path, follow_symlinks=False)
            opened = os.fstat(directory)
            if (final.st_dev, final.st_ino) != (opened.st_dev, opened.st_ino):
                raise _method_pack_error(
                    "METHOD_PACK_STORE_PATH_INVALID", "Store 路径复核时被替换。"
                )
        finally:
            os.close(directory)
        if require_private and not private_anchor_seen:
            raise _method_pack_error(
                "METHOD_PACK_STORE_PATH_UNSAFE", "Store 缺少可验证的私有目录锚点。"
            )

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[None]:
        if not self._check_root_path(create=exclusive):
            raise _method_pack_error(
                "METHOD_PACK_STORE_MISSING", "Method Pack Store 尚未由安装 Writer 初始化。"
            )
        self._check_pending()
        mutex = _store_mutex(self.root)
        deadline = time.monotonic() + 30.0
        if not mutex.acquire(timeout=30.0):
            raise _method_pack_error(
                "METHOD_PACK_STORE_LOCK_TIMEOUT", "Method Pack Store 锁等待超时。"
            )
        descriptor = -1
        try:
            lock_path = self.root / ".install.lock"
            flags = os.O_RDWR if exclusive else os.O_RDONLY
            if exclusive:
                flags |= os.O_CREAT
            try:
                descriptor = os.open(lock_path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileNotFoundError as error:
                raise _method_pack_error(
                    "METHOD_PACK_STORE_LOCK_MISSING", "既有 Method Pack Store 缺少锁文件。"
                ) from error
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise _method_pack_error(
                    "METHOD_PACK_STORE_LOCK_INVALID", "Store 锁文件身份或权限无效。"
                )
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            while True:
                try:
                    fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise _method_pack_error(
                            "METHOD_PACK_STORE_LOCK_TIMEOUT", "Method Pack Store 锁等待超时。"
                        ) from None
                    time.sleep(0.05)
            current = lock_path.lstat()
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise _method_pack_error("METHOD_PACK_STORE_LOCK_INVALID", "Store 锁文件被替换。")
            self._check_root_path(create=False)
            self._check_pending()
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            mutex.release()

    @contextmanager
    def read_lock(self) -> Iterator[None]:
        """Hold the shared Store lock across an entire formal read transaction."""
        with self._lock(exclusive=False):
            yield

    def install_archive(
        self,
        request: MethodPackInstall,
        archive: bytes,
    ) -> MethodPackSnapshot:
        prepared = self.prepare_archive(request, archive)
        self._commit_prepared((prepared,))
        return prepared.snapshot

    def prepare_archive(self, request: MethodPackInstall, archive: bytes) -> PreparedPack:
        """Validate all archive and qualification bytes without touching the Store."""
        archive_digest = sha256_bytes(archive)
        if archive_digest != request.archive_sha256:
            raise _method_pack_error(
                "METHOD_PACK_ARCHIVE_HASH_MISMATCH",
                "Method Pack 归档 SHA-256 与冻结的 Registry 元数据不一致。",
            )
        self._verify_registry_integrity(request.registry_integrity, archive)
        files = self._read_archive(request, archive)
        self._verify_package_identity(request, files)
        self._verify_method_entries(request, files)
        file_manifest = tuple(
            MethodPackFile(path=name, sha256=sha256_bytes(content), size_bytes=len(content))
            for name, content in sorted(files.items())
        )
        content_digest = sha256_json([item.model_dump(mode="json") for item in file_manifest])
        qualification_digest = sha256_json(
            {
                "package_name": request.package_name,
                "package_version": request.package_version,
                "tarball_uri": request.tarball_uri,
                "registry_integrity": request.registry_integrity,
                "archive_sha256": request.archive_sha256,
                "content_sha256": content_digest,
                "method_entries": [item.model_dump(mode="json") for item in request.method_entries],
                "policy_version": "method-pack-store-v1",
            }
        )
        snapshot = MethodPackSnapshot(
            package_name=request.package_name,
            package_version=request.package_version,
            tarball_uri=request.tarball_uri,
            registry_integrity=request.registry_integrity,
            archive_sha256=archive_digest,
            content_sha256=content_digest,
            qualification_sha256=qualification_digest,
            store_uri=f"method-pack://sha256/{content_digest}",
            method_entries=request.method_entries,
            files=file_manifest,
        )
        return PreparedPack(snapshot=snapshot, files=files)

    def install_locked_batch(
        self,
        items: tuple[tuple[MethodPackInstall, bytes, str, str], ...],
        lock_file: Path,
        *,
        lock_bytes: bytes | None = None,
    ) -> tuple[MethodPackSnapshot, ...]:
        """Prepare the whole frozen set before publishing any visible Store bytes."""
        if not items:
            raise _method_pack_error("METHOD_PACK_LOCK_INVALID", "Method Pack 批次不能为空。")
        prepared: list[PreparedPack] = []
        for request, archive, expected_content, expected_qualification in items:
            candidate = self.prepare_archive(request, archive)
            if (
                candidate.snapshot.content_sha256 != expected_content
                or candidate.snapshot.qualification_sha256 != expected_qualification
            ):
                raise _method_pack_error(
                    "METHOD_PACK_LOCK_SNAPSHOT_MISMATCH", "Method Pack 归档资格与冻结锁不一致。"
                )
            prepared.append(candidate)
        frozen_lock = lock_bytes if lock_bytes is not None else lock_file.read_bytes()
        self._validate_prepared_set(frozen_lock, items, tuple(prepared))
        self._commit_prepared(tuple(prepared), lock_file=lock_file, lock_bytes=frozen_lock)
        return tuple(item.snapshot for item in prepared)

    @staticmethod
    def _validate_prepared_set(
        lock_bytes: bytes,
        items: tuple[tuple[MethodPackInstall, bytes, str, str], ...],
        prepared: tuple[PreparedPack, ...],
    ) -> None:
        try:
            lock = json.loads(lock_bytes)
            rows = lock["packages"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise _method_pack_error(
                "METHOD_PACK_LOCK_INVALID", "Method Pack Lock 不可验证。"
            ) from error
        if (
            lock.get("policy_version") != "method-pack-store-v1"
            or not isinstance(rows, list)
            or len(rows) != len(prepared)
        ):
            raise _method_pack_error(
                "METHOD_PACK_LOCK_INVALID", "Method Pack Lock 的整组条目不匹配。"
            )
        ids: set[str] = set()
        for row, (request, _archive, _content, _qualification), item in zip(
            rows, items, prepared, strict=True
        ):
            snapshot = item.snapshot
            if not isinstance(row, dict) or not isinstance(row.get("install"), dict):
                raise _method_pack_error(
                    "METHOD_PACK_LOCK_INVALID", "Method Pack Lock Package 结构无效。"
                )
            try:
                locked_request = MethodPackInstall.model_validate(row["install"])
            except ValueError as error:
                raise _method_pack_error(
                    "METHOD_PACK_LOCK_INVALID", "Method Pack Lock Package 无效。"
                ) from error
            if (
                locked_request != request
                or row.get("expected_content_sha256") != snapshot.content_sha256
                or row.get("expected_qualification_sha256") != snapshot.qualification_sha256
            ):
                raise _method_pack_error(
                    "METHOD_PACK_LOCK_SNAPSHOT_MISMATCH", "Method Pack Lock 整组资格漂移。"
                )
            for entry in snapshot.method_entries:
                if entry.method_id in ids:
                    raise _method_pack_error(
                        "METHOD_ENTRY_COLLISION", "Method Pack Set 的入口重复。"
                    )
                ids.add(entry.method_id)

    def _commit_prepared(
        self,
        prepared: tuple[PreparedPack, ...],
        *,
        lock_file: Path | None = None,
        lock_bytes: bytes | None = None,
    ) -> None:
        created: list[tuple[Path, int, str, MethodPackSnapshot | None]] = []
        pending = self.root / ".install-in-progress"
        with self._lock(exclusive=True):
            descriptor = os.open(
                pending,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
            try:
                # Verify every reused entry before the first payload write.
                for item in prepared:
                    snapshot = item.snapshot
                    target = self._object_path(snapshot.content_sha256)
                    if target.exists() or target.is_symlink():
                        before = target.lstat()
                        self._verify_object(snapshot)
                        after = target.lstat()
                        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                            raise _method_pack_error(
                                "METHOD_PACK_STORE_PATH_INVALID",
                                "既有 Method 对象在核验期间被替换。",
                            )
                    existing = self._snapshot_path(snapshot.qualification_sha256)
                    if existing.exists() or existing.is_symlink():
                        before = existing.lstat()
                        self._load_snapshot_locked(snapshot.qualification_sha256)
                        payload = existing.read_text(encoding="utf-8")
                        after = existing.lstat()
                        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                            raise _method_pack_error(
                                "METHOD_PACK_STORE_PATH_INVALID", "既有 Snapshot 在核验期间被替换。"
                            )
                        if payload != snapshot.model_dump_json(
                            indent=2
                        ):
                            raise _method_pack_error(
                                "METHOD_PACK_SNAPSHOT_HASH_MISMATCH",
                                "既有 Snapshot 与冻结资格不一致。",
                            )
                for item in prepared:
                    self._persist_prepared_locked(item, created)
                for item in prepared:
                    self._load_snapshot_locked(item.snapshot.qualification_sha256)
                if lock_file is not None:
                    FrozenMethodPackSet(lock_file, self, lock_bytes=lock_bytes)._snapshot_locked()
                pending.unlink()
            except BaseException:
                if not self._rollback_created_locked(created):
                    # Unknown ownership must be handled by a separately authorised recovery.
                    raise
                pending.unlink()
                raise

    def _persist_prepared_locked(
        self, item: PreparedPack, created: list[tuple[Path, int, str, MethodPackSnapshot | None]]
    ) -> None:
        snapshot = item.snapshot
        target = self._object_path(snapshot.content_sha256)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            staging = Path(
                tempfile.mkdtemp(prefix=f".{snapshot.content_sha256}.", dir=self.objects)
            )
            object_inode = staging.lstat().st_ino
            created.append((staging, object_inode, snapshot.content_sha256, snapshot))
            for name, content in sorted(item.files.items()):
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
            self._verify_object(snapshot, root=staging)
            # Pre-record identity: a failed no-replace promotion must never
            # cause rollback to delete another writer's target.
            created.append((target, object_inode, snapshot.content_sha256, snapshot))
            _promote_directory_no_replace(staging, target)
            _make_tree_read_only(target)
            self._verify_object(snapshot)
        payload = snapshot.model_dump_json(indent=2).encode("utf-8")
        destination = self._snapshot_path(snapshot.qualification_sha256)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, staging_name = tempfile.mkstemp(prefix=".snapshot-", dir=destination.parent)
            staging = Path(staging_name)
            inode = os.fstat(descriptor).st_ino
            created.append((staging, inode, sha256_bytes(payload), None))
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            if sha256_bytes(staging.read_bytes()) != sha256_bytes(payload):
                raise _method_pack_error(
                    "METHOD_PACK_SNAPSHOT_HASH_MISMATCH", "新 Snapshot 写入后校验失败。"
                )
            created.append((destination, inode, sha256_bytes(payload), None))
            os.link(staging, destination, follow_symlinks=False)
            staging.unlink()

    def _rollback_created_locked(
        self, created: list[tuple[Path, int, str, MethodPackSnapshot | None]]
    ) -> bool:
        for path, inode, digest, snapshot in reversed(created):
            try:
                if not path.exists() and not path.is_symlink():
                    continue
                if path.is_symlink() or path.lstat().st_ino != inode:
                    return False
                if path.is_dir():
                    # Verify the complete object before removing only our new tree.
                    if snapshot is None or not (
                        path.name == digest
                        or (path.parent == self.objects and path.name.startswith(f".{digest}."))
                    ):
                        return False
                    self._verify_object(snapshot, root=path)
                    _remove_tree(path)
                elif sha256_bytes(path.read_bytes()) == digest:
                    path.unlink()
                else:
                    return False
            except (OSError, ProductError):
                return False
        return True

    def load_snapshot(self, qualification_sha256: str) -> MethodPackSnapshot:
        with self.read_lock():
            return self._load_snapshot_locked(qualification_sha256)

    def load_snapshots(self, qualifications: tuple[str, ...]) -> tuple[MethodPackSnapshot, ...]:
        with self.read_lock():
            return tuple(self._load_snapshot_locked(item) for item in qualifications)

    def _load_snapshot_locked(self, qualification_sha256: str) -> MethodPackSnapshot:
        try:
            qualification = Sha256.validate(qualification_sha256)
            payload = json.loads(self._snapshot_path(qualification).read_text(encoding="utf-8"))
            snapshot = MethodPackSnapshot.model_validate(payload)
        except (ValueError, OSError, json.JSONDecodeError) as error:
            raise _method_pack_error(
                "METHOD_PACK_SNAPSHOT_MISSING",
                f"Method Pack Qualification {qualification_sha256} 尚未进入本地 Store。",
            ) from error
        if snapshot.qualification_sha256 != qualification:
            raise _method_pack_error(
                "METHOD_PACK_SNAPSHOT_HASH_MISMATCH",
                "Method Pack Snapshot 文件与 Qualification Hash 不一致。",
            )
        self._verify_object(snapshot)
        return snapshot

    @contextmanager
    def runtime_overlay(
        self,
        snapshots: tuple[MethodPackSnapshot, ...],
        *,
        codex_auth_file: Path | None = None,
    ) -> Iterator[RuntimeMethodOverlay]:
        if not snapshots:
            raise _method_pack_error(
                "METHOD_PACK_SNAPSHOT_REQUIRED",
                "Runtime Overlay 至少需要一个已资格化的 Method Pack Snapshot。",
            )
        overlay_root: Path | None = None
        try:
            with self.read_lock():
                self._validate_overlay_inputs_locked(snapshots)
                overlay_root = Path(tempfile.mkdtemp(prefix="overlay-", dir=self.root))
                codex_home = overlay_root / "codex-home"
                skills_root = codex_home / "skills"
                skills_root.mkdir(parents=True)
                environment = {"CODEX_HOME": str(codex_home)}
                self._freeze_overlay_locked(snapshots, skills_root, environment)
            # Codex needs a writable ephemeral home for session state. Only the
            # Method Pack payload is immutable; it is removed with the overlay.
            _make_tree_read_only(skills_root)
            codex_config = codex_home / "config.toml"
            codex_config.write_text(
                "[features]\nmulti_agent = false\n",
                encoding="utf-8",
            )
            codex_config.chmod(0o444)
            if codex_auth_file is not None:
                auth_source = _validated_codex_auth_file(codex_auth_file)
                (codex_home / "auth.json").symlink_to(auth_source)
            yield RuntimeMethodOverlay(
                root=overlay_root,
                codex_home=codex_home,
                environment=environment,
                package_snapshots=snapshots,
            )
        finally:
            if overlay_root is not None and overlay_root.exists():
                _remove_tree(overlay_root)

    def _validate_overlay_inputs_locked(
        self, snapshots: tuple[MethodPackSnapshot, ...]
    ) -> None:
        installed_ids: set[str] = set()
        for snapshot in snapshots:
            stored = self._load_snapshot_locked(snapshot.qualification_sha256)
            if stored != snapshot:
                raise _method_pack_error(
                    "METHOD_PACK_SNAPSHOT_HASH_MISMATCH", "Runtime Snapshot 与 Store 不一致。"
                )
            if snapshot.package_name == "bmad-method":
                runtime_source = self._object_path(snapshot.content_sha256) / "src"
                required_support = (
                    runtime_source / "scripts" / "render_skill.py",
                    runtime_source / "scripts" / "config_utils.py",
                )
                if not all(item.is_file() for item in required_support):
                    raise _method_pack_error(
                        "METHOD_PACK_RUNTIME_SUPPORT_MISSING",
                        "BMAD Method Pack 缺少执行 Method Entry 必需的 Project Support 脚本。",
                    )
            for entry in snapshot.method_entries:
                if entry.method_id in installed_ids:
                    raise _method_pack_error(
                        "METHOD_ENTRY_COLLISION",
                        f"Runtime Overlay 中存在重复 Method Entry：{entry.method_id}。",
                    )
                installed_ids.add(entry.method_id)

    def _freeze_overlay_locked(
        self,
        snapshots: tuple[MethodPackSnapshot, ...],
        skills_root: Path,
        environment: dict[str, str],
    ) -> None:
        installed_ids: set[str] = set()
        for snapshot in snapshots:
            stored = self._load_snapshot_locked(snapshot.qualification_sha256)
            if stored != snapshot:
                raise _method_pack_error(
                    "METHOD_PACK_SNAPSHOT_HASH_MISMATCH", "Runtime Snapshot 与 Store 不一致。"
                )
            object_root = self._object_path(snapshot.content_sha256)
            if snapshot.package_name == "bmad-method":
                runtime_source = object_root / "src"
                required_support = (
                    runtime_source / "scripts" / "render_skill.py",
                    runtime_source / "scripts" / "config_utils.py",
                )
                if not all(item.is_file() for item in required_support):
                    raise _method_pack_error(
                        "METHOD_PACK_RUNTIME_SUPPORT_MISSING",
                        "BMAD Method Pack 缺少执行 Method Entry 必需的 Project Support 脚本。",
                    )
                # Published objects are immutable under this Store contract.
                environment["AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE"] = str(runtime_source)
            for entry in snapshot.method_entries:
                if entry.method_id in installed_ids:
                    raise _method_pack_error(
                        "METHOD_ENTRY_COLLISION",
                        f"Runtime Overlay 中存在重复 Method Entry：{entry.method_id}。",
                    )
                installed_ids.add(entry.method_id)
                shutil.copytree(object_root / entry.source_path, skills_root / entry.method_id)

    def _verify_object(self, snapshot: MethodPackSnapshot, *, root: Path | None = None) -> None:
        root = root or self._object_path(snapshot.content_sha256)
        try:
            actual = self._object_manifest(root)
        except FileNotFoundError as error:
            raise _method_pack_error(
                "METHOD_PACK_OBJECT_MISSING",
                f"Method Pack 对象 {snapshot.content_sha256} 不完整。",
            ) from error
        expected = [item.model_dump(mode="json") for item in snapshot.files]
        expected_dirs = {
            parent.as_posix()
            for file in snapshot.files
            for parent in PurePosixPath(file.path).parents
            if parent.as_posix() != "."
        }
        actual_dirs = {
            item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_dir()
        }
        if (
            actual != expected
            or actual_dirs != expected_dirs
            or sha256_json(actual) != snapshot.content_sha256
        ):
            raise _method_pack_error(
                "METHOD_PACK_CONTENT_HASH_MISMATCH",
                f"Method Pack 对象 {snapshot.content_sha256} 与 Snapshot 不一致。",
            )

    @staticmethod
    def _object_manifest(root: Path) -> list[dict[str, object]]:
        if root.is_symlink() or not root.is_dir():
            raise FileNotFoundError(root)
        actual: list[dict[str, object]] = []
        for item in sorted(root.rglob("*")):
            if item.is_symlink():
                raise _method_pack_error(
                    "METHOD_PACK_OBJECT_LINK_INVALID", "Method Pack 对象包含符号链接。"
                )
            if item.is_dir():
                continue
            if not item.is_file():
                raise _method_pack_error(
                    "METHOD_PACK_OBJECT_INVALID", "Method Pack 对象包含非普通文件。"
                )
            content = item.read_bytes()
            actual.append(
                MethodPackFile(
                    path=item.relative_to(root).as_posix(),
                    sha256=sha256_bytes(content),
                    size_bytes=len(content),
                ).model_dump(mode="json")
            )
        return actual

    def _object_path(self, digest: Sha256) -> Path:
        path = self.objects / str(digest)[:2] / str(digest)
        self._check_store_child(path)
        return path

    def _snapshot_path(self, digest: Sha256) -> Path:
        path = self.root / "snapshots" / f"{digest}.json"
        self._check_store_child(path)
        return path

    def _check_store_child(self, path: Path) -> None:
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            for part in path.relative_to(self.root).parts:
                try:
                    entry = os.stat(part, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    break
                if (
                    stat.S_ISLNK(entry.st_mode)
                    or entry.st_uid != os.getuid()
                    or stat.S_IMODE(entry.st_mode) & 0o022
                ):
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_PATH_UNSAFE", "Method Pack Store 子路径不可信。"
                    )
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                if stat.S_ISDIR(entry.st_mode):
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=directory)
                metadata = os.fstat(child)
                if (entry.st_dev, entry.st_ino) != (metadata.st_dev, metadata.st_ino):
                    os.close(child)
                    raise _method_pack_error(
                        "METHOD_PACK_STORE_PATH_INVALID", "Method Pack Store 子路径被替换。"
                    )
                os.close(directory)
                directory = child
        finally:
            os.close(directory)

    @staticmethod
    def _verify_registry_integrity(integrity: str, archive: bytes) -> None:
        encoded = integrity.removeprefix("sha512-")
        try:
            expected = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise _method_pack_error(
                "METHOD_PACK_REGISTRY_INTEGRITY_INVALID",
                "Registry Integrity 不是有效的 sha512 SRI。",
            ) from error
        actual = hashlib.sha512(archive).digest()
        if not hmac.compare_digest(actual, expected):
            raise _method_pack_error(
                "METHOD_PACK_REGISTRY_INTEGRITY_MISMATCH",
                "Method Pack 归档未通过 Registry Integrity 校验。",
            )

    @staticmethod
    def _read_archive(
        request: MethodPackInstall,
        archive: bytes,
    ) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        total_bytes = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as stream:
                for member in stream.getmembers():
                    archive_path = _safe_archive_path(member.name)
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise _method_pack_error(
                            "METHOD_PACK_ARCHIVE_LINK_NOT_ALLOWED",
                            f"Method Pack 归档包含不允许的链接或特殊条目：{member.name}。",
                        )
                    relative = archive_path.removeprefix("package/")
                    if relative in files:
                        raise _method_pack_error(
                            "METHOD_PACK_ARCHIVE_DUPLICATE_PATH",
                            f"Method Pack 归档包含重复路径：{relative}。",
                        )
                    if len(files) + 1 > request.max_file_count:
                        raise _method_pack_error(
                            "METHOD_PACK_FILE_LIMIT_EXCEEDED",
                            "Method Pack 归档文件数超过策略上限。",
                        )
                    total_bytes += member.size
                    if member.size < 0 or total_bytes > request.max_unpacked_bytes:
                        raise _method_pack_error(
                            "METHOD_PACK_SIZE_LIMIT_EXCEEDED",
                            "Method Pack 解包体积超过策略上限。",
                        )
                    source = stream.extractfile(member)
                    if source is None:
                        raise _method_pack_error(
                            "METHOD_PACK_ARCHIVE_INVALID",
                            f"无法读取 Method Pack 条目：{member.name}。",
                        )
                    content = source.read(member.size + 1)
                    if len(content) != member.size:
                        raise _method_pack_error(
                            "METHOD_PACK_ARCHIVE_INVALID",
                            f"Method Pack 条目大小不一致：{member.name}。",
                        )
                    files[relative] = content
        except (tarfile.TarError, OSError) as error:
            raise _method_pack_error(
                "METHOD_PACK_ARCHIVE_INVALID",
                "Method Pack 不是可安全读取的 gzip tar 归档。",
            ) from error
        return files

    @staticmethod
    def _verify_package_identity(
        request: MethodPackInstall,
        files: dict[str, bytes],
    ) -> None:
        try:
            metadata = json.loads(files["package.json"])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _method_pack_error(
                "METHOD_PACK_PACKAGE_METADATA_INVALID",
                "Method Pack 缺少有效的 package.json。",
            ) from error
        if not isinstance(metadata, dict) or (
            metadata.get("name") != request.package_name
            or metadata.get("version") != request.package_version
        ):
            raise _method_pack_error(
                "METHOD_PACK_PACKAGE_IDENTITY_MISMATCH",
                "Method Pack 的包名或版本与冻结的 Registry 元数据不一致。",
            )

    @staticmethod
    def _verify_method_entries(
        request: MethodPackInstall,
        files: dict[str, bytes],
    ) -> None:
        available = set(files)
        for entry in request.method_entries:
            skill_path = f"{entry.source_path}/SKILL.md"
            if skill_path not in available:
                raise _method_pack_error(
                    "METHOD_ENTRY_MISSING",
                    f"Method Entry {entry.method_id} 缺少 SKILL.md。",
                )


class FrozenMethodPackSet:
    """Resolve a committed package lock to locally verified immutable objects."""

    def __init__(
        self,
        lock_file: Path,
        store: ContentAddressedMethodPackStore,
        *,
        lock_bytes: bytes | None = None,
    ) -> None:
        self.lock_file = lock_file.resolve()
        self.store = store
        self._lock_bytes = lock_bytes

    def snapshot(self) -> DeliveryMethodSnapshot:
        with self.store.read_lock():
            return self._snapshot_locked()

    def _snapshot_locked(self) -> DeliveryMethodSnapshot:
        try:
            lock = json.loads(
                self._lock_bytes
                if self._lock_bytes is not None
                else self.lock_file.read_bytes()
            )
        except (OSError, json.JSONDecodeError) as error:
            raise _method_pack_error(
                "METHOD_PACK_LOCK_INVALID",
                "Method Pack Lock 文件缺失或不是有效 JSON。",
            ) from error
        if not isinstance(lock, dict) or lock.get("policy_version") != "method-pack-store-v1":
            raise _method_pack_error(
                "METHOD_PACK_LOCK_INVALID",
                "Method Pack Lock Policy Version 不受支持。",
            )
        packages_raw = lock.get("packages")
        if not isinstance(packages_raw, list) or not packages_raw:
            raise _method_pack_error(
                "METHOD_PACK_LOCK_INVALID",
                "Method Pack Lock 没有冻结 Package。",
            )
        package_views: list[dict[str, object]] = []
        method_entries: dict[str, dict[str, object]] = {}
        for raw in packages_raw:
            if not isinstance(raw, dict):
                raise _method_pack_error(
                    "METHOD_PACK_LOCK_INVALID",
                    "Method Pack Lock Package 结构无效。",
                )
            expected_qualification = raw.get("expected_qualification_sha256")
            expected_content = raw.get("expected_content_sha256")
            install_raw = raw.get("install")
            if not isinstance(expected_qualification, str) or not isinstance(install_raw, dict):
                raise _method_pack_error(
                    "METHOD_PACK_LOCK_INVALID",
                    "Method Pack Lock 缺少 Qualification 或 Install 数据。",
                )
            snapshot = self.store._load_snapshot_locked(expected_qualification)
            install = MethodPackInstall.model_validate(install_raw)
            if (
                snapshot.package_name != install.package_name
                or snapshot.package_version != install.package_version
                or snapshot.archive_sha256 != install.archive_sha256
                or snapshot.registry_integrity != install.registry_integrity
                or snapshot.method_entries != install.method_entries
                or snapshot.content_sha256 != expected_content
            ):
                raise _method_pack_error(
                    "METHOD_PACK_LOCK_SNAPSHOT_MISMATCH",
                    f"Package {install.package_name}@{install.package_version} 与 Lock 不一致。",
                )
            package_view: dict[str, object] = {
                "package_name": snapshot.package_name,
                "package_version": snapshot.package_version,
                "archive_sha256": snapshot.archive_sha256,
                "content_sha256": snapshot.content_sha256,
                "qualification_sha256": snapshot.qualification_sha256,
                "store_uri": snapshot.store_uri,
            }
            package_views.append(package_view)
            for entry in snapshot.method_entries:
                if entry.method_id in method_entries:
                    raise _method_pack_error(
                        "METHOD_ENTRY_COLLISION",
                        f"Method Pack Set 中存在重复入口：{entry.method_id}。",
                    )
                method_entries[entry.method_id] = {
                    "package_name": snapshot.package_name,
                    "package_version": snapshot.package_version,
                    "source_path": entry.source_path,
                    "content_sha256": snapshot.content_sha256,
                    "qualification_sha256": snapshot.qualification_sha256,
                }
        qualification_payload = {
            "policy_version": "method-pack-set-v1",
            "packages": package_views,
            "method_entries": method_entries,
        }
        qualification = sha256_json(qualification_payload)
        return DeliveryMethodSnapshot(
            snapshot_id=f"method-pack-set-v1:{qualification}",
            qualification_sha256=qualification,
            packages=tuple(package_views),
            method_entries=method_entries,
        )


def _safe_archive_path(value: str) -> str:
    try:
        normalized = _safe_relative_path(value)
    except ValueError as error:
        raise _method_pack_error(
            "METHOD_PACK_ARCHIVE_PATH_INVALID",
            f"Method Pack 归档包含不安全路径：{value}。",
        ) from error
    if normalized == "package":
        return normalized
    if not normalized.startswith("package/"):
        raise _method_pack_error(
            "METHOD_PACK_ARCHIVE_ROOT_INVALID",
            f"Method Pack 条目不在 npm package 根目录：{value}。",
        )
    return normalized


def _safe_relative_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("backslashes are not allowed in package paths")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("package paths must be normalized and relative")
    return candidate.as_posix().rstrip("/")


def _promote_directory_no_replace(staging: Path, target: Path) -> None:
    """macOS POSIX same-volume atomic rename that refuses an existing target."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renamex_np", None)
    if rename is None:
        raise _method_pack_error(
            "METHOD_PACK_ATOMIC_PROMOTION_UNSUPPORTED",
            "当前平台缺少不覆盖目标的原子目录提升原语。",
        )
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(staging), os.fsencode(target), 0x00000004) != 0:
        error = ctypes.get_errno()
        raise _method_pack_error(
            "METHOD_PACK_ATOMIC_PROMOTION_FAILED",
            f"Method Pack 对象原子提升失败（errno={error}）。",
        )


def _make_tree_read_only(root: Path) -> None:
    for item in sorted(root.rglob("*"), key=lambda candidate: len(candidate.parts), reverse=True):
        item.chmod(0o555 if item.is_dir() else 0o444)
    root.chmod(0o555)


def _validated_codex_auth_file(value: Path) -> Path:
    try:
        source = value.expanduser().resolve(strict=True)
        metadata = source.stat()
    except OSError as error:
        raise _codex_auth_error(
            "CODEX_CREDENTIAL_REFERENCE_MISSING",
            "Codex Credential Reference 不存在或不可读取。",
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise _codex_auth_error(
            "CODEX_CREDENTIAL_REFERENCE_INVALID",
            "Codex Credential Reference 必须指向普通文件。",
        )
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise _codex_auth_error(
            "CODEX_CREDENTIAL_REFERENCE_OWNER_INVALID",
            "Codex Credential Reference 必须由当前运行用户持有。",
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise _codex_auth_error(
            "CODEX_CREDENTIAL_REFERENCE_PERMISSIONS_INVALID",
            "Codex Credential Reference 不能向 Group 或 Other 开放权限。",
        )
    return source


def _remove_tree(root: Path) -> None:
    for item in root.rglob("*"):
        if item.is_symlink():
            continue
        if item.is_dir():
            item.chmod(0o755)
        else:
            item.chmod(0o644)
    root.chmod(0o755)
    shutil.rmtree(root)


def _method_pack_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Method Pack 资格检查失败",
        detail=detail,
        repair="重新获取锁定版本并检查 Registry、归档和 Method Entry 配置。",
        status_code=409,
    )


def _codex_auth_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Codex Credential Reference 无效",
        detail=detail,
        repair=(
            "设置 AGENT_TEAM_OS_CODEX_AUTH_FILE，或在 CODEX_HOME/default Codex Home 中完成登录；"
            "凭据文件必须仅当前用户可读写。"
        ),
        status_code=409,
    )
