"""Install source-checkout Method Packs through the common qualified CLI."""

from __future__ import annotations

from pathlib import Path

from agent_team_os.method_pack_cli import main as install_main


def main() -> int:
    return install_main(source_root=Path(__file__).parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
