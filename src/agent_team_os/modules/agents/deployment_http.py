from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from .deployment_application import AgentDeploymentCatalog
from .deployment_domain import (
    AgentDeployment,
    AgentDeploymentCreate,
    AgentDeploymentPatch,
    AgentDeploymentVersionRequest,
    ProviderManifestView,
)
from .provider_manifests import ProviderManifestCatalog


def create_agent_deployment_router(
    deployments: AgentDeploymentCatalog,
    providers: ProviderManifestCatalog,
    *,
    actor_id: Callable[[Request], str],
    authorize_manage: Callable[[Request], None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/provider-manifests", response_model=list[ProviderManifestView])
    def list_provider_manifests() -> tuple[ProviderManifestView, ...]:
        return providers.list()

    @router.get("/v1/provider-manifests/{provider_id}", response_model=ProviderManifestView)
    def get_provider_manifest(provider_id: str) -> ProviderManifestView:
        try:
            return providers.view(provider_id)
        except KeyError:
            from ...shared.errors import ProductError

            raise ProductError(
                code="PROVIDER_MANIFEST_NOT_FOUND",
                title="Provider Manifest 不存在",
                detail=f"没有找到 Provider {provider_id}。",
                repair="刷新 Provider 列表后重新选择。",
                status_code=404,
            ) from None

    @router.post("/v1/agent-deployments", response_model=AgentDeployment, status_code=201)
    def create_deployment(body: AgentDeploymentCreate, request: Request) -> AgentDeployment:
        authorize_manage(request)
        return deployments.create(body, actor_id=actor_id(request))

    @router.get("/v1/agent-deployments", response_model=list[AgentDeployment])
    def list_deployments() -> tuple[AgentDeployment, ...]:
        return deployments.list()

    @router.get("/v1/agent-deployments/{deployment_id}", response_model=AgentDeployment)
    def get_deployment(deployment_id: str) -> AgentDeployment:
        return deployments.get(deployment_id)

    @router.patch("/v1/agent-deployments/{deployment_id}", response_model=AgentDeployment)
    def patch_deployment(
        deployment_id: str, body: AgentDeploymentPatch, request: Request
    ) -> AgentDeployment:
        authorize_manage(request)
        return deployments.patch(deployment_id, body)

    @router.post(
        "/v1/agent-deployments/{deployment_id}/qualify",
        response_model=AgentDeployment,
    )
    def qualify_deployment(
        deployment_id: str, body: AgentDeploymentVersionRequest, request: Request
    ) -> AgentDeployment:
        authorize_manage(request)
        return deployments.qualify(deployment_id, body.expected_version)

    @router.post(
        "/v1/agent-deployments/{deployment_id}/enable",
        response_model=AgentDeployment,
    )
    def enable_deployment(
        deployment_id: str, body: AgentDeploymentVersionRequest, request: Request
    ) -> AgentDeployment:
        authorize_manage(request)
        return deployments.set_enabled(deployment_id, body.expected_version, True)

    @router.post(
        "/v1/agent-deployments/{deployment_id}/disable",
        response_model=AgentDeployment,
    )
    def disable_deployment(
        deployment_id: str, body: AgentDeploymentVersionRequest, request: Request
    ) -> AgentDeployment:
        authorize_manage(request)
        return deployments.set_enabled(deployment_id, body.expected_version, False)

    return router
