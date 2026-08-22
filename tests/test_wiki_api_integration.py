from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
    UserCreate,
)
from agent_team_os.modules.knowledge import SQLiteWikiRepository, WikiService
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService

ORIGIN = "http://test"
ADMIN_PASSWORD = "secure-admin-2026"
VIEWER_PASSWORD = "secure-viewer-2026"


def _app(database: Path) -> FastAPI:
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    administrator = identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    identity.create_user(
        administrator,
        UserCreate(
            username="viewer",
            display_name="只读审计员",
            role="viewer",
            password=VIEWER_PASSWORD,
        ),
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )
    return create_app(
        coordinator,
        identity=identity,
        knowledge=WikiService(SQLiteWikiRepository(database)),
    )


async def _login(client: AsyncClient, username: str, password: str) -> None:
    response = await client.post(
        "/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def _mutation_headers(client: AsyncClient) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
    }


@pytest.mark.anyio
async def test_wiki_routes_use_session_rbac_and_real_repository(tmp_path: Path) -> None:
    app = _app(tmp_path / "wiki-api.sqlite")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as anonymous:
        response = await anonymous.get("/v1/wiki/spaces")
        assert response.status_code == 401

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as administrator:
        await _login(administrator, "admin", ADMIN_PASSWORD)
        space = await administrator.post(
            "/v1/wiki/spaces",
            headers=_mutation_headers(administrator),
            json={"name": "交付手册", "description": "可追溯的交付知识"},
        )
        assert space.status_code == 201
        document = await administrator.post(
            "/v1/wiki/documents",
            headers=_mutation_headers(administrator),
            json={
                "space_id": space.json()["id"],
                "title": "候选版本审查",
                "content": {"text": "只接受已验证证据"},
            },
        )
        assert document.status_code == 201

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as viewer:
        await _login(viewer, "viewer", VIEWER_PASSWORD)
        listed = await viewer.get("/v1/wiki/documents")
        assert listed.status_code == 200
        assert listed.json()[0]["title"] == "候选版本审查"

        denied = await viewer.patch(
            f"/v1/wiki/documents/{document.json()['id']}",
            headers=_mutation_headers(viewer),
            json={"expected_version": 1, "title": "越权修改"},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "IDENTITY_PERMISSION_DENIED"
