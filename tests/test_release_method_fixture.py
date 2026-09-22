import base64
import hashlib
import io
import json
import tarfile

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
