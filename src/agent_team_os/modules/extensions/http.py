from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from .application import RuntimeExtensionCatalog
from .domain import (
    RuntimeExtension,
    RuntimeExtensionInstall,
    RuntimeExtensionVersionRequest,
)


def create_runtime_extension_router(
    catalog: RuntimeExtensionCatalog,
    *,
    actor_id: Callable[[Request], str],
    authorize_manage: Callable[[Request], None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/runtime-extensions", response_model=list[RuntimeExtension])
    def list_extensions() -> tuple[RuntimeExtension, ...]:
        return catalog.list()

    @router.get("/v1/runtime-extensions/{extension_id}", response_model=RuntimeExtension)
    def get_extension(extension_id: str) -> RuntimeExtension:
        return catalog.get(extension_id)

    @router.post("/v1/runtime-extensions", response_model=RuntimeExtension, status_code=201)
    def install_extension(body: RuntimeExtensionInstall, request: Request) -> RuntimeExtension:
        authorize_manage(request)
        return catalog.install(body, actor_id=actor_id(request))

    @router.post(
        "/v1/runtime-extensions/{extension_id}/qualify",
        response_model=RuntimeExtension,
    )
    def qualify_extension(
        extension_id: str,
        body: RuntimeExtensionVersionRequest,
        request: Request,
    ) -> RuntimeExtension:
        authorize_manage(request)
        return catalog.qualify(extension_id, expected_version=body.expected_version)

    return router
