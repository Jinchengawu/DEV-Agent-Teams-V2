from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_openapi_export_is_byte_for_byte_deterministic(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[1]
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    for output in (first, second):
        subprocess.run(
            [
                sys.executable,
                str(project_root / "scripts" / "export_openapi.py"),
                "--output",
                str(output),
            ],
            cwd=project_root,
            check=True,
        )

    assert first.read_bytes() == second.read_bytes()
