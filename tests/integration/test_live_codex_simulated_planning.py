import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from agent_team_os.codex_simulation import (
    ACWMCodexRoleRunner,
    CodexSimulatedHermesPlanning,
)


@pytest.mark.skipif(
    os.environ.get("AGENT_TEAM_OS_LIVE_CODEX") != "1",
    reason="set AGENT_TEAM_OS_LIVE_CODEX=1 to call the real Codex CLI",
)
def test_real_codex_can_simulate_bounded_planning_without_workspace_changes() -> None:
    workspace = Path(__file__).parents[2]
    before = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=workspace,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    runner = ACWMCodexRoleRunner(workspace=workspace)
    planning = CodexSimulatedHermesPlanning(runner)

    async def execute() -> None:
        requirements = await planning.analyze(
            "Add a Backend health endpoint that returns a machine-readable healthy status."
        )
        task = await planning.plan(requirements)
        assert requirements.acceptance_criteria
        assert set(task.acceptance_ids) <= {
            criterion.id for criterion in requirements.acceptance_criteria
        }
        await runner.close()

    asyncio.run(execute())
    after = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=workspace,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    assert after == before
