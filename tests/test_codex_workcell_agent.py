import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_team_os.infrastructure.acwm import CodexWorkcellAgent
from agent_team_os.modules.workcells import WorkcellAgentInvocation


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
