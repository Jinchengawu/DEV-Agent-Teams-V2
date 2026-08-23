import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.control_plane import ControlPlaneService, HealthResult
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class ReadyProbe:
    async def check(self, runtime_type: str, connection: dict[str, str]) -> HealthResult:
        assert "secret" not in connection
        return HealthResult(status="ready", identity=f"{runtime_type}:test", latency_ms=4)


def test_agent_instance_is_registered_without_persisting_secret_values(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_API_KEY", "only-in-process")
    control_plane = ControlPlaneService(tmp_path / "control.sqlite", probe=ReadyProbe())
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    with TestClient(create_app(coordinator, control_plane=control_plane)) as client:
        created = client.post(
            "/v1/agent-instances",
            json={
                "name": "Hermes PM Local",
                "runtime_type": "hermes-http",
                "connection": {"endpoint": "http://127.0.0.1:9001"},
                "credential_ref": "env:HERMES_API_KEY",
                "features": ["text-final", "remote-stop"],
            },
        )
        health = client.post(f"/v1/agent-instances/{created.json()['id']}/health-check")
        listed = client.get("/v1/agent-instances")
        monkeypatch.delenv("HERMES_API_KEY")
        missing_credential = client.post(f"/v1/agent-instances/{created.json()['id']}/health-check")
        rejected_secret = client.post(
            "/v1/agent-instances",
            json={
                "name": "unsafe",
                "runtime_type": "hermes-http",
                "connection": {"api_key": "super-secret-value"},
            },
        )

    assert created.status_code == 201
    assert created.json()["credential_ref"] == "env:HERMES_API_KEY"
    assert "super-secret-value" not in control_plane.database.read_text(errors="ignore")
    assert rejected_secret.status_code == 422
    assert health.status_code == 200
    assert health.json()["health"]["status"] == "ready"
    assert missing_credential.json()["health"]["error_code"] == "CREDENTIAL_REFERENCE_MISSING"
    assert listed.json()[0]["health"]["identity"] == "hermes-http:test"


def test_agent_instance_updates_use_cas_and_disabled_instances_cannot_be_bound(
    tmp_path: Path,
) -> None:
    control_plane = ControlPlaneService(tmp_path / "control.sqlite", probe=ReadyProbe())
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    with TestClient(create_app(coordinator, control_plane=control_plane)) as client:
        instance = client.post(
            "/v1/agent-instances",
            json={
                "name": "Codex local",
                "runtime_type": "codex-cli",
                "connection": {"command": "codex"},
            },
        ).json()
        healthy = client.post(f"/v1/agent-instances/{instance['id']}/health-check").json()
        stale = client.patch(
            f"/v1/agent-instances/{instance['id']}",
            json={"expected_version": 2, "enabled": False},
        )
        disabled = client.patch(
            f"/v1/agent-instances/{instance['id']}",
            json={"expected_version": healthy["version"], "enabled": False},
        )
        binding = client.put(
            "/v1/capability-bindings/codex-backend",
            json={"instance_id": instance["id"], "expected_version": 0},
        )

    assert stale.status_code == 409
    assert healthy["version"] == instance["version"]
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["version"] == healthy["version"] + 1
    assert binding.status_code == 409


def test_healthy_capability_bindings_publish_an_immutable_journey(tmp_path: Path) -> None:
    service = ControlPlaneService(
        tmp_path / "control.sqlite",
        probe=ReadyProbe(),
        config_root=Path(__file__).parents[1] / "config",
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    definition = {
        "id": "backend-delivery",
        "version": "2.0.0",
        "steps": [
            {
                "kind": "stage",
                "id": "requirements",
                "workflow_mode": "agentscope.role-turn",
                "bindings": {"actor": "hermes-pm"},
            },
            {
                "kind": "approval_gate",
                "id": "approve-plan",
                "subject_kind": "delivery-plan",
            },
            {
                "kind": "stage",
                "id": "tasking",
                "workflow_mode": "agentscope.role-turn",
                "bindings": {"actor": "hermes-project-admin"},
            },
            {
                "kind": "stage",
                "id": "delivery",
                "workflow_mode": "code-delivery",
                "bindings": {"developer": "codex-backend"},
            },
        ],
    }
    with TestClient(create_app(coordinator, control_plane=service)) as client:
        instances = {}
        for capability, runtime in (
            ("hermes-pm", "hermes-http"),
            ("codex-backend", "codex-cli"),
        ):
            item = client.post(
                "/v1/agent-instances",
                json={
                    "name": capability,
                    "runtime_type": runtime,
                    "connection": {"endpoint": "http://127.0.0.1:9001"},
                    "features": ["text-final", "cwd-binding", "remote-stop"],
                },
            ).json()
            client.post(f"/v1/agent-instances/{item['id']}/health-check")
            instances[capability] = item["id"]
            bound = client.put(
                f"/v1/capability-bindings/{capability}",
                json={"instance_id": item["id"], "expected_version": 0},
            )
            assert bound.status_code == 200

        admin_bound = client.put(
            "/v1/capability-bindings/hermes-project-admin",
            json={"instance_id": instances["hermes-pm"], "expected_version": 0},
        )
        assert admin_bound.status_code == 200

        draft = client.post(
            "/v1/journey-drafts",
            json={"name": "Backend delivery", "definition": definition, "layout": {}},
        ).json()
        validation = client.post(f"/v1/journey-drafts/{draft['id']}/validate")
        published = client.post(f"/v1/journey-drafts/{draft['id']}/publish")
        stale = client.patch(
            f"/v1/journey-drafts/{draft['id']}",
            json={"expected_version": 99, "name": "stale"},
        )
        revision = client.get("/v1/journeys/backend-delivery/revisions/1")
        drafts = client.get("/v1/journey-drafts")
        journeys = client.get("/v1/journeys")
        bindings = client.get("/v1/capability-bindings")
        incompatible_delivery = client.post(
            "/v1/deliveries",
            json={
                "workspace_id": "backend-demo",
                "user_request": "Must use a configured runtime adapter",
                "journey_revision_id": "backend-delivery:1",
            },
        )
        executor = next(
            item
            for item in client.get("/v1/agent-instances").json()
            if item["id"] == instances["codex-backend"]
        )
        client.patch(
            f"/v1/agent-instances/{executor['id']}",
            json={"expected_version": executor["version"], "enabled": False},
        )
        blocked_delivery = client.post(
            "/v1/deliveries",
            json={
                "workspace_id": "backend-demo",
                "user_request": "Must not start",
                "journey_revision_id": "backend-delivery:1",
            },
        )

    assert validation.json()["validation_status"] == "valid"
    assert published.status_code == 201
    assert len(published.json()["fingerprint"]) == 64
    assert (
        published.json()["binding_snapshot"]["codex-backend"]["instance_id"]
        == instances["codex-backend"]
    )
    assert stale.status_code == 409
    assert revision.json() == published.json()
    assert drafts.json()[0]["id"] == draft["id"]
    assert journeys.json()[0]["journey_id"] == "backend-delivery"
    assert [item["capability_id"] for item in bindings.json()] == [
        "codex-backend",
        "hermes-pm",
        "hermes-project-admin",
    ]
    assert incompatible_delivery.status_code == 409
    assert (
        incompatible_delivery.json()["code"]
        == "DELIVERY_RUNTIME_BINDING_MISMATCH"
    )
    assert blocked_delivery.status_code == 409


def test_delivery_pins_the_requested_published_journey_revision(tmp_path: Path) -> None:
    service = ControlPlaneService(
        tmp_path / "control.sqlite",
        probe=ReadyProbe(),
        config_root=Path(__file__).parents[1] / "config",
    )
    revision = service.import_builtin_journey(
        planning_identity="deterministic-test",
        execution_identity="deterministic-test",
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    with TestClient(create_app(coordinator, control_plane=service)) as client:
        created = client.post(
            "/v1/deliveries",
            json={
                "workspace_id": "backend-demo",
                "user_request": "Add status",
                "journey_revision_id": f"{revision.journey_id}:{revision.revision}",
            },
        )
        missing = client.post(
            "/v1/deliveries",
            json={
                "workspace_id": "another-demo",
                "user_request": "Add status",
                "journey_revision_id": "missing:99",
            },
        )

    assert created.status_code == 202
    assert created.json()["journey_revision_id"] == "backend-delivery:1"
    assert (
        created.json()["journey_binding_snapshot"]["codex-backend"]["identity"]
        == "deterministic-test"
    )
    assert created.json()["resolved_journey_sha256"] == revision.fingerprint
    assert missing.status_code == 404


def test_board_commands_drive_delivery_and_knowledge_keeps_provenance(tmp_path: Path) -> None:
    service = ControlPlaneService(tmp_path / "control.sqlite")
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )
    with TestClient(create_app(coordinator, control_plane=service)) as client:
        created = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add health status"},
        ).json()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            board = client.get("/v1/board").json()
            if board and board[0]["column"] == "plan-approval":
                break
            time.sleep(0.01)
        approved = client.post(
            f"/v1/work-items/{created['id']}/command",
            json={"command": "approve-plan", "expected_version": 1},
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            board = client.get("/v1/board").json()
            if board and board[0]["column"] == "candidate-approval":
                break
            time.sleep(0.01)
        illegal = client.post(
            f"/v1/work-items/{created['id']}/command",
            json={"command": "approve-plan", "expected_version": 2},
        )
        documents = client.get("/v1/knowledge/search?q=health").json()
        duplicate_one = client.post(
            "/v1/knowledge/documents",
            json={
                "title": "Runbook",
                "media_type": "text/markdown",
                "content": "# Health runbook",
            },
        )
        duplicate_two = client.post(
            "/v1/knowledge/documents",
            json={
                "title": "Runbook copy",
                "media_type": "text/markdown",
                "content": "# Health runbook",
            },
        )

    assert approved.status_code == 202
    assert board[0]["available_commands"] == ["accept-candidate", "reject-candidate", "cancel"]
    assert illegal.status_code == 409
    assert {document["artifact_type"] for document in documents} >= {"requirement", "task"}
    assert duplicate_one.json()["id"] == duplicate_two.json()["id"]
    assert duplicate_one.json()["sha256"] == duplicate_two.json()["sha256"]


def test_board_projection_and_control_events_are_restart_safe(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite"
    service = ControlPlaneService(database)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    with TestClient(create_app(coordinator, control_plane=service)) as client:
        client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Search (health): status"},
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            first = client.get("/v1/board").json()
            if first and first[0]["column"] == "plan-approval":
                break
            time.sleep(0.01)
        second = client.get("/v1/board").json()
        malformed_search = client.get("/v1/knowledge/search?q=(health):").json()
        event_count = len(service.list_events())

    restarted = ControlPlaneService(database)
    assert second == first
    assert len(restarted.list_events()) == event_count
    assert isinstance(malformed_search, list)
