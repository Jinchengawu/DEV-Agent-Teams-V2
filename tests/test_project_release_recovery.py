from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_external_forward_release_v2 import FakeForwardRemote, _release_fixture

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator, DeliveryRun, SQLiteDeliveryRepository
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ProjectGitWorkspaces
from agent_team_os.modules.projects import (
    ProjectCatalog,
    ProjectCreate,
    ProjectLeaseDeliveryRepository,
)
from agent_team_os.modules.releases import (
    ExternalForwardReleaseCoordinator,
    ExternalReleaseError,
    ReleaseBundleV2,
    SQLiteExternalReleaseRepository,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


def _partial_release(
    tmp_path: Path, *, configure_guard: bool = True
) -> tuple[
    ProjectCatalog,
    ProjectLeaseDeliveryRepository,
    SQLiteExternalReleaseRepository,
    ExternalForwardReleaseCoordinator,
    ReleaseBundleV2,
]:
    project_repository, repository, _catalog, bundle, bases = _release_fixture(tmp_path)
    projects = ProjectCatalog(project_repository, ProjectGitWorkspaces(tmp_path / "managed"))
    if configure_guard:
        projects.configure_release_recovery(
            repository.project_recovery_delivery_ids, database=repository.database
        )
    deliveries = ProjectLeaseDeliveryRepository(
        SQLiteDeliveryRepository(repository.database), projects
    )
    release = ExternalForwardReleaseCoordinator(
        repository, FakeForwardRemote(dict(bases), fail_once_at=2)
    )
    with pytest.raises(ExternalReleaseError, match="deterministic partial failure"):
        release.apply(bundle)
    return projects, deliveries, repository, release, bundle


def _next_delivery(delivery: DeliveryRun, *, identifier: str = "next-delivery") -> DeliveryRun:
    return delivery.model_copy(
        update={"id": identifier, "status": "queued", "version": 1, "error_code": None}
    )


def test_partial_apply_rejects_cancel_and_new_delivery_through_public_api(tmp_path: Path) -> None:
    projects, deliveries, repository, release, bundle = _partial_release(
        tmp_path, configure_guard=False
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        repository=deliveries,
    )
    app = create_app(coordinator, projects=projects, external_release=release)
    before = deliveries.get(bundle.delivery_id)
    assert before is not None and before.status == "needs_attention"
    receipts = repository.list_remote_receipts(bundle.delivery_id)
    events = deliveries.list_events(bundle.delivery_id)

    with TestClient(app) as client:
        cancelled = client.post(
            f"/v1/deliveries/{bundle.delivery_id}/cancel",
            json={"expected_version": before.version},
        )
        created = client.post(
            "/v1/deliveries",
            json={"project_id": bundle.project_id, "user_request": "恢复前不得创建新交付。"},
        )

    assert cancelled.status_code == 409, cancelled.text
    assert created.status_code == 409, created.text
    assert created.json()["code"] == "PROJECT_RELEASE_RECOVERY_REQUIRED"
    assert deliveries.get(bundle.delivery_id) == before
    assert deliveries.list_events(bundle.delivery_id) == events
    assert repository.list_remote_receipts(bundle.delivery_id) == receipts
    assert projects.get(bundle.project_id).active_delivery_id == bundle.delivery_id
    assert projects.release_recovery_delivery_ids(bundle.project_id) == (bundle.delivery_id,)


@pytest.mark.parametrize("legacy_status", ["cancelled", "completed"])
@pytest.mark.parametrize("lease_holder", ["missing", "pending"])
def test_restart_reclaims_recovery_owner_without_downgrading_other_pending_delivery(
    tmp_path: Path, legacy_status: str, lease_holder: str
) -> None:
    projects, deliveries, repository, _release, bundle = _partial_release(tmp_path)
    inner = SQLiteDeliveryRepository(repository.database)
    owner = inner.get(bundle.delivery_id)
    assert owner is not None
    terminal = owner.model_copy(update={"status": legacy_status, "version": owner.version + 1})
    inner.save(terminal)
    projects.repository.release_lease(bundle.project_id, bundle.delivery_id)
    pending = _next_delivery(owner)
    inner.save(pending)
    if lease_holder == "pending":
        projects.repository.acquire_lease(bundle.project_id, pending.id)
    attempt = repository.get_attempt(bundle.delivery_id)

    deliveries.reconcile_leases()
    deliveries.reconcile_leases()

    assert projects.get(bundle.project_id).active_delivery_id == bundle.delivery_id
    assert inner.get(bundle.delivery_id) == terminal
    assert inner.get(pending.id) == pending
    assert repository.get_attempt(bundle.delivery_id) == attempt
    assert projects.release_recovery_delivery_ids(bundle.project_id) == (bundle.delivery_id,)


def test_recovery_guard_rejects_prepare_and_initial_unit_of_work_without_a_lease(
    tmp_path: Path,
) -> None:
    projects, deliveries, repository, release, bundle = _partial_release(tmp_path)
    owner = deliveries.get(bundle.delivery_id)
    assert owner is not None
    projects.repository.release_lease(bundle.project_id, bundle.delivery_id)
    proposed = _next_delivery(owner)

    with pytest.raises(ProductError) as preparation:
        projects.prepare_delivery(bundle.project_id, proposed.id, None)
    with pytest.raises(ProductError) as initial_save:
        deliveries.save(proposed)

    assert preparation.value.code == "PROJECT_RELEASE_RECOVERY_REQUIRED"
    assert initial_save.value.code == "PROJECT_RELEASE_RECOVERY_REQUIRED"
    assert deliveries.get(proposed.id) is None
    assert deliveries.list_events(proposed.id) == ()
    assert projects.get(bundle.project_id).active_delivery_id is None
    assert repository.get_attempt(bundle.delivery_id) is not None

    release.resume_forward(bundle.delivery_id)
    projects.prepare_delivery(bundle.project_id, proposed.id, None)
    deliveries.save(proposed)

    assert projects.get(bundle.project_id).active_delivery_id == proposed.id
    assert deliveries.get(proposed.id) == proposed


def test_terminal_cas_write_cannot_release_an_unfinished_release_owner(tmp_path: Path) -> None:
    projects, deliveries, repository, _release, bundle = _partial_release(tmp_path)
    owner = deliveries.get(bundle.delivery_id)
    assert owner is not None
    legacy_cancel = owner.model_copy(update={"status": "cancelled", "version": owner.version + 1})

    deliveries.save_if_current(
        legacy_cancel, expected_version=owner.version, expected_status=owner.status
    )

    assert projects.get(bundle.project_id).active_delivery_id == bundle.delivery_id
    assert repository.project_recovery_delivery_ids(bundle.project_id) == (bundle.delivery_id,)


def test_multiple_recovery_owners_fail_closed_without_changing_pending_deliveries(
    tmp_path: Path,
) -> None:
    projects, deliveries, repository, release, bundle = _partial_release(tmp_path)
    owner = deliveries.get(bundle.delivery_id)
    assert owner is not None
    other = _next_delivery(owner, identifier="other-recovery-owner")
    SQLiteDeliveryRepository(repository.database).save(other)
    repository.put_health(
        release.health(bundle.project_id).model_copy(
            update={"delivery_id": other.id, "bundle_sha256": "f" * 64}
        )
    )

    with pytest.raises(ProductError) as conflict:
        deliveries.reconcile_leases()

    assert conflict.value.code == "PROJECT_RELEASE_RECOVERY_OWNER_CONFLICT"
    assert deliveries.get(owner.id) == owner
    assert deliveries.get(other.id) == other
    assert projects.get(bundle.project_id).active_delivery_id == bundle.delivery_id


def test_missing_guard_blocks_workcell_admission_but_preserves_legacy_projects(
    tmp_path: Path,
) -> None:
    projects, deliveries, _repository, _release, bundle = _partial_release(
        tmp_path, configure_guard=False
    )
    owner = deliveries.get(bundle.delivery_id)
    assert owner is not None
    with pytest.raises(ProductError) as preparation:
        projects.prepare_delivery(bundle.project_id, "workcell-without-guard", None)
    with pytest.raises(ProductError) as initial_save:
        deliveries.save(_next_delivery(owner, identifier="workcell-without-guard"))
    assert preparation.value.code == "PROJECT_RELEASE_RECOVERY_GUARD_UNAVAILABLE"
    assert initial_save.value.code == "PROJECT_RELEASE_RECOVERY_GUARD_UNAVAILABLE"

    projects.create(
        ProjectCreate(
            id="legacy-project", name="旧单仓项目", default_pipeline_revision_id="legacy:1"
        ),
        "admin",
    )
    context = projects.prepare_delivery("legacy-project", "legacy-delivery", None)
    legacy = _next_delivery(owner, identifier="legacy-delivery").model_copy(
        update={"project_id": "legacy-project", "workspace_id": context.workspace_id}
    )
    deliveries.save(legacy)

    assert projects.release_recovery_delivery_ids("legacy-project") == ()
    assert projects.get("legacy-project").active_delivery_id == legacy.id
    assert deliveries.get(legacy.id) == legacy


def test_cross_database_release_project_and_delivery_composition_is_rejected(
    tmp_path: Path,
) -> None:
    projects, deliveries, _repository, _release, _bundle = _partial_release(tmp_path)
    other_database = tmp_path / "other.sqlite"
    MigrationRunner(other_database, Path(__file__).parents[1] / "migrations").migrate()
    other_release = SQLiteExternalReleaseRepository(other_database)

    with pytest.raises(ValueError, match="same SQLite database|one SQLite database"):
        projects.configure_release_recovery(
            other_release.project_recovery_delivery_ids, database=other_database
        )
    with pytest.raises(ValueError, match="same SQLite database|one SQLite database"):
        ProjectLeaseDeliveryRepository(SQLiteDeliveryRepository(other_database), projects)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        repository=deliveries,
    )
    with pytest.raises(ValueError, match="same SQLite database|one SQLite database"):
        create_app(
            coordinator,
            projects=projects,
            external_release=ExternalForwardReleaseCoordinator(
                other_release, FakeForwardRemote({}, fail_once_at=-1)
            ),
        )
