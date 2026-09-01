from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, status

from .application import TeamTemplateCatalog
from .domain import (
    TeamTemplate,
    TeamTemplateCreate,
    TeamTemplateDraft,
    TeamTemplateDraftPatch,
    TeamTemplateRevision,
    TeamTemplateVersionRequest,
    TeamTemplateWithDraft,
)


def create_team_template_router(
    catalog: TeamTemplateCatalog,
    *,
    actor_id: Callable[[Request], str],
    authorize_edit: Callable[[Request], None] | None = None,
    authorize_publish: Callable[[Request], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/team-templates",
        response_model=TeamTemplateWithDraft,
        status_code=status.HTTP_201_CREATED,
    )
    def create_team_template(
        body: TeamTemplateCreate,
        request: Request,
    ) -> TeamTemplateWithDraft:
        if authorize_edit is not None:
            authorize_edit(request)
        return catalog.create(body, actor_id=actor_id(request))

    @router.get("/v1/team-templates", response_model=list[TeamTemplate])
    def list_team_templates() -> tuple[TeamTemplate, ...]:
        return catalog.list()

    @router.get(
        "/v1/team-template-drafts/{draft_id}",
        response_model=TeamTemplateDraft,
    )
    def get_team_template_draft(draft_id: str) -> TeamTemplateDraft:
        try:
            return catalog.get_draft(draft_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="team template draft not found") from error

    @router.get(
        "/v1/team-templates/{template_id}/drafts",
        response_model=list[TeamTemplateDraft],
    )
    def list_team_template_drafts(template_id: str) -> tuple[TeamTemplateDraft, ...]:
        try:
            return catalog.list_drafts(template_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="team template not found") from error

    @router.patch(
        "/v1/team-template-drafts/{draft_id}",
        response_model=TeamTemplateDraft,
    )
    def patch_team_template_draft(
        draft_id: str,
        body: TeamTemplateDraftPatch,
        request: Request,
    ) -> TeamTemplateDraft:
        if authorize_edit is not None:
            authorize_edit(request)
        try:
            return catalog.patch(draft_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="team template draft not found") from error

    @router.post(
        "/v1/team-template-drafts/{draft_id}/validate",
        response_model=TeamTemplateDraft,
    )
    def validate_team_template_draft(
        draft_id: str,
        body: TeamTemplateVersionRequest,
        request: Request,
    ) -> TeamTemplateDraft:
        if authorize_edit is not None:
            authorize_edit(request)
        try:
            return catalog.validate(draft_id, expected_version=body.expected_version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="team template draft not found") from error

    @router.post(
        "/v1/team-template-drafts/{draft_id}/publish",
        response_model=TeamTemplateRevision,
        status_code=status.HTTP_201_CREATED,
    )
    def publish_team_template_draft(
        draft_id: str,
        body: TeamTemplateVersionRequest,
        request: Request,
    ) -> TeamTemplateRevision:
        if authorize_publish is not None:
            authorize_publish(request)
        try:
            return catalog.publish(
                draft_id,
                expected_version=body.expected_version,
                actor_id=actor_id(request),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="team template draft not found") from error

    @router.get(
        "/v1/team-templates/{template_id}/revisions/{revision}",
        response_model=TeamTemplateRevision,
    )
    def get_team_template_revision(
        template_id: str,
        revision: int,
    ) -> TeamTemplateRevision:
        try:
            return catalog.get_revision(template_id, revision)
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="team template revision not found",
            ) from error

    return router
