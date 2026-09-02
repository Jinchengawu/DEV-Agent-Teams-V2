from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .application import ProjectCatalog
from .domain import (
    Project,
    ProjectAccessActor,
    ProjectAccessAudit,
    ProjectBindingUpdate,
    ProjectCapability,
    ProjectCreate,
    ProjectDeploymentAccess,
    ProjectDeploymentUpdate,
    ProjectDetail,
    ProjectKnowledgeSource,
    ProjectKnowledgeSourceApproval,
    ProjectKnowledgeSourceApprovalUpdate,
    ProjectKnowledgeSourceUpdate,
    ProjectMembership,
    ProjectMembershipUpdate,
    ProjectPatch,
    ProjectPipelineBinding,
    ProjectRepository,
)


class ProjectVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


def create_project_router(
    catalog: ProjectCatalog,
    *,
    actor_id: Callable[[Request], str],
    access_actor: Callable[[Request], ProjectAccessActor | None],
    authorize_manage: Callable[[Request], None],
    after_create: Callable[[ProjectDetail], None] | None = None,
    after_archive: Callable[[Project], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def authorize_project(
        request: Request,
        project_id: str,
        capability: ProjectCapability,
        *,
        resource: str,
        reason: str,
    ) -> None:
        catalog.authorize(
            access_actor(request),
            project_id,
            capability,
            resource=resource,
            reason=reason,
        )

    @router.get("/v1/projects", response_model=tuple[Project, ...])
    def list_projects(request: Request) -> tuple[Project, ...]:
        return catalog.list_for(access_actor(request))

    @router.post("/v1/projects", response_model=ProjectDetail, status_code=201)
    def create_project(body: ProjectCreate, request: Request) -> ProjectDetail:
        authorize_manage(request)
        created = catalog.create(body, actor_id(request))
        if after_create is not None:
            after_create(created)
        return created

    @router.get("/v1/projects/{project_id}", response_model=ProjectDetail)
    def get_project(project_id: str, request: Request) -> ProjectDetail:
        return catalog.get_for(access_actor(request), project_id)

    @router.patch("/v1/projects/{project_id}", response_model=Project)
    def patch_project(project_id: str, body: ProjectPatch, request: Request) -> Project:
        authorize_project(
            request,
            project_id,
            ProjectCapability.EDIT,
            resource=f"project:{project_id}",
            reason="patch project",
        )
        return catalog.patch(project_id, body)

    @router.post("/v1/projects/{project_id}/archive", response_model=Project)
    def archive_project(project_id: str, body: ProjectVersionRequest, request: Request) -> Project:
        authorize_project(
            request,
            project_id,
            ProjectCapability.EDIT,
            resource=f"project:{project_id}",
            reason="archive project",
        )
        archived = catalog.archive(project_id, body.expected_version)
        if after_archive is not None:
            after_archive(archived)
        return archived

    @router.post("/v1/projects/{project_id}/workspace/retry", response_model=ProjectDetail)
    def retry_workspace(project_id: str, request: Request) -> ProjectDetail:
        authorize_project(
            request,
            project_id,
            ProjectCapability.EDIT,
            resource=f"project:{project_id}:workspace",
            reason="retry project workspace",
        )
        return catalog.retry_workspace(project_id)

    @router.post("/v1/projects/{project_id}/workspace/reset")
    def reset_workspace(project_id: str, request: Request) -> dict[str, str]:
        authorize_project(
            request,
            project_id,
            ProjectCapability.EDIT,
            resource=f"project:{project_id}:workspace",
            reason="reset project workspace",
        )
        return {"main_revision": catalog.reset_workspace(project_id)}

    @router.get(
        "/v1/projects/{project_id}/repositories",
        response_model=tuple[ProjectRepository, ...],
    )
    def list_repositories(project_id: str, request: Request) -> tuple[ProjectRepository, ...]:
        return catalog.get_for(access_actor(request), project_id).repositories

    @router.post(
        "/v1/projects/{project_id}/repositories/provision-fullstack",
        response_model=ProjectDetail,
    )
    def provision_fullstack(project_id: str, request: Request) -> ProjectDetail:
        authorize_project(
            request,
            project_id,
            ProjectCapability.EDIT,
            resource=f"project:{project_id}:repositories",
            reason="provision project repositories",
        )
        return catalog.provision_fullstack(project_id)

    @router.get(
        "/v1/projects/{project_id}/pipeline-bindings",
        response_model=tuple[ProjectPipelineBinding, ...],
    )
    def list_pipeline_bindings(
        project_id: str, request: Request
    ) -> tuple[ProjectPipelineBinding, ...]:
        return catalog.get_for(access_actor(request), project_id).pipeline_bindings

    @router.put(
        "/v1/projects/{project_id}/pipeline-bindings", response_model=ProjectPipelineBinding
    )
    def put_pipeline_binding(
        project_id: str, body: ProjectBindingUpdate, request: Request
    ) -> ProjectPipelineBinding:
        authorize_project(
            request,
            project_id,
            ProjectCapability.EDIT,
            resource=f"project:{project_id}:pipeline-bindings",
            reason="put project pipeline binding",
        )
        return catalog.put_pipeline_binding(project_id, body)

    @router.get(
        "/v1/projects/{project_id}/deployment-access",
        response_model=tuple[ProjectDeploymentAccess, ...],
    )
    def list_deployment_access(
        project_id: str, request: Request
    ) -> tuple[ProjectDeploymentAccess, ...]:
        return catalog.get_for(access_actor(request), project_id).deployment_access

    @router.put(
        "/v1/projects/{project_id}/deployment-access", response_model=ProjectDeploymentAccess
    )
    def put_deployment_access(
        project_id: str, body: ProjectDeploymentUpdate, request: Request
    ) -> ProjectDeploymentAccess:
        authorize_project(
            request,
            project_id,
            ProjectCapability.EDIT,
            resource=f"project:{project_id}:deployment-access",
            reason="put project deployment access",
        )
        return catalog.put_deployment_access(project_id, body)

    @router.get(
        "/v1/projects/{project_id}/knowledge-sources",
        response_model=tuple[ProjectKnowledgeSource, ...],
    )
    def list_knowledge_sources(
        project_id: str, request: Request
    ) -> tuple[ProjectKnowledgeSource, ...]:
        return catalog.get_for(access_actor(request), project_id).knowledge_sources

    @router.get(
        "/v1/projects/{project_id}/memberships",
        response_model=tuple[ProjectMembership, ...],
    )
    def list_memberships(project_id: str, request: Request) -> tuple[ProjectMembership, ...]:
        authorize_project(
            request,
            project_id,
            ProjectCapability.READ,
            resource=f"project:{project_id}:memberships",
            reason="list project memberships",
        )
        return catalog.list_memberships(project_id)

    @router.get(
        "/v1/projects/{project_id}/access-audits",
        response_model=tuple[ProjectAccessAudit, ...],
    )
    def list_access_audits(project_id: str, request: Request) -> tuple[ProjectAccessAudit, ...]:
        authorize_project(
            request,
            project_id,
            ProjectCapability.MEMBERSHIP_MANAGE,
            resource=f"project:{project_id}:access-audits",
            reason="list project access audits",
        )
        return catalog.list_access_audits(project_id)

    @router.put(
        "/v1/projects/{project_id}/memberships/{user_id}",
        response_model=ProjectMembership,
    )
    def put_membership(
        project_id: str,
        user_id: str,
        body: ProjectMembershipUpdate,
        request: Request,
    ) -> ProjectMembership:
        authorize_project(
            request,
            project_id,
            ProjectCapability.MEMBERSHIP_MANAGE,
            resource=f"project:{project_id}:membership:{user_id}",
            reason="put project membership",
        )
        return catalog.put_membership(project_id, user_id, body)

    @router.delete(
        "/v1/projects/{project_id}/memberships/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_membership(
        project_id: str,
        user_id: str,
        body: ProjectVersionRequest,
        request: Request,
    ) -> Response:
        authorize_project(
            request,
            project_id,
            ProjectCapability.MEMBERSHIP_MANAGE,
            resource=f"project:{project_id}:membership:{user_id}",
            reason="delete project membership",
        )
        catalog.delete_membership(project_id, user_id, body.expected_version)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.put(
        "/v1/projects/{project_id}/knowledge-sources",
        response_model=ProjectKnowledgeSource,
    )
    def put_knowledge_source(
        project_id: str, body: ProjectKnowledgeSourceUpdate, request: Request
    ) -> ProjectKnowledgeSource:
        authorize_project(
            request,
            project_id,
            ProjectCapability.SOURCE_MANAGE,
            resource=f"project:{project_id}:knowledge-source:{body.binding_id}",
            reason="put project knowledge source",
        )
        return catalog.put_knowledge_source(project_id, body)

    @router.get(
        "/v1/projects/{project_id}/knowledge-source-approvals",
        response_model=tuple[ProjectKnowledgeSourceApproval, ...],
    )
    def list_knowledge_source_approvals(
        project_id: str, request: Request
    ) -> tuple[ProjectKnowledgeSourceApproval, ...]:
        authorize_project(
            request,
            project_id,
            ProjectCapability.READ,
            resource=f"project:{project_id}:knowledge-source-approvals",
            reason="list project knowledge source approvals",
        )
        return catalog.get(project_id).knowledge_source_approvals

    @router.put(
        "/v1/projects/{project_id}/knowledge-source-approvals/{binding_id}",
        response_model=ProjectKnowledgeSourceApproval,
    )
    def put_knowledge_source_approval(
        project_id: str,
        binding_id: str,
        body: ProjectKnowledgeSourceApprovalUpdate,
        request: Request,
    ) -> ProjectKnowledgeSourceApproval:
        authorize_project(
            request,
            project_id,
            ProjectCapability.SOURCE_APPROVE,
            resource=f"project:{project_id}:knowledge-source-approval:{binding_id}",
            reason="approve project knowledge source",
        )
        return catalog.put_knowledge_source_approval(
            project_id,
            binding_id,
            body,
            actor_id(request),
        )

    return router
