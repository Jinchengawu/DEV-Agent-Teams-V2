from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from agent_team_os import delivery_bundle
from agent_team_os.readiness import snapshot_delivery_build_identity

ROOT = Path(__file__).parents[1]


def _bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source = tmp_path / "source"
    for directory in ("config", "migrations", "evaluation/datasets/agent-team-os-mvp/1.3.0"):
        shutil.copytree(ROOT / directory, source / directory)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, source / name)
    (source / "console/dist").mkdir(parents=True)
    (source / "console/dist/index.html").write_text("test")
    package = source / "src/agent_team_os"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# packaged code\n")
    wheel = tmp_path / "dev_agent_teams_v2-0.5.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.write(package / "__init__.py", "agent_team_os/__init__.py")
    bundle = delivery_bundle.build_delivery_bundle(
        project_root=source, output_root=tmp_path / "output", wheel=wheel,
        git_revision="a" * 40, worktree_clean=True,
    ).bundle_root
    installed = tmp_path / "site-packages/agent_team_os"
    shutil.copytree(package, installed)
    monkeypatch.setattr(delivery_bundle, "_loaded_package_root", lambda: installed)
    return bundle, installed


def test_installed_bundle_freezes_identity_without_git(tmp_path, monkeypatch):
    bundle, _ = _bundle(tmp_path, monkeypatch)
    identity = snapshot_delivery_build_identity(bundle)
    assert identity.product_revision == "a" * 40
    assert identity.product_worktree_clean
    assert identity.framework_dependency_status == "ready"


@pytest.mark.parametrize(
    "tamper", ["code", "extra_code", "resource", "dirty", "revision", "legacy"],
)
def test_bundle_identity_rejects_mixed_code_resources_or_invalid_identity(
    tmp_path, monkeypatch, tamper,
):
    bundle, installed = _bundle(tmp_path, monkeypatch)
    if tamper == "code":
        (installed / "__init__.py").write_text("# different code")
    elif tamper == "extra_code":
        (installed / "shadow.py").write_text("# unexpected code")
    elif tamper == "resource":
        (bundle / "config/framework-lock.json").write_text("{}")
    else:
        manifest = bundle / "delivery-manifest.json"
        raw = json.loads(manifest.read_text())
        if tamper == "legacy":
            raw["schema"] = "agent-team-os-delivery-bundle-v1"
        else:
            raw["source_worktree_clean" if tamper == "dirty" else "git_revision"] = (
                False if tamper == "dirty" else "invalid"
            )
        manifest.write_text(json.dumps(raw))
    with pytest.raises(delivery_bundle.BundleBuildError):
        snapshot_delivery_build_identity(bundle)
