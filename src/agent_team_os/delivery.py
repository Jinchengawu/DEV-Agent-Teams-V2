"""Delivery aggregate and its narrow application service."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


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
    verification_commands: tuple[str, ...] = ("python -m pytest",)


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


class VerificationRun(ImmutableModel):
    status: Literal["passed", "failed"]
    commands: tuple[str, ...]
    exit_code: int
    log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApplyReceipt(ImmutableModel):
    before_revision: str
    candidate_revision: str
    after_revision: str
    result: Literal["applied"]


class DeliveryRun(ImmutableModel):
    id: str
    workspace_id: str
    user_request: str
    status: Literal[
        "planning",
        "awaiting_plan_decision",
        "executing",
        "awaiting_candidate_decision",
        "completed",
        "rejected",
        "failed",
    ]
    version: int
    requirements: RequirementArtifact | None = None
    task: TaskContract | None = None
    candidate: CandidateChange | None = None
    verification: VerificationRun | None = None
    apply_receipt: ApplyReceipt | None = None
    evidence_identity: str
    planning_identity: str
    execution_identity: str | None = None


class PlanningService(Protocol):
    evidence_identity: str

    async def analyze(self, user_request: str) -> RequirementArtifact: ...

    async def plan(self, requirements: RequirementArtifact) -> TaskContract: ...


class CodeExecutor(Protocol):
    evidence_identity: str

    async def execute(self, task: TaskContract, workspace_id: str) -> CandidateChange: ...


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


class DeliveryRepository(Protocol):
    def save(self, delivery: DeliveryRun) -> None: ...

    def get(self, delivery_id: str) -> DeliveryRun | None: ...


class InMemoryDeliveryRepository:
    def __init__(self) -> None:
        self._deliveries: dict[str, DeliveryRun] = {}

    def save(self, delivery: DeliveryRun) -> None:
        self._deliveries[delivery.id] = delivery

    def get(self, delivery_id: str) -> DeliveryRun | None:
        return self._deliveries.get(delivery_id)


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

    def save(self, delivery: DeliveryRun) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """INSERT INTO deliveries(id, snapshot_json) VALUES(?, ?)
                ON CONFLICT(id) DO UPDATE SET snapshot_json=excluded.snapshot_json""",
                (delivery.id, delivery.model_dump_json()),
            )

    def get(self, delivery_id: str) -> DeliveryRun | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM deliveries WHERE id = ?", (delivery_id,)
            ).fetchone()
        return None if row is None else DeliveryRun.model_validate_json(row[0])


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
    ) -> None:
        self._planning = planning
        self._executor = executor
        self._verifier = verifier
        self._applier = applier
        self._repository = repository or InMemoryDeliveryRepository()

    async def submit(self, *, workspace_id: str, user_request: str) -> DeliveryRun:
        requirements = await self._planning.analyze(user_request)
        task = await self._planning.plan(requirements)
        delivery = DeliveryRun(
            id=str(uuid4()),
            workspace_id=workspace_id,
            user_request=user_request,
            status="awaiting_plan_decision",
            version=1,
            requirements=requirements,
            task=task,
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

    async def decide_plan(
        self,
        delivery_id: str,
        *,
        decision: Literal["approve", "reject"],
        expected_version: int,
    ) -> DeliveryRun:
        delivery = self.get(delivery_id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery_id)
        if delivery.status != "awaiting_plan_decision" or delivery.task is None:
            raise DeliveryStateConflictError(delivery_id)
        if decision == "reject":
            updated = delivery.model_copy(
                update={"status": "rejected", "version": delivery.version + 1}
            )
        else:
            candidate = await self._executor.execute(delivery.task, delivery.workspace_id)
            verification = (
                await self._verifier.verify(candidate, delivery.task, delivery.workspace_id)
                if self._verifier is not None
                else None
            )
            updated = delivery.model_copy(
                update={
                    "status": (
                        "failed"
                        if verification is not None and verification.status == "failed"
                        else "awaiting_candidate_decision"
                    ),
                    "version": delivery.version + 1,
                    "candidate": candidate,
                    "verification": verification,
                    "evidence_identity": self._executor.evidence_identity,
                    "execution_identity": self._executor.evidence_identity,
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
    ) -> DeliveryRun:
        delivery = self.get(delivery_id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery_id)
        if delivery.status != "awaiting_candidate_decision" or delivery.candidate is None:
            raise DeliveryStateConflictError(delivery_id)
        if decision == "reject":
            updated = delivery.model_copy(
                update={"status": "rejected", "version": delivery.version + 1}
            )
        else:
            if delivery.verification is None or delivery.verification.status != "passed":
                raise DeliveryStateConflictError("candidate is not verified")
            if self._applier is None:
                raise DeliveryStateConflictError("candidate applier is not configured")
            receipt = await self._applier.apply(delivery.candidate, delivery.workspace_id)
            if (
                receipt.before_revision != delivery.candidate.base_revision
                or receipt.candidate_revision != delivery.candidate.candidate_revision
                or receipt.after_revision != delivery.candidate.candidate_revision
            ):
                raise DeliveryStateConflictError("apply receipt does not match candidate")
            updated = delivery.model_copy(
                update={
                    "status": "completed",
                    "version": delivery.version + 1,
                    "apply_receipt": receipt,
                }
            )
        self._repository.save(updated)
        return updated
