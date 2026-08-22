from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from agent_team_os.modules.settings import (
    AppSettings,
    AppSettingsPatch,
    SettingsManager,
    create_settings_router,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.ids import new_id


class StubSettingsManager:
    def __init__(self) -> None:
        self._settings = AppSettings()

    def get(self) -> AppSettings:
        return self._settings

    def patch(self, request: AppSettingsPatch) -> AppSettings:
        if request.expected_version != self._settings.version:
            raise ProductError(
                code="SETTINGS_VERSION_CONFLICT",
                title="设置版本冲突",
                detail="设置已被其他操作更新。",
                repair="刷新设置后重新保存。",
                expected_version=request.expected_version,
                actual_version=self._settings.version,
            )
        updates = request.model_dump(exclude_none=True, exclude={"expected_version"})
        self._settings = self._settings.model_copy(
            update={**updates, "version": self._settings.version + 1}
        )
        return self._settings


def _create_test_app(manager: SettingsManager) -> FastAPI:
    app = FastAPI()
    app.include_router(create_settings_router(manager))

    @app.exception_handler(ProductError)
    async def _product_error_handler(_request, error: ProductError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error.problem(new_id()).model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    return app


@pytest.mark.anyio
async def test_get_settings_router_returns_current_settings() -> None:
    manager = StubSettingsManager()
    app = _create_test_app(manager)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/settings")

    assert response.status_code == 200
    assert response.json()["version"] == manager.get().version
    assert response.json()["planning_timeout_seconds"] == 120
    assert response.json()["execution_timeout_seconds"] == 180
    assert response.json()["verification_timeout_seconds"] == 60
    assert response.json()["evidence_retention_days"] == 7


@pytest.mark.anyio
async def test_patch_settings_router_updates_and_returns_app_settings() -> None:
    manager = StubSettingsManager()
    app = _create_test_app(manager)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            "/v1/settings",
            json={"expected_version": 1, "evidence_retention_days": 14},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 2
    assert payload["evidence_retention_days"] == 14


@pytest.mark.anyio
async def test_patch_settings_router_propagates_product_error_for_stale_expected_version() -> None:
    manager = StubSettingsManager()
    app = _create_test_app(manager)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            "/v1/settings",
            json={"expected_version": 2, "evidence_retention_days": 14},
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    payload = response.json()
    assert payload["code"] == "SETTINGS_VERSION_CONFLICT"
    assert payload["expected_version"] == 2
    assert payload["actual_version"] == 1
