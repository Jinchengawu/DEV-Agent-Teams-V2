from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from .application import ProjectCatalog
from .domain import (
    Project,
    ProjectBindingUpdate,
    ProjectCreate,
    ProjectDeploymentAccess,
    ProjectDeploymentUpdate,
    ProjectDetail,
    ProjectKnowledgeSource,
    ProjectKnowledgeSourceUpdate,
    ProjectPatch,
    ProjectPipelineBinding,
)


class ProjectVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


def create_project_router(
    catalog: ProjectCatalog,
    *,
    actor_id: Callable[[Request], str],
    authorize_manage: Callable[[Request], None],
    after_create: Callable[[ProjectDetail], None] | None = None,
    after_archive: Callable[[Project], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/projects", response_model=tuple[Project, ...])
    def list_projects() -> tuple[Project, ...]:
        return catalog.list()

    @router.post("/v1/projects", response_model=ProjectDetail, status_code=201)
    def create_project(body: ProjectCreate, request: Request) -> ProjectDetail:
        authorize_manage(request)
        created = catalog.create(body, actor_id(request))
        if after_create is not None:
            after_create(created)
        return created

    @router.get("/v1/projects/{project_id}", response_model=ProjectDetail)
    def get_project(project_id: str) -> ProjectDetail:
        return catalog.get(project_id)

    @router.patch("/v1/projects/{project_id}", response_model=Project)
    def patch_project(project_id: str, body: ProjectPatch, request: Request) -> Project:
        authorize_manage(request)
        return catalog.patch(project_id, body)

    @router.post("/v1/projects/{project_id}/archive", response_model=Project)
    def archive_project(project_id: str, body: ProjectVersionRequest, request: Request) -> Project:
        authorize_manage(request)
        archived = catalog.archive(project_id, body.expected_version)
        if after_archive is not None:
            after_archive(archived)
        return archived

    @router.post("/v1/projects/{project_id}/workspace/retry", response_model=ProjectDetail)
    def retry_workspace(project_id: str, request: Request) -> ProjectDetail:
        authorize_manage(request)
        return catalog.retry_workspace(project_id)

    @router.post("/v1/projects/{project_id}/workspace/reset")
    def reset_workspace(project_id: str, request: Request) -> dict[str, str]:
        authorize_manage(request)
        return {"main_revision": catalog.reset_workspace(project_id)}

    @router.get(
        "/v1/projects/{project_id}/pipeline-bindings",
        response_model=tuple[ProjectPipelineBinding, ...],
    )
    def list_pipeline_bindings(project_id: str) -> tuple[ProjectPipelineBinding, ...]:
        return catalog.get(project_id).pipeline_bindings

    @router.put(
        "/v1/projects/{project_id}/pipeline-bindings", response_model=ProjectPipelineBinding
    )
    def put_pipeline_binding(
        project_id: str, body: ProjectBindingUpdate, request: Request
    ) -> ProjectPipelineBinding:
        authorize_manage(request)
        return catalog.put_pipeline_binding(project_id, body)

    @router.get(
        "/v1/projects/{project_id}/deployment-access",
        response_model=tuple[ProjectDeploymentAccess, ...],
    )
    def list_deployment_access(project_id: str) -> tuple[ProjectDeploymentAccess, ...]:
        return catalog.get(project_id).deployment_access

    @router.put(
        "/v1/projects/{project_id}/deployment-access", response_model=ProjectDeploymentAccess
    )
    def put_deployment_access(
        project_id: str, body: ProjectDeploymentUpdate, request: Request
    ) -> ProjectDeploymentAccess:
        authorize_manage(request)
        return catalog.put_deployment_access(project_id, body)

    @router.get(
        "/v1/projects/{project_id}/knowledge-sources",
        response_model=tuple[ProjectKnowledgeSource, ...],
    )
    def list_knowledge_sources(project_id: str) -> tuple[ProjectKnowledgeSource, ...]:
        return catalog.get(project_id).knowledge_sources

    @router.put(
        "/v1/projects/{project_id}/knowledge-sources",
        response_model=ProjectKnowledgeSource,
    )
    def put_knowledge_source(
        project_id: str, body: ProjectKnowledgeSourceUpdate, request: Request
    ) -> ProjectKnowledgeSource:
        authorize_manage(request)
        return catalog.put_knowledge_source(project_id, body)

    return router
