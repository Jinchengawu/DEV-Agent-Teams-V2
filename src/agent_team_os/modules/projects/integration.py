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
        if isinstance(inner, SQLiteDeliveryRepository):
            database = getattr(projects.repository, "database", None)
            if database is None or inner.path.resolve() != database.resolve():
                raise ValueError("Delivery and Project repositories must share one SQLite database")
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

    def save_if_current(
        self, delivery: DeliveryRun, *, expected_version: int, expected_status: str
    ) -> None:
        self.inner.save_if_current(
            delivery, expected_version=expected_version, expected_status=expected_status
        )
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
        recovery_projects: set[str] = set()
        for project in self.projects.list():
            owners = self.projects.release_recovery_delivery_ids(project.id)
            if len(owners) > 1:
                raise ProductError(
                    code="PROJECT_RELEASE_RECOVERY_OWNER_CONFLICT",
                    title="存在多个发布恢复所有者",
                    detail="、".join(owners),
                    repair="协调原发布记录；不得把任一未完成发布标记为普通失败。",
                    status_code=409,
                )
            if owners:
                recovery_projects.add(project.id)
                current = self.projects.repository.active_delivery_id(project.id)
                if current is not None and current != owners[0]:
                    self.projects.release_delivery(project.id, current)
                if self.projects.repository.active_delivery_id(project.id) is None:
                    self.projects.repository.acquire_lease(project.id, owners[0])
                continue
            lease_id = self.projects.repository.active_delivery_id(project.id)
            leased_delivery = None if lease_id is None else delivery_by_id.get(lease_id)
            if lease_id is not None and (
                leased_delivery is None or leased_delivery.status in TERMINAL_DELIVERY_STATES
            ):
                self.projects.release_delivery(project.id, lease_id)
        by_project: dict[str, list[DeliveryRun]] = {}
        for delivery in deliveries:
            if delivery.project_id in recovery_projects:
                continue
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
            self.projects.assert_release_ready(delivery.project_id)
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
            onboarding: tuple[object, ...] | None = None
            if delivery.purpose == "onboarding_evaluation":
                onboarding = connection.execute(
                    """SELECT mode,status,evaluation_delivery_id,version
                    FROM project_onboarding WHERE project_id=?""",
                    (delivery.project_id,),
                ).fetchone()
                if onboarding is None:
                    raise ProductError(
                        code="PROJECT_ONBOARDING_NOT_FOUND",
                        title="项目引导状态缺失",
                        detail="不能绑定评测交付。",
                        repair="运行最新数据库 Migration 后重试。",
                        status_code=503,
                    )
                if str(onboarding[0]) != "guided_evaluation" or str(onboarding[1]) != "setup":
                    raise ProductError(
                        code="PROJECT_ONBOARDING_STATE_CONFLICT",
                        title="项目不能启动评测交付",
                        detail="只有 setup 状态的 guided_evaluation 项目可以绑定评测交付。",
                        repair="刷新项目 onboarding 状态后重试。",
                        status_code=409,
                    )
                onboarding_version = onboarding[3]
                if not isinstance(onboarding_version, int):
                    raise ProductError(
                        code="PROJECT_ONBOARDING_VERSION_INVALID",
                        title="项目引导版本无效",
                        detail="onboarding version 不是有效整数。",
                        repair="检查 Migration 和运行库完整性后重试。",
                        status_code=503,
                    )
            self.inner.save_on(connection, delivery)
            if onboarding is not None:
                cursor = connection.execute(
                    """UPDATE project_onboarding SET status='in_evaluation',
                    evaluation_delivery_id=?,version=version+1,updated_at=?
                    WHERE project_id=? AND status='setup' AND evaluation_delivery_id IS NULL
                    AND version=?""",
                    (
                        delivery.id,
                        datetime.now(UTC).isoformat(),
                        delivery.project_id,
                        onboarding_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ProductError(
                        code="PROJECT_ONBOARDING_VERSION_CONFLICT",
                        title="项目引导版本冲突",
                        detail="onboarding 状态在交付创建期间发生变化。",
                        repair="刷新准备中心后重试。",
                        status_code=409,
                    )
