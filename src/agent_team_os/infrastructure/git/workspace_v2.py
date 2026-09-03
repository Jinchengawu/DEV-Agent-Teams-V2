from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from ...modules.releases import RemoteApplyReceipt, WorkspaceCandidateV2
from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_json
from .external import ExternalGitBinding, external_git_environment


class ExternalWriterPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_paths: tuple[str, ...] = Field(min_length=1)


class ExternalWriterWorkspace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    workspace_binding_id: str
    delivery_id: str
    workcell_key: str
    repository_uri: str
    base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_branch: str
    cache_root: Path
    worktree: Path
    credential_reference: str | None = None


class ExternalCandidateEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256
    candidate_branch: str
    changed_files: tuple[str, ...]


class ExternalGitWorkspaceManager:
    """Create isolated Writer worktrees and immutable detached Reviewer views."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def prepare_writer(
        self,
        *,
        workspace_binding_id: str,
        delivery_id: str,
        workcell_key: str,
        binding: ExternalGitBinding,
        expected_base_revision: str,
    ) -> ExternalWriterWorkspace:
        _safe_identifier(workspace_binding_id)
        _safe_identifier(delivery_id)
        _safe_identifier(workcell_key)
        candidate_branch = f"agent-team-os/{delivery_id}/{workcell_key}"
        cache_root = self.root / "bindings" / workspace_binding_id / "repository"
        worktree = self.root / "writers" / delivery_id / workcell_key
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        with external_git_environment(binding.credential_reference) as environment:
            if not (cache_root / ".git").is_dir():
                _git(
                    "clone",
                    "--no-checkout",
                    "--origin",
                    "origin",
                    binding.remote_uri,
                    str(cache_root),
                    environment=environment,
                )
            else:
                _git(
                    "remote",
                    "set-url",
                    "origin",
                    binding.remote_uri,
                    cwd=cache_root,
                    environment=environment,
                )
            _git(
                "fetch",
                "--prune",
                "--no-tags",
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
                cwd=cache_root,
                environment=environment,
            )
            remote_main = _git(
                "rev-parse",
                "refs/remotes/origin/main",
                cwd=cache_root,
                environment=environment,
            ).strip()
            if remote_main != expected_base_revision:
                raise _git_error(
                    "EXTERNAL_GIT_BASE_REVISION_CHANGED",
                    "远端 main 已偏离 Delivery 冻结的 Base Revision。",
                )
            remote_candidate = _optional_revision(
                cache_root,
                f"refs/remotes/origin/{candidate_branch}",
                environment,
            )
            start_revision = remote_candidate or expected_base_revision
            if remote_candidate is not None:
                _git(
                    "merge-base",
                    "--is-ancestor",
                    expected_base_revision,
                    remote_candidate,
                    cwd=cache_root,
                    environment=environment,
                )
            if worktree.exists():
                actual_branch = _git(
                    "branch",
                    "--show-current",
                    cwd=worktree,
                    environment=environment,
                ).strip()
                if actual_branch != candidate_branch:
                    raise _git_error(
                        "EXTERNAL_WRITER_WORKTREE_CONFLICT",
                        "现有 Writer Worktree 属于其他 Candidate Branch。",
                    )
            else:
                worktree.parent.mkdir(parents=True, exist_ok=True)
                _git(
                    "worktree",
                    "add",
                    "-B",
                    candidate_branch,
                    str(worktree),
                    start_revision,
                    cwd=cache_root,
                    environment=environment,
                )
        return ExternalWriterWorkspace(
            workspace_binding_id=workspace_binding_id,
            delivery_id=delivery_id,
            workcell_key=workcell_key,
            repository_uri=binding.remote_uri,
            base_revision=expected_base_revision,
            candidate_branch=candidate_branch,
            cache_root=cache_root,
            worktree=worktree,
            credential_reference=binding.credential_reference,
        )

    def freeze_candidate(
        self,
        workspace: ExternalWriterWorkspace,
        *,
        policy: ExternalWriterPolicy,
    ) -> ExternalCandidateEvidence:
        with external_git_environment(workspace.credential_reference) as environment:
            changed = _working_tree_files(workspace.worktree, environment)
            if changed:
                _validate_changed_files(workspace.worktree, changed, policy)
                _git("add", "--all", cwd=workspace.worktree, environment=environment)
                _git(
                    "-c",
                    "user.name=Agent-Team-OS",
                    "-c",
                    "user.email=agent-team-os@local",
                    "commit",
                    "-m",
                    f"candidate {workspace.delivery_id}/{workspace.workcell_key}",
                    cwd=workspace.worktree,
                    environment=environment,
                )
            candidate_revision = _git(
                "rev-parse",
                "HEAD",
                cwd=workspace.worktree,
                environment=environment,
            ).strip()
            if candidate_revision == workspace.base_revision:
                raise _git_error(
                    "EMPTY_WORKSPACE_CANDIDATE",
                    "Writer 没有产生相对 Base Revision 的 Candidate Commit。",
                )
            _git(
                "merge-base",
                "--is-ancestor",
                workspace.base_revision,
                candidate_revision,
                cwd=workspace.worktree,
                environment=environment,
            )
            final_files = tuple(
                sorted(
                    line
                    for line in _git(
                        "diff",
                        "--name-only",
                        workspace.base_revision,
                        candidate_revision,
                        cwd=workspace.worktree,
                        environment=environment,
                    ).splitlines()
                    if line
                )
            )
            _validate_changed_files(workspace.worktree, final_files, policy)
            unified_diff = _git(
                "diff",
                "--binary",
                "--no-ext-diff",
                workspace.base_revision,
                candidate_revision,
                "--",
                cwd=workspace.worktree,
                environment=environment,
            )
            _git(
                "push",
                "--porcelain",
                "origin",
                f"refs/heads/{workspace.candidate_branch}:"
                f"refs/heads/{workspace.candidate_branch}",
                cwd=workspace.worktree,
                environment=environment,
            )
            remote_candidate = _git(
                "ls-remote",
                "--exit-code",
                "origin",
                f"refs/heads/{workspace.candidate_branch}",
                cwd=workspace.worktree,
                environment=environment,
            ).split("\t", 1)[0]
            if remote_candidate != candidate_revision:
                raise _git_error(
                    "EXTERNAL_CANDIDATE_PUSH_READBACK_MISMATCH",
                    "远端 Candidate Branch SHA 回读不一致。",
                )
        return ExternalCandidateEvidence(
            base_revision=workspace.base_revision,
            candidate_revision=candidate_revision,
            diff_sha256=Sha256.validate(hashlib.sha256(unified_diff.encode()).hexdigest()),
            candidate_branch=workspace.candidate_branch,
            changed_files=final_files,
        )

    def prepare_review_view(
        self,
        workspace: ExternalWriterWorkspace,
        *,
        candidate_revision: str,
    ) -> Path:
        review = (
            self.root
            / "reviewers"
            / workspace.delivery_id
            / workspace.workcell_key
            / candidate_revision[:16]
        )
        with external_git_environment(workspace.credential_reference) as environment:
            remote_candidate = _git(
                "ls-remote",
                "--exit-code",
                workspace.repository_uri,
                f"refs/heads/{workspace.candidate_branch}",
                environment=environment,
            ).split("\t", 1)[0]
            if remote_candidate != candidate_revision:
                raise _git_error(
                    "REVIEW_CANDIDATE_SHA_MISMATCH",
                    "Reviewer 请求的 Candidate SHA 不等于远端 Candidate Branch。",
                )
            if not review.exists():
                review.parent.mkdir(parents=True, exist_ok=True)
                _git(
                    "worktree",
                    "add",
                    "--detach",
                    str(review),
                    candidate_revision,
                    cwd=workspace.cache_root,
                    environment=environment,
                )
        _make_read_only(review)
        return review


class ExternalForwardGitRemote:
    """Apply a remote Candidate Branch to main with a non-force fast-forward push."""

    def __init__(
        self,
        binding_resolver: Callable[[str], ExternalGitBinding],
    ) -> None:
        self.binding_resolver = binding_resolver

    def revision(self, candidate: WorkspaceCandidateV2) -> str:
        binding = self._binding(candidate)
        with external_git_environment(binding.credential_reference) as environment:
            return _git(
                "ls-remote",
                "--exit-code",
                binding.remote_uri,
                "refs/heads/main",
                environment=environment,
            ).split("\t", 1)[0]

    def apply(
        self,
        candidate: WorkspaceCandidateV2,
        *,
        ordinal: int,
    ) -> RemoteApplyReceipt:
        binding = self._binding(candidate)
        with tempfile.TemporaryDirectory(prefix="agent-team-os-forward-apply-") as directory:
            repository = Path(directory) / "repository"
            with external_git_environment(binding.credential_reference) as environment:
                _git("init", "--bare", str(repository), environment=environment)
                _git(
                    "fetch",
                    "--no-tags",
                    binding.remote_uri,
                    "+refs/heads/main:refs/remotes/source/main",
                    f"+refs/heads/{candidate.candidate_branch}:refs/remotes/source/candidate",
                    cwd=repository,
                    environment=environment,
                )
                base = _git(
                    "rev-parse",
                    "refs/remotes/source/main",
                    cwd=repository,
                    environment=environment,
                ).strip()
                head = _git(
                    "rev-parse",
                    "refs/remotes/source/candidate",
                    cwd=repository,
                    environment=environment,
                ).strip()
                if base != candidate.base_revision or head != candidate.candidate_revision:
                    raise _git_error(
                        "EXTERNAL_FORWARD_APPLY_PREFLIGHT_DRIFT",
                        "远端 Base 或 Candidate Branch 已偏离 ReleaseBundle。",
                    )
                _git(
                    "merge-base",
                    "--is-ancestor",
                    base,
                    head,
                    cwd=repository,
                    environment=environment,
                )
                _git(
                    "push",
                    "--porcelain",
                    binding.remote_uri,
                    f"{head}:refs/heads/main",
                    cwd=repository,
                    environment=environment,
                )
        after = self.revision(candidate)
        if after != candidate.candidate_revision:
            raise _git_error(
                "REMOTE_SHA_READBACK_MISMATCH",
                "Fast-forward Push 后远端 main SHA 回读不一致。",
            )
        payload = {
            "delivery_id": candidate.delivery_id,
            "ordinal": ordinal,
            "candidate_id": candidate.id,
            "workcell_key": candidate.workcell_key,
            "repository_uri": candidate.repository_uri,
            "before_revision": candidate.base_revision,
            "candidate_revision": candidate.candidate_revision,
            "after_revision": after,
            "recovered": False,
        }
        return RemoteApplyReceipt.model_validate(
            {**payload, "receipt_sha256": sha256_json(payload)}
        )

    def _binding(self, candidate: WorkspaceCandidateV2) -> ExternalGitBinding:
        binding = self.binding_resolver(candidate.workspace_binding_id)
        if (
            candidate.adapter_type == "external-git"
            and binding.remote_uri != candidate.repository_uri
        ):
            raise _git_error(
                "EXTERNAL_WORKSPACE_BINDING_MISMATCH",
                "Candidate Repository 不等于项目冻结的 Workspace Binding。",
            )
        return binding


def _working_tree_files(worktree: Path, environment: dict[str, str]) -> tuple[str, ...]:
    modified = _git("diff", "--name-only", "HEAD", cwd=worktree, environment=environment)
    untracked = _git(
        "ls-files",
        "--others",
        "--exclude-standard",
        cwd=worktree,
        environment=environment,
    )
    entries = (*modified.splitlines(), *untracked.splitlines())
    return tuple(sorted({item for item in entries if item}))


def _validate_changed_files(
    worktree: Path,
    changed_files: tuple[str, ...],
    policy: ExternalWriterPolicy,
) -> None:
    forbidden = tuple(
        item
        for item in changed_files
        if item == "_bmad"
        or item.startswith("_bmad/")
        or item == ".agents/skills"
        or item.startswith(".agents/skills/")
    )
    if forbidden:
        raise _git_error(
            "METHOD_OVERLAY_WORKSPACE_POLLUTION",
            "Candidate Diff 包含 BMAD/TEA Runtime Overlay 安装产物。",
        )
    invalid = tuple(
        item
        for item in changed_files
        if not any(_matches_allowed_path(item, pattern) for pattern in policy.allowed_paths)
    )
    if invalid:
        raise _git_error(
            "EXTERNAL_WORKSPACE_PATH_POLICY_VIOLATION",
            "Candidate 修改了 Workcell Policy 未授权路径。",
        )
    secret_patterns = (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*['\"]?[^\s'\"]{8,}"),
    )
    for item in changed_files:
        target = worktree / item
        if target.is_file() and any(
            pattern.search(target.read_text(encoding="utf-8", errors="replace"))
            for pattern in secret_patterns
        ):
            raise _git_error(
                "EXTERNAL_WORKSPACE_SECRET_MATERIAL_DETECTED",
                f"Candidate 文件 {item} 包含疑似凭证。",
            )


def _matches_allowed_path(item: str, pattern: str) -> bool:
    path = PurePosixPath(item)
    if path.is_absolute() or ".." in path.parts:
        return False
    if pattern.endswith("/**"):
        prefix = PurePosixPath(pattern.removesuffix("/**"))
        return (
            len(path.parts) > len(prefix.parts)
            and path.parts[: len(prefix.parts)] == prefix.parts
        )
    return path.match(pattern)


def _optional_revision(
    repository: Path,
    reference: str,
    environment: dict[str, str],
) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", reference],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _git(
    *arguments: str,
    cwd: Path | None = None,
    environment: dict[str, str],
) -> str:
    operation = _git_operation(arguments)
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise _git_error(
            "EXTERNAL_GIT_COMMAND_FAILED",
            f"Git {operation} 超过 120 秒，命令已终止。",
        ) from error
    except OSError as error:
        raise _git_error(
            "EXTERNAL_GIT_COMMAND_FAILED",
            f"Git {operation} 无法启动或完成。",
        ) from error
    if completed.returncode != 0:
        diagnostic = _redact_git_diagnostic(completed.stderr, environment)
        suffix = f"：{diagnostic}" if diagnostic else "；Git 未提供错误详情。"
        raise _git_error(
            "EXTERNAL_GIT_COMMAND_FAILED",
            f"Git {operation} 失败（exit {completed.returncode}）{suffix}",
        )
    return completed.stdout


def _git_operation(arguments: tuple[str, ...]) -> str:
    known_operations = {
        "add",
        "branch",
        "clone",
        "commit",
        "diff",
        "fetch",
        "init",
        "ls-files",
        "ls-remote",
        "merge-base",
        "push",
        "remote",
        "rev-parse",
        "worktree",
    }
    return next((item for item in arguments if item in known_operations), "command")


def _redact_git_diagnostic(value: str, environment: dict[str, str]) -> str:
    redacted = value
    sensitive_names = ("TOKEN", "SECRET", "PASSWORD", "API_KEY")
    for name, marker in environment.items():
        if any(fragment in name.upper() for fragment in sensitive_names) and len(marker) >= 8:
            redacted = redacted.replace(marker, "[REDACTED]")
    redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"\bgh[a-z]_[A-Za-z0-9]{16,}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"https://[^\s/@]+@", "https://[REDACTED]@", redacted)
    normalized = " | ".join(line.strip() for line in redacted.splitlines() if line.strip())
    return normalized[-2_000:]


def _safe_identifier(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("unsafe workspace identifier")


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _git_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="External Git Workspace 操作失败",
        detail=detail,
        repair="检查冻结仓库、Candidate Lineage 与服务身份权限后重试。",
    )
