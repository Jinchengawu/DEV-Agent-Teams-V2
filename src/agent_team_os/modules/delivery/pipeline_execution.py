"""Deep Pipeline execution module backed by the authoritative ACWM GraphRun."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from acwm.domain import GateSnapshot, GateSubject, open_gate

from ...delivery import (
    CandidateApplier,
    CandidateVerifier,
    CodeExecutor,
    DeliveryRepository,
    DeliveryRun,
    DeliveryStateConflictError,
    DeliveryVersionConflictError,
    PlanningService,
    _decide_gate,
    _gate_record,
    _pipeline_items,
    _pipeline_node,
    _sha256,
)
from ..orchestration import PipelineCatalog, PipelineRevision, PipelineRunLedger


class PipelineExecutionModule:
    """Hide graph scheduling, capability dispatch and product side effects.

    The external interface is deliberately small: start, advance, decide, cancel,
    fail and recover. ACWM remains the graph-state authority behind the seam.
    """

    def __init__(
        self,
        *,
        planning: PlanningService,
        executor: CodeExecutor,
        verifier: CandidateVerifier | None,
        applier: CandidateApplier | None,
        repository: DeliveryRepository,
        catalog: PipelineCatalog,
        runs: PipelineRunLedger,
    ) -> None:
        self._planning = planning
        self._executor = executor
        self._verifier = verifier
        self._applier = applier
        self._repository = repository
        self._catalog = catalog
        self._runs = runs

    def start(self, delivery: DeliveryRun) -> None:
        revision = self._revision(delivery)
        self._runs.start(
            delivery_id=delivery.id,
            revision=revision,
            run_id=delivery.pipeline_run_id,
        )

    async def advance(self, delivery_id: str) -> None:
        """Execute only nodes ACWM marks ready."""
        try:
            while True:
                delivery = self._get(delivery_id)
                run = self._runs.get_for_delivery(delivery_id)
                if run.status == "completed":
                    if delivery.apply_receipt is None:
                        raise DeliveryStateConflictError(
                            "pipeline completed without an Apply Receipt"
                        )
                    self._repository.save(
                        delivery.model_copy(
                            update={
                                "status": "completed",
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )
                    return
                if run.status in {"failed", "cancelled", "needs_attention"}:
                    return
                ready = tuple(
                    str(node["node_id"])
                    for node in _pipeline_items(run.snapshot.get("nodes"))
                    if node.get("status") == "ready"
                )
                if not ready:
                    return
                revision = self._revision(delivery)
                nodes = tuple(
                    (node_id, _pipeline_node(revision.definition, node_id))
                    for node_id in ready
                )
                role_stages = tuple(
                    (node_id, node)
                    for node_id, node in nodes
                    if node.get("kind") == "stage"
                    and node.get("workflow_mode") == "agentscope.role-turn"
                )
                if role_stages:
                    async with asyncio.TaskGroup() as tasks:
                        for node_id, node in role_stages:
                            tasks.create_task(
                                self._execute_stage(delivery_id, node_id, node)
                            )
                for node_id, node in nodes:
                    kind = node.get("kind")
                    if (node_id, node) in role_stages:
                        continue
                    if kind == "stage":
                        await self._execute_stage(delivery_id, node_id, node)
                    elif kind == "loop":
                        await self._execute_loop(delivery_id, node_id, node)
                    elif kind != "approval_gate":
                        raise DeliveryStateConflictError(
                            f"unsupported pipeline node kind: {kind}"
                        )
                gates = tuple(
                    (node_id, node)
                    for node_id, node in nodes
                    if node.get("kind") == "approval_gate"
                )
                if gates:
                    node_id, node = gates[0]
                    self._open_gate(delivery_id, node_id, node)
                    return
        except Exception as error:
            self.fail(delivery_id, error)

    async def decide_plan(
        self,
        delivery: DeliveryRun,
        *,
        decision: Literal["approve", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun:
        if delivery.plan_gate is not None and delivery.plan_gate.decision is not None:
            if delivery.plan_gate.decision == decision:
                return delivery
            raise DeliveryStateConflictError(delivery.id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery.id)
        if delivery.status != "awaiting_plan_decision" or delivery.plan_gate is None:
            raise DeliveryStateConflictError(delivery.id)
        decided = _decide_gate(
            delivery.plan_gate,
            decision=decision,
            expected_version=expected_version,
            expected_subject_sha256=expected_subject_sha256,
        )
        run = self._runs.get_for_delivery(delivery.id)
        if decision == "reject":
            self._runs.transition(
                run.id,
                command="cancel",
                node_id=delivery.plan_gate.gate_id,
                expected_version=run.version,
            )
            updated = delivery.model_copy(
                update={
                    "status": "rejected",
                    "version": delivery.version + 1,
                    "plan_gate": decided,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save(updated)
            return updated
        executing = delivery.model_copy(
            update={
                "status": "executing",
                "version": delivery.version + 1,
                "plan_gate": decided,
                "updated_at": datetime.now(UTC),
            }
        )
        self._repository.save(executing)
        self._runs.transition(
            run.id,
            command="succeed",
            node_id=delivery.plan_gate.gate_id,
            expected_version=run.version,
            activated_conditions=("approved", "plan-approved"),
        )
        await self.advance(delivery.id)
        return self._get(delivery.id)

    async def decide_candidate(
        self,
        delivery: DeliveryRun,
        *,
        decision: Literal["accept", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun:
        gate_decision: Literal["approve", "reject"] = (
            "approve" if decision == "accept" else "reject"
        )
        if (
            delivery.candidate_gate is not None
            and delivery.candidate_gate.decision is not None
        ):
            if delivery.candidate_gate.decision == gate_decision:
                return delivery
            raise DeliveryStateConflictError(delivery.id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery.id)
        if (
            delivery.status != "awaiting_candidate_decision"
            or delivery.candidate is None
            or delivery.candidate_gate is None
        ):
            raise DeliveryStateConflictError(delivery.id)
        decided = _decide_gate(
            delivery.candidate_gate,
            decision=gate_decision,
            expected_version=expected_version,
            expected_subject_sha256=expected_subject_sha256,
        )
        run = self._runs.get_for_delivery(delivery.id)
        if decision == "reject":
            self._runs.transition(
                run.id,
                command="cancel",
                node_id=delivery.candidate_gate.gate_id,
                expected_version=run.version,
            )
            updated = delivery.model_copy(
                update={
                    "status": "rejected",
                    "version": delivery.version + 1,
                    "candidate_gate": decided,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save(updated)
            return updated
        if delivery.verification is None or delivery.verification.status != "passed":
            raise DeliveryStateConflictError("candidate is not verified")
        if self._applier is None:
            raise DeliveryStateConflictError("candidate applier is not configured")
        applying = delivery.model_copy(
            update={
                "status": "applying",
                "version": delivery.version + 1,
                "candidate_gate": decided,
                "updated_at": datetime.now(UTC),
            }
        )
        self._repository.save(applying)
        receipt = await self._applier.apply(delivery.candidate, delivery.workspace_id)
        self._validate_receipt(delivery, receipt)
        self._repository.save(
            applying.model_copy(
                update={"apply_receipt": receipt, "updated_at": datetime.now(UTC)}
            )
        )
        self._runs.transition(
            run.id,
            command="succeed",
            node_id=delivery.candidate_gate.gate_id,
            expected_version=run.version,
            activated_conditions=("approved", "accepted", "candidate-accepted"),
        )
        await self.advance(delivery.id)
        return self._get(delivery.id)

    def cancel(self, delivery: DeliveryRun) -> None:
        run = self._runs.get_for_delivery(delivery.id)
        if run.status == "running":
            self._runs.transition(
                run.id,
                command="cancel",
                node_id="",
                expected_version=run.version,
            )

    def fail(self, delivery_id: str, error: Exception) -> None:
        delivery = self._get(delivery_id)
        run = self._runs.get_for_delivery(delivery_id)
        running = next(
            (
                str(node["node_id"])
                for node in _pipeline_items(run.snapshot.get("nodes"))
                if node.get("status") == "running"
            ),
            None,
        )
        if run.status == "running" and running is not None:
            self._runs.transition(
                run.id,
                command="fail",
                node_id=running,
                expected_version=run.version,
            )
        self._repository.save(
            delivery.model_copy(
                update={
                    "status": "failed",
                    "error_code": getattr(
                        error, "code", "PIPELINE_EXECUTION_FAILED"
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def recover_applying(self, delivery: DeliveryRun) -> None:
        if delivery.candidate is None or delivery.candidate_gate is None:
            raise DeliveryStateConflictError("applying Delivery is incomplete")
        if self._applier is None:
            raise DeliveryStateConflictError("candidate applier is missing")
        receipt = await self._applier.apply(delivery.candidate, delivery.workspace_id)
        self._validate_receipt(delivery, receipt)
        recovered = delivery.model_copy(
            update={
                "apply_receipt": receipt.model_copy(update={"recovered": True}),
                "updated_at": datetime.now(UTC),
            }
        )
        self._repository.save(recovered)
        run = self._runs.get_for_delivery(delivery.id)
        gate_node = next(
            (
                node
                for node in _pipeline_items(run.snapshot.get("nodes"))
                if node.get("node_id") == delivery.candidate_gate.gate_id
            ),
            None,
        )
        if run.status == "running" and gate_node is not None:
            if gate_node.get("status") == "running":
                self._runs.transition(
                    run.id,
                    command="succeed",
                    node_id=delivery.candidate_gate.gate_id,
                    expected_version=run.version,
                    activated_conditions=(
                        "approved",
                        "accepted",
                        "candidate-accepted",
                    ),
                )
            await self.advance(delivery.id)

    async def _execute_stage(
        self, delivery_id: str, node_id: str, node: dict[str, object]
    ) -> None:
        run = self._runs.get_for_delivery(delivery_id)
        self._runs.transition(
            run.id, command="start", node_id=node_id, expected_version=run.version
        )
        delivery = self._get(delivery_id)
        capabilities = self._capabilities(node)
        activated: tuple[str, ...]
        if "hermes-pm" in capabilities:
            requirements = await self._planning.analyze(delivery.user_request)
            self._repository.save(
                delivery.model_copy(
                    update={
                        "status": "planning",
                        "requirements": requirements,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
            activated = ("requirements-ready",)
        elif "hermes-project-admin" in capabilities:
            if delivery.requirements is None:
                raise DeliveryStateConflictError(
                    "project admin stage requires RequirementArtifact"
                )
            task = await self._planning.plan(delivery.requirements)
            self._repository.save(
                delivery.model_copy(
                    update={
                        "status": "planning",
                        "task": task,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
            activated = ("task-ready", "planning-complete")
        elif "codex-backend" in capabilities:
            activated = await self._execute_code(delivery)
        else:
            raise DeliveryStateConflictError(
                f"pipeline stage {node_id} has no supported capability binding"
            )
        run = self._runs.get_for_delivery(delivery_id)
        self._runs.transition(
            run.id,
            command="succeed",
            node_id=node_id,
            expected_version=run.version,
            activated_conditions=activated,
        )

    async def _execute_code(self, delivery: DeliveryRun) -> tuple[str, ...]:
        if delivery.task is None:
            raise DeliveryStateConflictError(
                "code delivery stage requires TaskContract"
            )
        executing = delivery.model_copy(
            update={"status": "executing", "updated_at": datetime.now(UTC)}
        )
        self._repository.save(executing)
        candidate = await self._executor.execute(
            delivery.task, delivery.workspace_id, delivery.id
        )
        self._repository.save(
            executing.model_copy(
                update={
                    "status": "verifying",
                    "candidate": candidate,
                    "evidence_identity": self._executor.evidence_identity,
                    "execution_identity": self._executor.evidence_identity,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        verification = (
            await self._verifier.verify(
                candidate, delivery.task, delivery.workspace_id
            )
            if self._verifier is not None
            else None
        )
        if verification is not None and verification.status != "passed":
            raise DeliveryStateConflictError("candidate verification failed")
        self._repository.save(
            self._get(delivery.id).model_copy(
                update={
                    "verification": verification,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        return ("tests-passed", "machine-tests-passed", "candidate-verified")

    def _open_gate(
        self, delivery_id: str, node_id: str, node: dict[str, object]
    ) -> None:
        delivery = self._get(delivery_id)
        run = self._runs.get_for_delivery(delivery_id)
        self._runs.transition(
            run.id, command="start", node_id=node_id, expected_version=run.version
        )
        subject_kind = node.get("subject_kind")
        if subject_kind == "delivery-plan":
            if delivery.requirements is None or delivery.task is None:
                raise DeliveryStateConflictError("plan gate subject is incomplete")
            artifact_id = "delivery-plan"
            subject_hash = _sha256(
                {"requirements": delivery.requirements, "task": delivery.task}
            )
            field = "plan_gate"
            status = "awaiting_plan_decision"
        elif subject_kind == "candidate-change":
            if delivery.candidate is None:
                raise DeliveryStateConflictError("candidate gate subject is incomplete")
            if (
                delivery.verification is not None
                and delivery.verification.status != "passed"
            ):
                raise DeliveryStateConflictError(
                    "candidate gate requires passed verification"
                )
            artifact_id = delivery.candidate.candidate_revision
            subject_hash = _sha256(
                {
                    "candidate": delivery.candidate,
                    "verification": delivery.verification,
                }
            )
            field = "candidate_gate"
            status = "awaiting_candidate_decision"
        else:
            raise DeliveryStateConflictError(
                f"unsupported delivery gate subject: {subject_kind}"
            )
        opened = open_gate(
            GateSnapshot(id=node_id, subject_kind=str(subject_kind)),
            subject=GateSubject(
                kind=str(subject_kind), artifact_id=artifact_id, sha256=subject_hash
            ),
            revision=delivery.version,
        )
        self._repository.save(
            delivery.model_copy(
                update={
                    "status": status,
                    field: _gate_record(opened),
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def _execute_loop(
        self, delivery_id: str, node_id: str, node: dict[str, object]
    ) -> None:
        run = self._runs.get_for_delivery(delivery_id)
        self._runs.transition(
            run.id, command="start", node_id=node_id, expected_version=run.version
        )
        while True:
            run = self._runs.get_for_delivery(delivery_id)
            self._runs.transition(
                run.id,
                command="start-loop-iteration",
                node_id=node_id,
                expected_version=run.version,
            )
            emitted: set[str] = set()
            while True:
                run = self._runs.get_for_delivery(delivery_id)
                loop_state = next(
                    item
                    for item in _pipeline_items(run.snapshot.get("nodes"))
                    if item["node_id"] == node_id
                )
                iterations = _pipeline_items(loop_state.get("iterations"))
                iteration = iterations[-1]
                ready = [
                    str(item["node_id"])
                    for item in _pipeline_items(iteration.get("nodes"))
                    if item["status"] == "ready"
                ]
                if not ready:
                    break
                for body_node_id in ready:
                    body_node = _pipeline_node(
                        {"nodes": node.get("nodes", [])}, body_node_id
                    )
                    if body_node.get("kind") != "stage":
                        raise DeliveryStateConflictError(
                            "nested approval gates require a durable nested Gate record"
                        )
                    run = self._runs.get_for_delivery(delivery_id)
                    self._runs.transition(
                        run.id,
                        command="start-loop-body-node",
                        node_id=node_id,
                        body_node_id=body_node_id,
                        expected_version=run.version,
                    )
                    emitted.update(
                        await self._execute_loop_capability(delivery_id, body_node)
                    )
                    run = self._runs.get_for_delivery(delivery_id)
                    self._runs.transition(
                        run.id,
                        command="succeed-loop-body-node",
                        node_id=node_id,
                        body_node_id=body_node_id,
                        expected_version=run.version,
                        activated_conditions=tuple(sorted(emitted)),
                    )
            policy = node.get("policy")
            exit_condition = (
                str(policy.get("exit_condition"))
                if isinstance(policy, dict)
                else ""
            )
            exit_met = exit_condition in emitted or self._exit_condition_met(
                delivery_id, exit_condition
            )
            run = self._runs.get_for_delivery(delivery_id)
            completed = self._runs.transition(
                run.id,
                command="complete-loop-iteration",
                node_id=node_id,
                expected_version=run.version,
                exit_condition_met=exit_met,
            )
            loop_status = next(
                item
                for item in _pipeline_items(completed.snapshot.get("nodes"))
                if item["node_id"] == node_id
            )["status"]
            if completed.status != "running" or loop_status == "succeeded":
                return

    async def _execute_loop_capability(
        self, delivery_id: str, node: dict[str, object]
    ) -> tuple[str, ...]:
        delivery = self._get(delivery_id)
        capabilities = self._capabilities(node)
        if "hermes-pm" in capabilities:
            requirements = await self._planning.analyze(delivery.user_request)
            self._repository.save(delivery.model_copy(update={"requirements": requirements}))
            return ("requirements-ready",)
        if "hermes-project-admin" in capabilities and delivery.requirements is not None:
            task = await self._planning.plan(delivery.requirements)
            self._repository.save(delivery.model_copy(update={"task": task}))
            return ("task-ready", "planning-complete")
        if "codex-backend" in capabilities:
            return await self._execute_code_loop_attempt(delivery)
        raise DeliveryStateConflictError(
            "LOOP body has no supported capability binding"
        )

    async def _execute_code_loop_attempt(
        self, delivery: DeliveryRun
    ) -> tuple[str, ...]:
        if delivery.task is None:
            raise DeliveryStateConflictError("code repair LOOP requires TaskContract")
        if self._verifier is None:
            raise DeliveryStateConflictError(
                "code repair LOOP requires machine verifier"
            )
        task = delivery.task
        if delivery.verification is not None and delivery.verification.status == "failed":
            task = task.model_copy(
                update={
                    "instructions": (
                        f"{task.instructions}\n\nRepair the previous candidate. "
                        "The fixed machine verification failed with this redacted "
                        f"log:\n{delivery.verification.redacted_log}"
                    )
                }
            )
        self._repository.save(
            delivery.model_copy(
                update={"status": "executing", "updated_at": datetime.now(UTC)}
            )
        )
        candidate = await self._executor.execute(
            task, delivery.workspace_id, delivery.id
        )
        verification = await self._verifier.verify(
            candidate, delivery.task, delivery.workspace_id
        )
        self._repository.save(
            self._get(delivery.id).model_copy(
                update={
                    "status": "verifying",
                    "candidate": candidate,
                    "verification": verification,
                    "evidence_identity": self._executor.evidence_identity,
                    "execution_identity": self._executor.evidence_identity,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        if verification.status == "passed":
            return ("tests-passed", "machine-tests-passed", "candidate-verified")
        return ("candidate-produced", "tests-failed")

    def _revision(self, delivery: DeliveryRun) -> PipelineRevision:
        if delivery.pipeline_revision_id is None:
            raise DeliveryStateConflictError("pipeline revision is unavailable")
        return self._catalog.resolve_revision(delivery.pipeline_revision_id)

    def _get(self, delivery_id: str) -> DeliveryRun:
        delivery = self._repository.get(delivery_id)
        if delivery is None:
            raise DeliveryStateConflictError(f"Delivery not found: {delivery_id}")
        return delivery

    @staticmethod
    def _capabilities(node: dict[str, object]) -> set[object]:
        bindings = node.get("bindings")
        return set(bindings.values()) if isinstance(bindings, dict) else set()

    def _exit_condition_met(self, delivery_id: str, exit_condition: str) -> bool:
        delivery = self._get(delivery_id)
        verified_conditions = {
            "tests-passed",
            "machine-tests-passed",
            "candidate-verified",
        }
        return exit_condition in verified_conditions and (
            delivery.verification is not None
            and delivery.verification.status == "passed"
        )

    @staticmethod
    def _validate_receipt(delivery: DeliveryRun, receipt: object) -> None:
        if delivery.candidate is None:
            raise DeliveryStateConflictError("candidate is missing")
        before = getattr(receipt, "before_revision", None)
        candidate = getattr(receipt, "candidate_revision", None)
        after = getattr(receipt, "after_revision", None)
        if (
            before != delivery.candidate.base_revision
            or candidate != delivery.candidate.candidate_revision
            or after != delivery.candidate.candidate_revision
        ):
            raise DeliveryStateConflictError("apply receipt does not match candidate")
