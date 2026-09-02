from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ...modules.agents import AgentRun, ArtifactEnvelope
from ...shared.events import ProductEvent
from ...shared.hashes import sha256_json
from ...shared.ids import new_id
from .execution_domain import (
    AgentAttempt,
    CandidateVerification,
    DelegationAssignment,
    DelegationPlan,
    ReviewArtifact,
    WorkcellResult,
    WorkcellResultValidation,
    WorkcellRun,
    WorkcellRunCreate,
)


class SQLiteWorkcellExecutionRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(self, request: WorkcellRunCreate) -> WorkcellRun:
        now = datetime.now(UTC)
        snapshot_payload = request.snapshot.model_dump(mode="json")
        snapshot_sha = sha256_json(snapshot_payload)
        main_binding = next(
            item for item in request.snapshot.slot_bindings if item.slot_key == "main"
        )
        attempt = AgentAttempt(
            agent_run_id="pending",
            phase="planning",
            ordinal=1,
            provider_binding_hash=main_binding.resolved_provider_binding_hash,
            runtime_identity=_runtime_identity(main_binding.deployment_snapshot),
            status="running",
            started_at=now,
        )
        main = AgentRun(
            delivery_id=request.delivery_id,
            pipeline_revision_id=request.snapshot.pipeline_revision_id,
            binding_site=f"{request.snapshot.stage_path}:main",
            resolved_binding_hash=main_binding.resolved_provider_binding_hash,
            deployment_snapshot=main_binding.deployment_snapshot,
            attempt_id=attempt.id,
            runtime_identity=attempt.runtime_identity,
            status="running",
            workcell_run_id="pending",
            depth=0,
            run_role="main",
            workspace_access="none",
            slot_key="main",
            created_at=now,
            updated_at=now,
        )
        main = main.model_copy(update={"root_agent_run_id": main.id})
        attempt = attempt.model_copy(update={"agent_run_id": main.id})
        run = WorkcellRun(
            delivery_id=request.delivery_id,
            pipeline_run_id=request.pipeline_run_id,
            stage_attempt_id=request.stage_attempt_id,
            stage_path=request.snapshot.stage_path,
            loop_iteration=request.loop_iteration,
            workcell_key=request.snapshot.workcell_key,
            workcell_snapshot=request.snapshot,
            workcell_snapshot_sha256=snapshot_sha,
            status="planning",
            main_agent_run_id=main.id,
            version=1,
            deadline_at=now
            + timedelta(seconds=request.snapshot.delegation_policy.wall_clock_budget_seconds),
            created_at=now,
            updated_at=now,
        )
        main = main.model_copy(update={"workcell_run_id": run.id})
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _insert_workcell(connection, run)
            _insert_agent(connection, main)
            _insert_attempt(connection, attempt)
            _append_event(
                connection,
                ProductEvent(
                    event_type="workcell-run.started",
                    aggregate_type="workcell-run",
                    aggregate_id=run.id,
                    aggregate_version=run.version,
                    payload={
                        "delivery_id": run.delivery_id,
                        "stage_attempt_id": run.stage_attempt_id,
                        "workcell_key": run.workcell_key,
                        "snapshot_sha256": run.workcell_snapshot_sha256,
                    },
                ),
            )
        return run

    def get(self, run_id: str) -> WorkcellRun:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_WORKCELL_COLUMNS} FROM workcell_runs WHERE id=?",  # noqa: S608
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _workcell(row)

    def list_delivery(self, delivery_id: str) -> tuple[WorkcellRun, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_WORKCELL_COLUMNS} FROM workcell_runs
                WHERE delivery_id=? ORDER BY created_at,id""",  # noqa: S608
                (delivery_id,),
            ).fetchall()
        return tuple(_workcell(row) for row in rows)

    def get_agent(self, agent_run_id: str) -> AgentRun:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_AGENT_COLUMNS} FROM agent_runs WHERE id=?",  # noqa: S608
                (agent_run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(agent_run_id)
        return _agent(row)

    def list_agents(self, run_id: str) -> tuple[AgentRun, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_AGENT_COLUMNS} FROM agent_runs
                WHERE workcell_run_id=? ORDER BY depth,created_at,id""",  # noqa: S608
                (run_id,),
            ).fetchall()
        return tuple(_agent(row) for row in rows)

    def list_attempts(self, run_id: str) -> tuple[AgentAttempt, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_ATTEMPT_COLUMNS} FROM agent_attempts
                WHERE agent_run_id IN (
                    SELECT id FROM agent_runs WHERE workcell_run_id=?
                ) ORDER BY started_at,id""",  # noqa: S608
                (run_id,),
            ).fetchall()
        return tuple(_attempt(row) for row in rows)

    def list_delivery_attempts(self, delivery_id: str) -> tuple[AgentAttempt, ...]:
        """Project all observable attempts, including non-Workcell planning attempts."""

        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_ATTEMPT_COLUMNS} FROM agent_attempts
                WHERE agent_run_id IN (
                    SELECT id FROM agent_runs WHERE delivery_id=?
                ) ORDER BY started_at,id""",  # noqa: S608
                (delivery_id,),
            ).fetchall()
        return tuple(_attempt(row) for row in rows)

    def put_plan(
        self,
        run: WorkcellRun,
        assignments: tuple[DelegationAssignment, ...],
        *,
        planning_artifact_sha256: str | None = None,
    ) -> tuple[DelegationPlan, WorkcellRun]:
        if run.main_agent_run_id is None:
            raise RuntimeError("WORKCELL_MAIN_RUN_MISSING")
        now = datetime.now(UTC)
        payload = {
            "workcell_run_id": run.id,
            "main_agent_run_id": run.main_agent_run_id,
            "assignments": [item.model_dump(mode="json") for item in assignments],
        }
        plan = DelegationPlan(
            workcell_run_id=run.id,
            main_agent_run_id=run.main_agent_run_id,
            assignments=assignments,
            sha256=sha256_json(payload),
            created_at=now,
        )
        binding_by_slot = {item.slot_key: item for item in run.workcell_snapshot.slot_bindings}
        children: list[AgentRun] = []
        for assignment in assignments:
            binding = binding_by_slot[assignment.slot_key]
            child = AgentRun(
                delivery_id=run.delivery_id,
                pipeline_revision_id=run.workcell_snapshot.pipeline_revision_id,
                binding_site=f"{run.stage_path}:{assignment.slot_key}",
                resolved_binding_hash=binding.resolved_provider_binding_hash,
                deployment_snapshot=binding.deployment_snapshot,
                attempt_id=new_id(),
                runtime_identity=_runtime_identity(binding.deployment_snapshot),
                status="planned",
                workcell_run_id=run.id,
                parent_agent_run_id=run.main_agent_run_id,
                root_agent_run_id=run.main_agent_run_id,
                depth=1,
                run_role="child",
                delegate_purpose=assignment.delegate_purpose,
                workspace_access=assignment.workspace_access,
                slot_key=assignment.slot_key,
                created_at=now,
                updated_at=now,
            )
            children.append(child)
        updated = run.model_copy(
            update={
                "status": "delegating",
                "version": run.version + 1,
                "updated_at": now,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status,version FROM workcell_runs WHERE id=?",
                (run.id,),
            ).fetchone()
            if current != ("planning", run.version):
                raise RuntimeError("WORKCELL_RUN_VERSION_CONFLICT")
            connection.execute(
                """INSERT INTO delegation_plans(
                id,workcell_run_id,main_agent_run_id,assignments_json,sha256,created_at)
                VALUES(?,?,?,?,?,?)""",
                (
                    plan.id,
                    plan.workcell_run_id,
                    plan.main_agent_run_id,
                    _json([item.model_dump(mode="json") for item in plan.assignments]),
                    plan.sha256,
                    plan.created_at.isoformat(),
                ),
            )
            for child in children:
                _insert_agent(connection, child)
            connection.execute(
                """UPDATE agent_attempts SET status='succeeded',finished_at=?,
                result_artifact_sha256=?
                WHERE agent_run_id=? AND phase='planning' AND status='running'""",
                (
                    now.isoformat(),
                    planning_artifact_sha256 or plan.sha256,
                    run.main_agent_run_id,
                ),
            )
            connection.execute(
                """UPDATE agent_runs SET status='waiting',updated_at=?
                WHERE id=? AND status='running'""",
                (now.isoformat(), run.main_agent_run_id),
            )
            _update_workcell(connection, updated, run.version)
            _append_event(
                connection,
                ProductEvent(
                    event_type="workcell-run.delegation-planned",
                    aggregate_type="workcell-run",
                    aggregate_id=run.id,
                    aggregate_version=updated.version,
                    payload={"delegation_plan_sha256": plan.sha256},
                ),
            )
        return plan, updated

    def get_plan(self, run_id: str) -> DelegationPlan | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_PLAN_COLUMNS} FROM delegation_plans WHERE workcell_run_id=?",  # noqa: S608
                (run_id,),
            ).fetchone()
        return None if row is None else _plan(row)

    def start_child(self, child: AgentRun, *, max_concurrency: int) -> AgentRun:
        now = datetime.now(UTC)
        if child.workcell_run_id is None:
            raise RuntimeError("AGENT_RUN_NOT_IN_WORKCELL")
        started = child.model_copy(update={"status": "running", "updated_at": now})
        attempt = AgentAttempt(
            id=child.attempt_id,
            agent_run_id=child.id,
            phase="delegate",
            ordinal=1,
            provider_binding_hash=child.resolved_binding_hash,
            runtime_identity=child.runtime_identity,
            status="running",
            started_at=now,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status FROM agent_runs WHERE id=?",
                (child.id,),
            ).fetchone()
            if current != ("planned",):
                raise RuntimeError("AGENT_RUN_NOT_PLANNED")
            active = int(
                connection.execute(
                    """SELECT COUNT(*) FROM agent_runs
                    WHERE workcell_run_id=? AND run_role='child' AND status='running'""",
                    (child.workcell_run_id,),
                ).fetchone()[0]
            )
            if active >= max_concurrency:
                raise RuntimeError("WORKCELL_CHILD_CONCURRENCY_EXCEEDED")
            if child.delegate_purpose == "review":
                verified = connection.execute(
                    """SELECT 1 FROM workcell_candidate_verifications
                    WHERE workcell_run_id=? AND status='passed'""",
                    (child.workcell_run_id,),
                ).fetchone()
                if verified is None:
                    raise RuntimeError("REVIEW_CANDIDATE_NOT_VERIFIED")
            connection.execute(
                "UPDATE agent_runs SET status='running',updated_at=? WHERE id=?",
                (now.isoformat(), child.id),
            )
            _insert_attempt(connection, attempt)
            _bump_workcell(connection, child.workcell_run_id, now)
        return started

    def finish_child(
        self,
        child: AgentRun,
        *,
        status: str,
        artifacts: tuple[ArtifactEnvelope, ...],
        error_code: str | None,
    ) -> AgentRun:
        now = datetime.now(UTC)
        if child.workcell_run_id is None:
            raise RuntimeError("AGENT_RUN_NOT_IN_WORKCELL")
        finished = child.model_copy(
            update={
                "status": status,
                "artifact_envelopes": artifacts,
                "updated_at": now,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE agent_runs SET status=?,artifact_envelopes_json=?,updated_at=?
                WHERE id=? AND status='running'""",
                (
                    status,
                    _json([item.model_dump(mode="json") for item in artifacts]),
                    now.isoformat(),
                    child.id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_RUN_NOT_RUNNING")
            connection.execute(
                """UPDATE agent_attempts SET status=?,error_code=?,finished_at=?,
                result_artifact_sha256=?
                WHERE id=? AND status='running'""",
                (
                    status,
                    error_code,
                    now.isoformat(),
                    artifacts[0].sha256 if artifacts else None,
                    child.attempt_id,
                ),
            )
            if status != "succeeded":
                connection.execute(
                    """UPDATE workcell_runs SET status=?,error_code=?,version=version+1,
                    updated_at=? WHERE id=?""",
                    (
                        status,
                        error_code or "CHILD_AGENT_FAILED",
                        now.isoformat(),
                        child.workcell_run_id,
                    ),
                )
            else:
                _bump_workcell(connection, child.workcell_run_id, now)
        return finished

    def put_verification(
        self,
        run: WorkcellRun,
        verification: CandidateVerification,
    ) -> WorkcellRun:
        next_status = "reviewing" if verification.status == "passed" else "failed"
        error_code = None if verification.status == "passed" else "MACHINE_VERIFICATION_FAILED"
        updated = run.model_copy(
            update={
                "status": next_status,
                "error_code": error_code,
                "version": run.version + 1,
                "updated_at": verification.created_at,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO workcell_candidate_verifications(
                id,workcell_run_id,writer_agent_run_id,candidate_sha,diff_sha256,status,
                report_json,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    verification.id,
                    verification.workcell_run_id,
                    verification.writer_agent_run_id,
                    verification.candidate_sha,
                    verification.diff_sha256,
                    verification.status,
                    _json(verification.report),
                    verification.sha256,
                    verification.created_at.isoformat(),
                ),
            )
            _update_workcell(connection, updated, run.version)
        return updated

    def get_verification(self, run_id: str) -> CandidateVerification | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT {_VERIFICATION_COLUMNS}
                FROM workcell_candidate_verifications WHERE workcell_run_id=?""",  # noqa: S608
                (run_id,),
            ).fetchone()
        return None if row is None else _verification(row)

    def put_result_validation(
        self,
        validation: WorkcellResultValidation,
    ) -> WorkcellResultValidation:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO workcell_result_validations(
                id,workcell_run_id,status,artifact_references_json,report_json,
                sha256,created_at) VALUES(?,?,?,?,?,?,?)""",
                (
                    validation.id,
                    validation.workcell_run_id,
                    validation.status,
                    _json(
                        [item.model_dump(mode="json") for item in validation.artifact_references]
                    ),
                    _json(validation.report),
                    validation.sha256,
                    validation.created_at.isoformat(),
                ),
            )
        return validation

    def get_result_validation(self, run_id: str) -> WorkcellResultValidation | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT {_RESULT_VALIDATION_COLUMNS}
                FROM workcell_result_validations WHERE workcell_run_id=?""",  # noqa: S608
                (run_id,),
            ).fetchone()
        return None if row is None else _result_validation(row)

    def put_review(
        self,
        run: WorkcellRun,
        review: ReviewArtifact,
    ) -> WorkcellRun:
        blocked = bool(review.blocking_findings)
        updated = run.model_copy(
            update={
                "status": "failed" if blocked else "reviewing",
                "error_code": "WORKCELL_BLOCKING_REVIEW" if blocked else None,
                "version": run.version + 1,
                "updated_at": review.created_at,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO review_artifacts(
                id,workcell_run_id,reviewer_agent_run_id,candidate_sha,diff_sha256,
                reviewer_binding_hash,blocking_findings_json,artifact_reference_json,
                sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    review.id,
                    review.workcell_run_id,
                    review.reviewer_agent_run_id,
                    review.candidate_sha,
                    review.diff_sha256,
                    review.reviewer_binding_hash,
                    _json([item.model_dump(mode="json") for item in review.blocking_findings]),
                    _json(review.artifact_reference.model_dump(mode="json")),
                    review.sha256,
                    review.created_at.isoformat(),
                ),
            )
            _update_workcell(connection, updated, run.version)
        return updated

    def list_reviews(self, run_id: str) -> tuple[ReviewArtifact, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_REVIEW_COLUMNS} FROM review_artifacts
                WHERE workcell_run_id=? ORDER BY created_at,id""",  # noqa: S608
                (run_id,),
            ).fetchall()
        return tuple(_review(row) for row in rows)

    def start_synthesis(self, run: WorkcellRun, main: AgentRun) -> WorkcellRun:
        now = datetime.now(UTC)
        attempt = AgentAttempt(
            agent_run_id=main.id,
            phase="synthesis",
            ordinal=2,
            provider_binding_hash=main.resolved_binding_hash,
            runtime_identity=main.runtime_identity,
            status="running",
            started_at=now,
        )
        updated = run.model_copy(
            update={
                "status": "synthesizing",
                "version": run.version + 1,
                "updated_at": now,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE agent_runs SET status='running',updated_at=?
                WHERE id=? AND status='waiting'""",
                (now.isoformat(), main.id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("WORKCELL_MAIN_NOT_WAITING")
            _insert_attempt(connection, attempt)
            _update_workcell(connection, updated, run.version)
        return updated

    def put_result(
        self,
        run: WorkcellRun,
        result: WorkcellResult,
        *,
        synthesis_artifact_sha256: str | None = None,
    ) -> WorkcellRun:
        now = result.created_at
        updated = run.model_copy(
            update={
                "status": "succeeded",
                "version": run.version + 1,
                "updated_at": now,
            }
        )
        if run.main_agent_run_id is None:
            raise RuntimeError("WORKCELL_MAIN_RUN_MISSING")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO workcell_results(
                id,workcell_run_id,candidate_sha,diff_sha256,verification_sha256,
                review_artifact_ids_json,output_artifact_references_json,
                knowledge_citation_ids_json,sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    result.id,
                    result.workcell_run_id,
                    result.candidate_sha,
                    result.diff_sha256,
                    result.verification_sha256,
                    _json(result.review_artifact_ids),
                    _json(
                        [item.model_dump(mode="json") for item in result.output_artifact_references]
                    ),
                    _json(result.knowledge_citation_ids),
                    result.sha256,
                    result.created_at.isoformat(),
                ),
            )
            connection.execute(
                """UPDATE agent_attempts SET status='succeeded',finished_at=?,
                result_artifact_sha256=?
                WHERE agent_run_id=? AND phase='synthesis' AND status='running'""",
                (
                    now.isoformat(),
                    synthesis_artifact_sha256 or result.sha256,
                    run.main_agent_run_id,
                ),
            )
            connection.execute(
                """UPDATE agent_runs SET status='succeeded',updated_at=?
                WHERE id=? AND status='running'""",
                (now.isoformat(), run.main_agent_run_id),
            )
            _update_workcell(connection, updated, run.version)
        return updated

    def get_result(self, run_id: str) -> WorkcellResult | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_RESULT_COLUMNS} FROM workcell_results WHERE workcell_run_id=?",  # noqa: S608
                (run_id,),
            ).fetchone()
        return None if row is None else _result(row)

    def cancel(self, run: WorkcellRun, *, expected_version: int) -> WorkcellRun:
        return self._terminate(
            run,
            expected_version=expected_version,
            status="cancelled",
            error_code="WORKCELL_CANCELLED",
            attempt_error_code="PARENT_CANCELLED",
        )

    def timeout(self, run: WorkcellRun, *, expected_version: int) -> WorkcellRun:
        return self._terminate(
            run,
            expected_version=expected_version,
            status="timed_out",
            error_code="WORKCELL_WALL_CLOCK_BUDGET_EXCEEDED",
            attempt_error_code="PARENT_TIMED_OUT",
        )

    def _terminate(
        self,
        run: WorkcellRun,
        *,
        expected_version: int,
        status: str,
        error_code: str,
        attempt_error_code: str,
    ) -> WorkcellRun:
        now = datetime.now(UTC)
        updated = run.model_copy(
            update={
                "status": status,
                "error_code": error_code,
                "version": run.version + 1,
                "updated_at": now,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _update_workcell(connection, updated, expected_version)
            connection.execute(
                """UPDATE agent_runs SET status=?,updated_at=?
                WHERE workcell_run_id=? AND status IN ('planned','running','waiting')""",
                (status, now.isoformat(), run.id),
            )
            connection.execute(
                """UPDATE agent_attempts SET status=?,error_code=?,
                finished_at=? WHERE status='running' AND agent_run_id IN (
                    SELECT id FROM agent_runs WHERE workcell_run_id=?
                )""",
                (status, attempt_error_code, now.isoformat(), run.id),
            )
        return updated

    def interrupt_running_codex_attempts(self) -> tuple[str, ...]:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT DISTINCT ar.workcell_run_id
                FROM agent_attempts aa JOIN agent_runs ar ON ar.id=aa.agent_run_id
                WHERE aa.status='running' AND ar.workcell_run_id IS NOT NULL
                  AND lower(COALESCE(aa.runtime_identity,'')) LIKE '%codex%'"""
            ).fetchall()
            run_ids = tuple(str(row[0]) for row in rows)
            if not run_ids:
                return ()
            placeholders = ",".join("?" for _ in run_ids)
            connection.execute(
                f"""UPDATE agent_attempts SET status='interrupted',
                error_code='CODEX_ATTEMPT_INTERRUPTED',finished_at=?
                WHERE status='running' AND agent_run_id IN (
                    SELECT id FROM agent_runs WHERE workcell_run_id IN ({placeholders})
                )""",  # noqa: S608
                (now.isoformat(), *run_ids),
            )
            connection.execute(
                f"""UPDATE agent_runs SET status='interrupted',updated_at=?
                WHERE status='running' AND workcell_run_id IN ({placeholders})""",  # noqa: S608
                (now.isoformat(), *run_ids),
            )
            connection.execute(
                f"""UPDATE agent_runs SET status='cancelled',updated_at=?
                WHERE status IN ('planned','waiting')
                AND workcell_run_id IN ({placeholders})""",  # noqa: S608
                (now.isoformat(), *run_ids),
            )
            connection.execute(
                f"""UPDATE workcell_runs SET status='interrupted',
                error_code='CODEX_ATTEMPT_INTERRUPTED',version=version+1,updated_at=?
                WHERE id IN ({placeholders})""",  # noqa: S608
                (now.isoformat(), *run_ids),
            )
        return run_ids

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


_WORKCELL_COLUMNS = (
    "id,delivery_id,pipeline_run_id,stage_attempt_id,stage_path,loop_iteration,workcell_key,"
    "workcell_snapshot_json,workcell_snapshot_sha256,status,main_agent_run_id,version,"
    "deadline_at,error_code,created_at,updated_at"
)
_AGENT_COLUMNS = (
    "id,delivery_id,pipeline_revision_id,binding_site,resolved_binding_hash,"
    "deployment_snapshot_json,attempt_id,runtime_identity,status,artifact_envelopes_json,"
    "created_at,updated_at,workcell_run_id,parent_agent_run_id,root_agent_run_id,depth,"
    "run_role,delegate_purpose,workspace_access,slot_key"
)
_ATTEMPT_COLUMNS = (
    "id,agent_run_id,phase,ordinal,provider_binding_hash,runtime_identity,status,error_code,"
    "result_artifact_sha256,started_at,finished_at"
)
_PLAN_COLUMNS = "id,workcell_run_id,main_agent_run_id,assignments_json,sha256,created_at"
_VERIFICATION_COLUMNS = (
    "id,workcell_run_id,writer_agent_run_id,candidate_sha,diff_sha256,status,report_json,"
    "sha256,created_at"
)
_REVIEW_COLUMNS = (
    "id,workcell_run_id,reviewer_agent_run_id,candidate_sha,diff_sha256,"
    "reviewer_binding_hash,blocking_findings_json,artifact_reference_json,sha256,created_at"
)
_RESULT_COLUMNS = (
    "id,workcell_run_id,candidate_sha,diff_sha256,verification_sha256,"
    "review_artifact_ids_json,output_artifact_references_json,"
    "knowledge_citation_ids_json,sha256,created_at"
)
_RESULT_VALIDATION_COLUMNS = (
    "id,workcell_run_id,status,artifact_references_json,report_json,sha256,created_at"
)


def _insert_workcell(connection: sqlite3.Connection, run: WorkcellRun) -> None:
    connection.execute(
        """INSERT INTO workcell_runs(
        id,delivery_id,pipeline_run_id,stage_attempt_id,stage_path,loop_iteration,workcell_key,
        workcell_snapshot_json,workcell_snapshot_sha256,status,main_agent_run_id,version,
        deadline_at,error_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run.id,
            run.delivery_id,
            run.pipeline_run_id,
            run.stage_attempt_id,
            run.stage_path,
            run.loop_iteration,
            run.workcell_key,
            _json(run.workcell_snapshot.model_dump(mode="json")),
            run.workcell_snapshot_sha256,
            run.status,
            run.main_agent_run_id,
            run.version,
            run.deadline_at.isoformat(),
            run.error_code,
            run.created_at.isoformat(),
            run.updated_at.isoformat(),
        ),
    )


def _insert_agent(connection: sqlite3.Connection, run: AgentRun) -> None:
    connection.execute(
        """INSERT INTO agent_runs(
        id,delivery_id,pipeline_revision_id,binding_site,resolved_binding_hash,
        deployment_snapshot_json,attempt_id,runtime_identity,status,artifact_envelopes_json,
        created_at,updated_at,workcell_run_id,parent_agent_run_id,root_agent_run_id,depth,
        run_role,delegate_purpose,workspace_access,slot_key)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run.id,
            run.delivery_id,
            run.pipeline_revision_id,
            run.binding_site,
            run.resolved_binding_hash,
            _json(run.deployment_snapshot),
            run.attempt_id,
            run.runtime_identity,
            run.status,
            _json([item.model_dump(mode="json") for item in run.artifact_envelopes]),
            run.created_at.isoformat(),
            run.updated_at.isoformat(),
            run.workcell_run_id,
            run.parent_agent_run_id,
            run.root_agent_run_id,
            run.depth,
            run.run_role,
            run.delegate_purpose,
            run.workspace_access,
            run.slot_key,
        ),
    )


def _insert_attempt(connection: sqlite3.Connection, attempt: AgentAttempt) -> None:
    connection.execute(
        """INSERT INTO agent_attempts(
        id,agent_run_id,phase,ordinal,provider_binding_hash,runtime_identity,status,error_code,
        result_artifact_sha256,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            attempt.id,
            attempt.agent_run_id,
            attempt.phase,
            attempt.ordinal,
            attempt.provider_binding_hash,
            attempt.runtime_identity,
            attempt.status,
            attempt.error_code,
            attempt.result_artifact_sha256,
            attempt.started_at.isoformat(),
            None if attempt.finished_at is None else attempt.finished_at.isoformat(),
        ),
    )


def _update_workcell(
    connection: sqlite3.Connection,
    run: WorkcellRun,
    expected_version: int,
) -> None:
    cursor = connection.execute(
        """UPDATE workcell_runs SET status=?,main_agent_run_id=?,version=?,error_code=?,updated_at=?
        WHERE id=? AND version=?""",
        (
            run.status,
            run.main_agent_run_id,
            run.version,
            run.error_code,
            run.updated_at.isoformat(),
            run.id,
            expected_version,
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("WORKCELL_RUN_VERSION_CONFLICT")


def _bump_workcell(connection: sqlite3.Connection, run_id: str, now: datetime) -> None:
    connection.execute(
        "UPDATE workcell_runs SET version=version+1,updated_at=? WHERE id=?",
        (now.isoformat(), run_id),
    )


def _workcell(row: tuple[object, ...]) -> WorkcellRun:
    values = dict(zip(_WORKCELL_COLUMNS.split(","), row, strict=True))
    values["workcell_snapshot"] = json.loads(str(values.pop("workcell_snapshot_json")))
    return WorkcellRun.model_validate(values)


def _agent(row: tuple[object, ...]) -> AgentRun:
    values = dict(zip(_AGENT_COLUMNS.split(","), row, strict=True))
    values["deployment_snapshot"] = json.loads(str(values.pop("deployment_snapshot_json")))
    values["artifact_envelopes"] = json.loads(str(values.pop("artifact_envelopes_json")))
    return AgentRun.model_validate(values)


def _attempt(row: tuple[object, ...]) -> AgentAttempt:
    return AgentAttempt.model_validate(dict(zip(_ATTEMPT_COLUMNS.split(","), row, strict=True)))


def _plan(row: tuple[object, ...]) -> DelegationPlan:
    values = dict(zip(_PLAN_COLUMNS.split(","), row, strict=True))
    values["assignments"] = json.loads(str(values.pop("assignments_json")))
    return DelegationPlan.model_validate(values)


def _verification(row: tuple[object, ...]) -> CandidateVerification:
    values = dict(zip(_VERIFICATION_COLUMNS.split(","), row, strict=True))
    values["report"] = json.loads(str(values.pop("report_json")))
    return CandidateVerification.model_validate(values)


def _review(row: tuple[object, ...]) -> ReviewArtifact:
    values = dict(zip(_REVIEW_COLUMNS.split(","), row, strict=True))
    values["blocking_findings"] = json.loads(str(values.pop("blocking_findings_json")))
    values["artifact_reference"] = json.loads(str(values.pop("artifact_reference_json")))
    return ReviewArtifact.model_validate(values)


def _result(row: tuple[object, ...]) -> WorkcellResult:
    values = dict(zip(_RESULT_COLUMNS.split(","), row, strict=True))
    values["review_artifact_ids"] = json.loads(str(values.pop("review_artifact_ids_json")))
    values["output_artifact_references"] = json.loads(
        str(values.pop("output_artifact_references_json"))
    )
    values["knowledge_citation_ids"] = json.loads(str(values.pop("knowledge_citation_ids_json")))
    return WorkcellResult.model_validate(values)


def _result_validation(row: tuple[object, ...]) -> WorkcellResultValidation:
    values = dict(zip(_RESULT_VALIDATION_COLUMNS.split(","), row, strict=True))
    values["artifact_references"] = json.loads(str(values.pop("artifact_references_json")))
    values["report"] = json.loads(str(values.pop("report_json")))
    return WorkcellResultValidation.model_validate(values)


def _runtime_identity(snapshot: dict[str, object]) -> str | None:
    value = snapshot.get("runtime_identity")
    return value if isinstance(value, str) else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _append_event(connection: sqlite3.Connection, event: ProductEvent) -> None:
    connection.execute(
        """INSERT INTO product_events(
        event_id,event_type,aggregate_type,aggregate_id,aggregate_version,payload_json,occurred_at)
        VALUES(?,?,?,?,?,?,?)""",
        (
            event.id,
            event.event_type,
            event.aggregate_type,
            event.aggregate_id,
            event.aggregate_version,
            _json(event.payload),
            event.occurred_at.isoformat(),
        ),
    )
