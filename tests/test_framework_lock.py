from pathlib import Path

from agent_team_os.readiness import inspect_acwm_revision_lock


def test_acwm_revision_lock_is_fail_closed_on_mismatch(tmp_path: Path) -> None:
    lock = tmp_path / "framework-lock.json"
    lock.write_text(
        '{"acwm":{"version":"0.4.0","revision":"' + "a" * 40 + '"}}',
        encoding="utf-8",
    )

    ready = inspect_acwm_revision_lock(lock, actual_revision="a" * 40)
    mismatch = inspect_acwm_revision_lock(lock, actual_revision="b" * 40)

    assert ready.status == "ready"
    assert ready.repair is None
    assert mismatch.status == "failed"
    assert "不一致" in str(mismatch.repair)


def test_repository_framework_lock_matches_checked_out_acwm() -> None:
    project_root = Path(__file__).parents[1]

    check = inspect_acwm_revision_lock(
        project_root / "config" / "framework-lock.json"
    )

    assert check.status == "ready", check.repair
