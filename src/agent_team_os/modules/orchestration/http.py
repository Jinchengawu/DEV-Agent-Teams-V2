from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .application import PipelineCatalog
from .domain import (
    Pipeline,
    PipelineCreate,
    PipelineDraft,
    PipelineDraftPatch,
    PipelineRevision,
    PipelineWithDraft,
)


class DraftVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class PipelineActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    expected_version: int = Field(ge=1)


def create_pipeline_router(
    catalog: PipelineCatalog,
    *,
    actor_id: Callable[[Request], str],
    authorize_edit: Callable[[Request], None] | None = None,
    authorize_publish: Callable[[Request], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/pipelines", response_model=PipelineWithDraft, status_code=201)
    def create_pipeline(request_body: PipelineCreate, request: Request) -> PipelineWithDraft:
        if authorize_edit is not None:
            authorize_edit(request)
        return catalog.create_pipeline(request_body, created_by=actor_id(request))

    @router.get("/v1/pipelines", response_model=list[Pipeline])
    def list_pipelines() -> tuple[Pipeline, ...]:
        return catalog.list_pipelines()

    @router.get("/v1/pipeline-drafts/{draft_id}", response_model=PipelineDraft)
    def get_pipeline_draft(draft_id: str) -> PipelineDraft:
        try:
            return catalog.get_draft(draft_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline draft not found") from error

    @router.patch("/v1/pipeline-drafts/{draft_id}", response_model=PipelineDraft)
    def patch_pipeline_draft(
        draft_id: str, request_body: PipelineDraftPatch, request: Request
    ) -> PipelineDraft:
        if authorize_edit is not None:
            authorize_edit(request)
        try:
            return catalog.patch_draft(draft_id, request_body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline draft not found") from error

    @router.post("/v1/pipeline-drafts/{draft_id}/validate", response_model=PipelineDraft)
    def validate_pipeline_draft(
        draft_id: str, request_body: DraftVersionRequest, request: Request
    ) -> PipelineDraft:
        if authorize_edit is not None:
            authorize_edit(request)
        try:
            return catalog.validate_draft(
                draft_id, expected_version=request_body.expected_version
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline draft not found") from error

    @router.post(
        "/v1/pipeline-drafts/{draft_id}/publish",
        response_model=PipelineRevision,
        status_code=status.HTTP_201_CREATED,
    )
    def publish_pipeline_draft(
        draft_id: str, request_body: DraftVersionRequest, request: Request
    ) -> PipelineRevision:
        if authorize_publish is not None:
            authorize_publish(request)
        try:
            return catalog.publish_draft(
                draft_id,
                expected_version=request_body.expected_version,
                published_by=actor_id(request),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline draft not found") from error

    @router.get(
        "/v1/pipelines/{pipeline_id}/revisions/{revision}",
        response_model=PipelineRevision,
    )
    def get_pipeline_revision(pipeline_id: str, revision: int) -> PipelineRevision:
        try:
            return catalog.get_revision(pipeline_id, revision)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline revision not found") from error

    @router.post("/v1/pipelines/{pipeline_id}/activate", response_model=Pipeline)
    def activate_pipeline_revision(
        pipeline_id: str, request_body: PipelineActivationRequest, request: Request
    ) -> Pipeline:
        if authorize_publish is not None:
            authorize_publish(request)
        try:
            return catalog.activate_revision(
                pipeline_id,
                revision=request_body.revision,
                expected_version=request_body.expected_version,
                activated_by=actor_id(request),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="pipeline revision not found") from error

    return router
