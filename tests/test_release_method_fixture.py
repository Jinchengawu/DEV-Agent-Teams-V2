import base64
import hashlib
import io
import json
import stat
import tarfile
import threading

import pytest

from agent_team_os import release
from agent_team_os.modules.extensions import (
    ContentAddressedMethodPackStore,
    FrozenMethodPackSet,
    MethodEntry,
    MethodPackInstall,
)
from agent_team_os.shared.errors import ProductError


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / "product"
    store_root = root / ".agent-team-os/method-packs"
    store_root.parent.mkdir(parents=True, mode=0o700)
    store = ContentAddressedMethodPackStore(store_root)
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, data in {
            "package/package.json": b'{"name":"test-method","version":"1.0.0"}',
            "package/skills/test/SKILL.md": b"# Test",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    data = payload.getvalue()
    request = MethodPackInstall(
        package_name="test-method",
        package_version="1.0.0",
        tarball_uri="https://registry.npmjs.org/test-method/-/test-method-1.0.0.tgz",
        registry_integrity="sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode(),
        archive_sha256=hashlib.sha256(data).hexdigest(),
        method_entries=(MethodEntry(method_id="test", source_path="skills/test"),),
    )
    snapshot = store.install_archive(request, data)
    lock = root / "config/method-packs-v050.json"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps(
            {
                "policy_version": "method-pack-store-v1",
                "packages": [
                    {
                        "install": request.model_dump(mode="json"),
                        "expected_content_sha256": snapshot.content_sha256,
                        "expected_qualification_sha256": snapshot.qualification_sha256,
                    }
                ],
            }
        )
    )
    monkeypatch.delenv("AGENT_TEAM_OS_DATA_DIR", raising=False)
    return root, store_root, snapshot, lock


def test_fixture_copies_only_locked_files_and_verifies(package, tmp_path):
    root, source, snapshot, lock = package
    (source / "unrelated-secret").write_text("not copied")
    target = tmp_path / "browser/method-packs"
    release._prepare_browser_method_packs(root, target)
    source_lock = (source / ".install.lock").lstat()
    target_lock = (target / ".install.lock").lstat()
    assert stat.S_ISREG(target_lock.st_mode)
    assert stat.S_IMODE(target_lock.st_mode) == 0o600
    assert not (target / ".install.lock").is_symlink()
    assert (target_lock.st_dev, target_lock.st_ino) != (
        source_lock.st_dev,
        source_lock.st_ino,
    )
    assert not (target / "unrelated-secret").exists()
    assert FrozenMethodPackSet(lock, ContentAddressedMethodPackStore(target)).snapshot() == (
        FrozenMethodPackSet(lock, ContentAddressedMethodPackStore(source)).snapshot()
    )


@pytest.mark.parametrize("fault", ["missing", "tamper", "symlink"])
def test_fixture_rejects_invalid_source(package, tmp_path, fault):
    root, source, snapshot, _ = package
    path = (
        source
        / "objects/sha256"
        / snapshot.content_sha256[:2]
        / snapshot.content_sha256
        / "skills/test/SKILL.md"
    )
    path.parent.chmod(0o700)
    path.chmod(0o600)
    if fault == "missing":
        path.unlink()
    elif fault == "tamper":
        path.write_text("changed")
    else:
        destination = tmp_path / "alias"
        destination.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(destination)
    target = tmp_path / "browser/method-packs"
    with pytest.raises((ProductError, RuntimeError)):
        release._prepare_browser_method_packs(root, target)
    assert not target.exists()


def test_missing_explicit_store_falls_back_but_invalid_store_does_not(
    package, tmp_path, monkeypatch
):
    root, _, _, _ = package
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(explicit))
    release._prepare_browser_method_packs(root, tmp_path / "first")
    (explicit / "method-packs").mkdir(parents=True)
    with pytest.raises(ProductError):
        release._prepare_browser_method_packs(root, tmp_path / "second")
    assert not (tmp_path / "second").exists()


def test_copy_failure_cleans_only_owned_destination(package, tmp_path, monkeypatch):
    root, source, _, _ = package

    def fail_copy(*args, **kwargs):
        raise OSError("injected-copy-failure")

    monkeypatch.setattr(release.shutil, "copyfile", fail_copy)
    target = tmp_path / "browser/method-packs"
    with pytest.raises(OSError, match="injected-copy-failure"):
        release._prepare_browser_method_packs(root, target)
    assert not target.exists()
    assert source.is_dir()


def test_pending_source_blocks_browser_copy(package, tmp_path):
    root, source, _, _ = package
    (source / ".install-in-progress").write_text("pending")
    target = tmp_path / "browser/method-packs"
    with pytest.raises(ProductError) as error:
        release._prepare_browser_method_packs(root, target)
    assert error.value.code == "METHOD_PACK_STORE_PENDING"
    assert not target.exists()


def test_source_removed_after_check_is_not_recreated_by_reader(
    package, tmp_path, monkeypatch
):
    root, source, _, _ = package
    target = tmp_path / "browser/method-packs"
    moved = source.with_name("method-packs-moved")
    original = release.ContentAddressedMethodPackStore

    def remove_before_constructor(path):
        if path == source:
            source.rename(moved)
        return original(path)

    monkeypatch.setattr(release, "ContentAddressedMethodPackStore", remove_before_constructor)
    with pytest.raises(ProductError) as error:
        release._prepare_browser_method_packs(root, target)
    assert error.value.code == "METHOD_PACK_STORE_MISSING"
    assert not source.exists()
    assert moved.is_dir()
    assert not target.exists()


def test_existing_browser_target_is_never_overwritten(package, tmp_path):
    root, _, _, _ = package
    target = tmp_path / "browser/method-packs"
    target.mkdir(parents=True)
    marker = target / "owned-by-another-run"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        release._prepare_browser_method_packs(root, target)
    assert marker.read_text() == "keep"


def test_target_validation_holds_no_source_lock(package, tmp_path, monkeypatch):
    root, source, _, _ = package
    original = release.FrozenMethodPackSet.snapshot

    def validate_without_source_lock(self):
        if self.store.root != source:
            acquired = threading.Event()

            def writer():
                with ContentAddressedMethodPackStore(source)._lock(exclusive=True):
                    acquired.set()

            thread = threading.Thread(target=writer)
            thread.start()
            assert acquired.wait(1), "目标校验期间仍持有源 Store 共享锁"
            thread.join(timeout=1)
        return original(self)

    monkeypatch.setattr(release.FrozenMethodPackSet, "snapshot", validate_without_source_lock)
    release._prepare_browser_method_packs(root, tmp_path / "browser/method-packs")


def test_failed_target_validation_removes_only_new_target(package, tmp_path, monkeypatch):
    root, source, _, _ = package
    source_lock = (source / ".install.lock").lstat()
    original = release.FrozenMethodPackSet.snapshot

    def reject_target(self):
        if self.store.root != source:
            raise RuntimeError("injected-target-validation-failure")
        return original(self)

    monkeypatch.setattr(release.FrozenMethodPackSet, "snapshot", reject_target)
    target = tmp_path / "browser/method-packs"
    with pytest.raises(RuntimeError, match="injected-target-validation-failure"):
        release._prepare_browser_method_packs(root, target)
    assert not target.exists()
    assert (source / ".install.lock").lstat().st_ino == source_lock.st_ino
    assert ContentAddressedMethodPackStore(source).root == source


def test_browser_copy_keeps_source_shared_lock_for_the_whole_copy(package, tmp_path, monkeypatch):
    root, source, _, _ = package
    store = ContentAddressedMethodPackStore(source)
    attempted = threading.Event()
    acquired = threading.Event()
    threads = []
    original = release.shutil.copyfile

    def attempt_writer():
        attempted.set()
        with store._lock(exclusive=True):  # noqa: SLF001
            acquired.set()

    def copy_while_writer_waits(*args, **kwargs):
        if not threads:
            thread = threading.Thread(target=attempt_writer)
            thread.start()
            threads.append(thread)
            assert attempted.wait(1)
            assert not acquired.wait(0.05)
        return original(*args, **kwargs)

    monkeypatch.setattr(release.shutil, "copyfile", copy_while_writer_waits)
    release._prepare_browser_method_packs(root, tmp_path / "browser/method-packs")
    threads[0].join(timeout=2)
    assert acquired.is_set()
