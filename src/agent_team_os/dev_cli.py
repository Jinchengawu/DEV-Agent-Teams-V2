from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .devtools.spark import SparkFailure, SparkRunner
from .infrastructure.database import MigrationRunner
from .modules.evaluation import (
    EvaluationRunRequest,
    EvaluationService,
    HumanReview,
    SQLiteEvaluationRepository,
    default_dataset_dir,
    load_evaluation_dataset,
)
from .modules.evidence import EvidenceLedger, SQLiteEvidenceRepository
from .modules.orchestration import PipelineCatalog, SQLitePipelineRepository
from .shared.hashes import sha256_json


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-team-os-dev")
    commands = parser.add_subparsers(dest="command", required=True)
    spark = commands.add_parser("spark")
    actions = spark.add_subparsers(dest="action", required=True)
    for action in ("run", "repair", "inspect", "accept", "reject"):
        command = actions.add_parser(action)
        command.add_argument("task_id")
    evaluation = commands.add_parser("eval")
    evaluation_actions = evaluation.add_subparsers(dest="action", required=True)
    run = evaluation_actions.add_parser("run")
    run.add_argument("--pipeline", default="backend-delivery")
    run.add_argument("--candidate-revision", type=int)
    run.add_argument("--baseline", choices=("active",), default="active")
    run.add_argument("--suite", default="agent-team-os-mvp")
    run.add_argument("--mode", choices=("offline", "live"), default="offline")
    run.add_argument("--profile", choices=("smoke", "standard", "live"))
    run.add_argument("--seed", type=int, default=20260824)
    run.add_argument("--timeout", type=int, default=60)
    run.add_argument("--max-cost-usd", type=float)
    run.add_argument("--bootstrap-fixture", action="store_true")
    run.add_argument("--require-gate-passed", action="store_true")
    validate_dataset = evaluation_actions.add_parser("validate-dataset")
    validate_dataset.add_argument("--dataset", type=Path)
    for action in ("inspect", "report", "cancel", "human-export"):
        command = evaluation_actions.add_parser(action)
        command.add_argument("run_id")
        if action == "human-export":
            command.add_argument("--output", type=Path)
    human_import = evaluation_actions.add_parser("human-import")
    human_import.add_argument("run_id")
    human_import.add_argument("input", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "eval":
        _run_evaluation_command(arguments)
        return
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


def _evaluation_service() -> EvaluationService:
    project_root = _repository_root()
    data_dir = Path(os.environ.get("AGENT_TEAM_OS_DATA_DIR", str(project_root / ".agent-team-os")))
    database = data_dir / "agent-team-os.sqlite"
    MigrationRunner(database, project_root / "migrations").migrate()
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))
    return EvaluationService(
        SQLiteEvaluationRepository(database),
        PipelineCatalog(SQLitePipelineRepository(database)),
        report_dir=data_dir / "reports" / "evaluations",
        project_root=project_root,
        evidence=evidence,
    )


def _run_evaluation_command(arguments: argparse.Namespace) -> None:
    action = str(arguments.action)
    if action == "validate-dataset":
        root = _repository_root()
        dataset = load_evaluation_dataset(arguments.dataset or default_dataset_dir(root))
        print(
            json.dumps(
                {
                    "status": "valid",
                    "suite_id": dataset.manifest.suite_id,
                    "suite_version": dataset.manifest.version,
                    "source_sha256": str(dataset.source_sha256),
                    "case_count": len(dataset.cases),
                    "case_counts": {
                        key.value: value for key, value in dataset.manifest.case_counts.items()
                    },
                    "official": dataset.manifest.official,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if action == "run" and arguments.bootstrap_fixture:
        _bootstrap_fixture_evaluation()
    service = _evaluation_service()
    if action == "run":
        result: object = service.start(
            EvaluationRunRequest(
                pipeline_id=arguments.pipeline,
                candidate_revision=arguments.candidate_revision,
                baseline=arguments.baseline,
                suite_id=arguments.suite,
                mode=arguments.mode,
                profile=arguments.profile or ("live" if arguments.mode == "live" else "standard"),
                seed=arguments.seed,
                timeout_seconds=arguments.timeout,
                max_cost_usd=arguments.max_cost_usd,
            )
        )
    elif action == "inspect":
        result = service.get(arguments.run_id)
    elif action == "report":
        result = service.report(arguments.run_id)
    elif action == "cancel":
        result = service.cancel(arguments.run_id)
    elif action == "human-export":
        sample = service.human_review_sample(arguments.run_id)
        payload = [
            {
                "run_id": item.run_id,
                "case_id": item.case_id,
                "dimension": item.dimension.value,
                "category": item.category,
                "reviewer_id": "",
                "outcome": None,
                "notes": "",
            }
            for item in sample
        ]
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        if arguments.output is not None:
            arguments.output.write_text(rendered, encoding="utf-8")
            result = {"output": str(arguments.output), "sample_size": len(sample)}
        else:
            print(rendered)
            return
    elif action == "human-import":
        payload = json.loads(arguments.input.read_text(encoding="utf-8"))
        reviews = tuple(
            HumanReview.model_validate(
                {
                    "run_id": arguments.run_id,
                    "case_id": item["case_id"],
                    "reviewer_id": item["reviewer_id"],
                    "outcome": item["outcome"],
                    "notes_sha256": item.get("notes_sha256")
                    or sha256_json({"notes": str(item.get("notes", ""))}),
                }
            )
            for item in payload
        )
        result = service.import_reviews(arguments.run_id, reviews)
    else:
        raise RuntimeError(f"unsupported eval action: {action}")
    if hasattr(result, "model_dump_json"):
        print(result.model_dump_json(indent=2))
    elif isinstance(result, tuple):
        print(
            json.dumps(
                [item.model_dump(mode="json") for item in result], ensure_ascii=False, indent=2
            )
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if action == "run" and arguments.require_gate_passed:
        run_id = getattr(result, "id", None)
        if run_id is None or service.report(str(run_id)).gate_status != "passed":
            raise SystemExit(1)


def _bootstrap_fixture_evaluation() -> None:
    project_root = _repository_root()
    configured = os.environ.get("AGENT_TEAM_OS_DATA_DIR")
    if configured is None:
        raise RuntimeError("--bootstrap-fixture requires AGENT_TEAM_OS_DATA_DIR")
    data_dir = Path(configured).resolve()
    if data_dir == (project_root / ".agent-team-os").resolve():
        raise RuntimeError("--bootstrap-fixture refuses the default project data directory")
    from .gate_app import build_gate_app

    build_gate_app()


if __name__ == "__main__":
    main()
