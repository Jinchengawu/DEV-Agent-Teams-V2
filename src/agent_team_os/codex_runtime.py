"""Product-pinned Codex CLI invocation policy."""

APPROVED_CODEX_MODEL = "gpt-5.6-luna"
APPROVED_CODEX_REASONING_EFFORT = "low"


def approved_codex_command(executable: str = "codex") -> tuple[str, ...]:
    """Keep product AgentAttempts independent from the operator's global model policy."""
    return (
        executable,
        "--model",
        APPROVED_CODEX_MODEL,
        "-c",
        f'model_reasoning_effort="{APPROVED_CODEX_REASONING_EFFORT}"',
    )
