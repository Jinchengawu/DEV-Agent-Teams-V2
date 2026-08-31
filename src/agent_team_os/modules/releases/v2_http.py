from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request

from .v2_application import ExternalForwardReleaseCoordinator
from .v2_domain import ExternalReleaseView, ReleaseHealthV2, ReleaseManifestV2


def create_external_release_router(
    coordinator: ExternalForwardReleaseCoordinator,
    *,
    authorize_apply: Callable[[Request], None] | None = None,
    after_resume: Callable[[str], Awaitable[None]] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/releases/{delivery_id}/resume-forward",
        response_model=ReleaseManifestV2,
    )
    async def resume_forward(delivery_id: str, request: Request) -> ReleaseManifestV2:
        if authorize_apply is not None:
            authorize_apply(request)
        manifest = coordinator.resume_forward(delivery_id)
        if after_resume is not None:
            await after_resume(delivery_id)
        return manifest

    @router.get(
        "/v1/projects/{project_id}/release-health",
        response_model=ReleaseHealthV2,
    )
    def get_release_health(project_id: str) -> ReleaseHealthV2:
        return coordinator.health(project_id)

    @router.get(
        "/v1/releases/{delivery_id}",
        response_model=ExternalReleaseView,
    )
    def get_external_release(delivery_id: str) -> ExternalReleaseView:
        return coordinator.details(delivery_id)

    return router
