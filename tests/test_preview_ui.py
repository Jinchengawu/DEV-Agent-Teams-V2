from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.testing import (
    DeterministicCandidateApplier,
    DeterministicCandidateVerifier,
    DeterministicCodeExecutor,
    DeterministicPlanningService,
)
from agent_team_os.ui import install_preview_ui


def test_preview_home_exposes_the_delivery_control_surface() -> None:
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        verifier=DeterministicCandidateVerifier(),
        applier=DeterministicCandidateApplier(),
    )
    app = create_app(coordinator)
    install_preview_ui(app)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Agent-Team-OS" in response.text
    assert "提交 Backend 需求" in response.text
    assert "等待创建 Delivery" in response.text
    assert "E2E Gate PASS" not in response.text

