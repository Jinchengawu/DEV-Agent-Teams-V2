"""显式从已有离线依赖准备完整四仓的 Node 工具环境；不会安装包或联网。"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_team_os.infrastructure.verification.tool_environment import prepare_node_environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-node-modules", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    arguments = parser.parse_args()
    root = prepare_node_environment(arguments.source_node_modules, arguments.target)
    print(root.parent / "environment.json")


if __name__ == "__main__":
    main()
