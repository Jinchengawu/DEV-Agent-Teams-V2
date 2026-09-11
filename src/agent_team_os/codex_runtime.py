"""Product-pinned Codex CLI invocation policies by observable Agent role."""

APPROVED_PLANNING_CODEX_MODEL = "gpt-5.6-luna"
APPROVED_WORKCELL_CODEX_MODEL = "gpt-6-astra"
APPROVED_CODEX_REASONING_EFFORT = "low"


def approved_planning_codex_command(executable: str = "codex") -> tuple[str, ...]:
    """Return the product-pinned planning role-turn command."""
    return _approved_codex_command(APPROVED_PLANNING_CODEX_MODEL, executable)


def approved_workcell_codex_command(executable: str = "codex") -> tuple[str, ...]:
    """Return the product-pinned Workcell execution command."""
    return _approved_codex_command(APPROVED_WORKCELL_CODEX_MODEL, executable)


def _approved_codex_command(model: str, executable: str) -> tuple[str, ...]:
    """Keep product AgentAttempts independent from the operator's global model policy."""
    return (
        executable,
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{APPROVED_CODEX_REASONING_EFFORT}"',
    )
