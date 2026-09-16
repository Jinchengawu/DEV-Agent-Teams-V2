"""Product-pinned Codex CLI invocation policies by observable Agent role."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

APPROVED_PLANNING_CODEX_MODEL = "gpt-5.6-sol"
APPROVED_WRITER_CODEX_MODEL = "gpt-5.6-sol"
APPROVED_WORKCELL_CODEX_MODEL = "gpt-6-astra"
APPROVED_CODEX_REASONING_EFFORT = "low"
CODEX_EXECUTABLE_ENV = "AGENT_TEAM_OS_CODEX_EXECUTABLE"
MINIMUM_CODEX_CLI_VERSION = (0, 153, 4)


def resolve_codex_executable(environment: Mapping[str, str] | None = None) -> str:
    """Resolve one product-owned executable for readiness and every AgentAttempt."""
    values = os.environ if environment is None else environment
    configured = values.get(CODEX_EXECUTABLE_ENV, "").strip()
    return configured or "codex"


def codex_cli_supports_frozen_models(version_output: str | bytes) -> bool:
    """Return whether a CLI version is qualified for the product-pinned model set."""
    output = (
        version_output.decode("utf-8", errors="replace")
        if isinstance(version_output, bytes)
        else version_output
    )
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", output)
    if match is None:
        return False
    return tuple(int(part) for part in match.groups()) >= MINIMUM_CODEX_CLI_VERSION


def approved_planning_codex_command(executable: str | None = None) -> tuple[str, ...]:
    """Return the product-pinned planning role-turn command."""
    return _approved_codex_command(
        APPROVED_PLANNING_CODEX_MODEL, executable or resolve_codex_executable()
    )


def approved_workcell_codex_command(executable: str | None = None) -> tuple[str, ...]:
    """Return the product-pinned Workcell execution command."""
    return _approved_codex_command(
        APPROVED_WORKCELL_CODEX_MODEL, executable or resolve_codex_executable()
    )


def approved_writer_codex_command(executable: str | None = None) -> tuple[str, ...]:
    """Return the product-pinned workspace writer command."""
    return _approved_codex_command(
        APPROVED_WRITER_CODEX_MODEL, executable or resolve_codex_executable()
    )


def _approved_codex_command(model: str, executable: str) -> tuple[str, ...]:
    """Keep product AgentAttempts independent from the operator's global model policy."""
    return (
        executable,
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{APPROVED_CODEX_REASONING_EFFORT}"',
    )
