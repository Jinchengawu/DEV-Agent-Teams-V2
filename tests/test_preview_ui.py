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


def test_preview_fallback_explains_how_to_build_the_chinese_console() -> None:
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
    assert "前端控制台尚未构建" in response.text
    assert "打开接口文档" in response.text
    assert "Control Plane" not in response.text
    assert "SYSTEM ONLINE" not in response.text
