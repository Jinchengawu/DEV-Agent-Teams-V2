"""Built-in Git sandbox with immutable candidates and compare-and-swap apply."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .delivery import ApplyReceipt, CandidateChange, VerificationRun
from .shared.repositories import RepositoryRole


class SandboxError(RuntimeError):
    code = "SANDBOX_ERROR"


class EmptyCandidateError(SandboxError):
    code = "EMPTY_CANDIDATE"


class PathPolicyError(SandboxError):
    code = "PATH_POLICY_VIOLATION"


class SecretMaterialError(SandboxError):
    code = "SECRET_MATERIAL_DETECTED"


class EvidenceMismatchError(SandboxError):
    code = "EVIDENCE_MISMATCH"


class BaseRevisionConflictError(SandboxError):
    code = "BASE_REVISION_CHANGED"


class SandboxPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_paths: tuple[str, ...] = ("src/**", "tests/**")
    verification_command: tuple[str, ...] = (
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
    )
    timeout_seconds: int = Field(default=60, ge=1, le=300)


class GitSandbox:
    def __init__(self, root: Path, repository_role: RepositoryRole = "backend") -> None:
        self.root = root.resolve()
        self.repository_role = repository_role
        self.bare_repo = self.root / "backend-demo.git"
        self.worktrees = self.root / "worktrees"

    def ensure_initialized(self) -> None:
        if self.bare_repo.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.worktrees.mkdir(parents=True, exist_ok=True)
        self._run("git", "init", "--bare", "--initial-branch=main", str(self.bare_repo))
        with tempfile.TemporaryDirectory(prefix="agent-team-os-seed-") as directory:
            seed = Path(directory)
            self._run("git", "init", "--initial-branch=main", cwd=seed)
            for relative_path, content in _seed_files(self.repository_role).items():
                path = seed / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            self._run("git", "add", "--all", cwd=seed)
            self._run(
                "git",
                "commit",
                "-m",
                f"seed {self.repository_role} demo",
                cwd=seed,
            )
            self._run("git", "remote", "add", "origin", str(self.bare_repo), cwd=seed)
            self._run("git", "push", "origin", "main", cwd=seed)

    def reset(self) -> str:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.ensure_initialized()
        return self.main_revision()

    def main_revision(self) -> str:
        self.ensure_initialized()
        return self._git_bare("rev-parse", "refs/heads/main").strip()

    def create_worktree(self, delivery_id: str, base_revision: str) -> Path:
        self.ensure_initialized()
        worktree = self.worktrees / self._safe_id(delivery_id)
        if worktree.exists():
            self._run(
                "git",
                "merge-base",
                "--is-ancestor",
                base_revision,
                "HEAD",
                cwd=worktree,
            )
            return worktree
        self._git_bare("worktree", "add", "--detach", str(worktree), base_revision)
        return worktree

    def worktree_for(self, delivery_id: str) -> Path:
        return self.worktrees / self._safe_id(delivery_id)

    def create_candidate(
        self,
        delivery_id: str,
        *,
        base_revision: str,
        policy: SandboxPolicy,
    ) -> CandidateChange:
        worktree = self.worktree_for(delivery_id)
        changed_files = self._working_tree_files(worktree)
        if not changed_files:
            raise EmptyCandidateError("Codex produced no file changes")
        self._validate_paths(changed_files, policy)
        self._scan_secrets(worktree, changed_files)
        self._run("git", "add", "--all", cwd=worktree)
        self._run("git", "commit", "-m", f"candidate {delivery_id}", cwd=worktree)
        candidate_revision = self._git("rev-parse", "HEAD", cwd=worktree).strip()
        candidate_ref = f"refs/candidates/{self._safe_id(delivery_id)}/{candidate_revision}"
        self._git_bare("update-ref", candidate_ref, candidate_revision, "0" * 40)
        unified_diff = self._git_bare(
            "diff", "--binary", "--no-ext-diff", base_revision, candidate_revision, "--"
        )
        changed_files = tuple(
            sorted(
                line
                for line in self._git_bare(
                    "diff", "--name-only", base_revision, candidate_revision
                ).splitlines()
                if line
            )
        )
        self._validate_paths(changed_files, policy)
        self._scan_secrets(worktree, changed_files)
        return CandidateChange(
            base_revision=base_revision,
            candidate_revision=candidate_revision,
            diff_sha256=hashlib.sha256(unified_diff.encode()).hexdigest(),
            changed_files=changed_files,
            candidate_ref=candidate_ref,
            unified_diff=unified_diff,
        )

    def verify_candidate(
        self,
        candidate: CandidateChange,
        policy: SandboxPolicy,
        *,
        acceptance_ids: tuple[str, ...] = (),
    ) -> VerificationRun:
        actual_diff = self._git_bare(
            "diff",
            "--binary",
            "--no-ext-diff",
            candidate.base_revision,
            candidate.candidate_revision,
            "--",
        )
        actual_files = tuple(
            sorted(
                line
                for line in self._git_bare(
                    "diff", "--name-only", candidate.base_revision, candidate.candidate_revision
                ).splitlines()
                if line
            )
        )
        if (
            hashlib.sha256(actual_diff.encode()).hexdigest() != candidate.diff_sha256
            or actual_files != candidate.changed_files
            or actual_diff != candidate.unified_diff
        ):
            raise EvidenceMismatchError("Candidate evidence does not match Git")
        self._validate_paths(actual_files, policy)
        worktree = self._worktree_for_revision(candidate.candidate_revision)
        runtime_command = (
            (sys.executable, *policy.verification_command[1:])
            if policy.verification_command[0] == "python"
            else policy.verification_command
        )
        try:
            result = subprocess.run(
                runtime_command,
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=policy.timeout_seconds,
                check=False,
            )
            log = self._redact(result.stdout + result.stderr)
            exit_code = result.returncode
        except subprocess.TimeoutExpired as error:
            stdout = (
                error.stdout.decode(errors="replace")
                if isinstance(error.stdout, bytes)
                else error.stdout
            )
            stderr = (
                error.stderr.decode(errors="replace")
                if isinstance(error.stderr, bytes)
                else error.stderr
            )
            output = (stdout or "") + (stderr or "")
            log = self._redact(str(output) + "\nverification timed out")
            exit_code = 124
        return VerificationRun(
            status="passed" if exit_code == 0 else "failed",
            commands=(" ".join(policy.verification_command),),
            exit_code=exit_code,
            log_sha256=hashlib.sha256(log.encode()).hexdigest(),
            redacted_log=log,
            acceptance_ids=acceptance_ids,
        )

    def apply_candidate(self, candidate: CandidateChange) -> ApplyReceipt:
        current = self.main_revision()
        if current == candidate.candidate_revision:
            return ApplyReceipt(
                before_revision=candidate.base_revision,
                candidate_revision=candidate.candidate_revision,
                after_revision=current,
                result="applied",
                recovered=True,
            )
        if current != candidate.base_revision:
            raise BaseRevisionConflictError("Main changed after candidate creation")
        try:
            self._git_bare(
                "update-ref",
                "refs/heads/main",
                candidate.candidate_revision,
                candidate.base_revision,
            )
        except subprocess.CalledProcessError as error:
            raise BaseRevisionConflictError("Main changed after candidate creation") from error
        after = self.main_revision()
        if after != candidate.candidate_revision:
            raise EvidenceMismatchError("Main does not point to the candidate revision")
        return ApplyReceipt(
            before_revision=candidate.base_revision,
            candidate_revision=candidate.candidate_revision,
            after_revision=after,
            result="applied",
        )

    def rollback_candidate(self, receipt: ApplyReceipt) -> str:
        """Compensate an applied candidate only while Main is unchanged."""
        current = self.main_revision()
        if current == receipt.before_revision:
            return current
        if current != receipt.after_revision:
            raise BaseRevisionConflictError("Main changed after repository apply")
        try:
            self._git_bare(
                "update-ref",
                "refs/heads/main",
                receipt.before_revision,
                receipt.after_revision,
            )
        except subprocess.CalledProcessError as error:
            raise BaseRevisionConflictError("Main changed during compensation") from error
        restored = self.main_revision()
        if restored != receipt.before_revision:
            raise EvidenceMismatchError("compensation did not restore reviewed base")
        return restored

    def _working_tree_files(self, worktree: Path) -> tuple[str, ...]:
        modified = self._git("diff", "--name-only", "HEAD", cwd=worktree).splitlines()
        untracked = self._git(
            "ls-files", "--others", "--exclude-standard", cwd=worktree
        ).splitlines()
        return tuple(sorted({name for name in (*modified, *untracked) if name}))

    @staticmethod
    def _validate_paths(changed_files: tuple[str, ...], policy: SandboxPolicy) -> None:
        invalid = [
            name
            for name in changed_files
            if not any(PurePosixPath(name).match(pattern) for pattern in policy.allowed_paths)
        ]
        if invalid:
            raise PathPolicyError("Files outside allowed paths: " + ", ".join(invalid))

    @staticmethod
    def _scan_secrets(worktree: Path, changed_files: tuple[str, ...]) -> None:
        patterns = (
            re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
            re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
            re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
            re.compile(r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*['\"]?[^\s'\"]{8,}"),
        )
        for name in changed_files:
            path = worktree / name
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if any(pattern.search(content) for pattern in patterns):
                raise SecretMaterialError(f"Secret-like material found in {name}")

    def _worktree_for_revision(self, revision: str) -> Path:
        for worktree in self.worktrees.iterdir():
            if not worktree.is_dir():
                continue
            head = self._git("rev-parse", "HEAD", cwd=worktree).strip()
            if head == revision:
                return worktree
        raise SandboxError(f"No worktree for candidate {revision}")

    def _git_bare(self, *args: str) -> str:
        return self._run("git", "--git-dir", str(self.bare_repo), *args)

    def _git(self, *args: str, cwd: Path) -> str:
        return self._run("git", *args, cwd=cwd)

    @staticmethod
    def _safe_id(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise SandboxError("Unsafe delivery id")
        return value

    @staticmethod
    def _redact(value: str) -> str:
        return re.sub(
            r"(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)\S+",
            r"\1[REDACTED]",
            value,
        )[-20_000:]

    @staticmethod
    def _run(*args: str, cwd: Path | None = None) -> str:
        environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Agent-Team-OS",
            "GIT_AUTHOR_EMAIL": "agent-team-os@local",
            "GIT_COMMITTER_NAME": "Agent-Team-OS",
            "GIT_COMMITTER_EMAIL": "agent-team-os@local",
        }
        result = subprocess.run(
            args,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout


def _seed_files(role: RepositoryRole) -> dict[str, str]:
    if role == "frontend":
        return {
            "src/index.html": (
                '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<title>Agent-Team-OS 前端</title></head><body><main id="app">'
                "前端项目</main></body></html>\n"
            ),
            "src/app.js": 'document.documentElement.dataset.app = "frontend-demo";\n',
            "tests/test_frontend.py": (
                "import unittest\nfrom pathlib import Path\n\n"
                "class FrontendSeedTest(unittest.TestCase):\n"
                "    def test_entry_exists(self) -> None:\n"
                "        self.assertIn('zh-CN', Path('src/index.html').read_text())\n"
            ),
        }
    if role == "design":
        return {
            "design/system.md": "# 设计系统\n\n状态：待产品需求与设计 Agent 更新。\n",
            "tests/test_design.py": (
                "import unittest\nfrom pathlib import Path\n\n"
                "class DesignSeedTest(unittest.TestCase):\n"
                "    def test_design_source_exists(self) -> None:\n"
                "        self.assertTrue(Path('design/system.md').is_file())\n"
            ),
        }
    if role == "qa":
        return {
            "reports/README.md": "# QA 报告\n\n机器报告由测试 Agent 生成。\n",
            "tests/test_qa.py": (
                "import unittest\nfrom pathlib import Path\n\n"
                "class QASeedTest(unittest.TestCase):\n"
                "    def test_report_directory_exists(self) -> None:\n"
                "        self.assertTrue(Path('reports').is_dir())\n"
            ),
        }
    return {
        "src/__init__.py": "",
        "src/service.py": (
            '"""Backend demo service."""\n\n\ndef service_info() -> dict[str, str]:\n'
            '    return {"name": "backend-demo"}\n'
        ),
        "tests/test_service.py": (
            "import unittest\n\nfrom src.service import service_info\n\n\n"
            "class ServiceInfoTest(unittest.TestCase):\n"
            "    def test_service_name(self) -> None:\n"
            "        self.assertEqual(service_info()['name'], 'backend-demo')\n\n\n"
            'if __name__ == "__main__":\n    unittest.main()\n'
        ),
    }
