from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
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


class ContentAddressedMethodPackStore:
    """Verify npm archives and expose selected skills through an ephemeral Codex home."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.objects = self.root / "objects" / "sha256"
        self.objects.mkdir(parents=True, exist_ok=True)

    def install_archive(
        self,
        request: MethodPackInstall,
        archive: bytes,
    ) -> MethodPackSnapshot:
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
        content_digest = sha256_json(
            [item.model_dump(mode="json") for item in file_manifest]
        )
        qualification_digest = sha256_json(
            {
                "package_name": request.package_name,
                "package_version": request.package_version,
                "tarball_uri": request.tarball_uri,
                "registry_integrity": request.registry_integrity,
                "archive_sha256": request.archive_sha256,
                "content_sha256": content_digest,
                "method_entries": [
                    item.model_dump(mode="json") for item in request.method_entries
                ],
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
        target = self._object_path(content_digest)
        if target.exists():
            self._verify_object(snapshot)
            self._persist_snapshot(snapshot)
            return snapshot
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{content_digest}.", dir=self.objects))
        try:
            for name, content in files.items():
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            try:
                os.replace(staging, target)
            except OSError:
                if not target.exists():
                    raise
            _make_tree_read_only(target)
            self._verify_object(snapshot)
            self._persist_snapshot(snapshot)
        finally:
            if staging.exists():
                _remove_tree(staging)
        return snapshot

    def load_snapshot(self, qualification_sha256: str) -> MethodPackSnapshot:
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
        overlay_root = Path(tempfile.mkdtemp(prefix="overlay-", dir=self.root))
        codex_home = overlay_root / "codex-home"
        skills_root = codex_home / "skills"
        skills_root.mkdir(parents=True)
        installed_ids: set[str] = set()
        environment = {"CODEX_HOME": str(codex_home)}
        try:
            for snapshot in snapshots:
                self._verify_object(snapshot)
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
                    environment["AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE"] = str(
                        runtime_source
                    )
                for entry in snapshot.method_entries:
                    if entry.method_id in installed_ids:
                        raise _method_pack_error(
                            "METHOD_ENTRY_COLLISION",
                            f"Runtime Overlay 中存在重复 Method Entry：{entry.method_id}。",
                        )
                    installed_ids.add(entry.method_id)
                    source = object_root / entry.source_path
                    shutil.copytree(source, skills_root / entry.method_id)
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
            if overlay_root.exists():
                _remove_tree(overlay_root)

    def _verify_object(self, snapshot: MethodPackSnapshot) -> None:
        root = self._object_path(snapshot.content_sha256)
        actual: list[dict[str, object]] = []
        try:
            for expected in snapshot.files:
                source = root / expected.path
                content = source.read_bytes()
                if len(content) != expected.size_bytes or sha256_bytes(content) != expected.sha256:
                    raise _method_pack_error(
                        "METHOD_PACK_CONTENT_HASH_MISMATCH",
                        f"Method Pack 对象 {snapshot.content_sha256} 的文件完整性校验失败。",
                    )
                actual.append(expected.model_dump(mode="json"))
        except FileNotFoundError as error:
            raise _method_pack_error(
                "METHOD_PACK_OBJECT_MISSING",
                f"Method Pack 对象 {snapshot.content_sha256} 不完整。",
            ) from error
        if sha256_json(actual) != snapshot.content_sha256:
            raise _method_pack_error(
                "METHOD_PACK_CONTENT_HASH_MISMATCH",
                f"Method Pack 对象 {snapshot.content_sha256} 与 Snapshot 不一致。",
            )

    def _object_path(self, digest: Sha256) -> Path:
        return self.objects / str(digest)[:2] / str(digest)

    def _snapshot_path(self, digest: Sha256) -> Path:
        return self.root / "snapshots" / f"{digest}.json"

    def _persist_snapshot(self, snapshot: MethodPackSnapshot) -> None:
        destination = self._snapshot_path(snapshot.qualification_sha256)
        payload = snapshot.model_dump_json(indent=2)
        if destination.exists():
            if destination.read_text(encoding="utf-8") != payload:
                raise _method_pack_error(
                    "METHOD_PACK_SNAPSHOT_HASH_MISMATCH",
                    "同一 Qualification Hash 已存在不同 Snapshot。",
                )
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")

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
    ) -> None:
        self.lock_file = lock_file.resolve()
        self.store = store

    def snapshot(self) -> DeliveryMethodSnapshot:
        try:
            lock = json.loads(self.lock_file.read_text(encoding="utf-8"))
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
            snapshot = self.store.load_snapshot(expected_qualification)
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
