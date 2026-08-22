from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

SPARK_MODEL = "gpt-5.3-codex-spark"
METADATA_PATHS = ("tasks/spark/**", "reviews/spark/**")
DEPENDENCY_FILES = {
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "pnpm-lock.yaml",
    "console/package.json",
    "console/pnpm-lock.yaml",
}
RUN_ARTIFACT_FILES = (
    "invocation.json",
    "codex-events.jsonl",
    "last-message.txt",
    "diff.patch",
    "verification.json",
    "result.json",
)
SECRET_PATTERN = re.compile(
    r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|api[_-]?key\s*[:=]|"
    r"password\s*[:=]|token\s*[:=]\s*[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9]{16,})"
)


class SparkTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^SPARK-[A-Z0-9-]+$")
    title: str = Field(min_length=1)
    model: Literal["gpt-5.3-codex-spark"]
    architecture_revision: str = Field(min_length=1)
    base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    kind: Literal["frontend-component", "backend-crud", "test-expansion", "localization"]
    allowed_paths: tuple[str, ...] = Field(min_length=1)
    forbidden_paths: tuple[str, ...] = ()
    contracts: tuple[str, ...] = Field(min_length=1)
    acceptance: tuple[str, ...] = Field(min_length=1)
    verification: tuple[str, ...] = Field(min_length=1)

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def safe_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Spark paths must be repository-relative")
        return values


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str


class SparkResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    status: Literal["candidate", "failed", "blocked", "accepted", "rejected"]
    model: str
    base_revision: str
    candidate_revision: str | None = None
    changed_files: tuple[str, ...] = ()
    diff_sha256: str | None = None
    verification: tuple[VerificationResult, ...] = ()
    error_code: str | None = None
    detail: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SparkRunner:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state_root = self.root / ".agent-team-os" / "spark-runs"
        self.worktree_root = self.root / ".agent-team-os" / "spark-worktrees"

    def load_task(self, task_id: str) -> SparkTask:
        matches = list((self.root / "tasks" / "spark").glob(f"{task_id}.*"))
        matches = [path for path in matches if path.suffix in {".yaml", ".yml", ".json"}]
        if len(matches) != 1:
            raise SparkFailure(
                "SPARK_TASK_NOT_FOUND", f"Expected exactly one tracked manifest for {task_id}"
            )
        payload = yaml.safe_load(matches[0].read_text(encoding="utf-8"))
        return SparkTask.model_validate(payload)

    def run(self, task_id: str) -> SparkResult:
        task = self.load_task(task_id)
        run_dir = self.state_root / task.id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._archive_previous_attempt(run_dir)
        try:
            self._preflight(task)
            worktree = self._create_worktree(task)
            self._invoke_spark(task, worktree, run_dir, self._prompt(task))
            result = self._candidate_result(task, worktree, run_dir)
        except SparkFailure as error:
            result = SparkResult(
                task_id=task.id,
                status="blocked" if error.code == "ARCHITECTURE_DECISION_REQUIRED" else "failed",
                model=SPARK_MODEL,
                base_revision=task.base_revision,
                error_code=error.code,
                detail=error.detail,
            )
        except subprocess.TimeoutExpired:
            result = SparkResult(
                task_id=task.id,
                status="failed",
                model=SPARK_MODEL,
                base_revision=task.base_revision,
                error_code="SPARK_TIMEOUT",
                detail="Codex Spark exceeded the 900 second development limit",
            )
        self._write_json(run_dir / "result.json", result.model_dump(mode="json"))
        return result

    def repair(self, task_id: str) -> SparkResult:
        task = self.load_task(task_id)
        previous = self.inspect(task_id)
        if previous.status != "failed" or not (
            previous.error_code or ""
        ).startswith("SPARK_VERIFICATION_"):
            raise SparkFailure(
                "SPARK_REPAIR_NOT_ALLOWED",
                "Only a machine-verification failure may enter a repair turn",
            )
        self._ensure_clean_main()
        worktree = self.worktree_root / task.id
        if not worktree.exists():
            raise SparkFailure("SPARK_WORKTREE_REQUIRED", "Failed Spark Worktree is missing")
        run_dir = self.state_root / task.id
        verification_log = (run_dir / "verification.json").read_text(encoding="utf-8")
        self._archive_previous_attempt(run_dir)
        try:
            self._invoke_spark(
                task,
                worktree,
                run_dir,
                self._repair_prompt(task, verification_log),
            )
            result = self._candidate_result(task, worktree, run_dir)
        except SparkFailure as error:
            result = SparkResult(
                task_id=task.id,
                status="blocked"
                if error.code == "ARCHITECTURE_DECISION_REQUIRED"
                else "failed",
                model=SPARK_MODEL,
                base_revision=task.base_revision,
                error_code=error.code,
                detail=error.detail,
            )
        except subprocess.TimeoutExpired:
            result = SparkResult(
                task_id=task.id,
                status="failed",
                model=SPARK_MODEL,
                base_revision=task.base_revision,
                error_code="SPARK_TIMEOUT",
                detail="Codex Spark exceeded the 900 second repair limit",
            )
        self._write_json(run_dir / "result.json", result.model_dump(mode="json"))
        return result

    @staticmethod
    def _archive_previous_attempt(run_dir: Path) -> None:
        existing = [run_dir / name for name in RUN_ARTIFACT_FILES if (run_dir / name).exists()]
        if not existing:
            return
        attempts = run_dir / "attempts"
        attempts.mkdir(parents=True, exist_ok=True)
        numbers = [
            int(path.name)
            for path in attempts.iterdir()
            if path.is_dir() and path.name.isdigit()
        ]
        archive = attempts / f"{max(numbers, default=0) + 1:03d}"
        archive.mkdir()
        for path in existing:
            path.replace(archive / path.name)

    def inspect(self, task_id: str) -> SparkResult:
        path = self.state_root / task_id / "result.json"
        if not path.exists():
            raise SparkFailure("SPARK_RUN_NOT_FOUND", f"No run exists for {task_id}")
        return SparkResult.model_validate_json(path.read_text(encoding="utf-8"))

    def accept(self, task_id: str) -> SparkResult:
        task = self.load_task(task_id)
        result = self.inspect(task_id)
        if result.status != "candidate" or result.candidate_revision is None:
            raise SparkFailure("SPARK_CANDIDATE_REQUIRED", "Task has no acceptable candidate")
        self._ensure_clean_main()
        self._ensure_metadata_only_since_base(task)
        review_path = self.root / "reviews" / "spark" / f"{task.id}.json"
        if not review_path.exists():
            raise SparkFailure("SPARK_REVIEW_REQUIRED", "Tracked architecture review is missing")
        review = json.loads(review_path.read_text(encoding="utf-8"))
        if (
            review.get("decision") != "accept"
            or review.get("candidate_revision") != result.candidate_revision
        ):
            raise SparkFailure("SPARK_REVIEW_MISMATCH", "Review does not accept this candidate")
        self._git(self.root, "cherry-pick", result.candidate_revision)
        accepted = result.model_copy(update={"status": "accepted"})
        self._write_json(
            self.state_root / task.id / "result.json", accepted.model_dump(mode="json")
        )
        self._cleanup_worktree(task)
        return accepted

    def reject(self, task_id: str) -> SparkResult:
        task = self.load_task(task_id)
        result = self.inspect(task_id)
        rejected = result.model_copy(update={"status": "rejected"})
        self._write_json(
            self.state_root / task_id / "result.json", rejected.model_dump(mode="json")
        )
        self._cleanup_worktree(task)
        return rejected

    def _cleanup_worktree(self, task: SparkTask) -> None:
        worktree = self.worktree_root / task.id
        branch = f"codex/spark/{task.id.lower()}"
        if worktree.exists():
            self._git(self.root, "worktree", "remove", "--force", str(worktree))
        if self._git(self.root, "branch", "--list", branch).stdout.strip():
            self._git(self.root, "branch", "-D", branch)

    def _preflight(self, task: SparkTask) -> None:
        if task.model != SPARK_MODEL:
            raise SparkFailure("SPARK_MODEL_MISMATCH", "Model fallback is forbidden")
        if not shutil.which("codex"):
            raise SparkFailure("SPARK_MODEL_UNAVAILABLE", "Codex CLI is not installed")
        self._ensure_clean_main()
        self._ensure_metadata_only_since_base(task)
        active = [path for path in self.worktree_root.glob("SPARK-*") if path.exists()]
        if len(active) >= 2:
            raise SparkFailure("SPARK_CONCURRENCY_LIMIT", "At most two Spark Worktrees may exist")

    def _ensure_clean_main(self) -> None:
        if self._git(self.root, "status", "--porcelain").stdout.strip():
            raise SparkFailure("SPARK_MAIN_DIRTY", "Main worktree must be clean")

    def _ensure_metadata_only_since_base(self, task: SparkTask) -> None:
        self._git(self.root, "cat-file", "-e", f"{task.base_revision}^{{commit}}")
        changed = self._git(
            self.root, "diff", "--name-only", f"{task.base_revision}..HEAD", "--"
        ).stdout.splitlines()
        disallowed = [path for path in changed if not _matches_any(path, METADATA_PATHS)]
        if disallowed:
            raise SparkFailure(
                "SPARK_BASE_MISMATCH",
                "Product code changed after Base Revision: " + ", ".join(disallowed),
            )

    def _create_worktree(self, task: SparkTask) -> Path:
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        worktree = self.worktree_root / task.id
        branch = f"codex/spark/{task.id.lower()}"
        if worktree.exists():
            raise SparkFailure("SPARK_WORKTREE_EXISTS", str(worktree))
        branches = self._git(self.root, "branch", "--list", branch).stdout.strip()
        if branches:
            raise SparkFailure("SPARK_BRANCH_EXISTS", branch)
        self._git(
            self.root,
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree),
            task.base_revision,
        )
        if any(pattern.startswith("console/") for pattern in task.allowed_paths):
            completed = subprocess.run(
                [
                    "pnpm",
                    "install",
                    "--offline",
                    "--frozen-lockfile",
                    "--ignore-scripts",
                ],
                cwd=worktree / "console",
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if completed.returncode != 0:
                raise SparkFailure("SPARK_DEPENDENCY_PREP_FAILED", completed.stderr[-4_000:])
        return worktree

    def _invoke_spark(
        self,
        task: SparkTask,
        worktree: Path,
        run_dir: Path,
        prompt: str,
    ) -> None:
        invocation = {
            "task_id": task.id,
            "model": SPARK_MODEL,
            "base_revision": task.base_revision,
            "worktree": str(worktree),
            "sandbox": "workspace-write",
            "ephemeral": True,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self._write_json(run_dir / "invocation.json", invocation)
        events = run_dir / "codex-events.jsonl"
        last_message = run_dir / "last-message.txt"
        command = [
            "codex",
            "exec",
            "--model",
            SPARK_MODEL,
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--json",
            "--cd",
            str(worktree),
            "--output-last-message",
            str(last_message),
            prompt,
        ]
        with events.open("w", encoding="utf-8") as output:
            completed = subprocess.run(
                command,
                cwd=worktree,
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
                timeout=900,
                check=False,
            )
        if completed.returncode != 0:
            event_error = _last_event_error(events)
            detail = event_error or completed.stderr[-4_000:]
            code = (
                "SPARK_MODEL_UNAVAILABLE"
                if event_error and "usage limit" in event_error.lower()
                else "SPARK_MODEL_FAILED"
            )
            raise SparkFailure(code, detail)
        self._verify_event_stream(events, task)

    def _candidate_result(
        self, task: SparkTask, worktree: Path, run_dir: Path
    ) -> SparkResult:
        changed_files = self._changed_files(worktree)
        self._verify_diff_scope(task, changed_files)
        diff = self._git(worktree, "diff", "--binary", task.base_revision, "--").stdout
        if not diff.strip():
            raise SparkFailure("SPARK_NO_CHANGES", "Spark produced no repository change")
        if SECRET_PATTERN.search(diff):
            raise SparkFailure("SPARK_SECRET_DETECTED", "Candidate Diff matched secret policy")
        (run_dir / "diff.patch").write_text(diff, encoding="utf-8")
        verification = self._verify_commands(task, worktree, run_dir)
        self._git(worktree, "add", "--", *changed_files)
        self._git(worktree, "commit", "-m", f"spark({task.id.lower()}): {task.title}")
        candidate = self._git(worktree, "rev-parse", "HEAD").stdout.strip()
        return SparkResult(
            task_id=task.id,
            status="candidate",
            model=SPARK_MODEL,
            base_revision=task.base_revision,
            candidate_revision=candidate,
            changed_files=tuple(changed_files),
            diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
            verification=verification,
        )

    def _prompt(self, task: SparkTask) -> str:
        return f"""Implement the following decision-complete Agent-Team-OS development task.
You are a development worker, not a product runtime Agent. Do not change architecture.
If a required decision is missing, make no changes and end with
blocked/ARCHITECTURE_DECISION_REQUIRED.

Task manifest:
{json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2)}

Mandatory architecture references:
- AGENTS.md
- CONTEXT.md
- docs/architecture/ADR-0002-MODULAR-MONOLITH.md
- docs/architecture/ADR-0004-HTTP-PROBLEMS-AND-TYPES.md
- docs/architecture/ADR-0007-WEB-FEATURE-SLICES.md
- docs/design/CONTROL-CONSOLE.md

Modify only allowed_paths. Do not add dependencies, edit lockfiles, migrations, public contracts,
state machines, authentication, permissions, evidence semantics, ACWM/AgentScope/Hermes/Codex
adapters, Git apply policy, concurrency or recovery behavior. Run only the listed verification.
"""

    def _repair_prompt(self, task: SparkTask, verification_log: str) -> str:
        return f"""Repair the existing implementation for this decision-complete task.
You are still the exact Spark worker in the same isolated Worktree. Make only the smallest
changes required to pass machine verification. Do not change architecture, contracts,
dependencies or allowed scope. Run every verification command before the final response.

Task manifest:
{json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2)}

Failed machine verification:
{verification_log}

If the failure cannot be fixed inside allowed_paths, make no additional changes and end with
blocked/ARCHITECTURE_DECISION_REQUIRED. Do not commit, merge or push.
"""

    def _verify_event_stream(self, events: Path, task: SparkTask) -> None:
        lines = [line for line in events.read_text(encoding="utf-8").splitlines() if line]
        if not lines:
            raise SparkFailure("SPARK_IDENTITY_UNVERIFIED", "Codex emitted no JSON events")
        parsed: list[object] = []
        for line in lines:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise SparkFailure("SPARK_EVENT_STREAM_INVALID", str(error)) from error
        messages = [
            str(item["item"].get("text", ""))
            for item in parsed
            if isinstance(item, dict)
            and isinstance(item.get("item"), dict)
            and item["item"].get("type") == "agent_message"
        ]
        if messages and _reports_architecture_block(messages[-1]):
            raise SparkFailure(
                "ARCHITECTURE_DECISION_REQUIRED", "Spark reported a missing architecture decision"
            )
        invocation = json.loads((self.state_root / task.id / "invocation.json").read_text())
        if invocation.get("model") != SPARK_MODEL:
            raise SparkFailure("SPARK_IDENTITY_UNVERIFIED", "Invocation model identity changed")

    def _changed_files(self, worktree: Path) -> list[str]:
        output = self._git(worktree, "status", "--porcelain=v1", "-z").stdout
        entries = [value for value in output.split("\0") if value]
        files = sorted({entry[3:] for entry in entries})
        return files

    def _verify_diff_scope(self, task: SparkTask, changed_files: list[str]) -> None:
        if not changed_files:
            raise SparkFailure("SPARK_NO_CHANGES", "Spark produced no changed file")
        for path in changed_files:
            if path in DEPENDENCY_FILES or Path(path).name in DEPENDENCY_FILES:
                raise SparkFailure("SPARK_DEPENDENCY_CHANGE", path)
            if _matches_any(path, task.forbidden_paths):
                raise SparkFailure("SPARK_FORBIDDEN_PATH", path)
            if not _matches_any(path, task.allowed_paths):
                raise SparkFailure("SPARK_PATH_VIOLATION", path)

    def _verify_commands(
        self, task: SparkTask, worktree: Path, run_dir: Path
    ) -> tuple[VerificationResult, ...]:
        results: list[VerificationResult] = []
        logs: list[dict[str, object]] = []
        for index, command in enumerate(task.verification, start=1):
            environment = {**os.environ, "CI": "1"}
            environment.pop("VIRTUAL_ENV", None)
            completed = subprocess.run(
                ["/bin/sh", "-lc", command],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                env=environment,
            )
            result = VerificationResult(
                command=command,
                exit_code=completed.returncode,
                stdout_sha256=hashlib.sha256(completed.stdout.encode()).hexdigest(),
                stderr_sha256=hashlib.sha256(completed.stderr.encode()).hexdigest(),
            )
            results.append(result)
            logs.append(
                {
                    **result.model_dump(mode="json"),
                    "stdout": completed.stdout[-8_000:],
                    "stderr": completed.stderr[-8_000:],
                }
            )
            if completed.returncode != 0:
                self._write_json(run_dir / "verification.json", logs)
                raise SparkFailure("SPARK_VERIFICATION_FAILED", f"Command {index} failed")
            violation = _verification_output_violation(
                f"{completed.stdout}\n{completed.stderr}"
            )
            if violation is not None:
                self._write_json(run_dir / "verification.json", logs)
                raise SparkFailure(
                    f"SPARK_VERIFICATION_{violation.upper()}",
                    f"Command {index} reported {violation}",
                )
        self._write_json(run_dir / "verification.json", logs)
        return tuple(results)

    @staticmethod
    def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=False
        )
        if completed.returncode != 0:
            raise SparkFailure("SPARK_GIT_FAILED", completed.stderr.strip())
        return completed

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )


class SparkFailure(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path.removeprefix("./")
    return any(
        fnmatch.fnmatchcase(normalized, pattern)
        or (pattern.endswith("/**") and normalized == pattern.removesuffix("/**"))
        for pattern in patterns
    )


def _reports_architecture_block(message: str) -> bool:
    return bool(
        re.match(
            r"^\s*blocked/ARCHITECTURE_DECISION_REQUIRED(?:\s|$)",
            message,
            flags=re.IGNORECASE,
        )
    )


def _verification_output_violation(output: str) -> str | None:
    for line in output.splitlines():
        normalized = line.strip().lower()
        if re.search(r"\bwarn(?:ing|ings)?\b", normalized) and not re.search(
            r"\b0\s+warnings?\b", normalized
        ):
            return "warning"
        if re.search(r"\bskipped\b", normalized) and not re.search(
            r"\b0\s+skipped\b", normalized
        ):
            return "skipped"
    return None


def _last_event_error(events: Path) -> str | None:
    messages: list[str] = []
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") == "error" and isinstance(item.get("message"), str):
            messages.append(item["message"])
        error = item.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            messages.append(error["message"])
    return messages[-1] if messages else None
