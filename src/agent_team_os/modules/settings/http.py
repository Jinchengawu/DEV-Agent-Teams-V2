from __future__ import annotations

from fastapi import APIRouter

from .application import SettingsManager
from .domain import AppSettings, AppSettingsPatch


def create_settings_router(manager: SettingsManager) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/settings", response_model=AppSettings)
    def get_settings() -> AppSettings:
        return manager.get()

    @router.patch("/v1/settings", response_model=AppSettings)
    def patch_settings(request: AppSettingsPatch) -> AppSettings:
        return manager.patch(request)

    return router
