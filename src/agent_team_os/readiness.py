"""Fail-closed checks for the real Agent-Team-OS runtime."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DependencyCheck(ImmutableModel):
    name: str
    status: Literal["ready", "missing", "failed"]
    repair: str | None = None


class ReadinessReport(ImmutableModel):
    status: Literal["ready", "not_ready"]
    checks: tuple[DependencyCheck, ...]


class ReadinessProbe(Protocol):
    def inspect(self) -> ReadinessReport: ...


class FrameworkRevision(ImmutableModel):
    version: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class FrameworkLock(ImmutableModel):
    acwm: FrameworkRevision


def inspect_acwm_revision_lock(
    lock_path: Path, *, actual_revision: str | None = None
) -> DependencyCheck:
    try:
        locked = FrameworkLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
        actual = actual_revision or imported_acwm_revision()
    except Exception:
        return DependencyCheck(
            name="python:acwm-revision",
            status="failed",
            repair="修复 config/framework-lock.json 并安装其指定的 ACWM Revision。",
        )
    matches = actual == locked.acwm.revision
    return DependencyCheck(
        name="python:acwm-revision",
        status="ready" if matches else "failed",
        repair=(
            None
            if matches
            else (
                f"当前 ACWM {actual} 与锁定 Revision "
                f"{locked.acwm.revision} 不一致；重新执行锁定依赖安装。"
            )
        ),
    )


def imported_acwm_revision() -> str:
    package_file = getattr(import_module("acwm"), "__file__", None)
    if isinstance(package_file, str):
        resolved_package = Path(package_file).resolve()
        for parent in resolved_package.parents:
            source_package = parent / "src" / "acwm"
            if (
                (parent / ".git").exists()
                and resolved_package.is_relative_to(source_package.resolve())
            ):
                result = subprocess.run(
                    ("git", "rev-parse", "HEAD"),
                    cwd=parent,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
    direct_url = importlib.metadata.distribution(
        "agent-capability-workflow-matrix"
    ).read_text("direct_url.json")
    if direct_url:
        data = json.loads(direct_url)
        commit = data.get("vcs_info", {}).get("commit_id")
        if commit:
            return str(commit)
    return importlib.metadata.version("agent-capability-workflow-matrix")


class RuntimeReadiness:
    """Inspect identities and credentials without exposing secret values."""

    def inspect(self) -> ReadinessReport:
        checks = (
            self._package("acwm", "Install the locked ACWM dependency."),
            self._package("agentscope", "Run `uv sync --extra live`."),
            self._command("hermes", "Install Hermes CLI and ensure it is on PATH."),
            self._hermes_credentials(),
            self._codex_login(),
        )
        return ReadinessReport(
            status="ready" if all(check.status == "ready" for check in checks) else "not_ready",
            checks=checks,
        )

    @staticmethod
    def _package(name: str, repair: str) -> DependencyCheck:
        return DependencyCheck(
            name=f"python:{name}",
            status="ready" if find_spec(name) is not None else "missing",
            repair=None if find_spec(name) is not None else repair,
        )

    @staticmethod
    def _command(name: str, repair: str) -> DependencyCheck:
        available = shutil.which(name) is not None
        return DependencyCheck(
            name=f"cli:{name}",
            status="ready" if available else "missing",
            repair=None if available else repair,
        )

    @staticmethod
    def _hermes_credentials() -> DependencyCheck:
        available = bool(os.environ.get("HERMES_API_KEY"))
        return DependencyCheck(
            name="hermes-credentials",
            status="ready" if available else "missing",
            repair=None if available else "Set HERMES_API_KEY for the Hermes model provider.",
        )

    @staticmethod
    def _codex_login() -> DependencyCheck:
        if shutil.which("codex") is None:
            return DependencyCheck(
                name="codex-login",
                status="missing",
                repair="Install Codex CLI and run `codex login`.",
            )
        try:
            result = subprocess.run(
                ["codex", "login", "status"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return DependencyCheck(
                name="codex-login",
                status="failed",
                repair="Run `codex login` and retry readiness.",
            )
        return DependencyCheck(
            name="codex-login",
            status="ready" if result.returncode == 0 else "failed",
            repair=None if result.returncode == 0 else "Run `codex login` and retry readiness.",
        )
