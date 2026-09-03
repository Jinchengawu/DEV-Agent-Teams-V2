import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_team_os.infrastructure.acwm import CodexWorkcellAgent
from agent_team_os.modules.workcells import WorkcellAgentInvocation
from agent_team_os.shared.errors import ProductError


def test_codex_workcell_agent_returns_only_observable_structured_attempt(
    tmp_path: Path,
) -> None:
    event = {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": json.dumps({"blocking_findings": []}),
        },
    }
    code = (
        "import json,sys; sys.stdin.read(); "
        f"print(json.dumps({event!r}, ensure_ascii=False))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-1",
                workcell_run_id="workcell-1",
                agent_run_id="agent-1",
                phase="delegate",
                workcell_key="frontend",
                stage_path="frontend-repair/frontend",
                instruction="review",
                workspace=tmp_path,
                workspace_access="candidate_read",
                method_id="bmad-code-review",
            )
        )
    )

    assert output.runtime_identity == "codex-test"
    assert output.content == {"blocking_findings": []}


def test_codex_workcell_agent_parses_only_the_last_agent_message(
    tmp_path: Path,
) -> None:
    progress = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "正在修改隔离工作区"},
    }
    final = {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": json.dumps(
                {"changed_files": ["design/health-contract-v1.json"], "knowledge_citation_ids": []}
            ),
        },
    }
    code = (
        "import json,sys; sys.stdin.read(); "
        f"print(json.dumps({progress!r}, ensure_ascii=False)); "
        f"print(json.dumps({final!r}, ensure_ascii=False))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-multiple-messages",
                workcell_run_id="workcell-multiple-messages",
                agent_run_id="agent-multiple-messages",
                phase="delegate",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="write",
                workspace=tmp_path,
                workspace_access="workspace_write",
                method_id="bmad-ux",
            )
        )
    )

    assert output.content == {"changed_files": ["design/health-contract-v1.json"]}


def test_codex_workcell_agent_mounts_bmad_support_without_git_pollution(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    source = tmp_path / "verified-bmad-source"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "render_skill.py").write_text("# verified renderer\n", encoding="utf-8")
    (scripts / "config_utils.py").write_text("# verified config\n", encoding="utf-8")
    code = """
import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdin.read()
root = Path.cwd()
overlay = root / "_bmad"
renderer = (overlay / "scripts" / "render_skill.py").read_text(encoding="utf-8")
status = subprocess.check_output(["git", "status", "--short"], text=True)
(root / "src").mkdir(exist_ok=True)
(root / "src" / "health.py").write_text("STATUS = 'ok'\\n", encoding="utf-8")
payload = {
    "overlay_present": overlay.is_dir(),
    "renderer_verified": renderer == "# verified renderer\\n",
    "overlay_hidden_from_git": "_bmad" not in status,
    "runtime_source_leaked": "AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE" in os.environ,
    "config_present": (overlay / "config.toml").is_file(),
    "bmm_config_present": (overlay / "bmm" / "config.yaml").is_file(),
}
event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}}
print(json.dumps(event))
"""
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-overlay",
                workcell_run_id="workcell-overlay",
                agent_run_id="agent-overlay",
                phase="delegate",
                workcell_key="frontend",
                stage_path="frontend-repair/frontend",
                instruction="write",
                workspace=workspace,
                workspace_access="workspace_write",
                method_id="bmad-build",
                environment={"AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE": str(source)},
            )
        )
    )

    assert output.content == {
        "overlay_present": True,
        "renderer_verified": True,
        "overlay_hidden_from_git": True,
        "runtime_source_leaked": False,
        "config_present": True,
        "bmm_config_present": True,
    }
    assert not (workspace / "_bmad").exists()
    assert subprocess.check_output(
        ["git", "status", "--short"], cwd=workspace, text=True
    ).splitlines() == ["?? src/"]


def test_codex_workcell_agent_requires_a_citation_for_non_empty_context(
    tmp_path: Path,
) -> None:
    required_clause = (
        "允许列表非空时，knowledge_citation_ids 必须至少包含其中一个 ID"
    )
    code = (
        "import json,sys; text=sys.stdin.read(); "
        f"required={required_clause!r} in text; "
        "payload={'requires_citation': required, "
        "'knowledge_citation_ids': ['citation-allowed']}; "
        "event={'type':'item.completed','item':{'type':'agent_message',"
        "'text':json.dumps(payload)}}; print(json.dumps(event))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-citation",
                workcell_run_id="workcell-citation",
                agent_run_id="agent-citation",
                phase="planning",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="plan",
                workspace=tmp_path,
                workspace_access="none",
                allowed_knowledge_citation_ids=("citation-allowed",),
            )
        )
    )

    assert output.content == {"requires_citation": True}
    assert output.knowledge_citation_ids == ("citation-allowed",)


def test_cancelling_the_parent_task_terminates_the_codex_attempt(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent = CodexWorkcellAgent(
            command=(
                sys.executable,
                "-c",
                "import sys,time; sys.stdin.read(); time.sleep(30)",
            ),
            runtime_identity="codex-test",
        )
        task = asyncio.create_task(
            agent.run(
                WorkcellAgentInvocation(
                    delivery_id="delivery-cancel",
                    workcell_run_id="workcell-cancel",
                    agent_run_id="agent-cancel",
                    phase="delegate",
                    workcell_key="frontend",
                    stage_path="frontend-repair/frontend",
                    instruction="write",
                    workspace=tmp_path,
                    workspace_access="workspace_write",
                    method_id="bmad-build",
                )
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await agent.cancel("agent-cancel") is False

    asyncio.run(scenario())


def test_codex_workcell_does_not_inherit_namespaced_service_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "ghp_livecredentialmustnotleak"
    monkeypatch.setenv("AGENT_TEAM_OS_GITHUB_TOKEN", credential)
    agent = CodexWorkcellAgent(
        command=(
            sys.executable,
            "-c",
            "import os,sys; "
            "sys.stderr.write(os.environ.get('AGENT_TEAM_OS_GITHUB_TOKEN', 'not-inherited')); "
            "sys.exit(7)",
        ),
        runtime_identity="codex-test",
    )

    with pytest.raises(ProductError) as error:
        asyncio.run(
            agent.run(
                WorkcellAgentInvocation(
                    delivery_id="delivery-redaction",
                    workcell_run_id="workcell-redaction",
                    agent_run_id="agent-redaction",
                    phase="planning",
                    workcell_key="design",
                    stage_path="design-repair/design",
                    instruction="plan",
                    workspace=tmp_path,
                    workspace_access="none",
                )
            )
        )

    assert error.value.code == "CODEX_WORKCELL_ATTEMPT_FAILED"
    assert credential not in error.value.detail
    assert "not-inherited" in error.value.detail


def test_codex_workcell_inherits_process_only_proxy_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = "http://127.0.0.1:7890"
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("UNRELATED_PARENT_SETTING", "must-not-be-inherited")
    code = (
        "import json,os,sys; sys.stdin.read(); "
        "payload={'https_proxy':os.environ.get('HTTPS_PROXY'),"
        "'no_proxy':os.environ.get('NO_PROXY'),"
        "'unrelated_present':'UNRELATED_PARENT_SETTING' in os.environ}; "
        "event={'type':'item.completed','item':{'type':'agent_message',"
        "'text':json.dumps(payload)}}; print(json.dumps(event))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-proxy",
                workcell_run_id="workcell-proxy",
                agent_run_id="agent-proxy",
                phase="planning",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="plan",
                workspace=tmp_path,
                workspace_access="none",
            )
        )
    )

    assert output.content == {
        "https_proxy": proxy,
        "no_proxy": "127.0.0.1,localhost",
        "unrelated_present": False,
    }


def test_codex_workcell_agent_filters_shell_metadata_and_non_utf8_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("_", "/tmp/invalid-\udcff-python")
    monkeypatch.setenv("AGENT_TEAM_OS_INVALID_UTF8", "invalid-\udcff-value")
    code = (
        "import json,os,sys; sys.stdin.read(); "
        "payload={'shell_metadata_present':'_' in os.environ,"
        "'invalid_value_present':'AGENT_TEAM_OS_INVALID_UTF8' in os.environ,"
        "'bytecode_disabled':os.environ.get('PYTHONDONTWRITEBYTECODE'),"
        "'valid_marker':os.environ.get('AGENT_TEAM_OS_VALID_MARKER')}; "
        "event={'type':'item.completed','item':{'type':'agent_message',"
        "'text':json.dumps(payload)}}; print(json.dumps(event))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-environment",
                workcell_run_id="workcell-environment",
                agent_run_id="agent-environment",
                phase="delegate",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="review",
                workspace=tmp_path,
                workspace_access="candidate_read",
                method_id="bmad-review",
                environment={"AGENT_TEAM_OS_VALID_MARKER": "preserved"},
            )
        )
    )

    assert output.content == {
        "shell_metadata_present": False,
        "invalid_value_present": False,
        "bytecode_disabled": "1",
        "valid_marker": "preserved",
    }
