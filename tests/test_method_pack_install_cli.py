from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_team_os import method_pack_cli
from agent_team_os.delivery_bundle import VerifiedBundleMethodLock
from agent_team_os.modules.extensions import (
    ContentAddressedMethodPackStore,
    MethodEntry,
    MethodPackInstall,
)
from agent_team_os.shared.errors import ProductError

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "install_method_packs.py"
_spec = importlib.util.spec_from_file_location("install_method_packs_script", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
install_method_packs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_method_packs)


def _fixture_package(
    name: str, version: str, method_id: str, *, skill_content: bytes = b"# Test\n"
) -> tuple[MethodPackInstall, bytes]:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for path, content in {
            "package/package.json": json.dumps({"name": name, "version": version}).encode(),
            "package/skills/test/SKILL.md": skill_content,
        }.items():
            info = tarfile.TarInfo(path)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    data = stream.getvalue()
    request = MethodPackInstall(
        package_name=name,
        package_version=version,
        tarball_uri=f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz",
        registry_integrity="sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode(),
        archive_sha256=hashlib.sha256(data).hexdigest(),
        method_entries=(MethodEntry(method_id=method_id, source_path="skills/test"),),
    )
    return request, data


def _lock(
    tmp_path: Path, packages: list[tuple[MethodPackInstall, bytes]], *, wrong_second: bool = False
) -> Path:
    product = tmp_path / "product"
    (product / "config").mkdir(parents=True)
    store = ContentAddressedMethodPackStore(tmp_path / "scratch")
    entries = []
    for index, (request, archive) in enumerate(packages):
        snapshot = store.prepare_archive(request, archive).snapshot
        entries.append(
            {
                "install": request.model_dump(mode="json"),
                "expected_content_sha256": "f" * 64
                if index == 1 and wrong_second
                else snapshot.content_sha256,
                "expected_qualification_sha256": snapshot.qualification_sha256,
            }
        )
    path = product / "config/method-packs-v050.json"
    path.write_text(json.dumps({"policy_version": "method-pack-store-v1", "packages": entries}))
    return product


class _Fetcher:
    archives: dict[str, bytes] = {}

    def fetch(self, request: MethodPackInstall, exact_locked_url: str) -> bytes:
        assert request.tarball_uri == exact_locked_url
        return self.archives[request.package_name]


def test_source_cli_publishes_qualified_set_but_not_bundle_ready(tmp_path, monkeypatch, capsys):
    package = _fixture_package("bmad-method", "6.11.0", "bmad")
    product = _lock(tmp_path, [package])
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    _Fetcher.archives = {package[0].package_name: package[1]}
    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", _Fetcher)

    assert method_pack_cli.main([], source_root=product) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "source-qualified"
    assert result["transport"] == {"proxy": "disabled", "redirects": 0}
    store = ContentAddressedMethodPackStore(data_root / "method-packs")
    assert (
        store.load_snapshot(result["packages"][0]["qualification_sha256"]).package_name
        == "bmad-method"
    )


def test_second_pack_drift_writes_no_payload(tmp_path, monkeypatch, capsys):
    packages = [
        _fixture_package("bmad-method", "6.11.0", "bmad"),
        _fixture_package("bmad-method-test-architecture-enterprise", "1.23.4", "tea"),
    ]
    product = _lock(tmp_path, packages, wrong_second=True)
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    _Fetcher.archives = {request.package_name: archive for request, archive in packages}
    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", _Fetcher)

    with pytest.raises(ProductError):
        method_pack_cli.main([], source_root=product)
    assert not capsys.readouterr().out
    assert not list((data_root / "method-packs" / "objects").rglob("package.json"))


def test_legacy_script_entry_delegates_to_common_cli(monkeypatch):
    calls = []
    assert Path(install_method_packs.__file__).resolve() == _SCRIPT_PATH

    def fake_main(*, source_root: Path) -> int:
        calls.append(source_root)
        return 0

    monkeypatch.setattr(install_method_packs, "install_main", fake_main)
    assert install_method_packs.main() == 0
    assert calls == [Path(install_method_packs.__file__).parents[1]]


def test_explicit_data_root_must_be_private_before_transport(tmp_path, monkeypatch):
    package = _fixture_package("bmad-method", "6.11.0", "bmad")
    product = _lock(tmp_path, [package])
    data_root = tmp_path / "public"
    data_root.mkdir(mode=0o755)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    _Fetcher.archives = {package[0].package_name: package[1]}
    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", _Fetcher)
    with pytest.raises(RuntimeError, match="DATA_ROOT_UNSAFE"):
        method_pack_cli.main([], source_root=product)


def test_default_data_root_is_created_private(tmp_path, monkeypatch):
    package = _fixture_package("bmad-method", "6.11.0", "bmad")
    product = _lock(tmp_path, [package])
    monkeypatch.delenv("AGENT_TEAM_OS_DATA_DIR", raising=False)
    _Fetcher.archives = {package[0].package_name: package[1]}
    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", _Fetcher)
    assert method_pack_cli.main([], source_root=product) == 0
    assert (product / ".agent-team-os").stat().st_mode & 0o077 == 0


def test_existing_source_default_data_root_0755_gets_private_store(tmp_path, monkeypatch):
    package = _fixture_package("bmad-method", "6.11.0", "bmad")
    product = _lock(tmp_path, [package])
    data_root = product / ".agent-team-os"
    data_root.mkdir(mode=0o755)
    monkeypatch.delenv("AGENT_TEAM_OS_DATA_DIR", raising=False)
    _Fetcher.archives = {package[0].package_name: package[1]}
    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", _Fetcher)
    assert method_pack_cli.main([], source_root=product) == 0
    assert data_root.stat().st_mode & 0o777 == 0o755
    assert (data_root / "method-packs").stat().st_mode & 0o777 == 0o700


def test_explicit_data_root_symlink_is_rejected_before_transport(tmp_path, monkeypatch):
    package = _fixture_package("bmad-method", "6.11.0", "bmad")
    product = _lock(tmp_path, [package])
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(alias))
    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", _Fetcher)
    with pytest.raises(ProductError) as error:
        method_pack_cli.main([], source_root=product)
    assert error.value.code == "METHOD_PACK_STORE_PATH_INVALID"


def _change_lock_bytes(lock_file: Path) -> None:
    payload = json.loads(lock_file.read_text(encoding="utf-8"))
    payload["local_test_marker"] = "different bytes, same package qualifications"
    lock_file.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("window", ["before_fetch", "after_fetch", "after_commit"])
@pytest.mark.parametrize("bundle_mode", [False, True])
def test_cli_rejects_three_lock_swap_windows(
    tmp_path, monkeypatch, capsys, window: str, bundle_mode: bool
):
    packages = [
        _fixture_package("bmad-method", "6.11.0", "bmad"),
        _fixture_package("bmad-method-test-architecture-enterprise", "1.23.4", "tea"),
    ]
    product = _lock(tmp_path, packages)
    lock_file = product / "config/method-packs-v050.json"
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    store = ContentAddressedMethodPackStore(data_root / "method-packs")
    old_request, old_archive = _fixture_package("old-method", "1.0.0", "old")
    old = store.install_archive(old_request, old_archive)
    old_path = store._snapshot_path(old.qualification_sha256)  # noqa: SLF001
    old_bytes, old_inode = old_path.read_bytes(), old_path.stat().st_ino
    a_snapshots = [
        store.prepare_archive(request, archive).snapshot for request, archive in packages
    ]
    _Fetcher.archives = {request.package_name: archive for request, archive in packages}
    if bundle_mode:
        manifest = product / "delivery-manifest.json"
        manifest.write_text("test manifest", encoding="utf-8")

        def verify(_root: Path) -> VerifiedBundleMethodLock:
            payload = lock_file.read_bytes()
            metadata = lock_file.stat()
            manifest_metadata = manifest.stat()
            return VerifiedBundleMethodLock(
                manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                manifest_identity=(manifest_metadata.st_dev, manifest_metadata.st_ino),
                product_revision="a" * 40,
                lock_bytes=payload,
                lock_sha256=hashlib.sha256(payload).hexdigest(),
                lock_identity=(metadata.st_dev, metadata.st_ino),
            )

        monkeypatch.setattr(method_pack_cli, "verify_delivery_bundle_with_method_lock", verify)

        def identity(_root: Path) -> SimpleNamespace:
            if window == "before_fetch":
                _change_lock_bytes(lock_file)
            return SimpleNamespace(
                product_worktree_clean=True,
                framework_dependency_status="ready",
                product_revision="a" * 40,
            )

        monkeypatch.setattr(method_pack_cli, "snapshot_delivery_build_identity", identity)

    class MutatingFetcher(_Fetcher):
        def __init__(self) -> None:
            if window == "before_fetch" and not bundle_mode:
                _change_lock_bytes(lock_file)

        def fetch(self, request: MethodPackInstall, exact_locked_url: str) -> bytes:
            archive = super().fetch(request, exact_locked_url)
            if window == "after_fetch" and request.package_name == packages[-1][0].package_name:
                _change_lock_bytes(lock_file)
            return archive

    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", MutatingFetcher)
    if window == "after_commit":
        original = method_pack_cli.FrozenMethodPackSet.snapshot

        def after_snapshot(frozen):
            result = original(frozen)
            _change_lock_bytes(lock_file)
            return result

        monkeypatch.setattr(method_pack_cli.FrozenMethodPackSet, "snapshot", after_snapshot)

    with pytest.raises(RuntimeError, match="METHOD_PACK_(BUNDLE_)?LOCK_CHANGED"):
        method_pack_cli.main([], source_root=product)
    assert not capsys.readouterr().out
    assert old_path.read_bytes() == old_bytes and old_path.stat().st_ino == old_inode
    assert not (store.root / ".install-in-progress").exists()
    for snapshot in a_snapshots:
        if window == "after_commit":
            assert store.load_snapshot(snapshot.qualification_sha256) == snapshot
        else:
            assert not store._object_path(snapshot.content_sha256).exists()  # noqa: SLF001
            assert not store._snapshot_path(snapshot.qualification_sha256).exists()  # noqa: SLF001


def test_source_cli_rejects_identical_lock_bytes_with_replaced_inode(
    tmp_path, monkeypatch, capsys
):
    package = _fixture_package("bmad-method", "6.11.0", "bmad")
    product = _lock(tmp_path, [package])
    lock_file = product / "config/method-packs-v050.json"
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    _Fetcher.archives = {package[0].package_name: package[1]}

    class ReplacingFetcher(_Fetcher):
        def fetch(self, request: MethodPackInstall, exact_locked_url: str) -> bytes:
            replacement = lock_file.with_suffix(".replacement")
            replacement.write_bytes(lock_file.read_bytes())
            os.replace(replacement, lock_file)
            return super().fetch(request, exact_locked_url)

    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", ReplacingFetcher)
    with pytest.raises(RuntimeError, match="METHOD_PACK_LOCK_CHANGED"):
        method_pack_cli.main([], source_root=product)
    assert not capsys.readouterr().out


def test_bundle_cli_rejects_identical_lock_bytes_with_replaced_inode(
    tmp_path, monkeypatch, capsys
):
    packages = [
        _fixture_package("bmad-method", "6.11.0", "bmad"),
        _fixture_package("bmad-method-test-architecture-enterprise", "1.23.4", "tea"),
    ]
    product = _lock(tmp_path, packages)
    lock_file = product / "config/method-packs-v050.json"
    manifest = product / "delivery-manifest.json"
    manifest.write_text("test manifest", encoding="utf-8")
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))

    def verify(_root: Path) -> VerifiedBundleMethodLock:
        payload = lock_file.read_bytes()
        metadata = lock_file.stat()
        manifest_metadata = manifest.stat()
        return VerifiedBundleMethodLock(
            manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            manifest_identity=(manifest_metadata.st_dev, manifest_metadata.st_ino),
            product_revision="a" * 40,
            lock_bytes=payload,
            lock_sha256=hashlib.sha256(payload).hexdigest(),
            lock_identity=(metadata.st_dev, metadata.st_ino),
        )

    monkeypatch.setattr(method_pack_cli, "verify_delivery_bundle_with_method_lock", verify)
    monkeypatch.setattr(
        method_pack_cli,
        "snapshot_delivery_build_identity",
        lambda _root: SimpleNamespace(
            product_worktree_clean=True,
            framework_dependency_status="ready",
            product_revision="a" * 40,
        ),
    )
    _Fetcher.archives = {request.package_name: archive for request, archive in packages}

    class ReplacingFetcher(_Fetcher):
        def fetch(self, request: MethodPackInstall, exact_locked_url: str) -> bytes:
            replacement = lock_file.with_suffix(".replacement")
            replacement.write_bytes(lock_file.read_bytes())
            os.replace(replacement, lock_file)
            return super().fetch(request, exact_locked_url)

    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", ReplacingFetcher)
    with pytest.raises(RuntimeError, match="METHOD_PACK_BUNDLE_LOCK_CHANGED"):
        method_pack_cli.main([], source_root=product)
    assert not capsys.readouterr().out
    assert not (data_root / "method-packs").exists()


def test_source_custom_lock_is_frozen_through_receipt(tmp_path, monkeypatch, capsys):
    package = _fixture_package("bmad-method", "6.11.0", "bmad")
    product = _lock(tmp_path, [package])
    custom_lock = tmp_path / "custom-method-lock.json"
    custom_lock.write_bytes((product / "config/method-packs-v050.json").read_bytes())
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    _Fetcher.archives = {package[0].package_name: package[1]}
    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", _Fetcher)
    original = method_pack_cli.FrozenMethodPackSet.snapshot

    def change_before_receipt(frozen):
        result = original(frozen)
        _change_lock_bytes(custom_lock)
        return result

    monkeypatch.setattr(method_pack_cli.FrozenMethodPackSet, "snapshot", change_before_receipt)
    with pytest.raises(RuntimeError, match="METHOD_PACK_LOCK_CHANGED"):
        method_pack_cli.main(["--lock", str(custom_lock)], source_root=product)
    assert not capsys.readouterr().out


def test_bundle_cli_never_mixes_new_a_install_with_preexisting_b_frozen_set(
    tmp_path, monkeypatch, capsys
):
    a = [
        _fixture_package("bmad-method", "6.11.0", "bmad", skill_content=b"# A\n"),
        _fixture_package(
            "bmad-method-test-architecture-enterprise", "1.23.4", "tea", skill_content=b"# A\n"
        ),
    ]
    b = [
        _fixture_package("bmad-method", "6.11.0", "bmad", skill_content=b"# B\n"),
        _fixture_package(
            "bmad-method-test-architecture-enterprise", "1.23.4", "tea", skill_content=b"# B\n"
        ),
    ]
    product = _lock(tmp_path, a)
    b_product = _lock(tmp_path / "other", b)
    b_lock_bytes = (b_product / "config/method-packs-v050.json").read_bytes()
    lock_file = product / "config/method-packs-v050.json"
    manifest = product / "delivery-manifest.json"
    manifest.write_text("test manifest", encoding="utf-8")
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    store = ContentAddressedMethodPackStore(data_root / "method-packs")
    b_snapshots = []
    for request, archive in b:
        b_snapshots.append(store.install_archive(request, archive))
    b_snapshots_before = [
        (
            store._snapshot_path(snapshot.qualification_sha256).read_bytes(),  # noqa: SLF001
            store._snapshot_path(snapshot.qualification_sha256).stat().st_ino,  # noqa: SLF001
        )
        for snapshot in b_snapshots
    ]
    a_snapshots = [store.prepare_archive(request, archive).snapshot for request, archive in a]
    _Fetcher.archives = {request.package_name: archive for request, archive in a}
    monkeypatch.setattr(method_pack_cli, "LockedMethodPackArchiveFetcher", _Fetcher)

    def verify(_root: Path) -> VerifiedBundleMethodLock:
        payload = lock_file.read_bytes()
        metadata = lock_file.stat()
        manifest_metadata = manifest.stat()
        return VerifiedBundleMethodLock(
            manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            manifest_identity=(manifest_metadata.st_dev, manifest_metadata.st_ino),
            product_revision="a" * 40,
            lock_bytes=payload,
            lock_sha256=hashlib.sha256(payload).hexdigest(),
            lock_identity=(metadata.st_dev, metadata.st_ino),
        )

    monkeypatch.setattr(method_pack_cli, "verify_delivery_bundle_with_method_lock", verify)
    monkeypatch.setattr(
        method_pack_cli,
        "snapshot_delivery_build_identity",
        lambda _root: SimpleNamespace(
            product_worktree_clean=True,
            framework_dependency_status="ready",
            product_revision="a" * 40,
        ),
    )
    original = ContentAddressedMethodPackStore.install_locked_batch

    def swap_to_b_after_a_commit(self, *args, **kwargs):
        snapshots = original(self, *args, **kwargs)
        lock_file.write_bytes(b_lock_bytes)
        return snapshots

    monkeypatch.setattr(
        ContentAddressedMethodPackStore, "install_locked_batch", swap_to_b_after_a_commit
    )
    with pytest.raises(RuntimeError, match="METHOD_PACK_BUNDLE_LOCK_CHANGED"):
        method_pack_cli.main([], source_root=product)
    assert not capsys.readouterr().out
    assert not (store.root / ".install-in-progress").exists()
    for snapshot in a_snapshots:
        assert store.load_snapshot(snapshot.qualification_sha256) == snapshot
    for snapshot, before in zip(b_snapshots, b_snapshots_before, strict=True):
        path = store._snapshot_path(snapshot.qualification_sha256)  # noqa: SLF001
        assert (path.read_bytes(), path.stat().st_ino) == before
