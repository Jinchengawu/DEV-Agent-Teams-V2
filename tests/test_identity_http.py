from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
    UserCreate,
    create_identity_router,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.ids import new_id
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService

ORIGIN = "http://test"
ADMIN_PASSWORD = "secure-admin-2026"
VIEWER_PASSWORD = "secure-viewer-2026"


def _app(database: Path) -> FastAPI:
    root = Path(__file__).parents[1]
    MigrationRunner(database, root / "migrations").migrate()
    app = FastAPI()
    app.include_router(
        create_identity_router(IdentityService(SQLiteIdentityRepository(database)))
    )

    @app.exception_handler(ProductError)
    async def product_error_handler(_request: Request, error: ProductError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error.problem(new_id()).model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    return app


@pytest.mark.anyio
async def test_bootstrap_login_session_and_secret_storage(tmp_path: Path) -> None:
    database = tmp_path / "identity.sqlite"
    app = _app(database)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        status = await client.get("/v1/auth/bootstrap-status")
        assert status.json() == {"bootstrap_required": True}

        bootstrap = await client.post(
            "/v1/auth/bootstrap",
            headers={"Origin": ORIGIN},
            json={
                "username": "admin",
                "display_name": "系统管理员",
                "password": ADMIN_PASSWORD,
            },
        )
        assert bootstrap.status_code == 201
        assert "password" not in bootstrap.text

        login = await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        assert login.status_code == 200
        assert login.json()["role"] == "administrator"
        assert "agent_team_os_session" in client.cookies
        assert "agent_team_os_csrf" in client.cookies

        session = await client.get("/v1/auth/session")
        assert session.status_code == 200
        assert session.json()["username"] == "admin"

    with sqlite3.connect(database) as connection:
        password_hash = str(connection.execute("SELECT password_hash FROM users").fetchone()[0])
        bearer_hash, csrf_hash = connection.execute(
            "SELECT bearer_hash,csrf_hash FROM sessions"
        ).fetchone()
    assert ADMIN_PASSWORD not in password_hash
    assert password_hash.startswith("scrypt$")
    assert len(bearer_hash) == 64
    assert len(csrf_hash) == 64


@pytest.mark.anyio
async def test_admin_user_management_requires_csrf_and_role(tmp_path: Path) -> None:
    database = tmp_path / "identity.sqlite"
    app = _app(database)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as admin:
        await admin.post(
            "/v1/auth/bootstrap",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        await admin.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        missing_csrf = await admin.post(
            "/v1/users",
            headers={"Origin": ORIGIN},
            json={
                "username": "viewer",
                "display_name": "审计访问者",
                "role": "viewer",
                "password": VIEWER_PASSWORD,
            },
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["code"] == "IDENTITY_CSRF_REJECTED"

        created = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "viewer",
                "display_name": "审计访问者",
                "role": "viewer",
                "password": VIEWER_PASSWORD,
            },
        )
        assert created.status_code == 201

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as viewer:
        await viewer.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "viewer", "password": VIEWER_PASSWORD},
        )
        denied = await viewer.get("/v1/users")
        assert denied.status_code == 403
        assert denied.json()["code"] == "IDENTITY_PERMISSION_DENIED"


@pytest.mark.anyio
async def test_origin_and_last_administrator_guards(tmp_path: Path) -> None:
    database = tmp_path / "identity.sqlite"
    app = _app(database)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        rejected = await client.post(
            "/v1/auth/bootstrap",
            headers={"Origin": "http://evil.invalid"},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        assert rejected.status_code == 403
        assert rejected.json()["code"] == "IDENTITY_ORIGIN_REJECTED"

        bootstrap = await client.post(
            "/v1/auth/bootstrap",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        demote = await client.patch(
            f"/v1/users/{bootstrap.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={"expected_version": 1, "role": "viewer"},
        )
        assert demote.status_code == 409
        assert demote.json()["code"] == "IDENTITY_LAST_ADMIN_REQUIRED"


@pytest.mark.anyio
async def test_product_api_requires_session_csrf_and_permission(tmp_path: Path) -> None:
    database = tmp_path / "identity.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    admin_user = identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    identity.create_user(
        admin_user,
        UserCreate(
            username="viewer",
            display_name="只读访问者",
            role="viewer",
            password=VIEWER_PASSWORD,
        ),
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )
    app = create_app(coordinator, identity=identity)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        anonymous = await client.get("/v1/deliveries")
        assert anonymous.status_code == 401

        await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        missing_csrf = await client.post(
            "/v1/deliveries",
            headers={"Origin": ORIGIN},
            json={"workspace_id": "backend-demo", "user_request": "增加健康检查"},
        )
        assert missing_csrf.status_code == 403
        created = await client.post(
            "/v1/deliveries",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={"workspace_id": "backend-demo", "user_request": "增加健康检查"},
        )
        assert created.status_code == 202

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN
    ) as viewer:
        await viewer.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "viewer", "password": VIEWER_PASSWORD},
        )
        denied = await viewer.post(
            "/v1/deliveries",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": viewer.cookies["agent_team_os_csrf"],
            },
            json={"workspace_id": "backend-demo", "user_request": "越权交付"},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "IDENTITY_PERMISSION_DENIED"
