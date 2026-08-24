from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ...delivery import DeliveryRepository, DeliveryRun, SQLiteDeliveryRepository
from ...shared.errors import ProductError
from ...shared.events import ProductEvent
from .application import ProjectCatalog

TERMINAL_DELIVERY_STATES = {"completed", "rejected", "failed", "cancelled"}


class ProjectLeaseDeliveryRepository:
    """Release project execution leases only after terminal state is durable."""

    def __init__(self, inner: DeliveryRepository, projects: ProjectCatalog) -> None:
        self.inner = inner
        self.projects = projects

    def save(self, delivery: DeliveryRun) -> None:
        if (
            isinstance(self.inner, SQLiteDeliveryRepository)
            and self.inner.get(delivery.id) is None
            and delivery.status not in TERMINAL_DELIVERY_STATES
        ):
            self._save_initial_in_unit_of_work(delivery)
            return
        self.inner.save(delivery)
        if delivery.status in TERMINAL_DELIVERY_STATES:
            self.projects.release_delivery(delivery.project_id, delivery.id)

    def get(self, delivery_id: str) -> DeliveryRun | None:
        return self.inner.get(delivery_id)

    def list(self) -> tuple[DeliveryRun, ...]:
        return self.inner.list()

    def list_events(self, delivery_id: str) -> tuple[ProductEvent, ...]:
        return self.inner.list_events(delivery_id)

    def reconcile_leases(self) -> None:
        """Repair lease state after a crash without releasing an applying delivery early."""
        deliveries = self.inner.list()
        delivery_by_id = {delivery.id: delivery for delivery in deliveries}
        for project in self.projects.list():
            lease_id = self.projects.repository.active_delivery_id(project.id)
            leased_delivery = None if lease_id is None else delivery_by_id.get(lease_id)
            if lease_id is not None and (
                leased_delivery is None or leased_delivery.status in TERMINAL_DELIVERY_STATES
            ):
                self.projects.release_delivery(project.id, lease_id)
        by_project: dict[str, list[DeliveryRun]] = {}
        for delivery in deliveries:
            if delivery.status in TERMINAL_DELIVERY_STATES:
                self.projects.release_delivery(delivery.project_id, delivery.id)
                continue
            by_project.setdefault(delivery.project_id, []).append(delivery)
        for project_id, active in by_project.items():
            current_id = self.projects.repository.active_delivery_id(project_id)
            selected = next((item for item in active if item.id == current_id), active[0])
            if current_id is not None and current_id != selected.id:
                self.projects.release_delivery(project_id, current_id)
            if self.projects.repository.active_delivery_id(project_id) is None:
                self.projects.repository.acquire_lease(project_id, selected.id)
            for duplicate in active:
                if duplicate.id == selected.id:
                    continue
                self.save(
                    duplicate.model_copy(
                        update={
                            "status": "failed",
                            "version": duplicate.version + 1,
                            "error_code": "PROJECT_DELIVERY_LEASE_CONFLICT",
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )

    def _save_initial_in_unit_of_work(self, delivery: DeliveryRun) -> None:
        assert isinstance(self.inner, SQLiteDeliveryRepository)
        with sqlite3.connect(self.inner.path, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            project = connection.execute(
                "SELECT lifecycle_status FROM projects WHERE id=?", (delivery.project_id,)
            ).fetchone()
            if project is None or project[0] != "active":
                raise ProductError(
                    code="PROJECT_NOT_ACTIVE",
                    title="项目不可运行",
                    detail="项目不是可运行状态，无法创建交付。",
                    repair="检查项目初始化或归档状态。",
                    status_code=409,
                )
            try:
                connection.execute(
                    """INSERT INTO project_delivery_leases(
                    project_id,delivery_id,acquired_at
                    ) VALUES(?,?,?)""",
                    (delivery.project_id, delivery.id, datetime.now(UTC).isoformat()),
                )
            except sqlite3.IntegrityError as error:
                current = connection.execute(
                    "SELECT delivery_id FROM project_delivery_leases WHERE project_id=?",
                    (delivery.project_id,),
                ).fetchone()
                raise ProductError(
                    code="PROJECT_ACTIVE_DELIVERY_CONFLICT",
                    title="项目已有活动交付",
                    detail=f"当前活动交付为 {current[0] if current else '未知'}。",
                    repair="等待活动交付进入终态后重新创建。",
                    status_code=409,
                ) from error
            self.inner.save_on(connection, delivery)
