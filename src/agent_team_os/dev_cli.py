from __future__ import annotations

import argparse
import json
from pathlib import Path

from .devtools.spark import SparkFailure, SparkRunner


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-team-os-dev")
    commands = parser.add_subparsers(dest="command", required=True)
    spark = commands.add_parser("spark")
    actions = spark.add_subparsers(dest="action", required=True)
    for action in ("run", "inspect", "accept", "reject"):
        command = actions.add_parser(action)
        command.add_argument("task_id")
    arguments = parser.parse_args()
    runner = SparkRunner(_repository_root())
    try:
        result = getattr(runner, arguments.action)(arguments.task_id)
    except SparkFailure as error:
        print(
            json.dumps(
                {"status": "failed", "error_code": error.code, "detail": error.detail},
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from error
    print(result.model_dump_json(indent=2))
    if result.status in {"failed", "blocked"}:
        raise SystemExit(1)


def _repository_root() -> Path:
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("agent-team-os-dev must run inside the repository")


if __name__ == "__main__":
    main()
