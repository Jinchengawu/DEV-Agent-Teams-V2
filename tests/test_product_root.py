from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent_team_os.product_root import ProductRootError, resolve_product_root

ROOT = Path(__file__).parents[1]


def _bundle_root(root: Path) -> Path:
    (root / "config").mkdir(parents=True)
    for name in (
        "capabilities.yaml",
        "framework-lock.json",
        "journeys.yaml",
        "method-packs-v050.json",
    ):
        (root / "config" / name).write_text("{}\n", encoding="utf-8")
    (root / "migrations").mkdir()
    (root / "migrations" / "0001_baseline.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (root / "console" / "dist").mkdir(parents=True)
    (root / "console" / "dist" / "index.html").write_text("ok\n", encoding="utf-8")
    shutil.copytree(
        ROOT / "evaluation" / "datasets" / "agent-team-os-mvp" / "1.3.0",
        root / "evaluation" / "datasets" / "agent-team-os-mvp" / "1.3.0",
    )
    return root


def test_explicit_product_root_is_validated_and_selected(tmp_path: Path) -> None:
    root = _bundle_root(tmp_path / "bundle")

    assert resolve_product_root(root) == root.resolve()


def test_invalid_explicit_product_root_never_falls_back_to_checkout(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid"
    invalid.mkdir()

    with pytest.raises(ProductRootError, match="config/framework-lock.json"):
        resolve_product_root(invalid)


def test_environment_product_root_is_explicit_and_fail_closed(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid"
    invalid.mkdir()

    with pytest.raises(ProductRootError):
        resolve_product_root(environ={"AGENT_TEAM_OS_PRODUCT_ROOT": str(invalid)})
