from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request, status

from ...shared.verification import VerificationProfileLike
from .domain import (
    ProjectWorkcellTopology,
    TeamActivationRequest,
    WorkspaceBinding,
    WorkspaceBindingAssignment,
    WorkspaceBindingCreate,
    WorkspaceBindingVerificationRequest,
    WorkspaceVerificationProfileRequest,
)
from .project_application import ProjectWorkcellGovernance


def create_project_workcell_router(
    governance: ProjectWorkcellGovernance,
    *,
    authorize_read: Callable[[Request, str], None] | None = None,
    authorize_manage: Callable[[Request, str], None] | None = None,
    authorize_workspace_manage: Callable[[Request, str], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/verification-profiles", response_model=tuple[VerificationProfileLike, ...])
    def list_verification_profiles(
        project_id: str, request: Request
    ) -> tuple[VerificationProfileLike, ...]:
        if authorize_read is not None:
            authorize_read(request, project_id)
        return governance.verification_profiles.list()

    @router.put(
        "/v1/workspace-bindings/{workspace_id}/verification-profile",
        response_model=WorkspaceBinding,
    )
    def set_verification_profile(
        workspace_id: str, body: WorkspaceVerificationProfileRequest, request: Request
    ) -> WorkspaceBinding:
        if authorize_workspace_manage is not None:
            authorize_workspace_manage(request, workspace_id)
        return governance.set_verification_profile(
            workspace_id,
            expected_version=body.expected_version,
            profile_id=body.verification_profile_id,
        )

    @router.post(
        "/v1/workspace-bindings/{workspace_id}/verification-profile/qualify",
        response_model=WorkspaceBinding,
    )
    def qualify_verification_profile(
        workspace_id: str, body: WorkspaceBindingVerificationRequest, request: Request
    ) -> WorkspaceBinding:
        if authorize_workspace_manage is not None:
            authorize_workspace_manage(request, workspace_id)
        return governance.qualify_verification_profile(
            workspace_id, expected_version=body.expected_version
        )

    @router.get(
        "/v1/projects/{project_id}/workcells",
        response_model=ProjectWorkcellTopology,
    )
    def get_project_workcells(project_id: str, request: Request) -> ProjectWorkcellTopology:
        if authorize_read is not None:
            authorize_read(request, project_id)
        return governance.topology(project_id)

    @router.post(
        "/v1/projects/{project_id}/workspace-bindings",
        response_model=WorkspaceBindingAssignment,
        status_code=status.HTTP_201_CREATED,
    )
    def create_workspace_binding(
        project_id: str,
        body: WorkspaceBindingCreate,
        request: Request,
    ) -> WorkspaceBindingAssignment:
        if authorize_manage is not None:
            authorize_manage(request, project_id)
        return governance.create_workspace_binding(project_id, body)

    @router.post(
        "/v1/workspace-bindings/{workspace_id}/verify",
        response_model=WorkspaceBinding,
    )
    def verify_workspace_binding(
        workspace_id: str,
        body: WorkspaceBindingVerificationRequest,
        request: Request,
    ) -> WorkspaceBinding:
        if authorize_workspace_manage is not None:
            authorize_workspace_manage(request, workspace_id)
        return governance.verify_workspace(
            workspace_id,
            expected_version=body.expected_version,
        )

    @router.post(
        "/v1/projects/{project_id}/team-activate",
        response_model=ProjectWorkcellTopology,
    )
    def activate_project_team(
        project_id: str,
        body: TeamActivationRequest,
        request: Request,
    ) -> ProjectWorkcellTopology:
        if authorize_manage is not None:
            authorize_manage(request, project_id)
        return governance.activate(project_id, expected_version=body.expected_version)

    return router
