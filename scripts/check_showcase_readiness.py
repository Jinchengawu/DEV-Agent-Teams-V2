"""Check whether a local deterministic runtime is safe to present as a product showcase."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_team_os.showcase_readiness import (
    collect_showcase_facts,
    evaluate_showcase_readiness,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--delivery-id")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()

    report = evaluate_showcase_readiness(
        collect_showcase_facts(
            arguments.database,
            project_id=arguments.project_id,
            delivery_id=arguments.delivery_id,
        )
    )
    rendered = report.model_dump_json(indent=2)
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if report.status == "ready" else 1)


if __name__ == "__main__":
    main()
