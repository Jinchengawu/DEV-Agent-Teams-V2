"""Deep Pipeline execution module backed by the authoritative ACWM GraphRun."""

from __future__ import annotations

import asyncio
import sqlite3
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
from ...shared.hashes import sha256_json
from ..agents import AgentRun, AgentRunLedger, ArtifactEnvelope
from ..orchestration import PipelineCatalog, PipelineRevision, PipelineRunLedger
from .publication import (
    PublicationBarrier,
    RoleDocumentPublicationPort,
    RoleDocumentPublicationRequest,
    RoleDocumentPublisher,
)


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
        agent_runs: AgentRunLedger | None = None,
        publications: RoleDocumentPublicationPort | None = None,
        publication_barrier: PublicationBarrier | None = None,
        document_publisher: RoleDocumentPublisher | None = None,
    ) -> None:
        self._planning = planning
        self._executor = executor
        self._verifier = verifier
        self._applier = applier
        self._repository = repository
        self._catalog = catalog
        self._runs = runs
        self._agent_runs = agent_runs
        self._publications = publications
        self._publication_barrier = publication_barrier
        self._document_publisher = document_publisher

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
                    if (
                        self._publication_barrier is not None
                        and not self._publication_barrier.is_satisfied(delivery_id)
                    ):
                        return
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
                    if (
                        self._publication_barrier is not None
                        and not self._publication_barrier.is_satisfied(delivery_id)
                    ):
                        return
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

    async def recover_publications(self, delivery_id: str) -> bool:
        if (
            self._publication_barrier is None
            or not self._publication_barrier.has_publications(delivery_id)
        ):
            return False
        if self._document_publisher is not None:
            try:
                self._document_publisher.publish_required(delivery_id)
            except Exception:
                return True
        if self._publication_barrier.is_satisfied(delivery_id):
            await self.advance(delivery_id)
        return True

    async def _execute_stage(
        self, delivery_id: str, node_id: str, node: dict[str, object]
    ) -> None:
        run = self._runs.get_for_delivery(delivery_id)
        self._runs.transition(
            run.id, command="start", node_id=node_id, expected_version=run.version
        )
        delivery = self._get(delivery_id)
        workflow_mode = str(node.get("workflow_mode", ""))
        binding_site = f"{node_id}.{self._slot(node)}"
        agent_run = self._start_agent_run(delivery, binding_site)
        updated_delivery: DeliveryRun | None = None
        try:
            if workflow_mode == "agentscope.role-turn":
                activated, artifact, updated_delivery = await self._execute_role_projection(
                    delivery, node
                )
            elif workflow_mode == "code-delivery":
                activated = await self._execute_code(delivery)
                current = self._get(delivery.id)
                artifact = current.candidate
            else:
                raise DeliveryStateConflictError(
                    f"pipeline stage {node_id} has unsupported Workflow Mode"
                )
        except Exception:
            self._finish_agent_run(agent_run, "failed")
            raise
        artifacts = self._artifact_envelopes(artifact)
        if not self._commit_role_publication(
            delivery=updated_delivery,
            node_id=node_id,
            binding_site=binding_site,
            agent_run=agent_run,
            artifacts=artifacts,
            activated_conditions=activated,
        ):
            if updated_delivery is not None:
                self._repository.save(updated_delivery)
            self._finish_agent_run(agent_run, "succeeded", artifacts=artifacts)
            run = self._runs.get_for_delivery(delivery_id)
            self._runs.transition(
                run.id,
                command="succeed",
                node_id=node_id,
                expected_version=run.version,
                activated_conditions=activated,
            )
        if self._document_publisher is not None:
            try:
                self._document_publisher.publish_required(delivery_id)
            except Exception:
                # Publication owns its failed state; AgentRun and GraphRun remain succeeded.
                return

    async def _execute_role_projection(
        self, delivery: DeliveryRun, node: dict[str, object]
    ) -> tuple[tuple[str, ...], object, DeliveryRun]:
        projection = node.get("output_validator")
        if projection == "requirement-artifact-v1" or (
            projection is None and delivery.requirements is None
        ):
            requirements = await self._planning.analyze(delivery.user_request)
            updated = delivery.model_copy(
                update={
                    "status": "planning",
                    "requirements": requirements,
                    "updated_at": datetime.now(UTC),
                }
            )
            return ("requirements-ready",), requirements, updated
        if projection == "task-contract-v1" or projection is None:
            if delivery.requirements is None:
                raise DeliveryStateConflictError(
                    "task projection requires RequirementArtifact"
                )
            task = await self._planning.plan(delivery.requirements)
            updated = delivery.model_copy(
                update={
                    "status": "planning",
                    "task": task,
                    "updated_at": datetime.now(UTC),
                }
            )
            return ("task-ready", "planning-complete"), task, updated
        raise DeliveryStateConflictError(f"unsupported Artifact projection: {projection}")

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
                        await self._execute_loop_capability(
                            delivery_id, node_id, body_node_id, body_node
                        )
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
        self,
        delivery_id: str,
        loop_node_id: str,
        body_node_id: str,
        node: dict[str, object],
    ) -> tuple[str, ...]:
        delivery = self._get(delivery_id)
        binding_site = f"{loop_node_id}/{body_node_id}.{self._slot(node)}"
        agent_run = self._start_agent_run(delivery, binding_site)
        try:
            workflow_mode = str(node.get("workflow_mode", ""))
            if workflow_mode == "agentscope.role-turn":
                activated, artifact, updated_delivery = (
                    await self._execute_role_projection(delivery, node)
                )
                self._repository.save(updated_delivery)
            elif workflow_mode == "code-delivery":
                activated = await self._execute_code_loop_attempt(delivery)
                artifact = self._get(delivery.id).candidate
            else:
                raise DeliveryStateConflictError(
                    "LOOP body has unsupported Workflow Mode"
                )
        except Exception:
            self._finish_agent_run(agent_run, "failed")
            raise
        self._finish_agent_run(agent_run, "succeeded", artifact)
        return activated

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
    def _slot(node: dict[str, object]) -> str:
        bindings = node.get("bindings")
        if not isinstance(bindings, dict) or len(bindings) != 1:
            raise DeliveryStateConflictError(
                "executable Stage requires exactly one binding slot"
            )
        return str(next(iter(bindings)))

    def _start_agent_run(
        self, delivery: DeliveryRun, binding_site: str
    ) -> AgentRun | None:
        if self._agent_runs is None:
            return None
        revision = self._revision(delivery)
        snapshot = revision.resolved_provider_bindings.get(binding_site)
        if snapshot is None:
            if revision.binding_model == "legacy-v0":
                return None
            raise DeliveryStateConflictError(
                f"resolved Provider binding missing for {binding_site}"
            )
        binding = snapshot.get("binding")
        deployment = snapshot.get("deployment")
        if not isinstance(binding, dict) or not isinstance(deployment, dict):
            raise DeliveryStateConflictError(
                f"resolved Provider binding is invalid for {binding_site}"
            )
        binding_hash = binding.get("binding_fingerprint")
        if not isinstance(binding_hash, str):
            raise DeliveryStateConflictError(
                f"resolved Provider binding hash is missing for {binding_site}"
            )
        return self._agent_runs.start(
            delivery_id=delivery.id,
            pipeline_revision_id=delivery.pipeline_revision_id or "",
            binding_site=binding_site,
            resolved_binding_hash=binding_hash,
            deployment_snapshot=deployment,
            runtime_identity=(
                str(snapshot["runtime_identity"])
                if snapshot.get("runtime_identity") is not None
                else None
            ),
        )

    @staticmethod
    def _artifact_envelopes(artifact: object | None) -> tuple[ArtifactEnvelope, ...]:
        if artifact is None:
            return ()
        content = (
            artifact.model_dump(mode="json")
            if hasattr(artifact, "model_dump")
            else {"value": str(artifact)}
        )
        contract_id = {
            "RequirementArtifact": "requirement-artifact-v1",
            "TaskContract": "task-contract-v1",
            "CandidateChange": "candidate-change-v1",
        }.get(type(artifact).__name__, "agent-output-v1")
        return (
            ArtifactEnvelope(
                contract_id=contract_id,
                artifact_key="primary",
                content=content,
                sha256=sha256_json(content),
            ),
        )

    def _finish_agent_run(
        self,
        run: AgentRun | None,
        status: str,
        artifact: object | None = None,
        *,
        artifacts: tuple[ArtifactEnvelope, ...] | None = None,
    ) -> None:
        if run is None or self._agent_runs is None:
            return
        resolved_artifacts = (
            self._artifact_envelopes(artifact) if artifacts is None else artifacts
        )
        self._agent_runs.finish(run, status=status, artifacts=resolved_artifacts)

    def _commit_role_publication(
        self,
        *,
        delivery: DeliveryRun | None,
        node_id: str,
        binding_site: str,
        agent_run: AgentRun | None,
        artifacts: tuple[ArtifactEnvelope, ...],
        activated_conditions: tuple[str, ...],
    ) -> bool:
        if (
            delivery is None
            or agent_run is None
            or self._agent_runs is None
            or self._publications is None
            or len(artifacts) != 1
            or artifacts[0].contract_id
            not in {"requirement-artifact-v1", "task-contract-v1"}
        ):
            return False
        delivery_repository = getattr(self._repository, "inner", self._repository)
        save_on = getattr(delivery_repository, "save_on", None)
        run_repository = getattr(self._runs, "repository", None)
        if (
            not callable(save_on)
            or run_repository is None
            or not hasattr(run_repository, "get_on")
            or not hasattr(run_repository, "compare_and_swap_on")
        ):
            return False
        database = self._agent_runs.database
        repository_path = getattr(delivery_repository, "path", database)
        if str(repository_path) != str(database):
            raise DeliveryStateConflictError(
                "role publication participants must share one SQLite database"
            )
        if delivery.pipeline_run_id is None:
            raise DeliveryStateConflictError(
                "role publication requires a persisted pipeline run"
            )
        artifact = artifacts[0]
        connection = sqlite3.connect(database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            save_on(connection, delivery)
            self._agent_runs.finish_on(
                connection,
                agent_run,
                status="succeeded",
                artifacts=artifacts,
            )
            run = run_repository.get_on(connection, delivery.pipeline_run_id)
            self._runs.transition_on(
                connection,
                run.id,
                command="succeed",
                node_id=node_id,
                expected_version=run.version,
                activated_conditions=activated_conditions,
            )
            self._publications.register_on(
                connection,
                RoleDocumentPublicationRequest(
                    project_id=delivery.project_id,
                    delivery_id=delivery.id,
                    node_id=node_id,
                    binding_site=binding_site,
                    agent_run_id=agent_run.id,
                    artifact_id=artifact.id,
                    artifact_key=artifact.artifact_key,
                    contract_id=artifact.contract_id,
                    artifact_sha256=artifact.sha256,
                    runtime_identity=agent_run.runtime_identity,
                ),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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
