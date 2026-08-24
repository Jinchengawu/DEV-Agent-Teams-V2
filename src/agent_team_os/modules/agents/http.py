from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Request

from .application import AgentProfileCatalog
from .domain import (
    AgentProfile,
    AgentProfileCreate,
    AgentProfileDraft,
    AgentProfileDraftPatch,
    AgentProfileRevision,
    AgentProfileVersionRequest,
    AgentProfileWithDraft,
    AgentSpecExport,
    AgentSpecImportRequest,
)


def create_agent_profile_router(
    catalog: AgentProfileCatalog,
    *,
    actor_id: Callable[[Request], str],
    authorize_edit: Callable[[Request], None],
    authorize_publish: Callable[[Request], None],
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/agent-profiles", response_model=AgentProfileWithDraft, status_code=201)
    def create_profile(body: AgentProfileCreate, request: Request) -> AgentProfileWithDraft:
        authorize_edit(request)
        return catalog.create(body, actor_id=actor_id(request))

    @router.get("/v1/agent-profiles", response_model=list[AgentProfile])
    def list_profiles() -> tuple[AgentProfile, ...]:
        return catalog.list_profiles()

    @router.get("/v1/agent-profiles/{profile_id}/draft", response_model=AgentProfileDraft)
    def get_draft(profile_id: str) -> AgentProfileDraft:
        return catalog.get_draft(profile_id)

    @router.patch("/v1/agent-profiles/{profile_id}/draft", response_model=AgentProfileDraft)
    def patch_draft(
        profile_id: str, body: AgentProfileDraftPatch, request: Request
    ) -> AgentProfileDraft:
        authorize_edit(request)
        return catalog.patch_draft(profile_id, body, actor_id=actor_id(request))

    @router.post("/v1/agent-profiles/{profile_id}/validate", response_model=AgentProfileDraft)
    def validate_draft(
        profile_id: str, body: AgentProfileVersionRequest, request: Request
    ) -> AgentProfileDraft:
        authorize_edit(request)
        return catalog.validate_draft(
            profile_id,
            expected_version=body.expected_version,
            actor_id=actor_id(request),
        )

    @router.post(
        "/v1/agent-profiles/{profile_id}/publish",
        response_model=AgentProfileRevision,
        status_code=201,
    )
    def publish(
        profile_id: str, body: AgentProfileVersionRequest, request: Request
    ) -> AgentProfileRevision:
        authorize_publish(request)
        return catalog.publish(
            profile_id,
            expected_version=body.expected_version,
            actor_id=actor_id(request),
        )

    @router.get(
        "/v1/agent-profiles/{profile_id}/revisions",
        response_model=list[AgentProfileRevision],
    )
    def list_revisions(profile_id: str) -> tuple[AgentProfileRevision, ...]:
        return catalog.list_revisions(profile_id)

    @router.get(
        "/v1/agent-profiles/{profile_id}/revisions/{revision}",
        response_model=AgentProfileRevision,
    )
    def get_revision(profile_id: str, revision: int) -> AgentProfileRevision:
        return catalog.get_revision(profile_id, revision)

    @router.post("/v1/agent-spec/import", response_model=AgentProfileWithDraft, status_code=201)
    def import_spec(body: AgentSpecImportRequest, request: Request) -> AgentProfileWithDraft:
        authorize_edit(request)
        return catalog.import_spec(body, actor_id=actor_id(request))

    @router.get(
        "/v1/agent-profiles/{profile_id}/revisions/{revision}/export",
        response_model=AgentSpecExport,
    )
    def export_revision(
        profile_id: str, revision: int, format: Literal["json", "yaml"] = "json"
    ) -> AgentSpecExport:
        return catalog.export_revision(profile_id, revision, format=format)

    return router
