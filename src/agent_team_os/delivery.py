"""Delivery aggregate and its narrow application service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from acwm.domain import (
    GateSnapshot,
    GateStatus,
    GateSubject,
    ResolvedProviderBinding,
    StaleGateDecision,
    decide_gate,
    open_gate,
)
from pydantic import BaseModel, ConfigDict, Field

from .modules.agents import AgentRunLedger
from .modules.orchestration import PipelineCatalog, PipelineRunLedger
from .shared.events import ProductEvent
from .shared.hashes import Sha256


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AcceptanceCriterion(ImmutableModel):
    id: str
    statement: str


class RequirementArtifact(ImmutableModel):
    summary: str
    non_goals: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...]


class SystemPolicy(ImmutableModel):
    allowed_paths: tuple[str, ...] = ("src/**", "tests/**")
    verification_commands: tuple[str, ...] = ("python -m unittest discover -s tests -v",)


class TaskContract(ImmutableModel):
    title: str
    instructions: str
    acceptance_ids: tuple[str, ...]
    system_policy: SystemPolicy = SystemPolicy()


class CandidateChange(ImmutableModel):
    base_revision: str
    candidate_revision: str
    diff_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_files: tuple[str, ...]
    candidate_ref: str = ""
    unified_diff: str = ""


class VerificationRun(ImmutableModel):
    status: Literal["passed", "failed"]
    commands: tuple[str, ...]
    exit_code: int
    log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redacted_log: str = ""
    acceptance_ids: tuple[str, ...] = ()


class ApplyReceipt(ImmutableModel):
    before_revision: str
    candidate_revision: str
    after_revision: str
    result: Literal["applied"]
    recovered: bool = False


class GateRecord(ImmutableModel):
    gate_id: str
    subject_kind: str
    artifact_id: str
    subject_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int
    decision: Literal["approve", "reject"] | None = None


class DeliveryRun(ImmutableModel):
    id: str
    workspace_id: str
    user_request: str
    status: Literal[
        "queued",
        "planning",
        "awaiting_plan_decision",
        "executing",
        "verifying",
        "awaiting_candidate_decision",
        "applying",
        "completed",
        "rejected",
        "failed",
        "cancelled",
    ]
    version: int
    requirements: RequirementArtifact | None = None
    task: TaskContract | None = None
    candidate: CandidateChange | None = None
    verification: VerificationRun | None = None
    apply_receipt: ApplyReceipt | None = None
    plan_gate: GateRecord | None = None
    candidate_gate: GateRecord | None = None
    pipeline_run_id: str | None = None
    pipeline_revision_id: str | None = None
    resolved_pipeline_sha256: Sha256 | None = None
    journey_revision_id: str | None = None
    journey_binding_snapshot: dict[str, dict[str, object]] = Field(default_factory=dict)
    resolved_provider_bindings: dict[str, dict[str, object]] = Field(default_factory=dict)
    resolved_journey_sha256: Sha256 | None
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence_identity: str
    planning_identity: str
    execution_identity: str | None = None


class PlanningService(Protocol):
    evidence_identity: str

    async def analyze(self, user_request: str) -> RequirementArtifact: ...

    async def plan(self, requirements: RequirementArtifact) -> TaskContract: ...


class CodeExecutor(Protocol):
    evidence_identity: str

    async def execute(
        self, task: TaskContract, workspace_id: str, delivery_id: str
    ) -> CandidateChange: ...


class PlanningServiceError(RuntimeError):
    pass


class CandidateVerifier(Protocol):
    async def verify(
        self, candidate: CandidateChange, task: TaskContract, workspace_id: str
    ) -> VerificationRun: ...


class CandidateApplier(Protocol):
    async def apply(self, candidate: CandidateChange, workspace_id: str) -> ApplyReceipt: ...


class DeliveryNotFoundError(LookupError):
    pass


class DeliveryVersionConflictError(RuntimeError):
    pass


class DeliveryStateConflictError(RuntimeError):
    pass


class ActiveDeliveryConflictError(DeliveryStateConflictError):
    pass


class ProcessInterruptedError(RuntimeError):
    code = "PROCESS_INTERRUPTED"


class RuntimeBindingConflictError(DeliveryStateConflictError):
    def __init__(self, capability_id: str, expected: str, actual: str | None) -> None:
        super().__init__(capability_id)
        self.capability_id = capability_id
        self.expected = expected
        self.actual = actual


class DeliveryRepository(Protocol):
    def save(self, delivery: DeliveryRun) -> None: ...

    def get(self, delivery_id: str) -> DeliveryRun | None: ...

    def list(self) -> tuple[DeliveryRun, ...]: ...

    def list_events(self, delivery_id: str) -> tuple[ProductEvent, ...]: ...


class PipelineExecution(Protocol):
    """Small product interface for the deep Pipeline execution module."""

    def start(self, delivery: DeliveryRun) -> None: ...

    async def advance(self, delivery_id: str) -> None: ...

    async def decide_plan(
        self,
        delivery: DeliveryRun,
        *,
        decision: Literal["approve", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun: ...

    async def decide_candidate(
        self,
        delivery: DeliveryRun,
        *,
        decision: Literal["accept", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun: ...

    def cancel(self, delivery: DeliveryRun) -> None: ...

    def fail(self, delivery_id: str, error: Exception) -> None: ...

    async def recover_applying(self, delivery: DeliveryRun) -> None: ...


class InMemoryDeliveryRepository:
    def __init__(self) -> None:
        self._deliveries: dict[str, DeliveryRun] = {}
        self._events: list[ProductEvent] = []

    def save(self, delivery: DeliveryRun) -> None:
        previous = self._deliveries.get(delivery.id)
        self._deliveries[delivery.id] = delivery
        if previous != delivery:
            self._events.append(_delivery_event(delivery))

    def get(self, delivery_id: str) -> DeliveryRun | None:
        return self._deliveries.get(delivery_id)

    def list(self) -> tuple[DeliveryRun, ...]:
        return tuple(self._deliveries.values())

    def list_events(self, delivery_id: str) -> tuple[ProductEvent, ...]:
        return tuple(event for event in self._events if event.aggregate_id == delivery_id)


class SQLiteDeliveryRepository:
    """A small durable boundary; the serialized aggregate is authoritative."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS deliveries(
                id TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS product_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                aggregate_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL)"""
            )

    def save(self, delivery: DeliveryRun) -> None:
        with sqlite3.connect(self.path) as connection:
            previous = connection.execute(
                "SELECT snapshot_json FROM deliveries WHERE id=?", (delivery.id,)
            ).fetchone()
            connection.execute(
                """INSERT INTO deliveries(id, snapshot_json) VALUES(?, ?)
                ON CONFLICT(id) DO UPDATE SET snapshot_json=excluded.snapshot_json""",
                (delivery.id, delivery.model_dump_json()),
            )
            if previous is None or previous[0] != delivery.model_dump_json():
                event = _delivery_event(delivery)
                connection.execute(
                    """INSERT INTO product_events(
                    event_id,event_type,aggregate_type,aggregate_id,aggregate_version,
                    payload_json,occurred_at) VALUES(?,?,?,?,?,?,?)""",
                    (
                        event.id,
                        event.event_type,
                        event.aggregate_type,
                        event.aggregate_id,
                        event.aggregate_version,
                        json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
                        event.occurred_at.isoformat(),
                    ),
                )

    def get(self, delivery_id: str) -> DeliveryRun | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM deliveries WHERE id = ?", (delivery_id,)
            ).fetchone()
        return None if row is None else DeliveryRun.model_validate_json(row[0])

    def list(self) -> tuple[DeliveryRun, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT snapshot_json FROM deliveries ORDER BY rowid DESC"
            ).fetchall()
        return tuple(DeliveryRun.model_validate_json(row[0]) for row in rows)

    def list_events(self, delivery_id: str) -> tuple[ProductEvent, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """SELECT event_id,event_type,aggregate_type,aggregate_id,
                aggregate_version,payload_json,occurred_at FROM product_events
                WHERE aggregate_type='delivery' AND aggregate_id=? ORDER BY sequence""",
                (delivery_id,),
            ).fetchall()
        return tuple(
            ProductEvent.model_validate(
                {
                    "id": row[0],
                    "event_type": row[1],
                    "aggregate_type": row[2],
                    "aggregate_id": row[3],
                    "aggregate_version": row[4],
                    "payload": json.loads(row[5]),
                    "occurred_at": row[6],
                }
            )
            for row in rows
        )


class DeliveryCoordinator:
    """Own the product state transition; runtimes only provide bounded capabilities."""

    def __init__(
        self,
        *,
        planning: PlanningService,
        executor: CodeExecutor,
        verifier: CandidateVerifier | None = None,
        applier: CandidateApplier | None = None,
        repository: DeliveryRepository | None = None,
        resolved_journey_sha256: str | None = None,
    ) -> None:
        self._planning = planning
        self._executor = executor
        self._verifier = verifier
        self._applier = applier
        self._repository = repository or InMemoryDeliveryRepository()
        self._resolved_journey_sha256 = (
            None
            if resolved_journey_sha256 is None
            else Sha256.validate(resolved_journey_sha256)
        )
        self._background: dict[str, asyncio.Task[None]] = {}
        self._pipeline_execution: PipelineExecution | None = None

    def configure_pipeline_runtime(
        self,
        catalog: PipelineCatalog,
        runs: PipelineRunLedger,
        agent_runs: AgentRunLedger | None = None,
    ) -> None:
        """Attach the product governance layer to the ACWM GraphRun ledger."""
        from .modules.delivery import PipelineExecutionModule

        self._pipeline_execution = PipelineExecutionModule(
            planning=self._planning,
            executor=self._executor,
            verifier=self._verifier,
            applier=self._applier,
            repository=self._repository,
            catalog=catalog,
            runs=runs,
            agent_runs=agent_runs,
        )

    def enqueue(
        self,
        *,
        workspace_id: str,
        user_request: str,
        journey_revision_id: str | None = None,
        pipeline_revision_id: str | None = None,
        pipeline_run_id: str | None = None,
        journey_binding_snapshot: dict[str, dict[str, object]] | None = None,
        resolved_provider_bindings: dict[str, dict[str, object]] | None = None,
        resolved_journey_sha256: str | None = None,
        resolved_pipeline_sha256: str | None = None,
    ) -> DeliveryRun:
        self._ensure_workspace_available(workspace_id)
        journey_hash = self._require_journey_hash(resolved_journey_sha256)
        binding_snapshot = journey_binding_snapshot or {}
        provider_bindings = resolved_provider_bindings or {}
        resolved_pipeline_run_id = (
            pipeline_run_id
            if pipeline_revision_id is None or pipeline_run_id is not None
            else str(uuid4())
        )
        if journey_revision_id is not None or pipeline_revision_id is not None:
            if provider_bindings:
                self._validate_provider_bindings(provider_bindings)
            else:
                self._validate_runtime_bindings(binding_snapshot)
        delivery = DeliveryRun(
            id=str(uuid4()),
            workspace_id=workspace_id,
            user_request=user_request,
            status="queued",
            version=1,
            evidence_identity=self._planning.evidence_identity,
            planning_identity=self._planning.evidence_identity,
            pipeline_run_id=resolved_pipeline_run_id,
            pipeline_revision_id=pipeline_revision_id,
            resolved_pipeline_sha256=(
                None
                if resolved_pipeline_sha256 is None
                else Sha256.validate(resolved_pipeline_sha256)
            ),
            journey_revision_id=journey_revision_id,
            journey_binding_snapshot=binding_snapshot,
            resolved_provider_bindings=provider_bindings,
            resolved_journey_sha256=journey_hash,
        )
        self._repository.save(delivery)
        if pipeline_revision_id is not None:
            if self._pipeline_execution is None:
                return self.fail_initialization(
                    delivery.id, "PIPELINE_GRAPH_RUNTIME_UNAVAILABLE"
                )
            self._pipeline_execution.start(delivery)
            self._schedule(
                delivery.id, self._pipeline_execution.advance(delivery.id)
            )
        else:
            self._schedule(delivery.id, self._plan_queued(delivery))
        return delivery

    def fail_initialization(self, delivery_id: str, error_code: str) -> DeliveryRun:
        task = self._background.pop(delivery_id, None)
        if task is not None:
            task.cancel()
        delivery = self.get(delivery_id)
        failed = delivery.model_copy(
            update={
                "status": "failed",
                "error_code": error_code,
                "updated_at": datetime.now(UTC),
            }
        )
        self._repository.save(failed)
        return failed

    def _validate_runtime_bindings(
        self, snapshot: dict[str, dict[str, object]]
    ) -> None:
        expected_identities = {
            "hermes-pm": self._planning.evidence_identity,
            "hermes-project-admin": self._planning.evidence_identity,
            "codex-backend": self._executor.evidence_identity,
        }
        if not snapshot:
            raise RuntimeBindingConflictError(
                "pipeline-capabilities", "published-binding-snapshot", None
            )
        for capability_id, binding in snapshot.items():
            expected_identity = expected_identities.get(capability_id)
            actual_identity = binding.get("identity")
            if expected_identity is None:
                raise RuntimeBindingConflictError(
                    capability_id, "registered-capability-adapter", None
                )
            if (
                not isinstance(binding.get("instance_id"), str)
                or not binding["instance_id"]
                or not isinstance(binding.get("instance_version"), int)
                or not isinstance(binding.get("runtime_type"), str)
                or actual_identity != expected_identity
            ):
                raise RuntimeBindingConflictError(
                    capability_id,
                    expected_identity,
                    actual_identity if isinstance(actual_identity, str) else None,
                )

    def _validate_provider_bindings(
        self, snapshot: dict[str, dict[str, object]]
    ) -> None:
        for site, value in snapshot.items():
            binding = value.get("binding")
            deployment = value.get("deployment")
            if not isinstance(binding, dict) or not isinstance(deployment, dict):
                raise RuntimeBindingConflictError(site, "resolved-provider-binding", None)
            try:
                resolved = ResolvedProviderBinding.model_validate(binding)
            except ValueError as error:
                raise RuntimeBindingConflictError(
                    site, "valid-resolved-provider-binding", None
                ) from error
            if not resolved.verify() or not deployment.get("enabled"):
                raise RuntimeBindingConflictError(
                    site, "verified-enabled-provider-binding", None
                )

    async def _plan_queued(self, delivery: DeliveryRun) -> None:
        planning = delivery.model_copy(
            update={"status": "planning", "updated_at": datetime.now(UTC)}
        )
        self._repository.save(planning)
        try:
            requirements = await self._planning.analyze(delivery.user_request)
            task = await self._planning.plan(requirements)
            subject_hash = _sha256({"requirements": requirements, "task": task})
            gate = open_gate(
                GateSnapshot(id="approve-plan", subject_kind="delivery-plan"),
                subject=GateSubject(
                    kind="delivery-plan",
                    artifact_id="delivery-plan",
                    sha256=subject_hash,
                ),
                revision=delivery.version,
            )
            self._repository.save(
                planning.model_copy(
                    update={
                        "status": "awaiting_plan_decision",
                        "requirements": requirements,
                        "task": task,
                        "plan_gate": _gate_record(gate),
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
        except Exception as error:
            self._save_failed(planning, error, "PLANNING_FAILED")

    def _schedule(self, delivery_id: str, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._background[delivery_id] = task
        task.add_done_callback(lambda _task: self._background.pop(delivery_id, None))

    def _save_failed(self, delivery: DeliveryRun, error: Exception, fallback_code: str) -> None:
        self._repository.save(
            delivery.model_copy(
                update={
                    "status": "failed",
                    "error_code": getattr(error, "code", fallback_code),
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    def _ensure_workspace_available(self, workspace_id: str) -> None:
        if workspace_id != "backend-demo":
            raise DeliveryStateConflictError("only backend-demo is supported")
        active = {
            "queued",
            "planning",
            "awaiting_plan_decision",
            "executing",
            "verifying",
            "awaiting_candidate_decision",
            "applying",
        }
        if any(item.workspace_id == workspace_id and item.status in active for item in self.list()):
            raise ActiveDeliveryConflictError(workspace_id)

    def _require_journey_hash(self, override: str | None = None) -> Sha256:
        if override is not None:
            return Sha256.validate(override)
        if self._resolved_journey_sha256 is None:
            raise DeliveryStateConflictError("a published Journey revision is required")
        return self._resolved_journey_sha256

    async def submit(self, *, workspace_id: str, user_request: str) -> DeliveryRun:
        self._ensure_workspace_available(workspace_id)
        journey_hash = self._require_journey_hash()
        requirements = await self._planning.analyze(user_request)
        task = await self._planning.plan(requirements)
        subject_hash = _sha256({"requirements": requirements, "task": task})
        gate = open_gate(
            GateSnapshot(id="approve-plan", subject_kind="delivery-plan"),
            subject=GateSubject(
                kind="delivery-plan",
                artifact_id="delivery-plan",
                sha256=subject_hash,
            ),
            revision=1,
        )
        delivery = DeliveryRun(
            id=str(uuid4()),
            workspace_id=workspace_id,
            user_request=user_request,
            status="awaiting_plan_decision",
            version=1,
            requirements=requirements,
            task=task,
            plan_gate=_gate_record(gate),
            resolved_journey_sha256=journey_hash,
            evidence_identity=self._planning.evidence_identity,
            planning_identity=self._planning.evidence_identity,
        )
        self._repository.save(delivery)
        return delivery

    def get(self, delivery_id: str) -> DeliveryRun:
        delivery = self._repository.get(delivery_id)
        if delivery is None:
            raise DeliveryNotFoundError(delivery_id)
        return delivery

    def list(self) -> tuple[DeliveryRun, ...]:
        return self._repository.list()

    def events(self, delivery_id: str) -> tuple[ProductEvent, ...]:
        if self._repository.get(delivery_id) is None:
            raise DeliveryNotFoundError(delivery_id)
        return self._repository.list_events(delivery_id)

    def start_plan_decision(
        self,
        delivery_id: str,
        *,
        decision: Literal["approve", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun:
        delivery = self.get(delivery_id)
        self._validate_gate_request(
            delivery,
            gate=delivery.plan_gate,
            expected_status="awaiting_plan_decision",
            expected_version=expected_version,
            expected_subject_sha256=expected_subject_sha256,
        )
        self._schedule(
            delivery_id,
            self._run_plan_decision(
                delivery_id,
                decision,
                expected_version,
                expected_subject_sha256,
            ),
        )
        return delivery

    async def _run_plan_decision(
        self,
        delivery_id: str,
        decision: Literal["approve", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> None:
        try:
            await self.decide_plan(
                delivery_id,
                decision=decision,
                expected_version=expected_version,
                expected_subject_sha256=expected_subject_sha256,
            )
        except Exception as error:
            self._save_failed(self.get(delivery_id), error, "EXECUTION_FAILED")

    def start_candidate_decision(
        self,
        delivery_id: str,
        *,
        decision: Literal["accept", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun:
        delivery = self.get(delivery_id)
        self._validate_gate_request(
            delivery,
            gate=delivery.candidate_gate,
            expected_status="awaiting_candidate_decision",
            expected_version=expected_version,
            expected_subject_sha256=expected_subject_sha256,
        )
        self._schedule(
            delivery_id,
            self._run_candidate_decision(
                delivery_id,
                decision,
                expected_version,
                expected_subject_sha256,
            ),
        )
        return delivery

    async def _run_candidate_decision(
        self,
        delivery_id: str,
        decision: Literal["accept", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> None:
        try:
            await self.decide_candidate(
                delivery_id,
                decision=decision,
                expected_version=expected_version,
                expected_subject_sha256=expected_subject_sha256,
            )
        except Exception as error:
            self._save_failed(self.get(delivery_id), error, "APPLY_FAILED")

    @staticmethod
    def _validate_gate_request(
        delivery: DeliveryRun,
        *,
        gate: GateRecord | None,
        expected_status: str,
        expected_version: int,
        expected_subject_sha256: str,
    ) -> None:
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery.id)
        if (
            delivery.status != expected_status
            or gate is None
            or gate.subject_sha256 != expected_subject_sha256
        ):
            raise DeliveryStateConflictError(delivery.id)

    async def decide_plan(
        self,
        delivery_id: str,
        *,
        decision: Literal["approve", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun:
        delivery = self.get(delivery_id)
        if delivery.pipeline_revision_id is not None:
            if self._pipeline_execution is None:
                raise DeliveryStateConflictError("pipeline runtime is unavailable")
            return await self._pipeline_execution.decide_plan(
                delivery,
                decision=decision,
                expected_version=expected_version,
                expected_subject_sha256=expected_subject_sha256,
            )
        if delivery.plan_gate is not None and delivery.plan_gate.decision is not None:
            if delivery.plan_gate.decision == decision:
                return delivery
            raise DeliveryStateConflictError(delivery_id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery_id)
        if (
            delivery.status != "awaiting_plan_decision"
            or delivery.task is None
            or delivery.plan_gate is None
        ):
            raise DeliveryStateConflictError(delivery_id)
        decided_gate = _decide_gate(
            delivery.plan_gate,
            decision=decision,
            expected_version=expected_version,
            expected_subject_sha256=expected_subject_sha256,
        )
        if decision == "reject":
            updated = delivery.model_copy(
                update={
                    "status": "rejected",
                    "version": delivery.version + 1,
                    "plan_gate": decided_gate,
                    "updated_at": datetime.now(UTC),
                }
            )
        else:
            executing = delivery.model_copy(
                update={
                    "status": "executing",
                    "version": delivery.version + 1,
                    "plan_gate": decided_gate,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save(executing)
            candidate = await self._executor.execute(
                delivery.task, delivery.workspace_id, delivery.id
            )
            verifying = executing.model_copy(
                update={"status": "verifying", "updated_at": datetime.now(UTC)}
            )
            self._repository.save(verifying)
            verification = (
                await self._verifier.verify(candidate, delivery.task, delivery.workspace_id)
                if self._verifier is not None
                else None
            )
            candidate_gate = None
            if verification is None or verification.status == "passed":
                candidate_hash = _sha256({"candidate": candidate, "verification": verification})
                opened = open_gate(
                    GateSnapshot(id="approve-candidate", subject_kind="candidate-change"),
                    subject=GateSubject(
                        kind="candidate-change",
                        artifact_id=candidate.candidate_revision,
                        sha256=candidate_hash,
                    ),
                    revision=executing.version,
                )
                candidate_gate = _gate_record(opened)
            updated = executing.model_copy(
                update={
                    "status": (
                        "failed"
                        if verification is not None and verification.status == "failed"
                        else "awaiting_candidate_decision"
                    ),
                    "candidate": candidate,
                    "verification": verification,
                    "candidate_gate": candidate_gate,
                    "evidence_identity": self._executor.evidence_identity,
                    "execution_identity": self._executor.evidence_identity,
                    "updated_at": datetime.now(UTC),
                }
            )
        self._repository.save(updated)
        return updated

    async def decide_candidate(
        self,
        delivery_id: str,
        *,
        decision: Literal["accept", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun:
        delivery = self.get(delivery_id)
        if delivery.pipeline_revision_id is not None:
            if self._pipeline_execution is None:
                raise DeliveryStateConflictError("pipeline runtime is unavailable")
            return await self._pipeline_execution.decide_candidate(
                delivery,
                decision=decision,
                expected_version=expected_version,
                expected_subject_sha256=expected_subject_sha256,
            )
        gate_decision: Literal["approve", "reject"] = (
            "approve" if decision == "accept" else "reject"
        )
        if delivery.candidate_gate is not None and delivery.candidate_gate.decision is not None:
            if delivery.candidate_gate.decision == gate_decision:
                return delivery
            raise DeliveryStateConflictError(delivery_id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery_id)
        if (
            delivery.status != "awaiting_candidate_decision"
            or delivery.candidate is None
            or delivery.candidate_gate is None
        ):
            raise DeliveryStateConflictError(delivery_id)
        decided_gate = _decide_gate(
            delivery.candidate_gate,
            decision=gate_decision,
            expected_version=expected_version,
            expected_subject_sha256=expected_subject_sha256,
        )
        if decision == "reject":
            updated = delivery.model_copy(
                update={
                    "status": "rejected",
                    "version": delivery.version + 1,
                    "candidate_gate": decided_gate,
                    "updated_at": datetime.now(UTC),
                }
            )
        else:
            if delivery.verification is None or delivery.verification.status != "passed":
                raise DeliveryStateConflictError("candidate is not verified")
            if self._applier is None:
                raise DeliveryStateConflictError("candidate applier is not configured")
            applying = delivery.model_copy(
                update={
                    "status": "applying",
                    "version": delivery.version + 1,
                    "candidate_gate": decided_gate,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save(applying)
            receipt = await self._applier.apply(delivery.candidate, delivery.workspace_id)
            if (
                receipt.before_revision != delivery.candidate.base_revision
                or receipt.candidate_revision != delivery.candidate.candidate_revision
                or receipt.after_revision != delivery.candidate.candidate_revision
            ):
                raise DeliveryStateConflictError("apply receipt does not match candidate")
            updated = applying.model_copy(
                update={
                    "status": "completed",
                    "apply_receipt": receipt,
                    "updated_at": datetime.now(UTC),
                }
            )
        self._repository.save(updated)
        return updated

    def cancel(self, delivery_id: str, *, expected_version: int) -> DeliveryRun:
        delivery = self.get(delivery_id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery_id)
        if delivery.status in {"completed", "rejected", "failed", "cancelled"}:
            raise DeliveryStateConflictError(delivery_id)
        task = self._background.get(delivery_id)
        if task is not None:
            task.cancel()
        updated = delivery.model_copy(
            update={
                "status": "cancelled",
                "version": delivery.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if delivery.pipeline_revision_id is not None:
            if self._pipeline_execution is None:
                raise DeliveryStateConflictError("pipeline runtime is unavailable")
            self._pipeline_execution.cancel(delivery)
        self._repository.save(updated)
        return updated

    async def cancel_and_wait(
        self, delivery_id: str, *, expected_version: int
    ) -> DeliveryRun:
        """取消交付并等待其后台任务完全退出。"""
        task = self._background.get(delivery_id)
        self.cancel(delivery_id, expected_version=expected_version)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.get(delivery_id)

    async def recover(self) -> None:
        for delivery in self.list():
            if delivery.status in {"planning", "executing", "verifying"}:
                if delivery.pipeline_revision_id is not None:
                    if self._pipeline_execution is None:
                        raise DeliveryStateConflictError(
                            "pipeline runtime is unavailable"
                        )
                    self._pipeline_execution.fail(
                        delivery.id,
                        ProcessInterruptedError("pipeline node was interrupted"),
                    )
                else:
                    self._repository.save(
                        delivery.model_copy(
                            update={
                                "status": "failed",
                                "version": delivery.version + 1,
                                "error_code": "PROCESS_INTERRUPTED",
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )
            elif delivery.status == "applying" and delivery.candidate is not None:
                if delivery.pipeline_revision_id is not None:
                    if self._pipeline_execution is None:
                        self._save_failed(
                            delivery,
                            DeliveryStateConflictError(
                                "pipeline runtime is unavailable"
                            ),
                            "APPLY_RECOVERY_FAILED",
                        )
                        continue
                    try:
                        await self._pipeline_execution.recover_applying(delivery)
                    except Exception as error:
                        self._save_failed(
                            delivery, error, "APPLY_RECOVERY_FAILED"
                        )
                    continue
                if self._applier is None:
                    self._save_failed(
                        delivery,
                        DeliveryStateConflictError("candidate applier is missing"),
                        "APPLY_RECOVERY_FAILED",
                    )
                    continue
                try:
                    receipt = await self._applier.apply(delivery.candidate, delivery.workspace_id)
                    self._repository.save(
                        delivery.model_copy(
                            update={
                                "status": "completed",
                                "apply_receipt": receipt.model_copy(
                                    update={"recovered": True}
                                ),
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )
                except Exception as error:
                    self._save_failed(delivery, error, "APPLY_RECOVERY_FAILED")


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda item: item.model_dump(mode="json"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _pipeline_node(
    definition: dict[str, object], node_id: str
) -> dict[str, object]:
    nodes = definition.get("nodes") or definition.get("steps") or []
    if not isinstance(nodes, list | tuple):
        raise DeliveryStateConflictError("pipeline node collection is invalid")
    for node in nodes:
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    raise DeliveryStateConflictError(f"pipeline node not found: {node_id}")


def _pipeline_items(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(
        cast(dict[str, object], item) for item in value if isinstance(item, dict)
    )


def _delivery_event(delivery: DeliveryRun) -> ProductEvent:
    return ProductEvent(
        event_type=f"delivery.{delivery.status}",
        aggregate_type="delivery",
        aggregate_id=delivery.id,
        aggregate_version=delivery.version,
        payload={
            "status": delivery.status,
            "title": delivery.task.title if delivery.task else delivery.user_request,
            "acceptance_ids": (
                list(delivery.task.acceptance_ids) if delivery.task else []
            ),
            "error_code": delivery.error_code,
            "planning_identity": delivery.planning_identity,
            "execution_identity": delivery.execution_identity,
        },
        occurred_at=delivery.updated_at,
    )


def _gate_record(
    gate: GateSnapshot, decision: Literal["approve", "reject"] | None = None
) -> GateRecord:
    if gate.subject is None:
        raise DeliveryStateConflictError("gate has no subject")
    return GateRecord(
        gate_id=gate.id,
        subject_kind=gate.subject.kind,
        artifact_id=gate.subject.artifact_id,
        subject_sha256=gate.subject.sha256,
        revision=gate.revision,
        decision=decision,
    )


def _decide_gate(
    record: GateRecord,
    *,
    decision: Literal["approve", "reject"],
    expected_version: int,
    expected_subject_sha256: str,
) -> GateRecord:
    snapshot = GateSnapshot(
        id=record.gate_id,
        subject_kind=record.subject_kind,
        status=GateStatus.OPEN,
        revision=record.revision,
        subject=GateSubject(
            kind=record.subject_kind,
            artifact_id=record.artifact_id,
            sha256=record.subject_sha256,
        ),
        plan_hash=record.subject_sha256,
    )
    try:
        decided = decide_gate(
            snapshot,
            decision=decision,
            expected_revision=expected_version,
            expected_subject_hash=expected_subject_sha256,
        )
    except StaleGateDecision as error:
        raise DeliveryVersionConflictError(record.gate_id) from error
    return _gate_record(decided, decision)
