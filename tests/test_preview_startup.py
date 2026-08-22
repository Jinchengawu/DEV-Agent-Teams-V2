from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_demo_checks_framework_lock_before_building_preview_app(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[1]
    environment = {
        **os.environ,
        "AGENT_TEAM_OS_DATA_DIR": str(tmp_path / "runtime"),
    }
    environment.pop("PYTHONPATH", None)
    command = (
        "from agent_team_os.preview import main; "
        "import sys; "
        "sys.argv=['agent-team-os','demo']; "
        "main()"
    )

    result = subprocess.run(
        (sys.executable, "-c", command),
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 2
    assert '"name":"python:acwm-revision"' in result.stdout.replace(" ", "")
    assert '"status":"failed"' in result.stdout.replace(" ", "")
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "runtime" / "agent-team-os.sqlite").exists()
