from pathlib import Path

import pytest

from agent_team_os import release


@pytest.mark.parametrize("drift", ["product", "acwm", "worktree"])
def test_gate_rejects_mid_run_revision_change_even_if_worktree_is_clean(monkeypatch, drift):
    monkeypatch.setattr(
        release, "_git_status", lambda root: " M source.py" if drift == "worktree" else ""
    )
    monkeypatch.setattr(
        release, "_git_revision", lambda root: "2" * 40 if drift == "product" else "1" * 40
    )
    monkeypatch.setattr(
        release, "imported_acwm_revision", lambda: "4" * 40 if drift == "acwm" else "3" * 40
    )
    with pytest.raises(RuntimeError, match="revision changed"):
        release._require_unchanged_gate_build(Path.cwd(), "1" * 40, "3" * 40)


def test_gate_accepts_unchanged_clean_product_and_dependency(monkeypatch):
    monkeypatch.setattr(release, "_git_status", lambda root: "")
    monkeypatch.setattr(release, "_git_revision", lambda root: "1" * 40)
    monkeypatch.setattr(release, "imported_acwm_revision", lambda: "3" * 40)
    release._require_unchanged_gate_build(Path.cwd(), "1" * 40, "3" * 40)
