from pathlib import Path

from agent_team_os.readiness import inspect_acwm_revision_lock


def _write_framework_project(
    tmp_path: Path,
    *,
    locked_version: str = "0.5.1",
    dependency_version: str = "0.5.1",
    locked_revision: str = "a" * 40,
    source_revision: str = "a" * 40,
    resolved_version: str | None = None,
    resolved_revision: str | None = None,
) -> Path:
    project = tmp_path / "product"
    config = project / "config"
    config.mkdir(parents=True)
    lock = config / "framework-lock.json"
    lock.write_text(
        '{"acwm":{"version":"'
        + locked_version
        + '","revision":"'
        + locked_revision
        + '"}}',
        encoding="utf-8",
    )
    source_declaration = (
        'agent-capability-workflow-matrix = { git = "https://example.invalid/acwm.git", '
        f'rev = "{source_revision}" }}'
    )
    (project / "pyproject.toml").write_text(
        (
            "[project]\n"
            "dependencies = "
            f'["agent-capability-workflow-matrix=={dependency_version}"]\n\n'
            "[tool.uv.sources]\n"
            f"{source_declaration}\n"
        ),
        encoding="utf-8",
    )
    if resolved_version is not None and resolved_revision is not None:
        resolved_source = (
            "https://example.invalid/acwm.git"
            f"?rev={source_revision}#{resolved_revision}"
        )
        (project / "uv.lock").write_text(
            (
                "version = 1\n\n"
                "[[package]]\n"
                'name = "agent-capability-workflow-matrix"\n'
                f'version = "{resolved_version}"\n'
                f'source = {{ git = "{resolved_source}" }}\n'
            ),
            encoding="utf-8",
        )
    return lock


def test_acwm_revision_lock_is_fail_closed_on_mismatch(tmp_path: Path) -> None:
    lock = tmp_path / "framework-lock.json"
    lock.write_text(
        '{"acwm":{"version":"0.4.0","revision":"' + "a" * 40 + '"}}',
        encoding="utf-8",
    )

    ready = inspect_acwm_revision_lock(
        lock, actual_revision="a" * 40, actual_worktree_dirty=False
    )
    mismatch = inspect_acwm_revision_lock(
        lock, actual_revision="b" * 40, actual_worktree_dirty=False
    )
    dirty = inspect_acwm_revision_lock(
        lock, actual_revision="a" * 40, actual_worktree_dirty=True
    )

    assert ready.status == "ready"
    assert ready.repair is None
    assert mismatch.status == "failed"
    assert "不一致" in str(mismatch.repair)
    assert dirty.status == "failed"
    assert "未提交" in str(dirty.repair)


def test_repository_framework_lock_matches_checked_out_acwm() -> None:
    project_root = Path(__file__).parents[1]

    check = inspect_acwm_revision_lock(
        project_root / "config" / "framework-lock.json"
    )

    assert check.status == "ready", check.repair


def test_framework_lock_rejects_pyproject_version_drift(tmp_path: Path) -> None:
    revision = "a" * 40
    lock = _write_framework_project(
        tmp_path,
        dependency_version="0.5.0",
    )

    check = inspect_acwm_revision_lock(
        lock,
        actual_revision=revision,
        actual_worktree_dirty=False,
    )

    assert check.status == "failed"
    assert "pyproject.toml" in str(check.repair)
    assert "0.5.1" in str(check.repair)


def test_framework_lock_rejects_pyproject_revision_drift(tmp_path: Path) -> None:
    revision = "a" * 40
    lock = _write_framework_project(
        tmp_path,
        source_revision="b" * 40,
    )

    check = inspect_acwm_revision_lock(
        lock,
        actual_revision=revision,
        actual_worktree_dirty=False,
    )

    assert check.status == "failed"
    assert "Git Revision" in str(check.repair)
    assert revision in str(check.repair)


def test_framework_lock_rejects_resolved_uv_lock_drift(tmp_path: Path) -> None:
    revision = "a" * 40
    lock = _write_framework_project(
        tmp_path,
        resolved_version="0.5.0",
        resolved_revision="b" * 40,
    )

    check = inspect_acwm_revision_lock(
        lock,
        actual_revision=revision,
        actual_worktree_dirty=False,
    )

    assert check.status == "failed"
    assert "uv.lock" in str(check.repair)
    assert "0.5.1" in str(check.repair)


def test_framework_lock_rejects_uv_resolved_revision_drift(tmp_path: Path) -> None:
    revision = "a" * 40
    lock = _write_framework_project(
        tmp_path,
        resolved_version="0.5.1",
        resolved_revision="b" * 40,
    )

    check = inspect_acwm_revision_lock(
        lock,
        actual_revision=revision,
        actual_worktree_dirty=False,
    )

    assert check.status == "failed"
    assert "解析 Revision" in str(check.repair)
    assert revision in str(check.repair)


def test_framework_lock_accepts_reproducible_project_dependency(tmp_path: Path) -> None:
    revision = "a" * 40
    lock = _write_framework_project(
        tmp_path,
        resolved_version="0.5.1",
        resolved_revision=revision,
    )

    check = inspect_acwm_revision_lock(
        lock,
        actual_revision=revision,
        actual_worktree_dirty=False,
    )

    assert check.status == "ready"
    assert check.repair is None


def test_framework_lock_fails_closed_on_malformed_uv_package_entries(
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    lock = _write_framework_project(tmp_path)
    (lock.parent.parent / "uv.lock").write_text(
        'version = 1\npackage = ["not-a-package-table"]\n',
        encoding="utf-8",
    )

    check = inspect_acwm_revision_lock(
        lock,
        actual_revision=revision,
        actual_worktree_dirty=False,
    )

    assert check.status == "failed"
    assert "uv.lock" in str(check.repair)
