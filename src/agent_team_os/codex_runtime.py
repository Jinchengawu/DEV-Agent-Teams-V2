"""Product-pinned Codex CLI invocation policy."""

APPROVED_CODEX_MODEL = "gpt-5.3-codex-spark"
APPROVED_CODEX_REASONING_EFFORT = "xhigh"


def approved_codex_command(executable: str = "codex") -> tuple[str, ...]:
    """Keep product AgentAttempts independent from the operator's global model policy."""
    return (
        executable,
        "--model",
        APPROVED_CODEX_MODEL,
        "-c",
        f'model_reasoning_effort="{APPROVED_CODEX_REASONING_EFFORT}"',
    )
