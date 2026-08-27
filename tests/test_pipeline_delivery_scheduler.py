import asyncio
from pathlib import Path

from agent_team_os.delivery import (
    AcceptanceCriterion,
    ApplyReceipt,
    CandidateChange,
    DeliveryCoordinator,
    DeliveryRun,
    InMemoryDeliveryRepository,
    RequirementArtifact,
    VerificationRun,
)
from agent_team_os.infrastructure.acwm import ACWMGraphCompiler, ACWMPipelineGraphRuntime
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.orchestration import (
    PipelineRevision,
    PipelineRunLedger,
    SQLitePipelineRunRepository,
)
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class RevisionCatalog:
    def __init__(self, revision: PipelineRevision) -> None:
        self.revision = revision

    def resolve_revision(self, reference: str) -> PipelineRevision:
        assert reference == "backend-delivery:1"
        return self.revision


class PassedVerifier:
    async def verify(self, candidate, task, workspace_id):  # type: ignore[no-untyped-def]
        return VerificationRun(
            status="passed",
            commands=("python -m unittest discover -s tests -v",),
            exit_code=0,
            log_sha256="a" * 64,
            acceptance_ids=task.acceptance_ids,
        )


class ExactApplier:
    async def apply(self, candidate: CandidateChange, workspace_id: str) -> ApplyReceipt:
        return ApplyReceipt(
            before_revision=candidate.base_revision,
            candidate_revision=candidate.candidate_revision,
            after_revision=candidate.candidate_revision,
            result="applied",
        )


class RepairingExecutor:
    evidence_identity = "deterministic-test"

    def __init__(self) -> None:
        self.attempts = 0

    async def execute(self, task, workspace_id, delivery_id):  # type: ignore[no-untyped-def]
        self.attempts += 1
        marker = str(self.attempts) * 40
        return CandidateChange(
            base_revision="b" * 40,
            candidate_revision=marker,
            diff_sha256=str(self.attempts) * 64,
            changed_files=("src/service.py", "tests/test_service.py"),
        )


class RepairVerifier:
    async def verify(self, candidate, task, workspace_id):  # type: ignore[no-untyped-def]
        passed = candidate.candidate_revision == "2" * 40
        return VerificationRun(
            status="passed" if passed else "failed",
            commands=("python -m unittest discover -s tests -v",),
            exit_code=0 if passed else 1,
            log_sha256=("2" if passed else "1") * 64,
            redacted_log="tests passed" if passed else "one test failed",
            acceptance_ids=task.acceptance_ids,
        )


class ConcurrentPlanningService(DeterministicPlanningService):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def analyze(self, user_request: str) -> RequirementArtifact:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return RequirementArtifact(
            summary=user_request,
            acceptance_criteria=(
                AcceptanceCriterion(id="AC-1", statement="并行规划完成"),
            ),
        )


class BlockingPlanningService(DeterministicPlanningService):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.released = asyncio.Event()
        self.quiesced = asyncio.Event()

    async def analyze(self, user_request: str) -> RequirementArtifact:
        self.started.set()
        try:
            await self.released.wait()
        finally:
            self.quiesced.set()
        return await super().analyze(user_request)


def test_cancel_and_wait_quiesces_the_background_delivery() -> None:
    async def scenario() -> None:
        planning = BlockingPlanningService()
        coordinator = DeliveryCoordinator(
            planning=planning,
            executor=DeterministicCodeExecutor(),
            resolved_journey_sha256="a" * 64,
        )
        created = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="Block until the delivery is cancelled.",
        )
        await asyncio.wait_for(planning.started.wait(), timeout=1)

        cancelled = await coordinator.cancel_and_wait(
            created.id,
            expected_version=coordinator.get(created.id).version,
        )

        assert cancelled.status == "cancelled"
        assert planning.quiesced.is_set()

    asyncio.run(scenario())


def _revision() -> PipelineRevision:
    definition: dict[str, object] = {
        "id": "backend-delivery",
        "version": "4.0.0",
        "nodes": [
            {
                "id": "requirements",
                "kind": "stage",
                "workflow_mode": "agentscope.role-turn",
                "bindings": {"actor": "hermes-pm"},
            },
            {
                "id": "tasking",
                "kind": "stage",
                "workflow_mode": "agentscope.role-turn",
                "bindings": {"actor": "hermes-project-admin"},
            },
            {
                "id": "approve-plan",
                "kind": "approval_gate",
                "subject_kind": "delivery-plan",
            },
            {
                "id": "delivery",
                "kind": "stage",
                "workflow_mode": "code-delivery",
                "bindings": {"developer": "codex-backend"},
            },
            {
                "id": "approve-candidate",
                "kind": "approval_gate",
                "subject_kind": "candidate-change",
            },
        ],
        "edges": [
            {"source": "requirements", "target": "tasking"},
            {"source": "tasking", "target": "approve-plan"},
            {"source": "approve-plan", "target": "delivery"},
            {"source": "delivery", "target": "approve-candidate"},
        ],
    }
    compilation = ACWMGraphCompiler().compile(definition)
    return PipelineRevision(
        pipeline_id="backend-delivery",
        revision=1,
        definition=definition,
        compiled_graph=compilation.graph,
        binding_snapshot={
            "hermes-pm": {
                "instance_id": "planning",
                "instance_version": 1,
                "runtime_type": "codex-cli",
                "identity": "deterministic-test",
            },
            "hermes-project-admin": {
                "instance_id": "planning",
                "instance_version": 1,
                "runtime_type": "codex-cli",
                "identity": "deterministic-test",
            },
            "codex-backend": {
                "instance_id": "execution",
                "instance_version": 1,
                "runtime_type": "codex-cli",
                "identity": "deterministic-test",
            },
        },
        fingerprint=compilation.fingerprint,
        published_by="admin",
    )


def _revision_for(definition: dict[str, object]) -> PipelineRevision:
    original = _revision()
    compilation = ACWMGraphCompiler().compile(definition)
    return original.model_copy(
        update={
            "definition": definition,
            "compiled_graph": compilation.graph,
            "fingerprint": compilation.fingerprint,
        }
    )


async def _wait_for(coordinator: DeliveryCoordinator, delivery_id: str, status: str):
    for _ in range(100):
        delivery = coordinator.get(delivery_id)
        if delivery.status == status:
            return delivery
        await asyncio.sleep(0.01)
    raise AssertionError(f"Delivery did not reach {status}: {delivery.status}")


def test_pipeline_graph_drives_real_delivery_gates_and_apply(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "agent-team-os.sqlite"
        MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
        revision = _revision()
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            verifier=PassedVerifier(),
            applier=ExactApplier(),
            repository=InMemoryDeliveryRepository(),
            resolved_journey_sha256="f" * 64,
        )
        coordinator.configure_pipeline_runtime(RevisionCatalog(revision), runs)

        created = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="增加健康检查",
            pipeline_revision_id="backend-delivery:1",
            journey_binding_snapshot=revision.binding_snapshot,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )
        plan = await _wait_for(coordinator, created.id, "awaiting_plan_decision")
        graph = runs.get_for_delivery(created.id)
        assert {node["node_id"]: node["status"] for node in graph.snapshot["nodes"]} == {
            "requirements": "succeeded",
            "tasking": "succeeded",
            "approve-plan": "running",
            "delivery": "blocked",
            "approve-candidate": "blocked",
        }

        await coordinator.decide_plan(
            created.id,
            decision="approve",
            expected_version=plan.version,
            expected_subject_sha256=plan.plan_gate.subject_sha256,  # type: ignore[union-attr]
        )
        candidate = await _wait_for(
            coordinator, created.id, "awaiting_candidate_decision"
        )
        graph = runs.get_for_delivery(created.id)
        assert next(
            node for node in graph.snapshot["nodes"] if node["node_id"] == "delivery"
        )["status"] == "succeeded"

        await coordinator.decide_candidate(
            created.id,
            decision="accept",
            expected_version=candidate.version,
            expected_subject_sha256=candidate.candidate_gate.subject_sha256,  # type: ignore[union-attr]
        )
        completed = await _wait_for(coordinator, created.id, "completed")
        graph = runs.get_for_delivery(created.id)
        assert graph.status == "completed"
        assert completed.apply_receipt is not None

    asyncio.run(scenario())


def test_pipeline_scheduler_obeys_conditional_branch_projection(tmp_path: Path) -> None:
    async def scenario() -> None:
        definition: dict[str, object] = {
            "id": "backend-delivery",
            "version": "4.0.0",
            "nodes": [
                {
                    "id": "requirements",
                    "kind": "stage",
                    "workflow_mode": "agentscope.role-turn",
                    "bindings": {"actor": "hermes-pm"},
                },
                {
                    "id": "tasking",
                    "kind": "stage",
                    "workflow_mode": "agentscope.role-turn",
                    "bindings": {"actor": "hermes-project-admin"},
                },
                {
                    "id": "unused-review",
                    "kind": "stage",
                    "workflow_mode": "agentscope.role-turn",
                    "bindings": {"actor": "hermes-pm"},
                },
                {
                    "id": "approve-plan",
                    "kind": "approval_gate",
                    "subject_kind": "delivery-plan",
                },
            ],
            "edges": [
                {
                    "source": "requirements",
                    "target": "tasking",
                    "condition": "requirements-ready",
                },
                {
                    "source": "requirements",
                    "target": "unused-review",
                    "condition": "rejected",
                },
                {"source": "tasking", "target": "approve-plan"},
            ],
        }
        revision = _revision_for(definition)
        database = tmp_path / "agent-team-os.sqlite"
        MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=InMemoryDeliveryRepository(),
            resolved_journey_sha256="f" * 64,
        )
        coordinator.configure_pipeline_runtime(RevisionCatalog(revision), runs)
        created = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="条件分支",
            pipeline_revision_id="backend-delivery:1",
            journey_binding_snapshot=revision.binding_snapshot,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )

        await _wait_for(coordinator, created.id, "awaiting_plan_decision")
        graph = runs.get_for_delivery(created.id)
        states = {node["node_id"]: node["status"] for node in graph.snapshot["nodes"]}
        assert states["tasking"] == "succeeded"
        assert states["unused-review"] == "skipped"

    asyncio.run(scenario())


def test_pipeline_scheduler_executes_bounded_loop_body_dag(tmp_path: Path) -> None:
    async def scenario() -> None:
        definition: dict[str, object] = {
            "id": "backend-delivery",
            "version": "4.0.0",
            "nodes": [
                {
                    "id": "planning-loop",
                    "kind": "loop",
                    "policy": {
                        "exit_condition": "planning-complete",
                        "max_iterations": 3,
                        "timeout_seconds": 60,
                        "on_exhausted": "fail",
                    },
                    "nodes": [
                        {
                            "id": "requirements",
                            "kind": "stage",
                            "workflow_mode": "agentscope.role-turn",
                            "bindings": {"actor": "hermes-pm"},
                        },
                        {
                            "id": "tasking",
                            "kind": "stage",
                            "workflow_mode": "agentscope.role-turn",
                            "bindings": {"actor": "hermes-project-admin"},
                        },
                    ],
                    "edges": [{"source": "requirements", "target": "tasking"}],
                },
                {
                    "id": "approve-plan",
                    "kind": "approval_gate",
                    "subject_kind": "delivery-plan",
                },
            ],
            "edges": [{"source": "planning-loop", "target": "approve-plan"}],
        }
        revision = _revision_for(definition)
        database = tmp_path / "agent-team-os.sqlite"
        MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=InMemoryDeliveryRepository(),
            resolved_journey_sha256="f" * 64,
        )
        coordinator.configure_pipeline_runtime(RevisionCatalog(revision), runs)
        created = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="LOOP 规划",
            pipeline_revision_id="backend-delivery:1",
            journey_binding_snapshot=revision.binding_snapshot,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )

        plan = await _wait_for(coordinator, created.id, "awaiting_plan_decision")
        graph = runs.get_for_delivery(created.id)
        loop = next(
            node
            for node in graph.snapshot["nodes"]
            if node["node_id"] == "planning-loop"
        )
        assert loop["status"] == "succeeded"
        assert len(loop["iterations"]) == 1
        assert {node["status"] for node in loop["iterations"][0]["nodes"]} == {
            "succeeded"
        }
        await coordinator.decide_plan(
            created.id,
            decision="reject",
            expected_version=plan.version,
            expected_subject_sha256=plan.plan_gate.subject_sha256,  # type: ignore[union-attr]
        )
        assert runs.get_for_delivery(created.id).status == "cancelled"

    asyncio.run(scenario())


def test_pipeline_recovery_fails_interrupted_node_in_graph_ledger(tmp_path: Path) -> None:
    async def scenario() -> None:
        revision = _revision()
        database = tmp_path / "agent-team-os.sqlite"
        MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        repository = InMemoryDeliveryRepository()
        delivery = DeliveryRun(
            id="delivery-interrupted",
            workspace_id="backend-demo",
            user_request="中断恢复",
            status="planning",
            version=1,
            pipeline_run_id="run-interrupted",
            pipeline_revision_id="backend-delivery:1",
            resolved_pipeline_sha256=revision.fingerprint,
            journey_binding_snapshot=revision.binding_snapshot,
            resolved_journey_sha256=revision.fingerprint,
            evidence_identity="deterministic-test",
            planning_identity="deterministic-test",
        )
        repository.save(delivery)
        started = runs.start(
            delivery_id=delivery.id,
            revision=revision,
            run_id=delivery.pipeline_run_id,
        )
        runs.transition(
            started.id,
            command="start",
            node_id="requirements",
            expected_version=started.version,
        )
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=repository,
            resolved_journey_sha256="f" * 64,
        )
        coordinator.configure_pipeline_runtime(RevisionCatalog(revision), runs)

        await coordinator.recover()

        assert coordinator.get(delivery.id).error_code == "PROCESS_INTERRUPTED"
        assert runs.get_for_delivery(delivery.id).status == "failed"

    asyncio.run(scenario())


def test_code_repair_loop_retries_until_machine_tests_pass(tmp_path: Path) -> None:
    async def scenario() -> None:
        definition = _revision().definition.copy()
        nodes = list(definition["nodes"])  # type: ignore[arg-type]
        code = next(node for node in nodes if node["id"] == "delivery")
        loop = {
            "id": "repair-loop",
            "kind": "loop",
            "policy": {
                "exit_condition": "machine-tests-passed",
                "max_iterations": 3,
                "timeout_seconds": 60,
                "on_exhausted": "fail",
            },
            "nodes": [code],
            "edges": [],
        }
        definition["nodes"] = [
            loop if node["id"] == "delivery" else node for node in nodes
        ]
        definition["edges"] = [
            {
                "source": "approve-plan" if edge["source"] == "approve-plan" else edge["source"],
                "target": "repair-loop" if edge["target"] == "delivery" else edge["target"],
            }
            for edge in definition["edges"]  # type: ignore[union-attr]
            if edge["source"] != "delivery"
        ] + [{"source": "repair-loop", "target": "approve-candidate"}]
        revision = _revision_for(definition)
        database = tmp_path / "agent-team-os.sqlite"
        MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        executor = RepairingExecutor()
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=executor,
            verifier=RepairVerifier(),
            applier=ExactApplier(),
            repository=InMemoryDeliveryRepository(),
            resolved_journey_sha256="f" * 64,
        )
        coordinator.configure_pipeline_runtime(RevisionCatalog(revision), runs)
        created = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="修复直到测试通过",
            pipeline_revision_id="backend-delivery:1",
            journey_binding_snapshot=revision.binding_snapshot,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )
        plan = await _wait_for(coordinator, created.id, "awaiting_plan_decision")
        await coordinator.decide_plan(
            created.id,
            decision="approve",
            expected_version=plan.version,
            expected_subject_sha256=plan.plan_gate.subject_sha256,  # type: ignore[union-attr]
        )

        candidate = await _wait_for(
            coordinator, created.id, "awaiting_candidate_decision"
        )
        graph = runs.get_for_delivery(created.id)
        loop_state = next(
            node
            for node in graph.snapshot["nodes"]
            if node["node_id"] == "repair-loop"
        )
        assert executor.attempts == 2
        assert len(loop_state["iterations"]) == 2
        assert candidate.verification is not None
        assert candidate.verification.status == "passed"
        assert candidate.candidate is not None
        assert candidate.candidate.candidate_revision == "2" * 40

    asyncio.run(scenario())


def test_independent_ready_role_stages_execute_concurrently(tmp_path: Path) -> None:
    async def scenario() -> None:
        definition: dict[str, object] = {
            "id": "backend-delivery",
            "version": "4.0.0",
            "nodes": [
                {
                    "id": node_id,
                    "kind": "stage",
                    "workflow_mode": "agentscope.role-turn",
                    "bindings": {"actor": "hermes-pm"},
                }
                for node_id in ("product-review", "risk-review")
            ]
            + [
                {
                    "id": "tasking",
                    "kind": "stage",
                    "workflow_mode": "agentscope.role-turn",
                    "bindings": {"actor": "hermes-project-admin"},
                },
                {
                    "id": "approve-plan",
                    "kind": "approval_gate",
                    "subject_kind": "delivery-plan",
                },
            ],
            "edges": [
                {"source": "product-review", "target": "tasking"},
                {"source": "risk-review", "target": "tasking"},
                {"source": "tasking", "target": "approve-plan"},
            ],
        }
        revision = _revision_for(definition)
        database = tmp_path / "agent-team-os.sqlite"
        MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        planning = ConcurrentPlanningService()
        coordinator = DeliveryCoordinator(
            planning=planning,
            executor=DeterministicCodeExecutor(),
            repository=InMemoryDeliveryRepository(),
            resolved_journey_sha256="f" * 64,
        )
        coordinator.configure_pipeline_runtime(RevisionCatalog(revision), runs)
        created = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="并行完成产品与风险分析",
            pipeline_revision_id="backend-delivery:1",
            journey_binding_snapshot=revision.binding_snapshot,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )

        await _wait_for(coordinator, created.id, "awaiting_plan_decision")

        assert planning.max_active == 2
        graph = runs.get_for_delivery(created.id)
        states = {node["node_id"]: node["status"] for node in graph.snapshot["nodes"]}
        assert states["product-review"] == "succeeded"
        assert states["risk-review"] == "succeeded"
        assert states["tasking"] == "succeeded"

    asyncio.run(scenario())
