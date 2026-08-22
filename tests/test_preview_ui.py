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


def test_built_console_supports_deep_links_without_masking_api_404(tmp_path) -> None:
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
    )
    distribution = tmp_path / "dist"
    distribution.mkdir()
    (distribution / "index.html").write_text(
        "<!doctype html><title>Agent-Team-OS</title>", encoding="utf-8"
    )
    app = create_app(coordinator)
    install_preview_ui(app, distribution)

    with TestClient(app) as client:
        deep_link = client.get("/knowledge")
        missing_api = client.get("/v1/does-not-exist")

    assert deep_link.status_code == 200
    assert "Agent-Team-OS" in deep_link.text
    assert missing_api.status_code == 404
    assert missing_api.headers["content-type"].startswith("application/problem+json")
