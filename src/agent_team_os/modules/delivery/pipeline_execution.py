"""Deep Pipeline execution module backed by the authoritative ACWM GraphRun."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from acwm.domain import GateSnapshot, GateSubject, open_gate

from ...delivery import (
    CandidateApplier,
    CandidateChange,
    CandidateVerifier,
    CodeExecutor,
    DeliveryRepository,
    DeliveryRun,
    DeliveryStateConflictError,
    DeliveryVersionConflictError,
    PlanningService,
    ReleaseApplier,
    RepositoryCandidate,
    RequirementArtifact,
    SystemPolicy,
    TaskContract,
    _decide_gate,
    _gate_record,
    _pipeline_items,
    _pipeline_node,
    _sha256,
)
from ...shared.hashes import Sha256, sha256_json
from ...shared.repositories import RepositoryRole, RepositorySnapshot
from ..agents import (
    AgentRun,
    AgentRunLedger,
    AgentRuntimeDispatcher,
    ArtifactEnvelope,
    RuntimeDispatchRequest,
    RuntimeOutputArtifact,
)
from ..orchestration import PipelineCatalog, PipelineRevision, PipelineRunLedger
from ..releases import FullStackVerificationError, FullStackVerifier
from .runtime_adapters import CodeDeliveryRuntimeAdapter, PlanningRoleTurnRuntimeAdapter


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
        runtime_dispatcher: AgentRuntimeDispatcher | None = None,
        fullstack_verifier: FullStackVerifier | None = None,
        release_applier: ReleaseApplier | None = None,
    ) -> None:
        self._planning = planning
        self._executor = executor
        self._verifier = verifier
        self._applier = applier
        self._repository = repository
        self._catalog = catalog
        self._runs = runs
        self._agent_runs = agent_runs
        self._runtime_dispatcher = runtime_dispatcher or AgentRuntimeDispatcher(
            (
                PlanningRoleTurnRuntimeAdapter(planning),
                CodeDeliveryRuntimeAdapter(executor),
            )
        )
        self._fullstack_verifier = fullstack_verifier or FullStackVerifier()
        self._release_applier = release_applier
        self._projection_locks: dict[str, asyncio.Lock] = {}

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
                    if delivery.apply_receipt is None and delivery.release_manifest is None:
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
                    (node_id, _pipeline_node(revision.definition, node_id)) for node_id in ready
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
                            tasks.create_task(self._execute_stage(delivery_id, node_id, node))
                for node_id, node in nodes:
                    kind = node.get("kind")
                    if (node_id, node) in role_stages:
                        continue
                    if kind == "stage":
                        await self._execute_stage(delivery_id, node_id, node)
                    elif kind == "loop":
                        await self._execute_loop(delivery_id, node_id, node)
                    elif kind != "approval_gate":
                        raise DeliveryStateConflictError(f"unsupported pipeline node kind: {kind}")
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
        if delivery.candidate_gate is not None and delivery.candidate_gate.decision is not None:
            if delivery.candidate_gate.decision == gate_decision:
                return delivery
            raise DeliveryStateConflictError(delivery.id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery.id)
        if (
            delivery.status != "awaiting_candidate_decision"
            or delivery.candidate_gate is None
            or (delivery.candidate is None and delivery.release_bundle is None)
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
        if delivery.release_bundle is None:
            if delivery.verification is None or delivery.verification.status != "passed":
                raise DeliveryStateConflictError("candidate is not verified")
            if self._applier is None or delivery.candidate is None:
                raise DeliveryStateConflictError("candidate applier is not configured")
        elif self._release_applier is None:
            raise DeliveryStateConflictError("release coordinator is not configured")
        applying = delivery.model_copy(
            update={
                "status": "applying",
                "version": delivery.version + 1,
                "candidate_gate": decided,
                "updated_at": datetime.now(UTC),
            }
        )
        self._repository.save(applying)
        if delivery.release_bundle is not None:
            release_applier = self._release_applier
            assert release_applier is not None
            manifest = await release_applier.apply(delivery.release_bundle)
            self._repository.save(
                applying.model_copy(
                    update={"release_manifest": manifest, "updated_at": datetime.now(UTC)}
                )
            )
        else:
            assert self._applier is not None and delivery.candidate is not None
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

    async def decide_design(
        self,
        delivery: DeliveryRun,
        *,
        decision: Literal["approve", "reject"],
        expected_version: int,
        expected_subject_sha256: str,
    ) -> DeliveryRun:
        if delivery.design_gate is not None and delivery.design_gate.decision is not None:
            if delivery.design_gate.decision == decision:
                return delivery
            raise DeliveryStateConflictError(delivery.id)
        if delivery.version != expected_version:
            raise DeliveryVersionConflictError(delivery.id)
        if delivery.status != "awaiting_design_decision" or delivery.design_gate is None:
            raise DeliveryStateConflictError(delivery.id)
        design_candidate = next(
            (item for item in delivery.repository_candidates if item.role == "design"),
            None,
        )
        if design_candidate is None or design_candidate.verification.status != "passed":
            raise DeliveryStateConflictError("design candidate is not verified")
        decided = _decide_gate(
            delivery.design_gate,
            decision=decision,
            expected_version=expected_version,
            expected_subject_sha256=expected_subject_sha256,
        )
        run = self._runs.get_for_delivery(delivery.id)
        if decision == "reject":
            self._runs.transition(
                run.id,
                command="cancel",
                node_id=delivery.design_gate.gate_id,
                expected_version=run.version,
            )
            updated = delivery.model_copy(
                update={
                    "status": "rejected",
                    "version": delivery.version + 1,
                    "design_gate": decided,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save(updated)
            return updated
        executing = delivery.model_copy(
            update={
                "status": "executing",
                "version": delivery.version + 1,
                "design_gate": decided,
                "updated_at": datetime.now(UTC),
            }
        )
        self._repository.save(executing)
        self._runs.transition(
            run.id,
            command="succeed",
            node_id=delivery.design_gate.gate_id,
            expected_version=run.version,
            activated_conditions=("approved", "design-approved"),
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
                    "error_code": getattr(error, "code", "PIPELINE_EXECUTION_FAILED"),
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def recover_applying(self, delivery: DeliveryRun) -> None:
        if delivery.candidate_gate is None:
            raise DeliveryStateConflictError("applying Delivery is incomplete")
        if delivery.release_bundle is not None:
            if self._release_applier is None:
                raise DeliveryStateConflictError("release coordinator is missing")
            manifest = await self._release_applier.apply(delivery.release_bundle)
            recovered = delivery.model_copy(
                update={
                    "release_manifest": manifest,
                    "updated_at": datetime.now(UTC),
                }
            )
        else:
            if delivery.candidate is None or self._applier is None:
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

    async def _execute_stage(self, delivery_id: str, node_id: str, node: dict[str, object]) -> None:
        run = self._runs.get_for_delivery(delivery_id)
        self._runs.transition(
            run.id, command="start", node_id=node_id, expected_version=run.version
        )
        delivery = self._get(delivery_id)
        workflow_mode = str(node.get("workflow_mode", ""))
        binding_site = f"{node_id}.{self._slot(node)}"
        agent_run = self._start_agent_run(delivery, binding_site)
        try:
            if self._revision(delivery).binding_model == "provider-v1":
                activated, artifact = await self._execute_resolved_provider(
                    delivery,
                    node,
                    binding_site,
                    allow_failed_verification=False,
                )
            elif workflow_mode == "agentscope.role-turn":
                activated, artifact = await self._execute_role_projection(delivery, node)
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
        self._finish_agent_run(agent_run, "succeeded", artifact)
        run = self._runs.get_for_delivery(delivery_id)
        self._runs.transition(
            run.id,
            command="succeed",
            node_id=node_id,
            expected_version=run.version,
            activated_conditions=activated,
        )

    async def _execute_role_projection(
        self, delivery: DeliveryRun, node: dict[str, object]
    ) -> tuple[tuple[str, ...], object]:
        projection = node.get("output_validator")
        if projection == "requirement-artifact-v1" or (
            projection is None and delivery.requirements is None
        ):
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
            return ("requirements-ready",), requirements
        if projection == "task-contract-v1" or projection is None:
            if delivery.requirements is None:
                raise DeliveryStateConflictError("task projection requires RequirementArtifact")
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
            return ("task-ready", "planning-complete"), task
        raise DeliveryStateConflictError(f"unsupported Artifact projection: {projection}")

    async def _execute_code(self, delivery: DeliveryRun) -> tuple[str, ...]:
        if delivery.task is None:
            raise DeliveryStateConflictError("code delivery stage requires TaskContract")
        executing = delivery.model_copy(
            update={"status": "executing", "updated_at": datetime.now(UTC)}
        )
        self._repository.save(executing)
        candidate = await self._executor.execute(delivery.task, delivery.workspace_id, delivery.id)
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
            await self._verifier.verify(candidate, delivery.task, delivery.workspace_id)
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

    def _open_gate(self, delivery_id: str, node_id: str, node: dict[str, object]) -> None:
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
            subject_hash = _sha256({"requirements": delivery.requirements, "task": delivery.task})
            field = "plan_gate"
            status = "awaiting_plan_decision"
        elif subject_kind == "candidate-change":
            if delivery.candidate is None:
                raise DeliveryStateConflictError("candidate gate subject is incomplete")
            if delivery.verification is not None and delivery.verification.status != "passed":
                raise DeliveryStateConflictError("candidate gate requires passed verification")
            artifact_id = delivery.candidate.candidate_revision
            subject_hash = _sha256(
                {
                    "candidate": delivery.candidate,
                    "verification": delivery.verification,
                }
            )
            field = "candidate_gate"
            status = "awaiting_candidate_decision"
        elif subject_kind == "design-candidate":
            design_candidate = next(
                (item for item in delivery.repository_candidates if item.role == "design"),
                None,
            )
            if design_candidate is None:
                raise DeliveryStateConflictError("design gate subject is incomplete")
            if design_candidate.verification.status != "passed":
                raise DeliveryStateConflictError("design gate requires passed verification")
            artifact_id = design_candidate.candidate.candidate_revision
            subject_hash = _sha256(design_candidate)
            field = "design_gate"
            status = "awaiting_design_decision"
        elif subject_kind == "release-bundle":
            if delivery.release_bundle is None:
                raise DeliveryStateConflictError("release gate subject is incomplete")
            artifact_id = delivery.release_bundle.bundle_sha256
            subject_hash = _sha256(delivery.release_bundle)
            field = "candidate_gate"
            status = "awaiting_candidate_decision"
        else:
            raise DeliveryStateConflictError(f"unsupported delivery gate subject: {subject_kind}")
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

    async def _execute_loop(self, delivery_id: str, node_id: str, node: dict[str, object]) -> None:
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
                body_nodes: dict[str, dict[str, object]] = {}
                for body_node_id in ready:
                    body_node = _pipeline_node({"nodes": node.get("nodes", [])}, body_node_id)
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
                    body_nodes[body_node_id] = body_node

                results: dict[str, tuple[str, ...]] = {}

                async def execute_body(
                    body_node_id: str,
                    body_node: dict[str, object],
                    target: dict[str, tuple[str, ...]] = results,
                ) -> None:
                    target[body_node_id] = await self._execute_loop_capability(
                        delivery_id, node_id, body_node_id, body_node
                    )

                async with asyncio.TaskGroup() as tasks:
                    for body_node_id, body_node in body_nodes.items():
                        tasks.create_task(execute_body(body_node_id, body_node))

                for body_node_id in ready:
                    activated = results[body_node_id]
                    emitted.update(activated)
                    run = self._runs.get_for_delivery(delivery_id)
                    self._runs.transition(
                        run.id,
                        command="succeed-loop-body-node",
                        node_id=node_id,
                        body_node_id=body_node_id,
                        expected_version=run.version,
                        activated_conditions=activated,
                    )
            policy = node.get("policy")
            exit_condition = str(policy.get("exit_condition")) if isinstance(policy, dict) else ""
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
            if self._revision(delivery).binding_model == "provider-v1":
                activated, artifact = await self._execute_resolved_provider(
                    delivery,
                    node,
                    binding_site,
                    allow_failed_verification=True,
                )
            elif workflow_mode == "agentscope.role-turn":
                activated, artifact = await self._execute_role_projection(delivery, node)
            elif workflow_mode == "code-delivery":
                activated = await self._execute_code_loop_attempt(delivery)
                artifact = self._get(delivery.id).candidate
            else:
                raise DeliveryStateConflictError("LOOP body has unsupported Workflow Mode")
        except Exception:
            self._finish_agent_run(agent_run, "failed")
            raise
        self._finish_agent_run(agent_run, "succeeded", artifact)
        return activated

    async def _execute_resolved_provider(
        self,
        delivery: DeliveryRun,
        node: dict[str, object],
        binding_site: str,
        *,
        allow_failed_verification: bool,
    ) -> tuple[tuple[str, ...], object]:
        revision = self._revision(delivery)
        snapshot = revision.resolved_provider_bindings.get(binding_site)
        if snapshot is None:
            raise DeliveryStateConflictError(
                f"resolved Provider binding missing for {binding_site}"
            )
        binding = snapshot.get("binding")
        if not isinstance(binding, dict) or not isinstance(binding.get("binding_fingerprint"), str):
            raise DeliveryStateConflictError(
                f"resolved Provider binding hash is missing for {binding_site}"
            )
        workflow_mode = str(node.get("workflow_mode", ""))
        validator = str(node.get("output_validator", ""))
        contract_id = _runtime_contract_id(validator)
        repository_role = _repository_role(validator)
        runtime_task = self._runtime_task(delivery, repository_role)
        workspace_id, repository_snapshot = self._repository_context(delivery, repository_role)
        if workflow_mode == "code-delivery":
            self._repository.save(
                delivery.model_copy(update={"status": "executing", "updated_at": datetime.now(UTC)})
            )
        result = await self._runtime_dispatcher.dispatch(
            RuntimeDispatchRequest(
                delivery_id=delivery.id,
                binding_site=binding_site,
                workflow_mode=workflow_mode,
                objective=self._runtime_objective(
                    delivery,
                    contract_id,
                    repository_role=repository_role,
                    task=runtime_task,
                ),
                expected_artifact_contract_id=contract_id,
                workspace_id=workspace_id,
                resolved_binding_hash=Sha256.validate(str(binding["binding_fingerprint"])),
                binding_snapshot=snapshot,
                inputs=self._runtime_inputs(delivery, task=runtime_task),
            )
        )
        if len(result.artifacts) != 1:
            raise DeliveryStateConflictError(
                f"{binding_site} must produce exactly one primary Artifact"
            )
        output = result.artifacts[0]
        if contract_id == "requirement-artifact-v1":
            requirements = RequirementArtifact.model_validate(output.content)
            self._repository.save(
                self._get(delivery.id).model_copy(
                    update={
                        "status": "planning",
                        "requirements": requirements,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
            return ("requirements-ready",), requirements
        if contract_id == "task-contract-v1":
            task = TaskContract.model_validate(output.content)
            self._repository.save(
                self._get(delivery.id).model_copy(
                    update={
                        "status": "planning",
                        "task": task,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
            return ("task-ready", "planning-complete"), task
        if contract_id == "candidate-change-v1":
            if repository_role is None or repository_snapshot is None:
                raise DeliveryStateConflictError(
                    "candidate projection requires a frozen Repository Role"
                )
            candidate = CandidateChange.model_validate(output.content)
            if runtime_task is None:
                raise DeliveryStateConflictError("candidate projection requires TaskContract")
            verification = (
                await self._verifier.verify(candidate, runtime_task, workspace_id)
                if self._verifier is not None
                else None
            )
            if verification is None:
                raise DeliveryStateConflictError("candidate verification is unavailable")
            repository_candidate = RepositoryCandidate(
                role=repository_role,
                workspace_ref=repository_snapshot.workspace_ref,
                repository_ref=repository_snapshot.repository_ref,
                candidate=candidate,
                verification=verification,
                producer_identity=result.runtime_identity,
            )
            lock = self._projection_locks.setdefault(delivery.id, asyncio.Lock())
            async with lock:
                activated = self._record_repository_candidate(
                    delivery.id,
                    repository_candidate,
                    allow_failed_verification=allow_failed_verification,
                )
            # AgentRun records the Artifact emitted by the runtime contract.
            # RepositoryCandidate is a product projection that combines that
            # Artifact with system-owned verification and repository scope.
            return activated, candidate
        raise DeliveryStateConflictError(f"unsupported Runtime Artifact projection: {contract_id}")

    def _record_repository_candidate(
        self,
        delivery_id: str,
        repository_candidate: RepositoryCandidate,
        *,
        allow_failed_verification: bool,
    ) -> tuple[str, ...]:
        current = self._get(delivery_id)
        role = repository_candidate.role
        candidate = repository_candidate.candidate
        verification = repository_candidate.verification
        candidates = {item.role: item for item in current.repository_candidates}
        candidates[role] = repository_candidate
        ordered_candidates = tuple(
            candidates[item_role]
            for item_role in ("backend", "design", "frontend", "qa")
            if item_role in candidates
        )
        update: dict[str, object] = {
            "status": "verifying",
            "repository_candidates": ordered_candidates,
            "evidence_identity": repository_candidate.producer_identity,
            "execution_identity": repository_candidate.producer_identity,
            "updated_at": datetime.now(UTC),
        }
        # Keep the V0.1 single-repository projection readable while new
        # provider-v1 Pipelines move to the repository-scoped ledger.
        if role == "backend":
            update.update({"candidate": candidate, "verification": verification})
        current = current.model_copy(update=update)
        self._repository.save(current)
        if verification.status == "passed":
            activated: tuple[str, ...] = (
                "tests-passed",
                "machine-tests-passed",
                "candidate-verified",
                f"{role}-candidate-verified",
            )
            project_snapshot = current.project_execution_snapshot
            if project_snapshot is not None and len(ordered_candidates) == 4:
                try:
                    release_bundle = self._fullstack_verifier.verify(
                        delivery_id=current.id,
                        snapshot=project_snapshot,
                        candidates=ordered_candidates,
                    )
                except FullStackVerificationError as error:
                    raise DeliveryStateConflictError(str(error)) from error
                self._repository.save(
                    current.model_copy(
                        update={
                            "release_bundle": release_bundle,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
                activated += ("release-bundle-verified",)
            return activated
        if allow_failed_verification:
            return (
                "candidate-produced",
                "tests-failed",
                f"{role}-tests-failed",
            )
        raise DeliveryStateConflictError("candidate verification failed")

    @staticmethod
    def _runtime_inputs(
        delivery: DeliveryRun, *, task: TaskContract | None = None
    ) -> tuple[RuntimeOutputArtifact, ...]:
        inputs: list[RuntimeOutputArtifact] = []
        for contract_id, artifact in (
            ("requirement-artifact-v1", delivery.requirements),
            ("task-contract-v1", task or delivery.task),
        ):
            if artifact is not None:
                inputs.append(
                    RuntimeOutputArtifact(
                        contract_id=contract_id,
                        media_type="application/json",
                        content=artifact.model_dump(mode="json"),
                    )
                )
        return tuple(inputs)

    @staticmethod
    def _runtime_objective(
        delivery: DeliveryRun,
        contract_id: str,
        *,
        repository_role: RepositoryRole | None = None,
        task: TaskContract | None = None,
    ) -> str:
        if contract_id == "requirement-artifact-v1":
            return delivery.user_request
        if contract_id == "task-contract-v1":
            return "把已批准的需求整理为一个可机器验收的单一任务。"
        if contract_id == "candidate-change-v1":
            if task is None:
                raise DeliveryStateConflictError("code delivery stage requires TaskContract")
            objective = task.instructions
            objective += _upstream_candidate_context(delivery, repository_role)
            previous = next(
                (
                    item.verification
                    for item in delivery.repository_candidates
                    if item.role == repository_role
                ),
                delivery.verification if repository_role == "backend" else None,
            )
            if previous is not None and previous.status == "failed":
                objective += (
                    f"\n\n修复上一候选版本。固定机器验证失败的脱敏日志：\n{previous.redacted_log}"
                )
            return objective
        raise DeliveryStateConflictError(f"unsupported Runtime Artifact contract: {contract_id}")

    @staticmethod
    def _runtime_task(
        delivery: DeliveryRun, repository_role: RepositoryRole | None
    ) -> TaskContract | None:
        if repository_role is None:
            return delivery.task
        if delivery.task is None:
            raise DeliveryStateConflictError("repository Stage requires TaskContract")
        policy_by_role: dict[RepositoryRole, SystemPolicy] = {
            "backend": delivery.task.system_policy,
            "frontend": SystemPolicy(allowed_paths=("src/**", "tests/**")),
            "design": SystemPolicy(allowed_paths=("design/**", "tests/**")),
            "qa": SystemPolicy(allowed_paths=("tests/**", "reports/**")),
        }
        role_instruction = {
            "backend": "实现并验证后端交付部分。",
            "frontend": "基于已批准需求和设计约束实现并验证前端交付部分。",
            "design": "产出可追溯的 UI 设计规范，并用机器测试验证规范文件。",
            "qa": "补充跨模块验收测试与测试报告，不修改产品实现。",
        }[repository_role]
        return delivery.task.model_copy(
            update={
                "title": f"[{repository_role}] {delivery.task.title}",
                "instructions": f"{role_instruction}\n\n{delivery.task.instructions}",
                "system_policy": policy_by_role[repository_role],
            }
        )

    @staticmethod
    def _repository_context(
        delivery: DeliveryRun, repository_role: RepositoryRole | None
    ) -> tuple[str, RepositorySnapshot | None]:
        if repository_role is None:
            return delivery.workspace_id, None
        snapshot = delivery.project_execution_snapshot
        if snapshot is not None:
            repository = next(
                (item for item in snapshot.repositories if item.role == repository_role),
                None,
            )
            if repository is not None:
                return repository.workspace_ref, repository
        if repository_role == "backend":
            candidate = delivery.candidate
            base_revision = candidate.base_revision if candidate is not None else "legacy"
            return delivery.workspace_id, RepositorySnapshot(
                role="backend",
                workspace_ref=delivery.workspace_id,
                repository_ref=(
                    snapshot.repository_ref if snapshot is not None else delivery.workspace_id
                ),
                seed_revision=base_revision,
            )
        raise DeliveryStateConflictError(
            f"project execution snapshot has no {repository_role} repository"
        )

    async def _execute_code_loop_attempt(self, delivery: DeliveryRun) -> tuple[str, ...]:
        if delivery.task is None:
            raise DeliveryStateConflictError("code repair LOOP requires TaskContract")
        if self._verifier is None:
            raise DeliveryStateConflictError("code repair LOOP requires machine verifier")
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
            delivery.model_copy(update={"status": "executing", "updated_at": datetime.now(UTC)})
        )
        candidate = await self._executor.execute(task, delivery.workspace_id, delivery.id)
        verification = await self._verifier.verify(candidate, delivery.task, delivery.workspace_id)
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
            raise DeliveryStateConflictError("executable Stage requires exactly one binding slot")
        return str(next(iter(bindings)))

    def _start_agent_run(self, delivery: DeliveryRun, binding_site: str) -> AgentRun | None:
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

    def _finish_agent_run(
        self, run: AgentRun | None, status: str, artifact: object | None = None
    ) -> None:
        if run is None or self._agent_runs is None:
            return
        artifacts: tuple[ArtifactEnvelope, ...] = ()
        if artifact is not None:
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
            artifacts = (
                ArtifactEnvelope(
                    contract_id=contract_id,
                    content=content,
                    sha256=sha256_json(content),
                ),
            )
        self._agent_runs.finish(run, status=status, artifacts=artifacts)

    def _exit_condition_met(self, delivery_id: str, exit_condition: str) -> bool:
        delivery = self._get(delivery_id)
        verified_conditions = {
            "tests-passed",
            "machine-tests-passed",
            "candidate-verified",
        }
        if exit_condition == "release-bundle-verified":
            return delivery.release_bundle is not None
        if exit_condition.endswith("-candidate-verified"):
            role = exit_condition.removesuffix("-candidate-verified")
            return any(
                item.role == role and item.verification.status == "passed"
                for item in delivery.repository_candidates
            )
        return exit_condition in verified_conditions and (
            delivery.verification is not None and delivery.verification.status == "passed"
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


def _runtime_contract_id(output_validator: str) -> str:
    contracts = {
        "requirement-artifact-v1": "requirement-artifact-v1",
        "task-contract-v1": "task-contract-v1",
        "backend-candidate-v1": "candidate-change-v1",
        "design-candidate-v1": "candidate-change-v1",
        "frontend-candidate-v1": "candidate-change-v1",
        "qa-candidate-v1": "candidate-change-v1",
    }
    try:
        return contracts[output_validator]
    except KeyError as error:
        raise DeliveryStateConflictError(
            f"output validator has no Runtime Artifact contract: {output_validator}"
        ) from error


def _repository_role(output_validator: str) -> RepositoryRole | None:
    roles: dict[str, RepositoryRole] = {
        "backend-candidate-v1": "backend",
        "design-candidate-v1": "design",
        "frontend-candidate-v1": "frontend",
        "qa-candidate-v1": "qa",
    }
    return roles.get(output_validator)


def _upstream_candidate_context(
    delivery: DeliveryRun, repository_role: RepositoryRole | None
) -> str:
    """Expose reviewed predecessor facts without granting cross-repository writes."""
    required_roles: tuple[RepositoryRole, ...]
    if repository_role in {"backend", "frontend"}:
        required_roles = ("design",)
    elif repository_role == "qa":
        required_roles = ("design", "backend", "frontend")
    else:
        return ""
    by_role = {item.role: item for item in delivery.repository_candidates}
    available = tuple(by_role[role] for role in required_roles if role in by_role)
    if not available:
        return ""
    sections = ["已验证的上游候选证据（只能作为输入，不得改写其仓库）："]
    for item in available:
        candidate = item.candidate
        sections.append(
            "\n".join(
                (
                    f"[{item.role}] Candidate {candidate.candidate_revision}",
                    f"Diff SHA-256: {candidate.diff_sha256}",
                    f"变更文件: {', '.join(candidate.changed_files)}",
                    f"机器验证: {item.verification.status} / 日志 {item.verification.log_sha256}",
                    "已审查 Diff（截断至 8000 字符）：",
                    candidate.unified_diff[:8_000],
                )
            )
        )
    return "\n\n" + "\n\n".join(sections)
