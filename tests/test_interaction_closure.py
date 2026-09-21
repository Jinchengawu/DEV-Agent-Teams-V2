from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.evidence import (
    EvidenceKind,
    EvidenceLedger,
    SQLiteEvidenceRepository,
)
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
    UserCreate,
)
from agent_team_os.modules.projects import (
    ProjectCatalog,
    ProjectCreate,
    ProjectLeaseDeliveryRepository,
    ProjectOnboardingComplete,
    SetupReadinessCheck,
    SetupReadinessService,
    SQLiteProjectRepository,
)
from agent_team_os.readiness import DependencyCheck, ReadinessReport
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.permissions import Role
from agent_team_os.testing import (
    DeterministicCandidateVerifier,
    DeterministicCodeExecutor,
    DeterministicPlanningService,
)

ROOT = Path(__file__).parents[1]
ORIGIN = "http://testserver"
ADMIN_PASSWORD = secrets.token_urlsafe(24)
VIEWER_PASSWORD = secrets.token_urlsafe(24)


class Provisioner:
    def provision(self, repository_ref: str) -> str:
        return "a" * 40

    def reset(self, repository_ref: str) -> str:
        return "b" * 40

    def revision(self, repository_ref: str) -> str:
        return "a" * 40


class ReadyRuntime:
    def inspect(self) -> ReadinessReport:
        return ReadinessReport(
            status="ready",
            checks=(DependencyCheck(name="codex-cli", status="ready"),),
        )


class BlockedRuntime:
    def inspect(self) -> ReadinessReport:
        return ReadinessReport(
            status="not_ready",
            checks=(
                DependencyCheck(
                    name="codex-cli",
                    status="missing",
                    repair="Install Codex CLI.",
                ),
            ),
        )


def _catalog(database: Path) -> ProjectCatalog:
    MigrationRunner(database, ROOT / "migrations").migrate()
    return ProjectCatalog(SQLiteProjectRepository(database), Provisioner())


def _create_project(
    projects: ProjectCatalog,
    project_id: str,
    *,
    onboarding_mode: str = "guided_evaluation",
) -> None:
    projects.create(
        ProjectCreate(
            id=project_id,
            name=project_id,
            default_pipeline_revision_id="delivery:1",
            deployment_ids=("codex",),
            onboarding_mode=onboarding_mode,
        ),
        "admin",
    )


def _app(
    database: Path,
    projects: ProjectCatalog,
    readiness: object,
    *,
    identity: IdentityService | None = None,
    evidence: EvidenceLedger | None = None,
):  # type: ignore[no-untyped-def]
    repository = ProjectLeaseDeliveryRepository(SQLiteDeliveryRepository(database), projects)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        verifier=DeterministicCandidateVerifier(),
        repository=repository,
        resolved_journey_sha256="a" * 64,
    )
    return create_app(
        coordinator,
        projects=projects,
        readiness=readiness,  # type: ignore[arg-type]
        identity=identity,
        evidence=evidence,
    )


def _identity(database: Path) -> IdentityService:
    identity = IdentityService(SQLiteIdentityRepository(database))
    administrator = identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    identity.create_user(
        administrator,
        UserCreate(
            username="interaction-viewer",
            display_name="Interaction Viewer",
            role=Role.VIEWER,
            password=VIEWER_PASSWORD,
        ),
    )
    return identity


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def _mutation_headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
    }


def _wait_for_delivery(client: TestClient, delivery_id: str, status: str) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.get(f"/v1/deliveries/{delivery_id}")
        assert response.status_code == 200, response.text
        delivery = response.json()
        if delivery["status"] == status:
            return delivery
        time.sleep(0.01)
    raise AssertionError(f"delivery {delivery_id} did not reach {status}")


def _create_candidate_evaluation(client: TestClient, project_id: str) -> dict[str, object]:
    created = client.post(
        "/v1/deliveries",
        headers=_mutation_headers(client),
        json={
            "project_id": project_id,
            "user_request": "验证 onboarding complete 公共接口",
            "purpose": "onboarding_evaluation",
        },
    )
    assert created.status_code == 202, created.text
    planned = _wait_for_delivery(
        client, created.json()["id"], "awaiting_plan_decision"
    )
    decision = client.post(
        f"/v1/deliveries/{planned['id']}/plan-decision",
        headers=_mutation_headers(client),
        json={
            "decision": "approve",
            "expected_version": planned["version"],
            "expected_subject_sha256": planned["plan_gate"]["subject_sha256"],
        },
    )
    assert decision.status_code == 202, decision.text
    return _wait_for_delivery(client, str(planned["id"]), "awaiting_candidate_decision")


def _candidate_subject(candidate: dict[str, object]) -> str:
    gate = candidate.get("candidate_gate")
    assert isinstance(gate, dict)
    subject = gate.get("subject_sha256")
    assert isinstance(subject, str)
    return subject


def test_migration_backfills_existing_projects_ready_and_new_guided_project_starts_setup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "onboarding.sqlite"
    projects = _catalog(database)

    legacy = projects.get("legacy-default")
    _create_project(projects, "guided")
    guided = projects.get("guided")

    assert legacy.onboarding.mode == "standard"
    assert legacy.onboarding.status == "ready"
    assert guided.onboarding.mode == "guided_evaluation"
    assert guided.onboarding.status == "setup"
    assert guided.onboarding.version == 1


def test_evaluation_delivery_atomically_binds_onboarding_and_rejects_duplicate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "evaluation.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")

    with TestClient(_app(database, projects, ReadyRuntime())) as client:
        created = client.post(
            "/v1/deliveries",
            json={
                "project_id": "guided",
                "user_request": "评测交付",
                "purpose": "onboarding_evaluation",
            },
        )
        assert created.status_code == 202
        onboarding = projects.get("guided").onboarding
        assert onboarding.status == "in_evaluation"
        assert onboarding.evaluation_delivery_id == created.json()["id"]
        assert onboarding.version == 2

        duplicate = client.post(
            "/v1/deliveries",
            json={
                "project_id": "guided",
                "user_request": "重复评测",
                "purpose": "onboarding_evaluation",
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "DELIVERY_READINESS_BLOCKED"


def test_product_delivery_is_blocked_until_guided_onboarding_is_ready(tmp_path: Path) -> None:
    database = tmp_path / "product-blocked.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")

    with TestClient(_app(database, projects, ReadyRuntime())) as client:
        response = client.post(
            "/v1/deliveries",
            json={"project_id": "guided", "user_request": "正式交付"},
        )

    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "DELIVERY_READINESS_BLOCKED"
    assert problem["context"]["checks"][0]["id"] == "project.onboarding"


def test_setup_readiness_and_create_share_the_same_runtime_blocker(tmp_path: Path) -> None:
    database = tmp_path / "readiness.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")

    with TestClient(_app(database, projects, BlockedRuntime())) as client:
        report = client.get("/v1/setup-readiness?project_id=guided")
        create = client.post(
            "/v1/deliveries",
            json={
                "project_id": "guided",
                "user_request": "阻塞评测",
                "purpose": "onboarding_evaluation",
            },
        )

    assert report.status_code == 200
    assert report.json()["status"] == "blocked"
    blocker = next(
        item for item in report.json()["global_checks"] if item["id"] == "runtime.codex-cli"
    )
    assert blocker["status"] == "blocked"
    assert create.status_code == 409
    assert create.json()["context"]["checks"] == [blocker]
    assert projects.get("guided").onboarding.status == "setup"


def test_workcell_execution_readiness_replaces_legacy_repository_and_deployment_checks(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workcell-readiness.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM project_repositories WHERE project_id='guided'")
        connection.execute("DELETE FROM project_deployment_access WHERE project_id='guided'")

    workcell_ready = (
        SetupReadinessCheck(
            id="project.repositories",
            status="ready",
            summary="Workcell 仓库已就绪",
            repair="无需修复。",
            navigation_target="/projects/guided/overview",
            blocking_scope="start_delivery",
        ),
        SetupReadinessCheck(
            id="project.deployments",
            status="ready",
            summary="Workcell Deployment 授权已就绪",
            repair="无需修复。",
            navigation_target="/agents",
            blocking_scope="start_delivery",
        ),
    )
    service = SetupReadinessService(
        ReadyRuntime(),
        projects,
        inspect_execution=lambda _project_id, _revision_id: (workcell_ready, ()),
    )

    report = service.inspect("guided")

    assert report.status == "ready"
    project_checks = {check.id: check for check in report.project_checks}
    assert project_checks["project.repositories"].summary == "Workcell 仓库已就绪"
    assert project_checks["project.deployments"].summary == "Workcell Deployment 授权已就绪"


def test_onboarding_completion_uses_cas_and_bound_delivery_evidence_callback(
    tmp_path: Path,
) -> None:
    database = tmp_path / "complete.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO deliveries(id,project_id,snapshot_json) VALUES(?,?,?)",
            ("evaluation-1", "guided", "{}"),
        )
        connection.execute(
            """UPDATE project_onboarding SET status='in_evaluation',
            evaluation_delivery_id='evaluation-1',version=2 WHERE project_id='guided'"""
        )

    calls: list[tuple[str, str, str]] = []
    request = ProjectOnboardingComplete(
        expected_version=2,
        evaluation_delivery_id="evaluation-1",
        expected_candidate_gate_subject_sha256="c" * 64,
    )
    completed = projects.complete_onboarding(
        "guided",
        request,
        validate_evidence=lambda *args: calls.append(args),
    )

    assert calls == [("guided", "evaluation-1", "c" * 64)]
    assert completed.status == "ready"
    assert completed.version == 3
    with pytest.raises(ProductError) as conflict:
        projects.complete_onboarding(
            "guided",
            request,
            validate_evidence=lambda *_args: None,
        )
    assert conflict.value.code == "PROJECT_ONBOARDING_VERSION_CONFLICT"


def test_onboarding_complete_api_requires_current_candidate_and_verified_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "complete-api.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")
    identity = _identity(database)
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))

    with TestClient(
        _app(
            database,
            projects,
            ReadyRuntime(),
            identity=identity,
            evidence=evidence,
        ),
        base_url=ORIGIN,
    ) as client:
        _login(client, "admin", ADMIN_PASSWORD)
        candidate = _create_candidate_evaluation(client, "guided")
        onboarding = projects.get("guided").onboarding
        completed = client.post(
            "/v1/projects/guided/onboarding/complete",
            headers=_mutation_headers(client),
            json={
                "expected_version": onboarding.version,
                "evaluation_delivery_id": candidate["id"],
                "expected_candidate_gate_subject_sha256": _candidate_subject(candidate),
            },
        )
        delivery_after = client.get(f"/v1/deliveries/{candidate['id']}")

    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "ready"
    assert completed.json()["version"] == onboarding.version + 1
    assert delivery_after.status_code == 200
    assert delivery_after.json()["status"] == "awaiting_candidate_decision"
    assert delivery_after.json()["apply_receipt"] is None
    assert delivery_after.json()["release_bundle"] is None
    assert delivery_after.json()["release_manifest"] is None
    records = evidence.list(delivery_id=str(candidate["id"]), project_id="guided")
    verified = {record.kind for record in records if record.status.value == "verified"}
    assert {
        EvidenceKind.CANDIDATE,
        EvidenceKind.VERIFICATION,
        EvidenceKind.CANDIDATE_GATE,
    }.issubset(verified)
    assert EvidenceKind.APPLY_RECEIPT not in {record.kind for record in records}
    assert EvidenceKind.RELEASE_BUNDLE not in {record.kind for record in records}
    assert EvidenceKind.RELEASE_MANIFEST not in {record.kind for record in records}


def test_onboarding_complete_api_rejects_non_administrator(tmp_path: Path) -> None:
    database = tmp_path / "complete-permission.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")
    identity = _identity(database)
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))

    with TestClient(
        _app(
            database,
            projects,
            ReadyRuntime(),
            identity=identity,
            evidence=evidence,
        ),
        base_url=ORIGIN,
    ) as client:
        _login(client, "admin", ADMIN_PASSWORD)
        candidate = _create_candidate_evaluation(client, "guided")
        onboarding = projects.get("guided").onboarding
        client.cookies.clear()
        _login(client, "interaction-viewer", VIEWER_PASSWORD)
        denied = client.post(
            "/v1/projects/guided/onboarding/complete",
            headers=_mutation_headers(client),
            json={
                "expected_version": onboarding.version,
                "evaluation_delivery_id": candidate["id"],
                "expected_candidate_gate_subject_sha256": _candidate_subject(candidate),
            },
        )

    assert denied.status_code == 403
    assert denied.json()["code"] == "IDENTITY_PERMISSION_DENIED"
    assert projects.get("guided").onboarding.status == "in_evaluation"


def test_onboarding_complete_api_rejects_cross_project_delivery(tmp_path: Path) -> None:
    database = tmp_path / "complete-cross-project.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided-a")
    _create_project(projects, "guided-b")
    identity = _identity(database)
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))

    with TestClient(
        _app(
            database,
            projects,
            ReadyRuntime(),
            identity=identity,
            evidence=evidence,
        ),
        base_url=ORIGIN,
    ) as client:
        _login(client, "admin", ADMIN_PASSWORD)
        candidate = _create_candidate_evaluation(client, "guided-b")
        with sqlite3.connect(database) as connection:
            connection.execute(
                """UPDATE project_onboarding
                SET status='setup',evaluation_delivery_id=NULL,version=3
                WHERE project_id='guided-b'"""
            )
            connection.execute(
                """UPDATE project_onboarding
                SET status='in_evaluation',evaluation_delivery_id=?,version=2
                WHERE project_id='guided-a'""",
                (candidate["id"],),
            )
        denied = client.post(
            "/v1/projects/guided-a/onboarding/complete",
            headers=_mutation_headers(client),
            json={
                "expected_version": 2,
                "evaluation_delivery_id": candidate["id"],
                "expected_candidate_gate_subject_sha256": _candidate_subject(candidate),
            },
        )

    assert denied.status_code == 409
    assert denied.json()["code"] == "PROJECT_ONBOARDING_PROJECT_MISMATCH"
    assert projects.get("guided-a").onboarding.status == "in_evaluation"


def test_onboarding_complete_api_rejects_stale_candidate_subject(tmp_path: Path) -> None:
    database = tmp_path / "complete-subject.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")
    identity = _identity(database)
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))

    with TestClient(
        _app(
            database,
            projects,
            ReadyRuntime(),
            identity=identity,
            evidence=evidence,
        ),
        base_url=ORIGIN,
    ) as client:
        _login(client, "admin", ADMIN_PASSWORD)
        candidate = _create_candidate_evaluation(client, "guided")
        onboarding = projects.get("guided").onboarding
        denied = client.post(
            "/v1/projects/guided/onboarding/complete",
            headers=_mutation_headers(client),
            json={
                "expected_version": onboarding.version,
                "evaluation_delivery_id": candidate["id"],
                "expected_candidate_gate_subject_sha256": "f" * 64,
            },
        )

    assert denied.status_code == 409
    assert denied.json()["code"] == "PROJECT_ONBOARDING_GATE_SUBJECT_MISMATCH"
    assert projects.get("guided").onboarding.status == "in_evaluation"


@pytest.mark.parametrize(
    "invalid_kind",
    ["candidate", "verification", "candidate-gate"],
)
def test_onboarding_complete_api_requires_each_verified_evidence_kind(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    database = tmp_path / f"complete-evidence-{invalid_kind}.sqlite"
    projects = _catalog(database)
    _create_project(projects, "guided")
    identity = _identity(database)
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))

    with TestClient(
        _app(
            database,
            projects,
            ReadyRuntime(),
            identity=identity,
            evidence=evidence,
        ),
        base_url=ORIGIN,
    ) as client:
        _login(client, "admin", ADMIN_PASSWORD)
        candidate = _create_candidate_evaluation(client, "guided")
        evidence.sync_delivery(candidate)
        with sqlite3.connect(database) as connection:
            connection.execute(
                """UPDATE evidence_records
                SET content_sha256=NULL,status='invalid',verification_error='TEST_INVALIDATED'
                WHERE delivery_id=? AND project_id='guided' AND kind=?""",
                (candidate["id"], invalid_kind),
            )
        onboarding = projects.get("guided").onboarding
        denied = client.post(
            "/v1/projects/guided/onboarding/complete",
            headers=_mutation_headers(client),
            json={
                "expected_version": onboarding.version,
                "evaluation_delivery_id": candidate["id"],
                "expected_candidate_gate_subject_sha256": _candidate_subject(candidate),
            },
        )

    assert denied.status_code == 409
    assert denied.json()["code"] == "PROJECT_ONBOARDING_EVIDENCE_INCOMPLETE"
    assert invalid_kind in denied.json()["detail"]
    assert projects.get("guided").onboarding.status == "in_evaluation"


def test_navigation_target_rejects_external_urls() -> None:
    with pytest.raises(ValueError):
        SetupReadinessCheck(
            id="bad",
            status="blocked",
            summary="bad",
            repair="bad",
            navigation_target="https://example.com/repair",
            blocking_scope="start_delivery",
        )
