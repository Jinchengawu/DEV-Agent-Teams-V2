import asyncio
import json
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


def test_codex_workcell_failure_redacts_namespaced_credentials(
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
            "sys.stderr.write(os.environ['AGENT_TEAM_OS_GITHUB_TOKEN']); "
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
    assert "[REDACTED]" in error.value.detail
