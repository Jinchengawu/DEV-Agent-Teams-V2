"""Fail-closed readiness projection for a deterministic product showcase.

This projection proves that a local runtime contains a reviewable Workcell delivery.
It deliberately does not represent provider, GitHub, Feishu, or Live acceptance.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict


class ShowcaseFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str | None = None
    delivery_id: str | None = None
    completed_deliveries: int = 0
    succeeded_workcell_runs: int = 0
    distinct_workcells: int = 0
    agent_runs: int = 0
    child_agent_runs: int = 0
    succeeded_agent_attempts: int = 0
    verified_candidates: int = 0
    distinct_candidate_workcells: int = 0
    pull_request_receipts: int = 0
    remote_apply_receipts: int = 0
    active_release_manifests: int = 0
    verified_evidence: int = 0


class ShowcaseCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    passed: bool
    observed: str
    required: str


class ShowcaseReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    proof_scope: str = "deterministic_showcase_only"
    live_accepted: bool = False
    project_id: str | None = None
    delivery_id: str | None = None
    checks: tuple[ShowcaseCheck, ...]


def evaluate_showcase_readiness(facts: ShowcaseFacts) -> ShowcaseReadinessReport:
    checks = (
        ShowcaseCheck(
            id="completed_delivery",
            passed=facts.completed_deliveries >= 1,
            observed=str(facts.completed_deliveries),
            required=">=1 completed delivery",
        ),
        ShowcaseCheck(
            id="workcell_execution",
            passed=facts.succeeded_workcell_runs >= 5 and facts.distinct_workcells == 4,
            observed=(
                f"{facts.succeeded_workcell_runs} succeeded runs / "
                f"{facts.distinct_workcells} workcells"
            ),
            required=">=5 succeeded runs across exactly 4 workcells",
        ),
        ShowcaseCheck(
            id="agent_observability",
            passed=(
                facts.agent_runs >= 8
                and facts.child_agent_runs >= 4
                and facts.succeeded_agent_attempts >= facts.agent_runs
            ),
            observed=(
                f"{facts.agent_runs} runs / {facts.child_agent_runs} children / "
                f"{facts.succeeded_agent_attempts} succeeded attempts"
            ),
            required=">=8 runs, >=4 visible children, attempts >= runs",
        ),
        ShowcaseCheck(
            id="four_repository_candidates",
            passed=facts.verified_candidates == 4 and facts.distinct_candidate_workcells == 4,
            observed=(
                f"{facts.verified_candidates} verified candidates / "
                f"{facts.distinct_candidate_workcells} workcells"
            ),
            required="exactly 4 verified candidates across 4 workcells",
        ),
        ShowcaseCheck(
            id="four_repository_release",
            passed=(
                facts.pull_request_receipts == 4
                and facts.remote_apply_receipts == 4
                and facts.active_release_manifests == 1
            ),
            observed=(
                f"{facts.pull_request_receipts} PR / {facts.remote_apply_receipts} apply / "
                f"{facts.active_release_manifests} manifest"
            ),
            required="4 PR receipts, 4 apply receipts, 1 active manifest",
        ),
        ShowcaseCheck(
            id="evidence_ledger",
            passed=facts.verified_evidence >= 6,
            observed=str(facts.verified_evidence),
            required=">=6 verified evidence records",
        ),
    )
    return ShowcaseReadinessReport(
        status="ready" if all(check.passed for check in checks) else "not_ready",
        project_id=facts.project_id,
        delivery_id=facts.delivery_id,
        checks=checks,
    )


def collect_showcase_facts(
    database: Path,
    *,
    project_id: str | None = None,
    delivery_id: str | None = None,
) -> ShowcaseFacts:
    """Collect non-secret facts from an initialized Agent-Team-OS SQLite database."""

    if not database.is_file():
        return ShowcaseFacts(project_id=project_id, delivery_id=delivery_id)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        selected = _select_delivery(connection, project_id=project_id, delivery_id=delivery_id)
        if selected is None:
            return ShowcaseFacts(project_id=project_id, delivery_id=delivery_id)
        selected_delivery_id = str(selected["id"])
        selected_project_id = str(selected["project_id"])

        def scalar(statement: str, parameters: tuple[object, ...]) -> int:
            row = connection.execute(statement, parameters).fetchone()
            return int(row[0]) if row is not None else 0

        return ShowcaseFacts(
            project_id=selected_project_id,
            delivery_id=selected_delivery_id,
            completed_deliveries=1,
            succeeded_workcell_runs=scalar(
                "SELECT COUNT(*) FROM workcell_runs WHERE delivery_id=? AND status='succeeded'",
                (selected_delivery_id,),
            ),
            distinct_workcells=scalar(
                "SELECT COUNT(DISTINCT workcell_key) FROM workcell_runs "
                "WHERE delivery_id=? AND status='succeeded'",
                (selected_delivery_id,),
            ),
            agent_runs=scalar(
                "SELECT COUNT(*) FROM agent_runs WHERE delivery_id=?",
                (selected_delivery_id,),
            ),
            child_agent_runs=scalar(
                "SELECT COUNT(*) FROM agent_runs WHERE delivery_id=? AND depth=1",
                (selected_delivery_id,),
            ),
            succeeded_agent_attempts=scalar(
                "SELECT COUNT(*) FROM agent_attempts aa JOIN agent_runs ar "
                "ON ar.id=aa.agent_run_id WHERE ar.delivery_id=? AND aa.status='succeeded'",
                (selected_delivery_id,),
            ),
            verified_candidates=scalar(
                "SELECT COUNT(*) FROM workspace_candidates_v2 "
                "WHERE delivery_id=? AND status='verified'",
                (selected_delivery_id,),
            ),
            distinct_candidate_workcells=scalar(
                "SELECT COUNT(DISTINCT workcell_key) FROM workspace_candidates_v2 "
                "WHERE delivery_id=? AND status='verified'",
                (selected_delivery_id,),
            ),
            pull_request_receipts=scalar(
                "SELECT COUNT(*) FROM github_pr_receipts pr JOIN workspace_candidates_v2 c "
                "ON c.id=pr.candidate_id WHERE c.delivery_id=?",
                (selected_delivery_id,),
            ),
            remote_apply_receipts=scalar(
                "SELECT COUNT(*) FROM remote_apply_receipts WHERE delivery_id=?",
                (selected_delivery_id,),
            ),
            active_release_manifests=scalar(
                "SELECT COUNT(*) FROM release_manifests_v2 "
                "WHERE delivery_id=? AND project_id=? AND status='active'",
                (selected_delivery_id, selected_project_id),
            ),
            verified_evidence=scalar(
                "SELECT COUNT(*) FROM evidence_records "
                "WHERE delivery_id=? AND project_id=? AND status='verified'",
                (selected_delivery_id, selected_project_id),
            ),
        )


def _select_delivery(
    connection: sqlite3.Connection,
    *,
    project_id: str | None,
    delivery_id: str | None,
) -> sqlite3.Row | None:
    filters = ["json_extract(snapshot_json, '$.status')='completed'"]
    parameters: list[object] = []
    if project_id:
        filters.append("project_id=?")
        parameters.append(project_id)
    if delivery_id:
        filters.append("id=?")
        parameters.append(delivery_id)
    return cast(
        sqlite3.Row | None,
        connection.execute(
            "SELECT id, project_id FROM deliveries WHERE "
            + " AND ".join(filters)
            + " ORDER BY rowid DESC LIMIT 1",
            tuple(parameters),
        ).fetchone(),
    )
