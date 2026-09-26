import base64
import errno
import hashlib
import io
import os
import select
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.control_plane import AgentInstanceCreate, ControlPlaneService, HealthResult
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentDeploymentCreate,
    AgentProfileCatalog,
    AgentProfileCreate,
    AgentProfileSpec,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
)
from agent_team_os.modules.extensions import (
    ContentAddressedMethodPackStore,
    FrozenMethodPackSet,
    MethodEntry,
    MethodPackInstall,
    RuntimeExtensionCatalog,
    RuntimeExtensionInstall,
    SQLiteRuntimeExtensionRepository,
)
from agent_team_os.modules.extensions import method_packs as method_pack_module
from agent_team_os.shared.errors import ProductError
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


def test_verified_method_pack_is_materialized_as_a_temporary_read_only_codex_overlay(
    tmp_path: Path,
) -> None:
    archive = _method_pack_archive(
        {
            "package/src/bmm-skills/ship/bmad-build/SKILL.md": b"# BMAD Build\n",
            "package/src/bmm-skills/ship/bmad-build/workflow.md": b"Build workflow\n",
            "package/src/scripts/render_skill.py": b"# renderer\n",
            "package/src/scripts/config_utils.py": b"# config helpers\n",
            "package/package.json": b'{"name":"bmad-method","version":"6.11.0"}\n',
        }
    )
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    registry_integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode(
        "ascii"
    )
    store = ContentAddressedMethodPackStore(tmp_path / "method-packs")

    snapshot = store.install_archive(
        MethodPackInstall(
            package_name="bmad-method",
            package_version="6.11.0",
            tarball_uri="https://registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz",
            registry_integrity=registry_integrity,
            archive_sha256=archive_sha256,
            method_entries=(
                MethodEntry(
                    method_id="bmad-build",
                    source_path="src/bmm-skills/ship/bmad-build",
                ),
            ),
        ),
        archive,
    )

    assert snapshot.package_name == "bmad-method"
    assert snapshot.archive_sha256 == archive_sha256
    assert snapshot.qualification_sha256
    auth_file = tmp_path / "operator-auth.json"
    auth_file.write_text('{"auth_mode":"test"}\n', encoding="utf-8")
    auth_file.chmod(0o600)
    with store.runtime_overlay((snapshot,), codex_auth_file=auth_file) as overlay:
        skill = overlay.codex_home / "skills" / "bmad-build" / "SKILL.md"
        auth_reference = overlay.codex_home / "auth.json"
        assert skill.read_text(encoding="utf-8") == "# BMAD Build\n"
        assert (overlay.codex_home / "config.toml").read_text(encoding="utf-8") == (
            "[features]\nmulti_agent = false\n"
        )
        assert overlay.environment == {
            "AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE": str(
                store._object_path(snapshot.content_sha256) / "src"  # noqa: SLF001
            ),
            "CODEX_HOME": str(overlay.codex_home),
        }
        assert skill.stat().st_mode & 0o222 == 0
        assert auth_reference.is_symlink()
        assert auth_reference.resolve() == auth_file.resolve()
        overlay_root = overlay.root
    assert not overlay_root.exists()
    assert auth_file.read_text(encoding="utf-8") == '{"auth_mode":"test"}\n'
    assert auth_file.stat().st_mode & 0o777 == 0o600

    auth_file.chmod(0o644)
    with (
        pytest.raises(ProductError) as error,
        store.runtime_overlay((snapshot,), codex_auth_file=auth_file),
    ):
        pass
    assert error.value.code == "CODEX_CREDENTIAL_REFERENCE_PERMISSIONS_INVALID"


def _method_pack_archive(files: dict[str, bytes]) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return payload.getvalue()


def _simple_method_pack(name: str) -> tuple[MethodPackInstall, bytes]:
    archive = _method_pack_archive(
        {
            "package/package.json": ('{"name":"' + name + '","version":"1.0.0"}').encode(),
            "package/skill/SKILL.md": b"# Test\n",
        }
    )
    request = MethodPackInstall(
        package_name=name,
        package_version="1.0.0",
        tarball_uri=f"https://registry.npmjs.org/{name}/-/{name}-1.0.0.tgz",
        registry_integrity="sha512-"
        + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii"),
        archive_sha256=hashlib.sha256(archive).hexdigest(),
        method_entries=(MethodEntry(method_id=name, source_path="skill"),),
    )
    return request, archive


def _store_identity(root: Path) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for path in (root, *root.rglob("*")):
        metadata = path.lstat()
        digest = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if stat.S_ISREG(metadata.st_mode)
            else ""
        )
        result[path.relative_to(root).as_posix()] = (
            metadata.st_ino,
            stat.S_IMODE(metadata.st_mode),
            digest,
        )
    return result


@pytest.mark.parametrize("missing_lock", [False, True])
def test_existing_pending_store_constructor_has_no_early_write(
    tmp_path: Path, missing_lock: bool
) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    if not missing_lock:
        lock = root / ".install.lock"
        lock.touch(mode=0o600)
    (root / ".install-in-progress").write_text("pending", encoding="utf-8")
    before = _store_identity(root)
    with pytest.raises(ProductError) as error:
        ContentAddressedMethodPackStore(root)
    assert error.value.code == "METHOD_PACK_STORE_PENDING"
    assert _store_identity(root) == before


@pytest.mark.parametrize("operation", ["load", "frozen", "overlay"])
def test_pending_with_missing_structure_and_lock_blocks_reader_without_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = ContentAddressedMethodPackStore(root)
    request, archive = _simple_method_pack("reader")
    snapshot = store.prepare_archive(request, archive).snapshot
    (root / ".install-in-progress").write_text("pending", encoding="utf-8")
    before = _store_identity(root)
    lock_file = tmp_path / "method-lock.json"
    lock_file.write_text('{"policy_version":"method-pack-store-v1","packages":[]}')
    if operation == "overlay":
        monkeypatch.setattr(
            method_pack_module.tempfile,
            "mkdtemp",
            lambda *args, **kwargs: pytest.fail("Overlay 在 pending 检查前创建临时目录"),
        )
    with pytest.raises(ProductError) as error:
        if operation == "load":
            store.load_snapshot(snapshot.qualification_sha256)
        elif operation == "frozen":
            FrozenMethodPackSet(lock_file, store).snapshot()
        else:
            with store.runtime_overlay((snapshot,)):
                pass
    assert error.value.code == "METHOD_PACK_STORE_PENDING"
    assert _store_identity(root) == before


def test_existing_private_store_missing_structure_is_not_repaired_by_reader(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    before = _store_identity(root)
    store = ContentAddressedMethodPackStore(root)
    assert _store_identity(root) == before
    with pytest.raises(ProductError) as error:
        store.load_snapshot("a" * 64)
    assert error.value.code == "METHOD_PACK_STORE_LOCK_MISSING"
    assert _store_identity(root) == before


@pytest.mark.parametrize("operation", ["construct", "read_lock", "load", "frozen", "overlay"])
def test_absent_store_reader_never_creates_root(
    tmp_path: Path, operation: str
) -> None:
    root = tmp_path / "method-packs"
    store = ContentAddressedMethodPackStore(root)
    assert not root.exists()
    if operation != "construct":
        request, archive = _simple_method_pack("reader")
        snapshot = store.prepare_archive(request, archive).snapshot
        lock_file = tmp_path / "method-lock.json"
        lock_file.write_text('{"policy_version":"method-pack-store-v1","packages":[]}')
        with pytest.raises(ProductError):
            if operation == "read_lock":
                with store.read_lock():
                    pass
            elif operation == "load":
                store.load_snapshot(snapshot.qualification_sha256)
            elif operation == "frozen":
                FrozenMethodPackSet(lock_file, store).snapshot()
            else:
                with store.runtime_overlay((snapshot,)):
                    pass
    assert not root.exists()


def test_new_store_is_created_only_by_writer_install(tmp_path: Path) -> None:
    root = tmp_path / "method-packs"
    store = ContentAddressedMethodPackStore(root)
    assert not root.exists()
    request, archive = _simple_method_pack("writer")
    snapshot = store.install_archive(request, archive)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / ".install.lock").stat().st_mode) == 0o600
    assert store.load_snapshot(snapshot.qualification_sha256) == snapshot


def test_writer_does_not_create_missing_parent_chain(tmp_path: Path) -> None:
    root = tmp_path / "absent-parent" / "method-packs"
    store = ContentAddressedMethodPackStore(root)
    assert not root.parent.exists()
    request, archive = _simple_method_pack("writer")
    with pytest.raises(ProductError):
        store.install_archive(request, archive)
    assert not root.parent.exists()


def test_overlay_does_not_create_temp_root_before_missing_object_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    store = ContentAddressedMethodPackStore(root)
    request, archive = _simple_method_pack("reader")
    snapshot = store.install_archive(request, archive)
    object_root = store._object_path(snapshot.content_sha256)
    method_pack_module._remove_tree(object_root)
    before = _store_identity(root)
    monkeypatch.setattr(
        method_pack_module.tempfile,
        "mkdtemp",
        lambda *args, **kwargs: pytest.fail("Overlay 验证缺失对象前创建临时根"),
    )
    with pytest.raises(ProductError), store.runtime_overlay((snapshot,)):
        pass
    assert _store_identity(root) == before


def test_new_store_writer_initializes_under_exclusive_pending_contract(tmp_path: Path) -> None:
    root = tmp_path / "store"
    store = ContentAddressedMethodPackStore(root)
    assert not root.exists()
    request, archive = _simple_method_pack("writer")
    snapshot = store.install_archive(request, archive)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    lock = root / ".install.lock"
    assert lock.is_file() and not lock.is_symlink()
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert store.load_snapshot(snapshot.qualification_sha256) == snapshot
    assert not (root / ".install-in-progress").exists()


def test_method_pack_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive = _method_pack_archive(
        {
            "package/../escape.txt": b"escape",
            "package/package.json": b'{"name":"bmad-method","version":"6.11.0"}\n',
            "package/src/bmad/SKILL.md": b"# Entry\n",
        }
    )
    request = MethodPackInstall(
        package_name="bmad-method",
        package_version="6.11.0",
        tarball_uri="https://registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz",
        registry_integrity="sha512-"
        + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii"),
        archive_sha256=hashlib.sha256(archive).hexdigest(),
        method_entries=(MethodEntry(method_id="bmad", source_path="src/bmad"),),
    )

    with pytest.raises(ProductError) as error:
        ContentAddressedMethodPackStore(tmp_path / "store").install_archive(request, archive)

    assert error.value.code == "METHOD_PACK_ARCHIVE_PATH_INVALID"
    assert not (tmp_path / "escape.txt").exists()


def test_method_pack_pending_blocks_public_snapshot_read(tmp_path: Path) -> None:
    archive = _method_pack_archive(
        {
            "package/package.json": b'{"name":"bmad-method","version":"6.11.0"}',
            "package/src/bmad/SKILL.md": b"# Entry\n",
        }
    )
    request = MethodPackInstall(
        package_name="bmad-method",
        package_version="6.11.0",
        tarball_uri="https://registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz",
        registry_integrity="sha512-"
        + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii"),
        archive_sha256=hashlib.sha256(archive).hexdigest(),
        method_entries=(MethodEntry(method_id="bmad", source_path="src/bmad"),),
    )
    store = ContentAddressedMethodPackStore(tmp_path / "store")
    snapshot = store.install_archive(request, archive)
    (store.root / ".install-in-progress").write_text("incomplete", encoding="utf-8")
    with pytest.raises(ProductError, match="pending|进行中|未完成"):
        store.load_snapshot(snapshot.qualification_sha256)
    with pytest.raises(ProductError) as error:
        store.install_archive(request, archive)
    assert error.value.code == "METHOD_PACK_STORE_PENDING"


def test_runtime_overlay_pending_or_writer_lock_creates_no_early_store_files(
    tmp_path: Path,
) -> None:
    store = ContentAddressedMethodPackStore(tmp_path / "store")
    request, archive = _simple_method_pack("overlay")
    snapshot = store.install_archive(request, archive)
    pending = store.root / ".install-in-progress"
    pending.write_text("interrupted", encoding="utf-8")
    with pytest.raises(ProductError) as error, store.runtime_overlay((snapshot,)):
        pass
    assert error.value.code == "METHOD_PACK_STORE_PENDING"
    assert not list(store.root.glob("overlay-*"))
    pending.unlink()

    started = threading.Event()
    finished = threading.Event()

    def enter_overlay() -> None:
        started.set()
        with store.runtime_overlay((snapshot,)):
            finished.set()

    with store._lock(exclusive=True):  # noqa: SLF001
        worker = threading.Thread(target=enter_overlay)
        worker.start()
        assert started.wait(2)
        assert not finished.wait(0.1)
        assert not list(store.root.glob("overlay-*"))
    worker.join(timeout=2)
    assert not worker.is_alive() and finished.is_set()
    assert not list(store.root.glob("overlay-*"))


def test_method_pack_false_registry_sri_fails_as_product_error(tmp_path: Path) -> None:
    archive = _method_pack_archive(
        {
            "package/package.json": b'{"name":"bmad-method","version":"6.11.0"}',
            "package/src/bmad/SKILL.md": b"# Entry\n",
        }
    )
    request = MethodPackInstall(
        package_name="bmad-method",
        package_version="6.11.0",
        tarball_uri="https://registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz",
        registry_integrity="sha512-"
        + base64.b64encode(hashlib.sha512(b"wrong").digest()).decode("ascii"),
        archive_sha256=hashlib.sha256(archive).hexdigest(),
        method_entries=(MethodEntry(method_id="bmad", source_path="src/bmad"),),
    )
    with pytest.raises(ProductError) as error:
        ContentAddressedMethodPackStore(tmp_path / "store").install_archive(request, archive)
    assert error.value.code == "METHOD_PACK_REGISTRY_INTEGRITY_MISMATCH"


def test_failed_batch_only_rolls_back_new_object_and_preserves_old_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def package(name: str) -> tuple[MethodPackInstall, bytes]:
        archive = _method_pack_archive(
            {
                "package/package.json": ('{"name":"' + name + '","version":"1.0.0"}').encode(),
                "package/skill/SKILL.md": b"# Test\n",
            }
        )
        request = MethodPackInstall(
            package_name=name,
            package_version="1.0.0",
            tarball_uri=f"https://registry.npmjs.org/{name}/-/{name}-1.0.0.tgz",
            registry_integrity="sha512-"
            + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii"),
            archive_sha256=hashlib.sha256(archive).hexdigest(),
            method_entries=(MethodEntry(method_id=name, source_path="skill"),),
        )
        return request, archive

    store = ContentAddressedMethodPackStore(tmp_path / "store")
    old_request, old_archive = package("old")
    old = store.install_archive(old_request, old_archive)
    old_path = store._snapshot_path(old.qualification_sha256)  # noqa: SLF001
    old_bytes, old_inode = old_path.read_bytes(), old_path.stat().st_ino
    first_request, first_archive = package("first")
    second_request, second_archive = package("second")
    first = store.prepare_archive(first_request, first_archive)
    second = store.prepare_archive(second_request, second_archive)
    original = store._persist_prepared_locked  # noqa: SLF001
    calls = 0

    def fail_second(item, created):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected-second-package-failure")
        return original(item, created)

    monkeypatch.setattr(store, "_persist_prepared_locked", fail_second)
    with pytest.raises(OSError, match="injected-second-package-failure"):
        store._commit_prepared((first, second))  # noqa: SLF001
    assert old_path.read_bytes() == old_bytes and old_path.stat().st_ino == old_inode
    assert not store._object_path(first.snapshot.content_sha256).exists()  # noqa: SLF001
    assert not (store.root / ".install-in-progress").exists()
    assert store.load_snapshot(old.qualification_sha256) == old


def test_no_replace_race_never_overwrites_or_deletes_foreign_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ContentAddressedMethodPackStore(tmp_path / "store")
    request, archive = _simple_method_pack("race")
    prepared = store.prepare_archive(request, archive)
    with store._lock(exclusive=True):
        pass
    target = store._object_path(prepared.snapshot.content_sha256)  # noqa: SLF001
    original = method_pack_module._promote_directory_no_replace  # noqa: SLF001

    def occupy_target(staging: Path, destination: Path) -> None:
        assert destination == target
        destination.mkdir(mode=0o700)
        (destination / "foreign").write_bytes(b"do-not-delete")
        original(staging, destination)

    monkeypatch.setattr(method_pack_module, "_promote_directory_no_replace", occupy_target)
    with pytest.raises(ProductError):
        store.install_archive(request, archive)
    assert (target / "foreign").read_bytes() == b"do-not-delete"
    assert (store.root / ".install-in-progress").is_file()
    with pytest.raises(ProductError) as error:
        store.load_snapshot(prepared.snapshot.qualification_sha256)
    assert error.value.code == "METHOD_PACK_STORE_PENDING"


@pytest.mark.parametrize("target_kind", ("empty", "nonempty", "symlink"))
def test_native_directory_promotion_never_replaces_an_occupied_target(
    tmp_path: Path, target_kind: str
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "payload").write_bytes(b"new")
    target = tmp_path / "target"
    if target_kind == "symlink":
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / "payload").write_bytes(b"foreign")
        target.symlink_to(foreign, target_is_directory=True)
    else:
        target.mkdir()
        if target_kind == "nonempty":
            (target / "payload").write_bytes(b"foreign")
    target_inode = target.lstat().st_ino
    source_inode = staging.stat().st_ino
    with pytest.raises(ProductError):
        method_pack_module._promote_directory_no_replace(staging, target)  # noqa: SLF001
    assert target.lstat().st_ino == target_inode
    assert target.is_symlink() == (target_kind == "symlink")
    if target_kind == "empty":
        assert not list(target.iterdir())
    else:
        assert (target / "payload").read_bytes() == b"foreign"
    assert staging.stat().st_ino == source_inode
    assert (staging / "payload").read_bytes() == b"new"


def test_linux_directory_promotion_uses_noreplace_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[int, bytes, int, bytes, int]] = []

    class FakeRenameAt2:
        argtypes = None
        restype = None

        def __call__(self, *arguments):
            calls.append(arguments)
            return 0

    rename = FakeRenameAt2()
    monkeypatch.setattr(method_pack_module, "sys", SimpleNamespace(platform="linux"), raising=False)
    monkeypatch.setattr(
        method_pack_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(renameat2=rename),
    )
    staging, target = tmp_path / "staging", tmp_path / "target"
    method_pack_module._promote_directory_no_replace(staging, target)  # noqa: SLF001
    assert calls == [(-100, os.fsencode(staging), -100, os.fsencode(target), 1)]
    assert len(rename.argtypes) == 5
    assert rename.restype is method_pack_module.ctypes.c_int


@pytest.mark.parametrize(
    "unsupported_errno", (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EXDEV)
)
def test_linux_directory_promotion_without_supported_flag_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsupported_errno: int
) -> None:
    class FakeRenameAt2:
        argtypes = None
        restype = None

        def __call__(self, *_arguments):
            return -1

    monkeypatch.setattr(method_pack_module, "sys", SimpleNamespace(platform="linux"), raising=False)
    monkeypatch.setattr(
        method_pack_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(renameat2=FakeRenameAt2()),
    )
    monkeypatch.setattr(method_pack_module.ctypes, "get_errno", lambda: unsupported_errno)
    with pytest.raises(ProductError) as error:
        method_pack_module._promote_directory_no_replace(  # noqa: SLF001
            tmp_path / "staging", tmp_path / "target"
        )
    assert error.value.code == "METHOD_PACK_ATOMIC_PROMOTION_UNSUPPORTED"


def test_linux_directory_promotion_without_symbol_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(method_pack_module, "sys", SimpleNamespace(platform="linux"), raising=False)
    monkeypatch.setattr(
        method_pack_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    with pytest.raises(ProductError) as error:
        method_pack_module._promote_directory_no_replace(  # noqa: SLF001
            tmp_path / "staging", tmp_path / "target"
        )
    assert error.value.code == "METHOD_PACK_ATOMIC_PROMOTION_UNSUPPORTED"


def test_snapshot_link_failure_rolls_back_only_new_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ContentAddressedMethodPackStore(tmp_path / "store")
    old_request, old_archive = _simple_method_pack("old")
    old = store.install_archive(old_request, old_archive)
    old_path = store._snapshot_path(old.qualification_sha256)  # noqa: SLF001
    old_bytes, old_inode = old_path.read_bytes(), old_path.stat().st_ino
    request, archive = _simple_method_pack("new")
    prepared = store.prepare_archive(request, archive)

    def fail_link(*_args, **_kwargs) -> None:
        raise OSError("injected-snapshot-link-failure")

    monkeypatch.setattr(method_pack_module.os, "link", fail_link)
    with pytest.raises(OSError, match="injected-snapshot-link-failure"):
        store.install_archive(request, archive)
    assert old_path.read_bytes() == old_bytes and old_path.stat().st_ino == old_inode
    assert not store._object_path(prepared.snapshot.content_sha256).exists()  # noqa: SLF001
    assert not store._snapshot_path(prepared.snapshot.qualification_sha256).exists()  # noqa: SLF001
    assert not (store.root / ".install-in-progress").exists()
    assert store.load_snapshot(old.qualification_sha256) == old


def test_sigkill_writer_leaves_pending_and_blocks_next_reader(tmp_path: Path) -> None:
    store = ContentAddressedMethodPackStore(tmp_path / "store")
    script = (
        "import os,sys,time; from pathlib import Path; "
        "from agent_team_os.modules.extensions import ContentAddressedMethodPackStore; "
        "store=ContentAddressedMethodPackStore(Path(sys.argv[1])); "
        "with_lock=store._lock(exclusive=True); with_lock.__enter__(); "
        "fd=os.open(store.root/'.install-in-progress', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600); "
        "os.close(fd); print('locked',flush=True); time.sleep(60)"
    )
    child = subprocess.Popen(
        (sys.executable, "-c", script, str(store.root)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
    finally:
        child.kill()
        child.communicate(timeout=5)
    with pytest.raises(ProductError) as error:
        store.load_snapshot("a" * 64)
    assert error.value.code == "METHOD_PACK_STORE_PENDING"


@pytest.mark.parametrize(
    ("first_exclusive", "second_exclusive", "second_can_enter"),
    [(False, False, True), (False, True, False), (True, True, False)],
)
def test_two_processes_share_or_exclude_the_same_store_lock(
    tmp_path: Path,
    first_exclusive: bool,
    second_exclusive: bool,
    second_can_enter: bool,
) -> None:
    store = ContentAddressedMethodPackStore(tmp_path / "store")
    with store._lock(exclusive=True):
        pass
    script = (
        "import sys,time; from pathlib import Path; "
        "from agent_team_os.modules.extensions import ContentAddressedMethodPackStore; "
        "s=ContentAddressedMethodPackStore(Path(sys.argv[1])); "
        "c=s._lock(exclusive=sys.argv[2]=='1'); c.__enter__(); "
        "print('locked',flush=True); time.sleep(60)"
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }

    def launch(exclusive: bool) -> subprocess.Popen[str]:
        return subprocess.Popen(
            (sys.executable, "-c", script, str(store.root), "1" if exclusive else "0"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

    first = launch(first_exclusive)
    second = None
    try:
        assert first.stdout is not None
        assert select.select([first.stdout], [], [], 3)[0]
        assert first.stdout.readline().strip() == "locked"
        second = launch(second_exclusive)
        assert second.stdout is not None
        can_enter = bool(select.select([second.stdout], [], [], 0.5)[0])
        assert can_enter is second_can_enter
        if can_enter:
            assert second.stdout.readline().strip() == "locked"
        first.kill()
        first.communicate(timeout=5)
        if not can_enter:
            assert select.select([second.stdout], [], [], 3)[0]
            assert second.stdout.readline().strip() == "locked"
    finally:
        if first.poll() is None:
            first.kill()
        first.communicate(timeout=5)
        if second is not None:
            if second.poll() is None:
                second.kill()
            second.communicate(timeout=5)


def test_source_preview_default_0755_data_root_creates_private_store(tmp_path: Path) -> None:
    data_root = tmp_path / ".agent-team-os"
    data_root.mkdir(mode=0o755)
    store = ContentAddressedMethodPackStore(data_root / "method-packs")
    assert not store.root.exists()
    request, archive = _simple_method_pack("writer")
    store.install_archive(request, archive)
    assert store.root.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("mode", [0o775, 0o777])
def test_writer_rejects_group_or_world_writable_parent(tmp_path: Path, mode: int) -> None:
    parent = tmp_path / "unsafe"
    parent.mkdir(mode=mode)
    parent.chmod(mode)
    with pytest.raises(ProductError) as error:
        ContentAddressedMethodPackStore(parent / "store")
    assert error.value.code == "METHOD_PACK_STORE_PATH_UNSAFE"


def test_existing_public_store_is_not_silently_chmodded(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    store_root.mkdir(mode=0o755)
    with pytest.raises(ProductError) as error:
        ContentAddressedMethodPackStore(store_root)
    assert error.value.code == "METHOD_PACK_STORE_PATH_UNSAFE"
    assert store_root.stat().st_mode & 0o777 == 0o755


def test_store_rejects_symlink_and_foreign_uid_parent(tmp_path: Path, monkeypatch) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ProductError):
        ContentAddressedMethodPackStore(alias / "store")

    parent = tmp_path / "foreign"
    parent.mkdir(mode=0o700)
    actual_uid = os.getuid()
    with monkeypatch.context() as patcher:
        patcher.setattr(method_pack_module.os, "getuid", lambda: actual_uid + 10_000)
        with pytest.raises(ProductError) as error:
            ContentAddressedMethodPackStore(parent / "store")
    assert error.value.code == "METHOD_PACK_STORE_PATH_UNSAFE"


def test_store_detects_parent_inode_replacement_during_creation(
    tmp_path: Path, monkeypatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o755)
    original_mkdir = os.mkdir

    def replace_parent_before_store_creation(path, *args, **kwargs):
        if path == "store" and kwargs.get("dir_fd") is not None:
            parent.rename(tmp_path / "parent-old")
            original_mkdir(parent, 0o755)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(method_pack_module.os, "mkdir", replace_parent_before_store_creation)
    store = ContentAddressedMethodPackStore(parent / "store")
    with pytest.raises(ProductError) as error:
        request, archive = _simple_method_pack("writer")
        store.install_archive(request, archive)
    assert error.value.code == "METHOD_PACK_STORE_PATH_INVALID"
    assert not (parent / "store").exists()
    assert not (tmp_path / "parent-old" / "store").exists()


def test_first_root_child_open_replacement_does_not_chmod_or_delete_other_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o755)
    original_open = os.open
    replacement: Path = parent / "store"
    owned_root = parent / "created-by-writer"
    injected = False

    def replace_before_child_open(path, flags, *args, **kwargs):
        nonlocal injected
        if path == "store" and kwargs.get("dir_fd") is not None and not injected:
            injected = True
            replacement.rename(owned_root)
            replacement.mkdir(mode=0o500)
            replacement.chmod(0o500)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(method_pack_module.os, "open", replace_before_child_open)
    store = ContentAddressedMethodPackStore(replacement)
    request, archive = _simple_method_pack("writer")
    with pytest.raises(ProductError) as error:
        store.install_archive(request, archive)
    assert injected
    assert error.value.code == "METHOD_PACK_STORE_RECOVERY_REQUIRED"
    assert replacement.is_dir()
    assert replacement.stat().st_mode & 0o777 == 0o500
    assert owned_root.is_dir()
    assert list(owned_root.iterdir()) == []
    assert list(replacement.iterdir()) == []


def test_first_root_non_private_mode_is_not_chmodded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o755)
    store_root = parent / "store"
    original_mkdir = os.mkdir

    def create_restricted_root(path, mode=0o777, *args, **kwargs):
        if path == "store" and kwargs.get("dir_fd") is not None:
            mode = 0o500
        return original_mkdir(path, mode, *args, **kwargs)

    monkeypatch.setattr(method_pack_module.os, "mkdir", create_restricted_root)
    store = ContentAddressedMethodPackStore(store_root)
    request, archive = _simple_method_pack("writer")
    with pytest.raises(ProductError) as error:
        store.install_archive(request, archive)
    assert error.value.code == "METHOD_PACK_STORE_RECOVERY_REQUIRED"
    assert store_root.is_dir()
    assert store_root.stat().st_mode & 0o777 == 0o500
    assert list(store_root.iterdir()) == []


def test_first_root_child_open_nonempty_replacement_is_not_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o755)
    store_root = parent / "store"
    owned_root = parent / "created-by-writer"
    original_open = os.open
    injected = False

    def replace_before_child_open(path, flags, *args, **kwargs):
        nonlocal injected
        if path == "store" and kwargs.get("dir_fd") is not None and not injected:
            injected = True
            store_root.rename(owned_root)
            store_root.mkdir(mode=0o700)
            (store_root / "existing-marker").write_text("existing", encoding="utf-8")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(method_pack_module.os, "open", replace_before_child_open)
    store = ContentAddressedMethodPackStore(store_root)
    request, archive = _simple_method_pack("writer")
    with pytest.raises(ProductError) as error:
        store.install_archive(request, archive)
    assert injected
    assert error.value.code == "METHOD_PACK_STORE_RECOVERY_REQUIRED"
    assert (store_root / "existing-marker").read_text(encoding="utf-8") == "existing"
    assert list(owned_root.iterdir()) == []
    assert sorted(path.name for path in store_root.iterdir()) == ["existing-marker"]


def test_first_root_cleanup_uncertain_requires_manual_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o755)
    original_mkdir = os.mkdir
    original_rmdir = os.rmdir

    def replace_parent(path, *args, **kwargs):
        if path == "store" and kwargs.get("dir_fd") is not None:
            parent.rename(tmp_path / "parent-old")
            original_mkdir(parent, 0o755)
        return original_mkdir(path, *args, **kwargs)

    def reject_owned_cleanup(path, *args, **kwargs):
        if path == "store" and kwargs.get("dir_fd") is not None:
            raise PermissionError("injected-rmdir-denial")
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(method_pack_module.os, "mkdir", replace_parent)
    monkeypatch.setattr(method_pack_module.os, "rmdir", reject_owned_cleanup)
    store = ContentAddressedMethodPackStore(parent / "store")
    request, archive = _simple_method_pack("writer")
    with pytest.raises(ProductError) as error:
        store.install_archive(request, archive)
    assert error.value.code == "METHOD_PACK_STORE_RECOVERY_REQUIRED"
    assert not (parent / "store").exists()
    residual = tmp_path / "parent-old" / "store"
    assert residual.is_dir()
    assert list(residual.iterdir()) == []


def test_root_owned_sticky_tmp_ancestor_allows_private_store() -> None:
    candidates = (Path("/private/tmp"), Path("/tmp"))
    sticky_parent = next(
        (
            candidate
            for candidate in candidates
            if not candidate.is_symlink()
            and candidate.is_dir()
            and candidate.stat().st_uid == 0
            and candidate.stat().st_mode & 0o7777 == 0o1777
        ),
        None,
    )
    assert sticky_parent is not None, "没有可验证的 root-owned 01777 临时祖先"
    with tempfile.TemporaryDirectory(prefix="atos-method-lock-", dir=sticky_parent) as root:
        store = ContentAddressedMethodPackStore(Path(root) / "store")
        assert not store.root.exists()
        request, archive = _simple_method_pack("writer")
        store.install_archive(request, archive)
        assert store.root.stat().st_mode & 0o777 == 0o700


class _ReadyProbe:
    async def check(self, runtime_type: str, connection: dict[str, str]) -> HealthResult:
        return HealthResult(status="ready", identity="codex-cli")


def _catalog(tmp_path: Path) -> RuntimeExtensionCatalog:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    return RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database))


def test_installed_skill_requires_explicit_qualification(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.0.0",
            source_uri="skill://open-design@1.0.0",
            revision_sha256="a" * 64,
            requested_permissions=("artifact:write", "workspace:read"),
        ),
        actor_id="admin",
    )

    assert installed.status == "installed"
    qualified = catalog.qualify(installed.id, expected_version=installed.version)
    assert qualified.status == "qualified"
    assert qualified.qualification_sha256 is not None


def test_extension_qualification_fails_closed_on_unsafe_permission(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="unsafe-design",
            name="不安全设计扩展",
            kind="skill",
            version="1.0.0",
            source_uri="skill://unsafe-design@1.0.0",
            revision_sha256="b" * 64,
            requested_permissions=("shell:arbitrary",),
        ),
        actor_id="admin",
    )

    failed = catalog.qualify(installed.id, expected_version=installed.version)
    assert failed.status == "failed"
    assert failed.qualification_errors == ("EXTENSION_PERMISSION_NOT_ALLOWED",)


def test_profile_extension_resolution_freezes_exact_qualified_revision(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.2.0",
            source_uri="skill://open-design@1.2.0",
            revision_sha256="c" * 64,
            requested_permissions=("artifact:write",),
        ),
        actor_id="admin",
    )
    qualified = catalog.qualify(installed.id, expected_version=installed.version)

    snapshot = catalog.resolve({"id": "open-design", "kind": "skill", "version": ">=1,<2"})

    assert snapshot["id"] == "open-design"
    assert snapshot["version"] == "1.2.0"
    assert snapshot["revision_sha256"] == qualified.revision_sha256
    assert snapshot["qualification_sha256"] == qualified.qualification_sha256


def test_agent_deployment_freezes_qualified_profile_extension_snapshot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    extensions = RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database))
    installed = extensions.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.0.0",
            source_uri="skill://open-design@1.0.0",
            revision_sha256="d" * 64,
            requested_permissions=("artifact:write",),
        ),
        actor_id="admin",
    )
    extensions.qualify(installed.id, expected_version=installed.version)
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    spec = AgentProfileSpec.model_validate(
        {
            "schema_version": "1",
            "id": "ui-designer",
            "name": "UI 设计师",
            "instructions": {"custom_text": "输出可审查的设计规范"},
            "capabilities": [{"id": "frontend.implementation", "version": ">=1,<2"}],
            "policies": {
                "tool_policy_ref": "policy://design-tools@1",
                "resource_policy_ref": "policy://design-resources@1",
                "approval_policy_ref": "policy://design-approval@1",
                "memory_policy_ref": "policy://session-isolated@1",
                "delegation_policy_ref": "policy://no-delegation@1",
            },
            "extensions": {
                "runtime_extensions": [
                    {
                        "id": "open-design",
                        "kind": "skill",
                        "version": ">=1,<2",
                    }
                ]
            },
        }
    )
    created = profiles.create(AgentProfileCreate(spec=spec), actor_id="admin")
    validated = profiles.validate_draft(
        spec.id, expected_version=created.draft.version, actor_id="admin"
    )
    profiles.publish(spec.id, expected_version=validated.version, actor_id="admin")
    instances = ControlPlaneService(database, probe=_ReadyProbe())
    instance = instances.create_instance(
        AgentInstanceCreate(
            name="Codex 设计实例",
            runtime_type="codex-cli",
            connection={"command": "codex"},
        )
    )
    instance = __import__("asyncio").run(instances.check_instance(instance.id))
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database),
        profiles,
        instances,
        ProviderManifestCatalog(),
        extensions=extensions,
    )
    deployment = deployments.create(
        AgentDeploymentCreate(
            id="ui-design-codex",
            name="UI 设计 Codex",
            profile_id=spec.id,
            profile_revision=1,
            instance_id=instance.id,
            provider_id="codex-cli-provider",
        ),
        actor_id="admin",
    )
    qualified = deployments.qualify(deployment.id, deployment.version)

    assert qualified.qualification_status == "qualified"
    assert qualified.extension_snapshot[0]["id"] == "open-design"
    assert qualified.extension_snapshot[0]["revision_sha256"] == "d" * 64


def test_runtime_extension_public_interface_installs_and_qualifies(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    with TestClient(create_app(coordinator, runtime_extensions=catalog)) as client:
        installed = client.post(
            "/v1/runtime-extensions",
            json={
                "id": "open-design",
                "name": "Open Design",
                "kind": "skill",
                "version": "1.0.0",
                "source_uri": "skill://open-design@1.0.0",
                "revision_sha256": "e" * 64,
                "requested_permissions": ["artifact:write"],
            },
        )
        qualified = client.post(
            "/v1/runtime-extensions/open-design/qualify",
            json={"expected_version": installed.json()["version"]},
        )
        records = client.get("/v1/runtime-extensions")

    assert installed.status_code == 201
    assert qualified.json()["status"] == "qualified"
    assert records.json() == [qualified.json()]
