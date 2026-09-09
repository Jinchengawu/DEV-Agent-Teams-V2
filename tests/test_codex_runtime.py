from agent_team_os.codex_runtime import (
    APPROVED_CODEX_MODEL,
    APPROVED_CODEX_REASONING_EFFORT,
    approved_codex_command,
)
from agent_team_os.codex_simulation import ACWMCodexRoleRunner
from agent_team_os.git_delivery import ACWMCodexWorkspaceAgent
from agent_team_os.infrastructure.acwm import CodexWorkcellAgent


def test_approved_codex_command_does_not_inherit_operator_model_policy() -> None:
    assert APPROVED_CODEX_MODEL == "gpt-5.3-codex-spark"
    assert APPROVED_CODEX_REASONING_EFFORT == "xhigh"
    assert approved_codex_command() == (
        "codex",
        "--model",
        "gpt-5.3-codex-spark",
        "-c",
        'model_reasoning_effort="xhigh"',
    )


def test_every_product_owned_codex_adapter_uses_the_approved_command(tmp_path) -> None:
    command = approved_codex_command()
    planning = ACWMCodexRoleRunner(workspace=tmp_path)
    legacy_delivery = ACWMCodexWorkspaceAgent()
    workcell = CodexWorkcellAgent()

    assert planning._config.command == command
    assert legacy_delivery._config.command == command
    assert workcell.command == command
