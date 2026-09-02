from __future__ import annotations

import subprocess
from pathlib import Path

from agent_team_os.readiness import snapshot_delivery_build_identity


def test_delivery_build_identity_freezes_reproducible_and_dirty_product_states(
    tmp_path: Path,
) -> None:
    project = tmp_path / "product"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "gate@example.invalid")
    _git(project, "config", "user.name", "Release Gate")
    revision = "a" * 40
    _write_framework_files(project, revision)
    (project / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "baseline")

    clean = snapshot_delivery_build_identity(
        project,
        actual_acwm_revision=revision,
        actual_acwm_worktree_dirty=False,
    )
    (project / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    dirty = snapshot_delivery_build_identity(
        project,
        actual_acwm_revision=revision,
        actual_acwm_worktree_dirty=False,
    )

    assert clean.product_revision == _git(project, "rev-parse", "HEAD").strip()
    assert clean.product_worktree_clean is True
    assert clean.framework_dependency_status == "ready"
    assert clean.acwm_revision == revision
    assert dirty.product_revision == clean.product_revision
    assert dirty.product_worktree_clean is False
    assert dirty.snapshot_sha256 != clean.snapshot_sha256


def _write_framework_files(project: Path, revision: str) -> None:
    config = project / "config"
    config.mkdir()
    (config / "framework-lock.json").write_text(
        '{"acwm":{"version":"0.5.1","revision":"' + revision + '"}}',
        encoding="utf-8",
    )
    (project / "pyproject.toml").write_text(
        (
            "[project]\n"
            'dependencies = ["agent-capability-workflow-matrix==0.5.1"]\n\n'
            "[tool.uv.sources]\n"
            "agent-capability-workflow-matrix = { "
            'git = "https://example.invalid/acwm.git", '
            f'rev = "{revision}" }}\n'
        ),
        encoding="utf-8",
    )
    source = f"https://example.invalid/acwm.git?rev={revision}#{revision}"
    (project / "uv.lock").write_text(
        (
            "version = 1\n\n"
            "[[package]]\n"
            'name = "agent-capability-workflow-matrix"\n'
            'version = "0.5.1"\n'
            f'source = {{ git = "{source}" }}\n'
        ),
        encoding="utf-8",
    )


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout
