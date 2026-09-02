from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from .domain import KnowledgeActor
from .index_application import KnowledgeIndexManager
from .index_domain import (
    EmbeddingQualificationRequest,
    EmbeddingQualificationSnapshot,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexCatalog,
    KnowledgeIndexProfileCreate,
    KnowledgeIndexProfileRevision,
    KnowledgeIndexRevision,
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
    ProjectKnowledgeRetrievalOption,
    RetrievalEvaluationPolicyCreate,
    RetrievalEvaluationPolicyRevision,
    RetrievalEvaluationReport,
    RetrievalEvaluationRunRequest,
    RetrievalPolicyCreate,
    RetrievalPolicyRevision,
)


class KnowledgeIndexActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_pointer_version: int | None = Field(default=None, ge=1)


class ProjectKnowledgeRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_binding_id: str = Field(min_length=1, max_length=180)
    retrieval_policy_revision_id: str = Field(min_length=1, max_length=180)
    query: str = Field(min_length=1, max_length=10_000)


def create_knowledge_index_router(
    service: KnowledgeIndexManager,
    *,
    actor: Callable[[Request], KnowledgeActor],
    authorize_project_retrieval: Callable[
        [Request, str, str], tuple[KnowledgeActor, tuple[str, ...]]
    ],
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/v1/knowledge/index-catalog",
        response_model=KnowledgeIndexCatalog,
    )
    def get_catalog(request: Request) -> KnowledgeIndexCatalog:
        return service.catalog(actor(request))

    @router.post(
        "/v1/knowledge/index-profiles",
        response_model=KnowledgeIndexProfileRevision,
        status_code=201,
    )
    def publish_profile(
        body: KnowledgeIndexProfileCreate, request: Request
    ) -> KnowledgeIndexProfileRevision:
        return service.publish_index_profile(actor(request), body)

    @router.post(
        "/v1/knowledge/embedding-qualifications",
        response_model=EmbeddingQualificationSnapshot,
        status_code=201,
    )
    def qualify_embedding(
        body: EmbeddingQualificationRequest, request: Request
    ) -> EmbeddingQualificationSnapshot:
        return service.qualify_embedding(actor(request), body)

    @router.post(
        "/v1/knowledge/retrieval-policies",
        response_model=RetrievalPolicyRevision,
        status_code=201,
    )
    def publish_policy(body: RetrievalPolicyCreate, request: Request) -> RetrievalPolicyRevision:
        return service.publish_retrieval_policy(actor(request), body)

    @router.post(
        "/v1/knowledge/retrieval-evaluation-policies",
        response_model=RetrievalEvaluationPolicyRevision,
        status_code=201,
    )
    def publish_evaluation_policy(
        body: RetrievalEvaluationPolicyCreate, request: Request
    ) -> RetrievalEvaluationPolicyRevision:
        return service.publish_evaluation_policy(actor(request), body)

    @router.post(
        "/v1/knowledge/index-revisions",
        response_model=KnowledgeIndexRevision,
        status_code=201,
    )
    def build_index(body: KnowledgeIndexBuildRequest, request: Request) -> KnowledgeIndexRevision:
        return service.build(actor(request), body)

    @router.post(
        "/v1/knowledge/retrieval-evaluation-runs",
        response_model=RetrievalEvaluationReport,
        status_code=201,
    )
    def evaluate_retrieval(
        body: RetrievalEvaluationRunRequest, request: Request
    ) -> RetrievalEvaluationReport:
        return service.evaluate(actor(request), body)

    @router.post(
        "/v1/knowledge/index-revisions/{revision_id}/activate",
        response_model=KnowledgeIndexRevision,
    )
    def activate_index(
        revision_id: str,
        body: KnowledgeIndexActivationRequest,
        request: Request,
    ) -> KnowledgeIndexRevision:
        return service.activate(
            actor(request),
            revision_id,
            expected_pointer_version=body.expected_pointer_version,
        )

    @router.post(
        "/v1/projects/{project_id}/knowledge-retrieval-preview",
        response_model=KnowledgeRetrievalResult,
    )
    def retrieve(
        project_id: str,
        body: ProjectKnowledgeRetrievalRequest,
        request: Request,
    ) -> KnowledgeRetrievalResult:
        project_actor, allowed_source_ids = authorize_project_retrieval(
            request, project_id, body.provider_binding_id
        )
        return service.retrieve(
            project_actor,
            KnowledgeRetrievalRequest(
                project_id=project_id,
                provider_binding_id=body.provider_binding_id,
                retrieval_policy_revision_id=body.retrieval_policy_revision_id,
                query=body.query,
                allowed_source_ids=allowed_source_ids,
            ),
        )

    @router.get(
        "/v1/projects/{project_id}/knowledge-retrieval-options",
        response_model=tuple[ProjectKnowledgeRetrievalOption, ...],
    )
    def retrieval_options(
        project_id: str,
        provider_binding_id: str,
        request: Request,
    ) -> tuple[ProjectKnowledgeRetrievalOption, ...]:
        project_actor, _allowed_source_ids = authorize_project_retrieval(
            request, project_id, provider_binding_id
        )
        return service.project_retrieval_options(project_actor, provider_binding_id)

    return router
