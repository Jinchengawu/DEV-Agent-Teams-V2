from agent_team_os.codex_runtime import (
    APPROVED_CODEX_REASONING_EFFORT,
    APPROVED_PLANNING_CODEX_MODEL,
    APPROVED_WORKCELL_CODEX_MODEL,
    APPROVED_WRITER_CODEX_MODEL,
    approved_planning_codex_command,
    approved_workcell_codex_command,
    approved_writer_codex_command,
    resolve_codex_executable,
)
from agent_team_os.codex_simulation import ACWMCodexRoleRunner
from agent_team_os.git_delivery import ACWMCodexWorkspaceAgent
from agent_team_os.infrastructure.acwm import CodexWorkcellAgent


def test_approved_codex_commands_do_not_inherit_operator_model_policy() -> None:
    assert APPROVED_PLANNING_CODEX_MODEL == "gpt-5.6-sol"
    assert APPROVED_WRITER_CODEX_MODEL == "gpt-5.6-luna"
    assert APPROVED_WORKCELL_CODEX_MODEL == "gpt-6-astra"
    assert APPROVED_CODEX_REASONING_EFFORT == "low"
    assert approved_planning_codex_command() == (
        "codex",
        "--model",
        "gpt-5.6-sol",
        "-c",
        'model_reasoning_effort="low"',
    )
    assert approved_workcell_codex_command() == (
        "codex",
        "--model",
        "gpt-6-astra",
        "-c",
        'model_reasoning_effort="low"',
    )
    assert approved_writer_codex_command() == (
        "codex",
        "--model",
        "gpt-5.6-luna",
        "-c",
        'model_reasoning_effort="low"',
    )


def test_approved_codex_commands_share_the_explicit_product_executable(
    monkeypatch,
) -> None:
    executable = "/Applications/ChatGPT.app/Contents/Resources/codex"
    monkeypatch.setenv("AGENT_TEAM_OS_CODEX_EXECUTABLE", executable)

    assert resolve_codex_executable() == executable
    assert approved_planning_codex_command()[0] == executable
    assert approved_workcell_codex_command()[0] == executable
    assert approved_writer_codex_command()[0] == executable

    monkeypatch.setenv("AGENT_TEAM_OS_CODEX_EXECUTABLE", "   ")
    assert resolve_codex_executable() == "codex"


def test_product_owned_codex_adapters_use_the_role_specific_command(tmp_path) -> None:
    planning = ACWMCodexRoleRunner(workspace=tmp_path)
    legacy_delivery = ACWMCodexWorkspaceAgent()
    workcell = CodexWorkcellAgent()

    assert planning._config.command == approved_planning_codex_command()
    assert legacy_delivery._config.command == approved_workcell_codex_command()
    assert workcell.command == approved_workcell_codex_command()
    assert workcell.writer_command == approved_writer_codex_command()
    assert workcell._command_for("workspace_write") == approved_writer_codex_command()
    assert workcell._command_for("candidate_read") == approved_workcell_codex_command()
