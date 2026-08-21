from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


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

    assert response.status_code == 201
    delivery = response.json()
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
        response = client.post(
            f"/v1/deliveries/{created['id']}/plan-decision",
            json={"decision": "approve", "expected_version": created["version"]},
        )

    assert response.status_code == 200
    delivery = response.json()
    assert delivery["status"] == "awaiting_candidate_decision"
    assert delivery["version"] == 2
    assert delivery["candidate"] == {
        "base_revision": "base-revision",
        "candidate_revision": "candidate-revision",
        "diff_sha256": "a" * 64,
        "changed_files": ["src/health.py", "tests/test_health.py"],
    }
    assert delivery["evidence_identity"] == "deterministic-test"


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
        response = client.post(
            f"/v1/deliveries/{created['id']}/plan-decision",
            json={"decision": "approve", "expected_version": 99},
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
        candidate = client.post(
            f"/v1/deliveries/{created['id']}/plan-decision",
            json={"decision": "approve", "expected_version": 1},
        ).json()

    restarted = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        repository=SQLiteDeliveryRepository(database),
    )
    with TestClient(create_app(restarted)) as client:
        recovered = client.get(f"/v1/deliveries/{created['id']}")
        rejected = client.post(
            f"/v1/deliveries/{created['id']}/candidate-decision",
            json={"decision": "reject", "expected_version": candidate["version"]},
        )

    assert recovered.status_code == 200
    assert recovered.json()["candidate"] == candidate["candidate"]
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["version"] == 3

