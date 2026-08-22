import time

from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from agent_team_os.testing import (
    DeterministicCandidateApplier,
    DeterministicCandidateVerifier,
    DeterministicCodeExecutor,
    DeterministicPlanningService,
)


def wait_for(client: TestClient, delivery_id: str, status: str) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        delivery = client.get(f"/v1/deliveries/{delivery_id}").json()
        if delivery["status"] == status:
            return delivery
        time.sleep(0.01)
    raise AssertionError(f"delivery did not reach {status}")


def test_backend_request_reaches_auditable_candidate_decision() -> None:
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
    )

    with TestClient(create_app(coordinator)) as client:
        response = client.post(
            "/v1/deliveries",
            json={
                "workspace_id": "backend-demo",
                "user_request": "Add a health endpoint that returns the service status.",
            },
        )
        delivery = wait_for(client, response.json()["id"], "awaiting_plan_decision")

    assert response.status_code == 202
    assert delivery["status"] == "awaiting_plan_decision"
    assert delivery["requirements"]["acceptance_criteria"] == [
        {
            "id": "AC-1",
            "statement": "The requested Backend behavior is implemented and machine-verifiable.",
        }
    ]
    assert delivery["task"]["acceptance_ids"] == ["AC-1"]
    assert delivery["task"]["system_policy"]["allowed_paths"] == ["src/**", "tests/**"]
    assert delivery["candidate"] is None
    assert delivery["evidence_identity"] == "deterministic-test"
    assert delivery["planning_identity"] == "deterministic-test"
    assert delivery["execution_identity"] is None


def test_approved_plan_executes_once_and_exposes_candidate_evidence() -> None:
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
    )

    with TestClient(create_app(coordinator)) as client:
        created = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add GET /health."},
        ).json()
        created = wait_for(client, created["id"], "awaiting_plan_decision")
        response = client.post(
            f"/v1/deliveries/{created['id']}/plan-decision",
            json={
                "decision": "approve",
                "expected_version": created["version"],
                "expected_subject_sha256": created["plan_gate"]["subject_sha256"],
            },
        )

        delivery = wait_for(client, created["id"], "awaiting_candidate_decision")

    assert response.status_code == 202
    assert delivery["status"] == "awaiting_candidate_decision"
    assert delivery["version"] == 2
    assert delivery["candidate"] == {
        "base_revision": "base-revision",
        "candidate_revision": "candidate-revision",
        "diff_sha256": "a" * 64,
        "changed_files": ["src/health.py", "tests/test_health.py"],
        "candidate_ref": "",
        "unified_diff": "",
    }
    assert delivery["evidence_identity"] == "deterministic-test"
    assert delivery["planning_identity"] == "deterministic-test"
    assert delivery["execution_identity"] == "deterministic-test"


def test_stale_plan_decision_is_rejected_without_execution() -> None:
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
    )

    with TestClient(create_app(coordinator)) as client:
        created = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add GET /health."},
        ).json()
        created = wait_for(client, created["id"], "awaiting_plan_decision")
        response = client.post(
            f"/v1/deliveries/{created['id']}/plan-decision",
            json={
                "decision": "approve",
                "expected_version": 99,
                "expected_subject_sha256": created["plan_gate"]["subject_sha256"],
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "delivery version conflict"


def test_candidate_can_be_recovered_after_restart_and_rejected(tmp_path) -> None:
    database = tmp_path / "deliveries.sqlite"
    first = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        repository=SQLiteDeliveryRepository(database),
    )
    with TestClient(create_app(first)) as client:
        created = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add GET /health."},
        ).json()
        created = wait_for(client, created["id"], "awaiting_plan_decision")
        candidate = client.post(
            f"/v1/deliveries/{created['id']}/plan-decision",
            json={
                "decision": "approve",
                "expected_version": 1,
                "expected_subject_sha256": created["plan_gate"]["subject_sha256"],
            },
        ).json()
        candidate = wait_for(client, created["id"], "awaiting_candidate_decision")

    restarted = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        repository=SQLiteDeliveryRepository(database),
    )
    with TestClient(create_app(restarted)) as client:
        recovered = client.get(f"/v1/deliveries/{created['id']}")
        rejected = client.post(
            f"/v1/deliveries/{created['id']}/candidate-decision",
            json={
                "decision": "reject",
                "expected_version": candidate["version"],
                "expected_subject_sha256": candidate["candidate_gate"]["subject_sha256"],
            },
        )
        rejected_delivery = wait_for(client, created["id"], "rejected")

    assert recovered.status_code == 200
    assert recovered.json()["candidate"] == candidate["candidate"]
    assert rejected.status_code == 202
    assert rejected_delivery["status"] == "rejected"
    assert rejected_delivery["version"] == 3


def test_verified_candidate_accepts_only_with_an_exact_apply_receipt() -> None:
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        verifier=DeterministicCandidateVerifier(),
        applier=DeterministicCandidateApplier(),
    )
    with TestClient(create_app(coordinator)) as client:
        created = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add GET /health."},
        ).json()
        created = wait_for(client, created["id"], "awaiting_plan_decision")
        candidate = client.post(
            f"/v1/deliveries/{created['id']}/plan-decision",
            json={
                "decision": "approve",
                "expected_version": 1,
                "expected_subject_sha256": created["plan_gate"]["subject_sha256"],
            },
        ).json()
        candidate = wait_for(client, created["id"], "awaiting_candidate_decision")
        accepted = client.post(
            f"/v1/deliveries/{created['id']}/candidate-decision",
            json={
                "decision": "accept",
                "expected_version": candidate["version"],
                "expected_subject_sha256": candidate["candidate_gate"]["subject_sha256"],
            },
        )
        completed = wait_for(client, created["id"], "completed")

    assert candidate["verification"]["status"] == "passed"
    assert accepted.status_code == 202
    assert completed["status"] == "completed"
    assert completed["apply_receipt"] == {
        "before_revision": "base-revision",
        "candidate_revision": "candidate-revision",
        "after_revision": "candidate-revision",
        "result": "applied",
        "recovered": False,
    }
