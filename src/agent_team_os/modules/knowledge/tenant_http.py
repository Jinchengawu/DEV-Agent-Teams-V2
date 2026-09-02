from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from ...shared.errors import ProductError
from .domain import KnowledgeActor
from .provider_domain import ProviderNode, ProviderSpace
from .tenant_application import TenantKnowledgeManager
from .tenant_domain import (
    KnowledgeSyncJob,
    KnowledgeSyncJobRequest,
    TenantConnection,
    TenantConnectionCreate,
    TenantProviderBinding,
    TenantProviderBindingCreate,
    TenantProviderSnapshotRecord,
)


def create_tenant_knowledge_router(
    service: TenantKnowledgeManager,
    *,
    actor: Callable[[Request], KnowledgeActor],
    authorize_project_source: Callable[[Request, str, str], KnowledgeActor] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/knowledge/connections", response_model=tuple[TenantConnection, ...])
    def list_connections(request: Request) -> tuple[TenantConnection, ...]:
        return service.list_connections(actor(request))

    @router.post(
        "/v1/knowledge/connections",
        response_model=TenantConnection,
        status_code=201,
    )
    def create_connection(
        request_body: TenantConnectionCreate, request: Request
    ) -> TenantConnection:
        return service.create_connection(actor(request), request_body)

    @router.post(
        "/v1/knowledge/connections/{connection_id}/diagnose",
        response_model=TenantConnection,
    )
    def diagnose_connection(connection_id: str, request: Request) -> TenantConnection:
        return service.diagnose_connection(actor(request), connection_id)

    @router.get(
        "/v1/knowledge/connections/{connection_id}/spaces",
        response_model=tuple[ProviderSpace, ...],
    )
    def list_connection_spaces(
        connection_id: str, request: Request
    ) -> tuple[ProviderSpace, ...]:
        return service.list_connection_spaces(actor(request), connection_id)

    @router.get(
        "/v1/knowledge/provider-bindings-v2",
        response_model=tuple[TenantProviderBinding, ...],
    )
    def list_bindings(request: Request) -> tuple[TenantProviderBinding, ...]:
        return service.list_bindings(actor(request))

    @router.post(
        "/v1/knowledge/provider-bindings-v2",
        response_model=TenantProviderBinding,
        status_code=201,
    )
    def create_binding(
        request_body: TenantProviderBindingCreate, request: Request
    ) -> TenantProviderBinding:
        return service.create_binding(actor(request), request_body)

    @router.post(
        "/v1/knowledge/provider-bindings-v2/{binding_id}/diagnose",
        response_model=TenantProviderBinding,
    )
    def refresh_binding(binding_id: str, request: Request) -> TenantProviderBinding:
        return service.refresh_binding(actor(request), binding_id)

    @router.get(
        "/v1/knowledge/provider-bindings-v2/{binding_id}/nodes",
        response_model=tuple[ProviderNode, ...],
    )
    def list_binding_nodes(binding_id: str, request: Request) -> tuple[ProviderNode, ...]:
        return service.list_binding_nodes(actor(request), binding_id)

    @router.post(
        "/v1/projects/{project_id}/knowledge-sync-jobs",
        response_model=KnowledgeSyncJob,
        status_code=202,
    )
    def request_sync(
        project_id: str,
        request_body: KnowledgeSyncJobRequest,
        request: Request,
    ) -> KnowledgeSyncJob:
        if authorize_project_source is None:
            raise _project_authorization_unavailable()
        project_actor = authorize_project_source(request, project_id, request_body.binding_id)
        return service.request_sync(project_actor, project_id, request_body)

    @router.get(
        "/v1/projects/{project_id}/knowledge-sync-jobs",
        response_model=tuple[KnowledgeSyncJob, ...],
    )
    def list_sync_jobs(
        project_id: str,
        binding_id: str,
        request: Request,
    ) -> tuple[KnowledgeSyncJob, ...]:
        if authorize_project_source is None:
            raise _project_authorization_unavailable()
        project_actor = authorize_project_source(request, project_id, binding_id)
        return service.list_project_sync_jobs(project_actor, project_id, binding_id)

    @router.get(
        "/v1/projects/{project_id}/knowledge-snapshots",
        response_model=tuple[TenantProviderSnapshotRecord, ...],
    )
    def list_snapshots(
        project_id: str,
        binding_id: str,
        request: Request,
    ) -> tuple[TenantProviderSnapshotRecord, ...]:
        if authorize_project_source is None:
            raise _project_authorization_unavailable()
        project_actor = authorize_project_source(request, project_id, binding_id)
        return service.list_project_snapshots(project_actor, binding_id)

    @router.get(
        "/v1/projects/{project_id}/knowledge-bindings/{binding_id}/nodes",
        response_model=tuple[ProviderNode, ...],
    )
    def list_project_binding_nodes(
        project_id: str,
        binding_id: str,
        request: Request,
    ) -> tuple[ProviderNode, ...]:
        if authorize_project_source is None:
            raise _project_authorization_unavailable()
        project_actor = authorize_project_source(request, project_id, binding_id)
        return service.list_project_binding_nodes(project_actor, binding_id)

    @router.get(
        "/v1/projects/{project_id}/knowledge-sync-jobs/{job_id}",
        response_model=KnowledgeSyncJob,
    )
    def get_sync_job(project_id: str, job_id: str, request: Request) -> KnowledgeSyncJob:
        job = service.get_sync_job(job_id)
        if job.project_id != project_id:
            raise ProductError(
                code="KNOWLEDGE_SYNC_JOB_NOT_FOUND",
                title="知识同步任务不存在",
                detail="指定任务不属于当前项目。",
                repair="刷新当前项目的同步任务列表。",
                status_code=404,
            )
        if authorize_project_source is None:
            raise _project_authorization_unavailable()
        authorize_project_source(request, project_id, job.binding_id)
        return job

    return router


def _project_authorization_unavailable() -> ProductError:
    return ProductError(
        code="PROJECT_GOVERNANCE_UNAVAILABLE",
        title="项目知识授权未配置",
        detail="知识同步必须经过 Project Access Policy 与 Approved Source Scope。",
        repair="启用 Project Catalog 后重试。",
        status_code=503,
    )
