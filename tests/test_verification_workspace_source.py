from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_team_os.infrastructure.git import ExternalGitBinding
from agent_team_os.infrastructure.verification import workspace_source
from agent_team_os.shared.errors import ProductError


def test_configuration_reader_retries_transient_read_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision = "a" * 40
    clone_calls = 0
    sleeps: list[float] = []

    def run(args, **_kwargs):
        nonlocal clone_calls
        if args[1] == "clone":
            clone_calls += 1
            if clone_calls == 1:
                Path(args[-1]).mkdir()
                return subprocess.CompletedProcess(args, 128, b"", b"transient")
            assert not Path(args[-1]).exists()
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if "rev-parse" in args:
            return subprocess.CompletedProcess(args, 0, f"{revision}\n".encode(), b"")
        if "ls-tree" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                b"100644 blob deadbeef\tverification.json\n",
                b"",
            )
        if "show" in args:
            return subprocess.CompletedProcess(args, 0, b'{"profile":"test"}\n', b"")
        raise AssertionError(args)

    monkeypatch.setattr(workspace_source.subprocess, "run", run)
    monkeypatch.setattr(workspace_source.time, "sleep", sleeps.append)

    with workspace_source.read_configuration(
        ExternalGitBinding(remote_uri=str(tmp_path / "remote.git")),
        revision,
        ("verification.json",),
    ) as configuration:
        assert (configuration / "verification.json").read_text() == '{"profile":"test"}\n'

    assert clone_calls == 2
    assert sleeps == [1.0]


def test_configuration_reader_fails_closed_after_bounded_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    sleeps: list[float] = []

    def run(args, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args, 128, b"", b"credential must not leak")

    monkeypatch.setattr(workspace_source.subprocess, "run", run)
    monkeypatch.setattr(workspace_source.time, "sleep", sleeps.append)

    with pytest.raises(ProductError) as error, workspace_source.read_configuration(
        ExternalGitBinding(remote_uri=str(tmp_path / "remote.git")),
        "b" * 40,
        ("verification.json",),
    ):
        pass

    assert error.value.code == "WORKCELL_VERIFICATION_ENVIRONMENT_UNQUALIFIED"
    assert "连续 3 次失败" in error.value.detail
    assert "credential must not leak" not in error.value.detail
    assert calls == 3
    assert sleeps == [1.0, 2.0]
