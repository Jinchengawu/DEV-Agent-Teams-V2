"""公开检索预检仅使用临时 ASGI 与确定性夹具；不调用真实 Provider。"""

import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from test_knowledge_hybrid_index import ADMIN_PASSWORD
from test_knowledge_query_integration import _active

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["missing_qualification", "over_budget"])
async def test_public_preflight_is_safe_422_without_provider_or_run(tmp_path, failure, caplog):
    _, embedding, indexes, request, identity, projects, tenant = _active(
        tmp_path, include_services=True
    )
    sentinel = "CONFIDENTIAL_QUERY_SENTINEL"
    if failure == "missing_qualification":
        indexes.query_measurement = None
    app = create_app(
        DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            resolved_journey_sha256="a" * 64,
        ),
        identity=identity,
        projects=projects,
        tenant_knowledge=tenant,
        knowledge_indexes=indexes,
    )
    with sqlite3.connect(indexes.repository.database) as connection:
        before = connection.execute("SELECT count(*) FROM knowledge_retrieval_runs").fetchone()[0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/auth/login",
            headers={"Origin": "http://test"},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        response = await client.post(
            "/v1/projects/rag-project/knowledge-retrieval-preview",
            headers={"Origin": "http://test", "X-CSRF-Token": client.cookies["agent_team_os_csrf"]},
            json={
                "provider_binding_id": request.provider_binding_id,
                "retrieval_policy_revision_id": request.retrieval_policy_revision_id,
                "query": sentinel if failure == "missing_qualification" else sentinel * 100,
            },
        )
    assert response.status_code == 422
    assert sentinel not in response.text
    assert "Frontend 和 Backend" not in response.text
    assert embedding.calls == []
    payload = response.json()
    assert payload["code"] == (
        "KNOWLEDGE_QUERY_INPUT_UNQUALIFIED"
        if failure == "missing_qualification"
        else "KNOWLEDGE_QUERY_BUDGET_EXCEEDED"
    )
    assert set(payload.get("context", {})) == {"measured_bytes", "allowed_bytes"}
    assert all(isinstance(value, int) or value is None for value in payload["context"].values())
    assert sentinel not in caplog.text
    with sqlite3.connect(indexes.repository.database) as connection:
        assert sentinel not in "\n".join(connection.iterdump())
        assert (
            connection.execute("SELECT count(*) FROM knowledge_retrieval_runs").fetchone()[0]
            == before
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM knowledge_context_preparation_runs"
            ).fetchone()[0]
            == 0
        )
