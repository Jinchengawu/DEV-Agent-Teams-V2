"""Fail-closed checks for the real Agent-Team-OS runtime."""

from __future__ import annotations

import os
import shutil
import subprocess
from importlib.util import find_spec
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict


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

