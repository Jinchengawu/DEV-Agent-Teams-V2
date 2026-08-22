from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from .application import SettingsManager
from .domain import AppSettings, AppSettingsPatch


def create_settings_router(
    manager: SettingsManager,
    authorize_mutation: Callable[[Request], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/settings", response_model=AppSettings)
    def get_settings() -> AppSettings:
        return manager.get()

    @router.patch("/v1/settings", response_model=AppSettings)
    def patch_settings(request_body: AppSettingsPatch, request: Request) -> AppSettings:
        if authorize_mutation is not None:
            authorize_mutation(request)
        return manager.patch(request_body)

    return router
