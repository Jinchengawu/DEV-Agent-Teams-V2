from __future__ import annotations

import json
import secrets
import sqlite3
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage

_spec = spec_from_file_location(
    "browser_interaction_closure_full_e2e",
    Path(__file__).parents[1] / "scripts/browser_interaction_closure_full_e2e.py",
)
assert _spec is not None and _spec.loader is not None
full_harness = module_from_spec(_spec)
_spec.loader.exec_module(full_harness)

EVALUATION_TARGET = full_harness.EVALUATION_TARGET
WORKCELL_KEYS = full_harness.WORKCELL_KEYS
_build_failure_artifact = full_harness._build_failure_artifact
_read_attempt_summaries = full_harness._read_attempt_summaries
_repository_main_invariants = full_harness._repository_main_invariants
_write_session_baseline = full_harness._write_session_baseline


def test_evaluation_target_freezes_two_acceptance_ids_per_workcell() -> None:
    for workcell in WORKCELL_KEYS:
        assert EVALUATION_TARGET.count(f"IC-{workcell.upper()}-") == 2


def test_final_workcell_selection_accepts_only_older_failed_repair_history() -> None:
    trees = [
        {
            "workcell_run": {
                "stage_path": "frontend-repair/frontend",
                "loop_iteration": 1,
                "status": "failed",
            }
        },
        {
            "workcell_run": {
                "stage_path": "frontend-repair/frontend",
                "loop_iteration": 2,
                "status": "succeeded",
            }
        },
        {
            "workcell_run": {
                "stage_path": "design-repair/design",
                "loop_iteration": 1,
                "status": "succeeded",
            }
        },
    ]

    selected = full_harness._select_final_workcell_trees(trees)

    assert [item["workcell_run"]["stage_path"] for item in selected] == [
        "design-repair/design",
        "frontend-repair/frontend",
    ]


def test_final_workcell_selection_rejects_unrepaired_or_nonterminal_history() -> None:
    with pytest.raises(AssertionError, match="no successful bounded repair"):
        full_harness._select_final_workcell_trees(
            [
                {
                    "workcell_run": {
                        "stage_path": "frontend-repair/frontend",
                        "loop_iteration": 1,
                        "status": "failed",
                    }
                }
            ]
        )
    with pytest.raises(AssertionError, match="not terminal"):
        full_harness._select_final_workcell_trees(
            [
                {
                    "workcell_run": {
                        "stage_path": "frontend-repair/frontend",
                        "loop_iteration": 1,
                        "status": "running",
                    }
                }
            ]
        )
    assert "只能修改自己的 Repository" in EVALUATION_TARGET
    assert "不执行 Candidate/Release Gate 或 Apply" in EVALUATION_TARGET


def test_resume_baseline_must_match_track_project_and_four_mains(tmp_path: Path) -> None:
    initial_main = {key: str(index) * 40 for index, key in enumerate(WORKCELL_KEYS, 1)}
    _write_session_baseline(
        tmp_path,
        track="real-codex-local-pr",
        project_id="guided",
        initial_main=initial_main,
    )

    assert full_harness._load_session_baseline(
        tmp_path,
        track="real-codex-local-pr",
        project_id="guided",
    ) == initial_main
    with pytest.raises(AssertionError):
        full_harness._load_session_baseline(
            tmp_path,
            track="deterministic",
            project_id="guided",
        )


def test_delivery_status_wait_retries_transient_browser_request_timeout(
    monkeypatch,
) -> None:
    responses: list[object] = [
        PlaywrightTimeoutError("transient API read timeout"),
        {"status": "awaiting_candidate_decision"},
    ]

    def get_json(_request, _url):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(full_harness, "_get_json", get_json)

    result = full_harness._wait_delivery_status(
        object(), "http://local.invalid/delivery", "awaiting_candidate_decision"
    )

    assert result == {"status": "awaiting_candidate_decision"}


def _create_bare_main(root: Path, project_id: str, workcell_key: str) -> tuple[Path, str]:
    work = root / f"source-{workcell_key}"
    bare = (
        root
        / "browser-workspaces"
        / "projects"
        / project_id
        / workcell_key
        / "backend-demo.git"
    )
    subprocess.run(("git", "init", "-b", "main", str(work)), check=True, capture_output=True)
    subprocess.run(
        ("git", "-C", str(work), "config", "user.email", "test@example.invalid"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(work), "config", "user.name", "Harness Test"), check=True
    )
    work.joinpath("README.md").write_text(f"{workcell_key}\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(work), "add", "README.md"), check=True)
    subprocess.run(
        ("git", "-C", str(work), "commit", "-m", "baseline"),
        check=True,
        capture_output=True,
    )
    bare.parent.mkdir(parents=True)
    subprocess.run(
        ("git", "clone", "--bare", str(work), str(bare)),
        check=True,
        capture_output=True,
    )
    revision = subprocess.run(
        ("git", f"--git-dir={bare}", "rev-parse", "refs/heads/main"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return work, revision


def test_session_baseline_and_main_invariants_capture_before_and_after(tmp_path: Path) -> None:
    project_id = "evaluation-project"
    design_work, design_main = _create_bare_main(tmp_path, project_id, "design")
    _, backend_main = _create_bare_main(tmp_path, project_id, "backend")
    initial = {"design": design_main, "backend": backend_main}
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    baseline = _write_session_baseline(
        evidence,
        track="real-codex-local-pr",
        project_id=project_id,
        initial_main=initial,
    )
    assert json.loads((evidence / "session-baseline.json").read_text()) == baseline
    assert baseline["initial_main"] == {"backend": backend_main, "design": design_main}
    assert all(
        item["unchanged"] is True
        for item in _repository_main_invariants(
            tmp_path, project_id=project_id, initial_main=initial
        ).values()
    )

    design_work.joinpath("README.md").write_text("changed\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(design_work), "add", "README.md"), check=True)
    subprocess.run(
        ("git", "-C", str(design_work), "commit", "-m", "changed"),
        check=True,
        capture_output=True,
    )
    changed = subprocess.run(
        ("git", "-C", str(design_work), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    design_bare = (
        tmp_path
        / "browser-workspaces"
        / "projects"
        / project_id
        / "design"
        / "backend-demo.git"
    )
    subprocess.run(
        (
            "git",
            f"--git-dir={design_bare}",
            "fetch",
            str(design_work),
            "+refs/heads/main:refs/heads/main",
        ),
        check=True,
        capture_output=True,
    )
    invariants = _repository_main_invariants(
        tmp_path, project_id=project_id, initial_main=initial
    )
    assert invariants["design"] == {
        "initial_main": design_main,
        "current_main": changed,
        "unchanged": False,
    }
    assert invariants["backend"]["unchanged"] is True


def test_attempt_reader_and_failure_builder_emit_only_allowlisted_diagnostics(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE agent_runs(
            id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL,
            binding_site TEXT NOT NULL,
            run_role TEXT NOT NULL
        );
        CREATE TABLE agent_attempts(
            id TEXT PRIMARY KEY,
            agent_run_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            runtime_identity TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            result_artifact_sha256 TEXT
        );
        INSERT INTO agent_runs VALUES(
            'run-1', 'delivery-1', 'design-repair/design:main', 'main'
        );
        INSERT INTO agent_attempts VALUES(
            'attempt-1', 'run-1', 'planning', 1, 'failed',
            'CODEX_WORKCELL_ATTEMPT_FAILED', 'codex-cli',
            '2026-09-21T00:00:00+00:00', '2026-09-21T00:00:34.500000+00:00', NULL
        );
        """
    )
    diagnostic = ContentAddressedArtifactStorage(tmp_path / "artifacts").put_json(
        {
            "contract_version": "codex-cli-exit-diagnostic-v1",
            "exit_code": 1,
            "stderr_bytes": 27,
            "stderr_sha256": "d" * 64,
            "stable_category": "CODEX_WORKCELL_ATTEMPT_FAILED",
        }
    )
    connection.execute(
        "UPDATE agent_attempts SET result_artifact_sha256=? WHERE id='attempt-1'",
        (diagnostic.sha256,),
    )
    connection.commit()
    connection.close()
    attempts = _read_attempt_summaries(
        database,
        "delivery-1",
        artifacts_root=tmp_path / "artifacts",
    )
    sensitive_value = secrets.token_urlsafe(24)
    attempts[0]["raw_stderr"] = sensitive_value

    report = _build_failure_artifact(
        track="real-codex-local-pr",
        project_id="project-1",
        delivery_id="delivery-1",
        delivery={
            "status": "failed",
            "error_code": "PIPELINE_EXECUTION_FAILED",
            "apply_receipt": None,
            "password": sensitive_value,
            "snapshot": {"secret": sensitive_value},
        },
        project={"onboarding": {"status": "in_evaluation"}, "token": sensitive_value},
        release={
            "manifest": None,
            "apply_attempt": None,
            "remote_apply_receipts": [],
            "stderr": sensitive_value,
        },
        evidence=[
            {"kind": "plan-gate", "status": "verified", "payload": sensitive_value}
        ],
        attempts=attempts,
        main_invariants={
            "design": {
                "initial_main": "a" * 40,
                "current_main": "a" * 40,
                "unchanged": True,
            }
        },
        planning_timeout_seconds=120,
    )

    assert report["failure_classification"] == "CODEX_WORKCELL_ATTEMPT_FAILED"
    assert report["stderr_diagnostic_available"] is True
    assert report["stderr_diagnostic"] == {
        "contract_version": "codex-cli-exit-diagnostic-v1",
        "exit_code": 1,
        "stderr_bytes": 27,
        "stderr_sha256": "d" * 64,
        "stable_category": "CODEX_WORKCELL_ATTEMPT_FAILED",
    }
    assert report["remote_main_unchanged"] is True
    assert report["verified_evidence_kinds"] == ["plan-gate"]
    assert report["attempts"] == [
        {
            "binding_site": "design-repair/design:main",
            "run_role": "main",
            "phase": "planning",
            "ordinal": 1,
            "status": "failed",
            "error_code": "CODEX_WORKCELL_ATTEMPT_FAILED",
            "runtime_identity": "codex-cli",
            "duration_seconds": 34.5,
        }
    ]
    serialized = json.dumps(report)
    assert sensitive_value not in serialized
