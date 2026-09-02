from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from ...shared.clock import Clock, SystemClock
from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from ...shared.ids import new_id
from ...shared.permissions import Role
from ..projects.domain import ProjectKnowledgeSourceApproval
from .domain import KnowledgeActor
from .tenant_application import TenantKnowledgeManager
from .tenant_domain import KnowledgeSyncJob, KnowledgeSyncJobRequest, TenantProviderBinding

_logger = logging.getLogger(__name__)

KNOWLEDGE_SYNC_RUNTIME_CONTRACT = {
    "contract_id": "knowledge-sync-runtime-v1",
    "scheduler_authority": "knowledge-sync-job-repository",
    "poll_interval_seconds": 900,
    "directory_reconciliation_interval_seconds": 86400,
    "worker_concurrency": 2,
    "max_attempts": 5,
    "lease_seconds": 300,
}


class KnowledgeSyncApprovalReader(Protocol):
    def list_all_knowledge_source_approvals(
        self,
    ) -> tuple[ProjectKnowledgeSourceApproval, ...]: ...


@dataclass(frozen=True)
class KnowledgeSyncPolicy:
    poll_interval: timedelta = timedelta(minutes=15)
    directory_reconciliation_interval: timedelta = timedelta(hours=24)
    worker_concurrency: int = 2
    max_attempts: int = 5
    worker_batch_size: int = 32
    supervisor_tick_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.poll_interval.total_seconds() <= 0:
            raise ValueError("poll_interval must be positive")
        if self.directory_reconciliation_interval.total_seconds() <= 0:
            raise ValueError("directory_reconciliation_interval must be positive")
        if self.worker_concurrency != 2:
            raise ValueError("knowledge-sync-runtime-v1 requires worker_concurrency=2")
        if self.max_attempts != 5:
            raise ValueError("knowledge-sync-runtime-v1 requires max_attempts=5")
        if self.worker_batch_size < self.worker_concurrency:
            raise ValueError("worker_batch_size cannot be smaller than worker_concurrency")
        if self.supervisor_tick_seconds <= 0:
            raise ValueError("supervisor_tick_seconds must be positive")


class KnowledgeSyncScheduler:
    """Create durable work only; Provider I/O belongs to the Worker."""

    def __init__(
        self,
        manager: TenantKnowledgeManager,
        approvals: KnowledgeSyncApprovalReader,
        *,
        policy: KnowledgeSyncPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.manager = manager
        self.approvals = approvals
        self.policy = policy or KnowledgeSyncPolicy()
        self.clock = clock or SystemClock()

    def enqueue_due(self) -> tuple[KnowledgeSyncJob, ...]:
        now = self.clock.now()
        bucket = int(now.timestamp() // self.policy.poll_interval.total_seconds())
        created: list[KnowledgeSyncJob] = []
        for approval in self.approvals.list_all_knowledge_source_approvals():
            if not approval.enabled:
                continue
            binding = self.manager.repository.get_binding(approval.binding_id)
            if binding is None or binding.status != "ready":
                continue
            connection = self.manager.repository.get_connection(binding.connection_id)
            if connection is None or connection.status != "ready":
                continue
            source_ids = tuple(
                sorted(
                    {
                        node.source_id
                        for node in self.manager.repository.list_binding_nodes(binding.id)
                        if node.source_id is not None
                    }
                )
            )
            for source_id in source_ids:
                idempotency_key = "scheduled-v1:" + str(
                    sha256_json(
                        {
                            "project_id": approval.project_id,
                            "approval_id": approval.id,
                            "approval_version": approval.version,
                            "binding_id": binding.id,
                            "source_id": source_id,
                            "poll_bucket": bucket,
                        }
                    )
                )
                try:
                    job, was_created = self.manager.enqueue_sync_job(
                        approval.created_by,
                        approval.project_id,
                        KnowledgeSyncJobRequest(
                            binding_id=binding.id,
                            source_id=source_id,
                            idempotency_key=idempotency_key,
                        ),
                        require_fresh_binding_probe=False,
                        max_attempts=self.policy.max_attempts,
                    )
                except ProductError as error:
                    _logger.warning(
                        "Knowledge scheduler rejected approved source project=%s binding=%s "
                        "source_sha256=%s code=%s",
                        approval.project_id,
                        binding.id,
                        sha256_json({"source_id": source_id}),
                        error.code,
                    )
                    continue
                if was_created:
                    created.append(job)
        return tuple(created)


class KnowledgeDirectoryReconciler:
    """Reconcile the authoritative Provider directory at most once per due window."""

    def __init__(
        self,
        manager: TenantKnowledgeManager,
        *,
        policy: KnowledgeSyncPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.manager = manager
        self.policy = policy or KnowledgeSyncPolicy()
        self.clock = clock or SystemClock()

    def reconcile_due(self) -> tuple[TenantProviderBinding, ...]:
        now = self.clock.now()
        threshold = now - self.policy.directory_reconciliation_interval
        reconciled: list[TenantProviderBinding] = []
        for binding in self.manager.repository.list_bindings():
            if binding.status != "ready" or (
                binding.last_permission_probe_at is not None
                and binding.last_permission_probe_at > threshold
            ):
                continue
            try:
                reconciled.append(
                    self.manager.refresh_binding(
                        KnowledgeActor(
                            user_id=binding.created_by,
                            role=Role.ADMINISTRATOR,
                        ),
                        binding.id,
                    )
                )
            except ProductError as error:
                _logger.warning(
                    "Knowledge directory reconciliation failed binding=%s code=%s",
                    binding.id,
                    error.code,
                )
        return tuple(reconciled)


class KnowledgeSyncWorker:
    """Drain due persistent Jobs with CAS leases and bounded local concurrency."""

    def __init__(
        self,
        manager: TenantKnowledgeManager,
        *,
        policy: KnowledgeSyncPolicy | None = None,
        clock: Clock | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.manager = manager
        self.policy = policy or KnowledgeSyncPolicy()
        self.clock = clock or SystemClock()
        self.worker_id = worker_id or f"knowledge-worker:{new_id()}"

    async def run_once(self) -> tuple[KnowledgeSyncJob, ...]:
        now = self.clock.now()
        await asyncio.to_thread(self.manager.repository.recover_expired_sync_jobs, now)
        due = await asyncio.to_thread(
            self.manager.repository.list_due_sync_jobs,
            now,
            limit=self.policy.worker_batch_size,
        )
        semaphore = asyncio.Semaphore(self.policy.worker_concurrency)

        async def run(job: KnowledgeSyncJob) -> KnowledgeSyncJob:
            async with semaphore:
                return await asyncio.to_thread(
                    self.manager.run_sync_job,
                    job.id,
                    lease_owner=self.worker_id,
                )

        return tuple(await asyncio.gather(*(run(job) for job in due)))


class KnowledgeSyncSupervisor:
    """Single-process supervisor for reconciliation, scheduling and leased work."""

    def __init__(
        self,
        scheduler: KnowledgeSyncScheduler,
        reconciler: KnowledgeDirectoryReconciler,
        worker: KnowledgeSyncWorker,
        *,
        policy: KnowledgeSyncPolicy | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.reconciler = reconciler
        self.worker = worker
        self.policy = policy or KnowledgeSyncPolicy()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> tuple[KnowledgeSyncJob, ...]:
        await asyncio.to_thread(self.reconciler.reconcile_due)
        await asyncio.to_thread(self.scheduler.enqueue_due)
        return await self.worker.run_once()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="agent-team-os-knowledge-sync",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        await task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                _logger.exception("Knowledge sync supervisor cycle failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.policy.supervisor_tick_seconds,
                )
            except TimeoutError:
                continue
