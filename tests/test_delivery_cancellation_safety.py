"""取消、审批与持久化竞争的合同测试；全部使用本地 Deterministic 边界。"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent_team_os.delivery as delivery_module
from agent_team_os.api import create_app
from agent_team_os.delivery import (
    ApplyReceipt,
    CandidateChange,
    DeliveryCoordinator,
    DeliveryRepository,
    DeliveryRun,
    InMemoryDeliveryRepository,
    SQLiteDeliveryRepository,
)
from agent_team_os.modules.workcells import (
    FrozenSlotBinding,
    WorkcellExecutionModule,
    WorkcellExecutionSnapshot,
    WorkcellWorkspaceSnapshot,
    create_workcell_execution_router,
)
from agent_team_os.modules.workcells.execution_domain import WorkcellRun, WorkcellRunTree
from agent_team_os.shared.errors import ProductError
from agent_team_os.testing import (
    DeterministicCandidateApplier,
    DeterministicCandidateVerifier,
    DeterministicCodeExecutor,
    DeterministicPlanningService,
)


def _run(status: str = "awaiting_candidate_decision") -> DeliveryRun:
    return DeliveryRun(
        id="delivery-cancellation-safety",
        workspace_id="backend-demo",
        user_request="验证取消与发布边界",
        status=status,
        version=3,
        resolved_journey_sha256="a" * 64,
        evidence_identity="deterministic-test",
        planning_identity="deterministic-test",
    )


def _coordinator(
    repository: DeliveryRepository,
    *,
    applier: DeterministicCandidateApplier | None = None,
) -> DeliveryCoordinator:
    return DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        verifier=DeterministicCandidateVerifier(),
        applier=applier or DeterministicCandidateApplier(),
        repository=repository,
        resolved_journey_sha256="a" * 64,
    )


async def _candidate(coordinator: DeliveryCoordinator) -> DeliveryRun:
    planned = await coordinator.submit(workspace_id="backend-demo", user_request="新增健康检查。")
    assert planned.plan_gate is not None
    candidate = await coordinator.decide_plan(
        planned.id,
        decision="approve",
        expected_version=planned.version,
        expected_subject_sha256=planned.plan_gate.subject_sha256,
    )
    assert candidate.status == "awaiting_candidate_decision"
    assert candidate.candidate_gate is not None
    return candidate


@pytest.mark.parametrize("status", ["applying", "needs_attention"])
def test_cancel_api_preserves_release_state_and_events(tmp_path: Path, status: str) -> None:
    repository = SQLiteDeliveryRepository(tmp_path / "deliveries.sqlite")
    delivery = _run(status)
    repository.save(delivery)
    before_events = repository.list_events(delivery.id)

    with TestClient(create_app(_coordinator(repository))) as client:
        response = client.post(
            f"/v1/deliveries/{delivery.id}/cancel",
            json={"expected_version": delivery.version},
        )
        observed = client.get(f"/v1/deliveries/{delivery.id}")

    assert response.status_code == 409
    assert response.json()["repair"]
    assert observed.status_code == 200
    assert observed.json()["status"] == status
    assert repository.get(delivery.id) == delivery
    assert repository.list_events(delivery.id) == before_events


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_compare_and_set_has_one_winner_and_no_loser_event(tmp_path: Path, backend: str) -> None:
    if backend == "sqlite":
        database = tmp_path / "deliveries.sqlite"
        repositories = [SQLiteDeliveryRepository(database), SQLiteDeliveryRepository(database)]
    else:
        repository = InMemoryDeliveryRepository()
        repositories = [repository, repository]
    initial = _run()
    repositories[0].save(initial)
    barrier = threading.Barrier(2)

    def compete(index: int) -> tuple[str, Exception | None]:
        repository = repositories[index]
        observed = repository.get(initial.id)
        assert observed == initial
        target = "applying" if index == 0 else "cancelling"
        proposed = observed.model_copy(update={"status": target, "version": observed.version + 1})
        barrier.wait(timeout=3)
        try:
            repository.save_if_current(
                proposed,
                expected_version=observed.version,
                expected_status=observed.status,
            )
        except Exception as error:
            return target, error
        return target, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, [0, 1]))

    winners = [status for status, error in results if error is None]
    losers = [error for _, error in results if error is not None]
    assert len(winners) == 1, results
    assert len(losers) == 1, results
    assert isinstance(losers[0], delivery_module.DeliveryTransitionConflictError)
    final = repositories[0].get(initial.id)
    assert final is not None
    assert final.status == winners[0]
    assert final.version == initial.version + 1
    events = repositories[0].list_events(initial.id)
    assert len(events) == 2
    assert events[-1].event_type == f"delivery.{winners[0]}"
    assert events[-1].aggregate_version == final.version


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_compare_and_set_checks_status_even_when_version_matches(
    tmp_path: Path, backend: str
) -> None:
    repository = (
        SQLiteDeliveryRepository(tmp_path / "deliveries.sqlite")
        if backend == "sqlite"
        else InMemoryDeliveryRepository()
    )
    initial = _run("applying")
    repository.save(initial)
    before = repository.list_events(initial.id)
    proposed = initial.model_copy(update={"status": "cancelled", "version": 4})

    with pytest.raises(delivery_module.DeliveryTransitionConflictError):
        repository.save_if_current(
            proposed,
            expected_version=initial.version,
            expected_status="awaiting_candidate_decision",
        )

    assert repository.get(initial.id) == initial
    assert repository.list_events(initial.id) == before


class BlockingApplier(DeterministicCandidateApplier):
    """真实线程停在可观察副作用边界，不连接任何 Git Remote。"""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    async def apply(self, candidate: CandidateChange, workspace_id: str) -> ApplyReceipt:
        self.calls += 1

        def remote_boundary() -> None:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise AssertionError("测试未释放 Apply 边界")

        await asyncio.to_thread(remote_boundary)
        return await super().apply(candidate, workspace_id)


def test_apply_winner_rejects_cancel_while_remote_thread_is_running(tmp_path: Path) -> None:
    repository = SQLiteDeliveryRepository(tmp_path / "deliveries.sqlite")
    applier = BlockingApplier()
    coordinator = _coordinator(repository, applier=applier)
    candidate = asyncio.run(_candidate(coordinator))

    with TestClient(create_app(coordinator)) as client:
        accepted = client.post(
            f"/v1/deliveries/{candidate.id}/candidate-decision",
            json={
                "decision": "accept",
                "expected_version": candidate.version,
                "expected_subject_sha256": candidate.candidate_gate.subject_sha256,
            },
        )
        try:
            assert accepted.status_code == 202
            assert applier.entered.wait(timeout=3)
            applying = client.get(f"/v1/deliveries/{candidate.id}").json()
            assert applying["status"] == "applying"
            rejected = client.post(
                f"/v1/deliveries/{candidate.id}/cancel",
                json={"expected_version": applying["version"]},
            )
            assert rejected.status_code == 409
            assert client.get(f"/v1/deliveries/{candidate.id}").json()["status"] == "applying"
            assert applier.calls == 1
        finally:
            applier.release.set()


class CountingApplier(DeterministicCandidateApplier):
    def __init__(self) -> None:
        self.calls = 0

    async def apply(self, candidate: CandidateChange, workspace_id: str) -> ApplyReceipt:
        self.calls += 1
        return await super().apply(candidate, workspace_id)


@pytest.mark.parametrize("winner", ["cancel", "reject"])
def test_cancel_or_reject_winner_prevents_a_stale_apply(winner: str) -> None:
    async def exercise() -> None:
        repository = InMemoryDeliveryRepository()
        applier = CountingApplier()
        coordinator = _coordinator(repository, applier=applier)
        candidate = await _candidate(coordinator)
        if winner == "cancel":
            result = await coordinator.cancel(candidate.id, expected_version=candidate.version)
            expected_terminal = "cancelled"
        else:
            result = await coordinator.decide_candidate(
                candidate.id,
                decision="reject",
                expected_version=candidate.version,
                expected_subject_sha256=candidate.candidate_gate.subject_sha256,
            )
            expected_terminal = "rejected"
        assert result.status == expected_terminal
        before = repository.list_events(candidate.id)
        with pytest.raises(
            (
                delivery_module.DeliveryVersionConflictError,
                delivery_module.DeliveryStateConflictError,
            )
        ):
            await coordinator.decide_candidate(
                candidate.id,
                decision="accept",
                expected_version=candidate.version,
                expected_subject_sha256=candidate.candidate_gate.subject_sha256,
            )
        assert applier.calls == 0
        assert coordinator.get(candidate.id).status == expected_terminal
        assert repository.list_events(candidate.id) == before

    asyncio.run(exercise())


class SimultaneousReadRepository(SQLiteDeliveryRepository):
    """让两个协调器确实读到同一旧版本，不能只靠较早的状态判断通过。"""

    def __init__(self, path: Path, barrier: threading.Barrier, delivery_id: str) -> None:
        super().__init__(path)
        self.barrier = barrier
        self.delivery_id = delivery_id
        self.first_read = True

    def get(self, delivery_id: str) -> DeliveryRun | None:
        current = super().get(delivery_id)
        if delivery_id == self.delivery_id and self.first_read:
            self.first_read = False
            self.barrier.wait(timeout=3)
        return current


@pytest.mark.parametrize("other_decision", ["cancel", "reject"])
def test_two_coordinators_cannot_apply_and_cancel_the_same_frozen_gate(
    tmp_path: Path, other_decision: str
) -> None:
    database = tmp_path / "deliveries.sqlite"
    original_repository = SQLiteDeliveryRepository(database)
    candidate = asyncio.run(_candidate(_coordinator(original_repository)))
    before_events = original_repository.list_events(candidate.id)
    barrier = threading.Barrier(2)
    applier = CountingApplier()
    coordinators = [
        _coordinator(SimultaneousReadRepository(database, barrier, candidate.id), applier=applier)
        for _ in range(2)
    ]

    async def command(index: int) -> DeliveryRun:
        coordinator = coordinators[index]
        if index == 1 and other_decision == "cancel":
            return await coordinator.cancel(candidate.id, expected_version=candidate.version)
        return await coordinator.decide_candidate(
            candidate.id,
            decision="accept" if index == 0 else "reject",
            expected_version=candidate.version,
            expected_subject_sha256=candidate.candidate_gate.subject_sha256,
        )

    def execute(index: int) -> DeliveryRun | Exception:
        try:
            return asyncio.run(command(index))
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [0, 1]))
    winners = [result for result in results if isinstance(result, DeliveryRun)]
    losers = [result for result in results if isinstance(result, Exception)]
    assert len(winners) == 1, results
    assert len(losers) == 1, results
    assert isinstance(
        losers[0],
        (
            delivery_module.DeliveryVersionConflictError,
            delivery_module.DeliveryStateConflictError,
            ProductError,
        ),
    )
    final = original_repository.get(candidate.id)
    assert final is not None
    assert final.status == winners[0].status
    events = original_repository.list_events(candidate.id)[len(before_events) :]
    if final.status == "completed":
        assert applier.calls == 1
        assert not any(event.event_type == "delivery.cancelling" for event in events)
    else:
        assert final.status == ("cancelled" if other_decision == "cancel" else "rejected")
        assert applier.calls == 0
        assert not any(event.event_type == "delivery.applying" for event in events)


class CleanupFailureBoundary:
    """在产品 Pipeline 清理 Port 注入故障，检查可恢复状态。"""

    def __init__(self, repository: DeliveryRepository, *, fail: bool) -> None:
        self.repository = repository
        self.fail = fail
        self.calls = 0

    def cancel(self, delivery: DeliveryRun) -> None:
        self.calls += 1
        persisted = self.repository.get(delivery.id)
        assert persisted is not None
        assert persisted.status == "cancelling"
        if self.fail:
            raise RuntimeError("CANCEL_CLEANUP_INJECTED")


def test_cleanup_failure_persists_cancelling_and_recovers_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "deliveries.sqlite"
    repository = SQLiteDeliveryRepository(database)
    initial = _run("executing").model_copy(
        update={"pipeline_revision_id": "pipeline:1", "pipeline_run_id": "pipeline-run-one"}
    )
    repository.save(initial)
    first = _coordinator(repository)
    broken = CleanupFailureBoundary(repository, fail=True)
    monkeypatch.setattr(first, "_pipeline_execution", broken)

    async def first_cancel() -> None:
        # HTTP 可以返回失败；持久化状态和可重试证据是这里的合同。
        with suppress(Exception):
            await first.cancel(initial.id, expected_version=initial.version)

    asyncio.run(first_cancel())
    pending = repository.get(initial.id)
    assert broken.calls == 1
    assert pending is not None
    assert pending.status == "cancelling"
    assert pending.error_code is not None
    assert not any(
        event.event_type in {"delivery.cancelled", "delivery.failed"}
        for event in repository.list_events(initial.id)
    )

    restarted_repository = SQLiteDeliveryRepository(database)
    restarted = _coordinator(restarted_repository)
    repaired = CleanupFailureBoundary(restarted_repository, fail=False)
    monkeypatch.setattr(restarted, "_pipeline_execution", repaired)
    asyncio.run(restarted.recover())

    assert repaired.calls == 1
    assert restarted.get(initial.id).status == "cancelled"
    assert restarted_repository.list_events(initial.id)[-1].event_type == "delivery.cancelled"


@pytest.mark.parametrize(
    "winner_status",
    ["applying", "needs_attention", "cancelling", "completed", "rejected", "cancelled"],
)
def test_queued_stale_candidate_decision_cannot_overwrite_winner(winner_status: str) -> None:
    async def exercise() -> None:
        repository = InMemoryDeliveryRepository()
        applier = CountingApplier()
        coordinator = _coordinator(repository, applier=applier)
        candidate = await _candidate(coordinator)
        coordinator.start_candidate_decision(
            candidate.id,
            decision="accept",
            expected_version=candidate.version,
            expected_subject_sha256=candidate.candidate_gate.subject_sha256,
        )
        winner = candidate.model_copy(
            update={"status": winner_status, "version": candidate.version + 1}
        )
        repository.save(winner)
        before = repository.list_events(candidate.id)
        # 已调度任务在下次事件循环恢复后才读到胜者；不依赖墙钟轮询。
        for _ in range(3):
            await asyncio.sleep(0)
        assert repository.get(candidate.id) == winner
        assert repository.list_events(candidate.id) == before
        assert applier.calls == 0

    asyncio.run(exercise())


def test_background_reject_does_not_cancel_or_await_itself() -> None:
    async def exercise() -> None:
        repository = InMemoryDeliveryRepository()
        coordinator = _coordinator(repository)
        candidate = await _candidate(coordinator)
        coordinator.start_candidate_decision(
            candidate.id,
            decision="reject",
            expected_version=candidate.version,
            expected_subject_sha256=candidate.candidate_gate.subject_sha256,
        )

        async def wait_for_rejection() -> None:
            while coordinator.get(candidate.id).status not in {"rejected", "failed", "cancelled"}:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_rejection(), timeout=2)
        assert coordinator.get(candidate.id).status == "rejected"
        assert coordinator.get(candidate.id).candidate_gate.decision == "reject"

    asyncio.run(exercise())


@pytest.mark.parametrize("retry", [False, True])
def test_cancellation_waits_for_cleanup_and_retry_cannot_interrupt_it(retry: bool) -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_allowed = asyncio.Event()
        cleanup_finished = asyncio.Event()

        class SlowCleanupExecutor(DeterministicCodeExecutor):
            async def execute(self, task, workspace_id, delivery_id):
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    cleanup_started.set()
                    await cleanup_allowed.wait()
                    cleanup_finished.set()

        repository = InMemoryDeliveryRepository()
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=SlowCleanupExecutor(),
            repository=repository,
            resolved_journey_sha256="a" * 64,
        )
        planned = await coordinator.submit(
            workspace_id="backend-demo", user_request="验证取消等待清理。"
        )
        coordinator.start_plan_decision(
            planned.id,
            decision="approve",
            expected_version=planned.version,
            expected_subject_sha256=planned.plan_gate.subject_sha256,
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        executing = coordinator.get(planned.id)
        cancel_tasks = [
            asyncio.create_task(
                coordinator.cancel(executing.id, expected_version=executing.version)
            )
        ]
        try:
            await asyncio.wait_for(cleanup_started.wait(), timeout=2)
            pending = coordinator.get(planned.id)
            assert pending.status == "cancelling"
            if retry:
                cancel_tasks.append(
                    asyncio.create_task(
                        coordinator.cancel(pending.id, expected_version=pending.version)
                    )
                )
                for _ in range(3):
                    await asyncio.sleep(0)
            assert coordinator.get(planned.id).status == "cancelling"
            assert not cleanup_finished.is_set()
            assert not any(task.done() for task in cancel_tasks)
            with pytest.raises(delivery_module.ActiveDeliveryConflictError):
                coordinator.enqueue(
                    workspace_id="backend-demo", user_request="清理前不得启动下一次交付。"
                )
        finally:
            cleanup_allowed.set()
            await asyncio.wait_for(asyncio.gather(*cancel_tasks), timeout=2)
        assert cleanup_finished.is_set()
        assert coordinator.get(planned.id).status == "cancelled"

    asyncio.run(exercise())


@pytest.mark.parametrize("status", ["succeeded", "failed", "timed_out", "interrupted", "cancelled"])
def test_terminal_workcell_cancel_cannot_cancel_its_active_delivery(status: str) -> None:
    snapshot = WorkcellExecutionSnapshot(
        team_template_revision_id="team:1",
        team_template_sha256="1" * 64,
        pipeline_revision_id="pipeline:1",
        pipeline_revision_sha256="2" * 64,
        stage_path="frontend.delivery",
        workcell_key="frontend",
        workspace=WorkcellWorkspaceSnapshot(
            workspace_binding_id="workspace-frontend",
            kind="git_repository_v1",
            adapter_type="managed-bare-git",
            repository_uri="projects/test/frontend",
            base_revision="3" * 40,
            verification_sha256="4" * 64,
        ),
        delegation_policy={
            "max_children": 3,
            "max_concurrency": 2,
            "max_writers": 1,
            "max_depth": 1,
            "wall_clock_budget_seconds": 900,
        },
        slot_bindings=(
            FrozenSlotBinding(
                slot_key="main",
                deployment_id="deployment-main",
                resolved_provider_binding_hash="5" * 64,
                deployment_snapshot={"runtime_identity": "deterministic-test"},
            ),
        ),
        method_snapshot_sha256="6" * 64,
    )
    delivery = _run("executing")
    tree = WorkcellRunTree(
        workcell_run=WorkcellRun(
            id="finished-workcell",
            delivery_id=delivery.id,
            pipeline_run_id="pipeline-run-one",
            stage_attempt_id="old-stage-attempt",
            stage_path="frontend.delivery",
            loop_iteration=1,
            workcell_key="frontend",
            workcell_snapshot=snapshot,
            workcell_snapshot_sha256="7" * 64,
            status=status,
            version=4,
            deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    )

    class TerminalKernel(WorkcellExecutionModule):
        """只替换读取边界；保留真实 Kernel.cancel 的终态与版本规则。"""

        def __init__(self) -> None:
            pass

        def tree(self, run_id: str) -> WorkcellRunTree:
            assert run_id == tree.workcell_run.id
            return tree

    repository = InMemoryDeliveryRepository()
    repository.save(delivery)
    coordinator = _coordinator(repository)
    before = repository.list_events(delivery.id)
    parent_cancellations: list[str] = []

    async def cancel_parent(current: WorkcellRunTree) -> None:
        parent_cancellations.append(current.workcell_run.delivery_id)
        current_delivery = coordinator.get(current.workcell_run.delivery_id)
        await coordinator.cancel(current_delivery.id, expected_version=current_delivery.version)

    app = create_app(coordinator)
    app.include_router(
        create_workcell_execution_router(TerminalKernel(), before_cancel=cancel_parent)
    )
    with TestClient(app) as client:
        response = client.post(
            f"/v1/workcell-runs/{tree.workcell_run.id}/cancel",
            json={"expected_version": tree.workcell_run.version},
        )

    assert response.status_code == (200 if status == "cancelled" else 409)
    assert parent_cancellations == []
    assert repository.get(delivery.id) == delivery
    assert repository.list_events(delivery.id) == before
