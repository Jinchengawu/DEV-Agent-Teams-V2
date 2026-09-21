from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from agent_team_os.delivery_bundle import (
    PRODUCT_VERSION,
    BundleBuildError,
    build_delivery_bundle,
    verify_delivery_bundle,
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
    wheel = root / "dist" / f"dev_agent_teams_v2-{PRODUCT_VERSION}-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
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
    assert manifest["schema"] == "agent-team-os-delivery-bundle-v1"
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
