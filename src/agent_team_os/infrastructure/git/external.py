from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_json


class ExternalGitBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    remote_uri: str = Field(min_length=1, max_length=500)
    default_branch: Literal["main"] = "main"
    credential_reference: str | None = Field(default=None, max_length=500)

    @field_validator("credential_reference")
    @classmethod
    def credential_reference_is_indirect(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not re.fullmatch(
            r"env://[A-Z][A-Z0-9_]{0,127}|keychain://[^/\s]{1,120}/[^/\s]{1,120}",
            value,
        ):
            raise ValueError("credential reference must use env:// or keychain://")
        return value


class ExternalGitCapabilityReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ready"] = "ready"
    remote_main_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    direct_fast_forward_main: Literal[True] = True
    transport: Literal["github-https", "local-test"]
    verification_sha256: Sha256
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExternalGitCapabilityProbe:
    """Verify an existing remote and direct fast-forward permission without writing refs."""

    def __init__(
        self,
        scratch_root: Path,
        *,
        allow_local_test_transport: bool = False,
    ) -> None:
        self.scratch_root = scratch_root.resolve()
        self.allow_local_test_transport = allow_local_test_transport

    def verify(self, binding: ExternalGitBinding) -> ExternalGitCapabilityReceipt:
        transport = self._transport(binding)
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        with _credential_environment(binding.credential_reference) as environment:
            listing = self._git(
                "ls-remote",
                "--exit-code",
                binding.remote_uri,
                f"refs/heads/{binding.default_branch}",
                environment=environment,
                failure_code="EXTERNAL_GIT_MAIN_UNAVAILABLE",
            )
            lines = tuple(line for line in listing.splitlines() if line)
            if len(lines) != 1:
                raise _external_git_error(
                    "EXTERNAL_GIT_MAIN_UNAVAILABLE",
                    "远端仓库必须存在唯一的 refs/heads/main。",
                )
            revision, separator, reference = lines[0].partition("\t")
            if (
                not separator
                or reference != f"refs/heads/{binding.default_branch}"
                or not re.fullmatch(r"[0-9a-f]{40}", revision)
            ):
                raise _external_git_error(
                    "EXTERNAL_GIT_MAIN_UNAVAILABLE",
                    "远端 main 引用返回了无效 SHA。",
                )
            self._git(
                "push",
                "--dry-run",
                "--porcelain",
                binding.remote_uri,
                f"{revision}:refs/heads/{binding.default_branch}",
                environment=environment,
                failure_code="REMOTE_MAIN_APPLY_NOT_ALLOWED",
            )
        return ExternalGitCapabilityReceipt(
            remote_main_sha=revision,
            transport=transport,
            verification_sha256=sha256_json(
                {
                    "remote_uri": binding.remote_uri,
                    "default_branch": binding.default_branch,
                    "credential_reference": binding.credential_reference,
                    "remote_main_sha": revision,
                    "transport": transport,
                    "direct_fast_forward_main": True,
                    "policy_version": "external-git-capability-v1",
                }
            ),
        )

    def _transport(
        self,
        binding: ExternalGitBinding,
    ) -> Literal["github-https", "local-test"]:
        parsed = urlparse(binding.remote_uri)
        if parsed.scheme == "https":
            if (
                parsed.hostname != "github.com"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", parsed.path)
            ):
                raise _external_git_error(
                    "EXTERNAL_GIT_REMOTE_URI_INVALID",
                    "v0.5 Live 仅接受不含内嵌凭证的 GitHub HTTPS 仓库地址。",
                )
            if binding.credential_reference is None:
                raise _external_git_error(
                    "EXTERNAL_GIT_CREDENTIAL_REQUIRED",
                    "GitHub HTTPS Workspace 必须绑定环境变量或 Keychain Credential Reference。",
                )
            return "github-https"
        if (
            self.allow_local_test_transport
            and not parsed.scheme
            and Path(binding.remote_uri).is_dir()
            and binding.credential_reference is None
        ):
            return "local-test"
        raise _external_git_error(
            "EXTERNAL_GIT_TRANSPORT_NOT_ALLOWED",
            "v0.5 只支持 GitHub HTTPS；本地路径仅允许 Deterministic 测试。",
        )

    @staticmethod
    def _git(
        *arguments: str,
        environment: dict[str, str],
        failure_code: str,
    ) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _external_git_error(
                failure_code,
                "Git 远端能力检查无法完成。",
            ) from error
        if completed.returncode != 0:
            raise _external_git_error(
                failure_code,
                "Git 远端拒绝了只读检查或 main 的非 Force Fast-forward Dry Run。",
            )
        return completed.stdout


@contextmanager
def _credential_environment(reference: str | None) -> Iterator[dict[str, str]]:
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
    }
    if reference is None:
        yield environment
        return
    secret = _resolve_credential(reference)
    with tempfile.TemporaryDirectory(prefix="agent-team-os-git-credential-") as directory:
        askpass = Path(directory) / "askpass.py"
        askpass.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "prompt = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "name = 'AGENT_TEAM_OS_GIT_USERNAME' if 'Username' in prompt "
            "else 'AGENT_TEAM_OS_GIT_SECRET'\n"
            "print(os.environ[name])\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        environment.update(
            {
                "GIT_ASKPASS": str(askpass),
                "AGENT_TEAM_OS_GIT_USERNAME": "x-access-token",
                "AGENT_TEAM_OS_GIT_SECRET": secret,
            }
        )
        try:
            yield environment
        finally:
            environment.pop("AGENT_TEAM_OS_GIT_SECRET", None)


@contextmanager
def external_git_environment(reference: str | None) -> Iterator[dict[str, str]]:
    """Provide a process-only Git credential environment without persisting its secret."""
    with _credential_environment(reference) as environment:
        yield environment


def resolve_git_credential(reference: str) -> str:
    """Resolve an already validated Credential Reference for an immediate provider call."""
    return _resolve_credential(reference)


def _resolve_credential(reference: str) -> str:
    if reference.startswith("env://"):
        name = reference.removeprefix("env://")
        secret = os.environ.get(name)
        if secret:
            return secret
        raise _external_git_error(
            "EXTERNAL_GIT_CREDENTIAL_UNAVAILABLE",
            f"环境 Credential Reference {name} 当前不可用。",
        )
    service, account = reference.removeprefix("keychain://").split("/", 1)
    try:
        completed = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _external_git_error(
            "EXTERNAL_GIT_CREDENTIAL_UNAVAILABLE",
            "无法从 Keychain 解析 Git Credential Reference。",
        ) from error
    secret = completed.stdout.rstrip("\n")
    if completed.returncode != 0 or not secret:
        raise _external_git_error(
            "EXTERNAL_GIT_CREDENTIAL_UNAVAILABLE",
            "Keychain 中不存在指定的 Git Credential Reference。",
        )
    return secret


def _external_git_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="外部 Git Workspace 资格检查失败",
        detail=detail,
        repair="检查已有私有仓库、HTTPS Credential Reference 与 main 直推权限后重试。",
        status_code=409,
    )
