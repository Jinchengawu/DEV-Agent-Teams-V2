from pathlib import Path

import pytest

from agent_team_os.delivery import CandidateChange
from agent_team_os.git_sandbox import (
    BaseRevisionConflictError,
    EmptyCandidateError,
    GitSandbox,
    PathPolicyError,
    SandboxPolicy,
    SecretMaterialError,
)


def test_real_candidate_is_verified_and_applied_with_compare_and_swap(tmp_path: Path) -> None:
    sandbox = GitSandbox(tmp_path / "runtime")
    sandbox.ensure_initialized()
    base_revision = sandbox.main_revision()
    worktree = sandbox.create_worktree("delivery-1", base_revision)

    service = worktree / "src" / "service.py"
    service.write_text(
        service.read_text(encoding="utf-8")
        + '\n\ndef health() -> dict[str, str]:\n    return {"status": "ok"}\n',
        encoding="utf-8",
    )
    test_file = worktree / "tests" / "test_health.py"
    test_file.write_text(
        """import unittest

from src.service import health


class HealthTest(unittest.TestCase):
    def test_health_is_ok(self) -> None:
        self.assertEqual(health(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
""",
        encoding="utf-8",
    )

    candidate = sandbox.create_candidate(
        "delivery-1",
        base_revision=base_revision,
        policy=SandboxPolicy(),
    )
    verification = sandbox.verify_candidate(candidate, SandboxPolicy())

    assert candidate.base_revision == base_revision
    assert candidate.candidate_revision != base_revision
    assert candidate.changed_files == ("src/service.py", "tests/test_health.py")
    assert candidate.unified_diff
    assert verification.status == "passed"
    assert sandbox.main_revision() == base_revision

    receipt = sandbox.apply_candidate(candidate)

    assert receipt.before_revision == base_revision
    assert receipt.candidate_revision == candidate.candidate_revision
    assert receipt.after_revision == candidate.candidate_revision
    assert sandbox.main_revision() == candidate.candidate_revision


def test_candidate_rejects_empty_out_of_scope_and_secret_changes(tmp_path: Path) -> None:
    sandbox = GitSandbox(tmp_path / "runtime")
    sandbox.ensure_initialized()
    base = sandbox.main_revision()

    sandbox.create_worktree("empty", base)
    with pytest.raises(EmptyCandidateError):
        sandbox.create_candidate("empty", base_revision=base, policy=SandboxPolicy())

    outside = sandbox.create_worktree("outside", base)
    (outside / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    with pytest.raises(PathPolicyError):
        sandbox.create_candidate("outside", base_revision=base, policy=SandboxPolicy())

    secret = sandbox.create_worktree("secret", base)
    (secret / "src" / "leak.py").write_text(
        'api_key = "sk-1234567890abcdef1234567890"\n', encoding="utf-8"
    )
    with pytest.raises(SecretMaterialError):
        sandbox.create_candidate("secret", base_revision=base, policy=SandboxPolicy())


def test_failed_verification_and_changed_base_never_apply(tmp_path: Path) -> None:
    sandbox = GitSandbox(tmp_path / "runtime")
    sandbox.ensure_initialized()
    base = sandbox.main_revision()
    worktree = sandbox.create_worktree("broken", base)
    (worktree / "tests" / "test_broken.py").write_text(
        "import unittest\n\n"
        "class Broken(unittest.TestCase):\n"
        "    def test_broken(self) -> None:\n"
        "        self.fail('broken')\n",
        encoding="utf-8",
    )
    candidate = sandbox.create_candidate("broken", base_revision=base, policy=SandboxPolicy())

    assert sandbox.verify_candidate(candidate, SandboxPolicy()).status == "failed"

    fake = CandidateChange(
        base_revision="0" * 40,
        candidate_revision=candidate.candidate_revision,
        diff_sha256=candidate.diff_sha256,
        changed_files=candidate.changed_files,
        candidate_ref=candidate.candidate_ref,
        unified_diff=candidate.unified_diff,
    )
    with pytest.raises(BaseRevisionConflictError):
        sandbox.apply_candidate(fake)
    assert sandbox.main_revision() == base
