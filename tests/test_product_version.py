from __future__ import annotations

import json
import re
from pathlib import Path

from agent_team_os.version import __version__

ROOT = Path(__file__).parents[1]


def test_product_version_is_consistent_across_package_console_and_ui() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    console = json.loads((ROOT / "console" / "package.json").read_text(encoding="utf-8"))
    shell = (ROOT / "console" / "src" / "app" / "shell" / "AppShell.tsx").read_text(
        encoding="utf-8"
    )
    api = (ROOT / "src" / "agent_team_os" / "api.py").read_text(encoding="utf-8")
    fallback = (ROOT / "src" / "agent_team_os" / "ui.py").read_text(encoding="utf-8")

    package_match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    assert package_match is not None
    assert package_match.group(1) == console["version"] == __version__ == "0.5.1"
    assert f"V{__version__}" in shell
    assert "version=__version__" in api
    assert f"V{__version__}" in fallback
