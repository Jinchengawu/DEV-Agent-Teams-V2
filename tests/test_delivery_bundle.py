from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from agent_team_os import delivery_bundle as bundle_module
from agent_team_os.delivery_bundle import (
    PRODUCT_VERSION,
    BundleBuildError,
    PinnedRegularFile,
    build_delivery_bundle,
    verify_delivery_bundle,
    verify_delivery_bundle_with_method_lock,
)
from agent_team_os.modules.evaluation import load_evaluation_dataset

ROOT = Path(__file__).parents[1]


def _write_delivery_inputs(root: Path) -> Path:
    (root / "config").mkdir(parents=True)
    for name in (
        "capabilities.yaml",
        "framework-lock.json",
        "journeys.yaml",
        "method-packs-v050.json",
    ):
        (root / "config" / name).write_text(f"{name}\n", encoding="utf-8")
    (root / "migrations").mkdir()
    (root / "migrations" / "0001_baseline.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (root / "console" / "dist" / "assets").mkdir(parents=True)
    (root / "console" / "dist" / "index.html").write_text(
        '<script src="/assets/app.js"></script>\n', encoding="utf-8"
    )
    (root / "console" / "dist" / "assets" / "app.js").write_text(
        "console.log('ok')\n", encoding="utf-8"
    )
    shutil.copytree(
        ROOT / "evaluation" / "datasets" / "agent-team-os-mvp" / "1.3.0",
        root / "evaluation" / "datasets" / "agent-team-os-mvp" / "1.3.0",
    )
    (root / "dist").mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, root / name)
    package = root / "src/agent_team_os"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# test package\n")
    wheel = root / "dist" / f"dev_agent_teams_v2-{PRODUCT_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.write(package / "__init__.py", "agent_team_os/__init__.py")
    return wheel


def test_delivery_bundle_has_stable_allow_list_manifest(tmp_path: Path) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)

    result = build_delivery_bundle(
        project_root=root,
        output_root=tmp_path / "output",
        wheel=wheel,
        git_revision="a" * 40,
        worktree_clean=True,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in manifest["files"]]
    assert manifest["schema"] == "agent-team-os-delivery-bundle-v2"
    assert manifest["product_version"] == PRODUCT_VERSION == "0.5.1"
    assert manifest["git_revision"] == "a" * 40
    assert manifest["source_worktree_clean"] is True
    assert paths == sorted(paths)
    assert "config/framework-lock.json" in paths
    assert "console/dist/index.html" in paths
    assert "migrations/0001_baseline.sql" in paths
    assert "evaluation/datasets/agent-team-os-mvp/1.3.0/manifest.json" in paths
    assert f"backend/{wheel.name}" in paths
    assert all(not path.startswith((".agent-team-os/", "node_modules/")) for path in paths)
    assert verify_delivery_bundle(result.bundle_root)["status"] == "verified"


def test_verified_method_lock_is_captured_from_same_manifest_bytes(tmp_path: Path) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    bundle = build_delivery_bundle(
        project_root=root,
        output_root=tmp_path / "output",
        wheel=wheel,
        git_revision="a" * 40,
        worktree_clean=True,
    ).bundle_root
    verified = verify_delivery_bundle_with_method_lock(bundle)
    manifest_bytes = (bundle / "delivery-manifest.json").read_bytes()
    lock_bytes = (bundle / "config/method-packs-v050.json").read_bytes()
    assert verified.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    assert verified.lock_bytes == lock_bytes
    assert verified.lock_sha256 == hashlib.sha256(lock_bytes).hexdigest()
    assert verified.product_revision == "a" * 40


@pytest.mark.parametrize(
    "changed_name", ("delivery-manifest.json", "config/method-packs-v050.json")
)
def test_pinned_bundle_verifier_rejects_two_same_byte_replacements(
    tmp_path: Path, changed_name: str
) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    bundle = build_delivery_bundle(
        project_root=root,
        output_root=tmp_path / "output",
        wheel=wheel,
        git_revision="a" * 40,
        worktree_clean=True,
    ).bundle_root
    manifest = bundle / "delivery-manifest.json"
    lock = bundle / "config/method-packs-v050.json"
    with PinnedRegularFile(manifest) as manifest_pin, PinnedRegularFile(lock) as lock_pin:
        verified = verify_delivery_bundle_with_method_lock(
            bundle, pinned_manifest=manifest_pin, pinned_lock=lock_pin
        )
        assert verified.lock_bytes == lock_pin.content
        changed = bundle / changed_name
        original_inode = changed.stat().st_ino
        for index in range(2):
            replacement = changed.with_suffix(f".replacement-{index}")
            replacement.write_bytes(changed.read_bytes())
            os.replace(replacement, changed)
        assert changed.stat().st_ino != original_inode
        with pytest.raises(BundleBuildError):
            verify_delivery_bundle_with_method_lock(
                bundle, pinned_manifest=manifest_pin, pinned_lock=lock_pin
            )
        pinned_descriptor = (manifest_pin if changed == manifest else lock_pin)._file_fd  # noqa: SLF001
    with pytest.raises(OSError):
        os.fstat(pinned_descriptor)


def test_pinned_regular_file_closes_descriptor_on_exception(tmp_path: Path) -> None:
    lock = tmp_path / "method-lock.json"
    lock.write_bytes(b"frozen")
    with pytest.raises(RuntimeError, match="injected-abort"), PinnedRegularFile(lock) as pinned:
        descriptor = pinned._file_fd  # noqa: SLF001
        raise RuntimeError("injected-abort")
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_verified_method_lock_rejects_tamper_and_in_read_inode_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    bundle = build_delivery_bundle(
        project_root=root,
        output_root=tmp_path / "output",
        wheel=wheel,
        git_revision="a" * 40,
        worktree_clean=True,
    ).bundle_root
    lock = bundle / "config/method-packs-v050.json"
    original = lock.read_bytes()
    lock.write_bytes(b"changed")
    with pytest.raises(BundleBuildError, match="SHA-256"):
        verify_delivery_bundle_with_method_lock(bundle)
    lock.write_bytes(original)

    original_read = bundle_module.os.read
    original_inode = lock.stat().st_ino
    swapped = False

    def swap_after_lock_read(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        chunk = original_read(descriptor, count)
        if not swapped and os.fstat(descriptor).st_ino == original_inode and chunk:
            replacement = lock.with_suffix(".replacement")
            replacement.write_bytes(original)
            os.replace(replacement, lock)
            swapped = True
        return chunk

    monkeypatch.setattr(bundle_module.os, "read", swap_after_lock_read)
    with pytest.raises(BundleBuildError, match="读取期间漂移"):
        verify_delivery_bundle_with_method_lock(bundle)
    assert swapped


def test_delivery_bundle_fails_closed_for_missing_or_sensitive_inputs(tmp_path: Path) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    (root / "config" / ".env").write_text("TOKEN=do-not-copy\n", encoding="utf-8")

    with pytest.raises(BundleBuildError, match="敏感或运行态"):
        build_delivery_bundle(
            project_root=root,
            output_root=tmp_path / "output",
            wheel=wheel,
            git_revision="b" * 40,
            worktree_clean=True,
        )

    (root / "config" / ".env").unlink()
    (root / "console" / "dist" / "index.html").unlink()
    with pytest.raises(BundleBuildError, match="console/dist/index.html"):
        build_delivery_bundle(
            project_root=root,
            output_root=tmp_path / "output",
            wheel=wheel,
            git_revision="b" * 40,
            worktree_clean=True,
        )


def test_delivery_bundle_verifier_rejects_tampering(tmp_path: Path) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    result = build_delivery_bundle(
        project_root=root,
        output_root=tmp_path / "output",
        wheel=wheel,
        git_revision="c" * 40,
        worktree_clean=True,
    )
    target = result.bundle_root / "config" / "framework-lock.json"
    target.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(BundleBuildError, match="SHA-256"):
        verify_delivery_bundle(result.bundle_root)

    assert hashlib.sha256(target.read_bytes()).hexdigest() != next(
        entry["sha256"]
        for entry in json.loads(result.manifest_path.read_text(encoding="utf-8"))["files"]
        if entry["path"] == "config/framework-lock.json"
    )


def test_delivery_bundle_verifier_rejects_manifest_symlink(tmp_path: Path) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    result = build_delivery_bundle(
        project_root=root,
        output_root=tmp_path / "output",
        wheel=wheel,
        git_revision="d" * 40,
        worktree_clean=True,
    )
    external = tmp_path / "external-manifest.json"
    result.manifest_path.replace(external)
    result.manifest_path.symlink_to(external)

    with pytest.raises(BundleBuildError, match="符号链接"):
        verify_delivery_bundle(result.bundle_root)


def test_bundle_contains_loadable_real_evaluation_dataset(tmp_path: Path) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    result = build_delivery_bundle(
        project_root=root,
        output_root=tmp_path / "output",
        wheel=wheel,
        git_revision="e" * 40,
        worktree_clean=True,
    )

    dataset = load_evaluation_dataset(
        result.bundle_root / "evaluation" / "datasets" / "agent-team-os-mvp" / "1.3.0"
    )

    assert dataset.manifest.suite_id == "agent-team-os-mvp"
    assert dataset.cases


def test_verifier_rejects_internal_symlink_even_with_matching_bytes(tmp_path: Path) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    bundle = build_delivery_bundle(
        project_root=root, output_root=tmp_path / "output", wheel=wheel,
        git_revision="a" * 40, worktree_clean=True,
    ).bundle_root
    target = bundle / "config/capabilities.yaml"
    alias = bundle / "config/journeys.yaml"
    target.unlink()
    target.symlink_to(alias.name)
    manifest = bundle / "delivery-manifest.json"
    raw = json.loads(manifest.read_text())
    for entry in raw["files"]:
        if entry["path"] == "config/capabilities.yaml":
            entry["size"] = alias.stat().st_size
            entry["sha256"] = hashlib.sha256(alias.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(raw))
    with pytest.raises(BundleBuildError, match="符号链接"):
        verify_delivery_bundle(bundle)


def test_builder_rejects_stale_wheel_for_current_source(tmp_path: Path) -> None:
    root = tmp_path / "source"
    wheel = _write_delivery_inputs(root)
    (root / "src/agent_team_os/__init__.py").write_text("# changed after wheel built")
    with pytest.raises(BundleBuildError, match="wheel 不一致"):
        build_delivery_bundle(
            project_root=root, output_root=tmp_path / "output", wheel=wheel,
            git_revision="a" * 40, worktree_clean=True,
        )
