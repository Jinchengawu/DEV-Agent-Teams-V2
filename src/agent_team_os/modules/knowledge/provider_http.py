from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from .domain import KnowledgeActor
from .provider_domain import (
    ProviderBinding,
    ProviderBindingCreate,
    ProviderNode,
    ProviderSyncResult,
)


class ProviderSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=480)


class ProviderKnowledgeService(Protocol):
    def create_binding(
        self, actor: KnowledgeActor, request: ProviderBindingCreate
    ) -> ProviderBinding: ...

    def list_bindings(self, actor: KnowledgeActor) -> tuple[ProviderBinding, ...]: ...

    def list_nodes(
        self, actor: KnowledgeActor, binding_id: str
    ) -> tuple[ProviderNode, ...]: ...

    def sync(
        self, actor: KnowledgeActor, binding_id: str, source_id: str
    ) -> ProviderSyncResult: ...


def create_provider_knowledge_router(
    service: ProviderKnowledgeService,
    read_actor: Callable[[Request], KnowledgeActor],
    mutation_actor: Callable[[Request], KnowledgeActor],
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/v1/knowledge/provider-bindings",
        response_model=tuple[ProviderBinding, ...],
    )
    def list_bindings(request: Request) -> tuple[ProviderBinding, ...]:
        return service.list_bindings(read_actor(request))

    @router.post(
        "/v1/knowledge/provider-bindings",
        response_model=ProviderBinding,
        status_code=201,
    )
    def create_binding(
        request_body: ProviderBindingCreate, request: Request
    ) -> ProviderBinding:
        return service.create_binding(mutation_actor(request), request_body)

    @router.get(
        "/v1/knowledge/provider-bindings/{binding_id}/nodes",
        response_model=tuple[ProviderNode, ...],
    )
    def list_nodes(binding_id: str, request: Request) -> tuple[ProviderNode, ...]:
        return service.list_nodes(read_actor(request), binding_id)

    @router.post(
        "/v1/knowledge/provider-bindings/{binding_id}/sync",
        response_model=ProviderSyncResult,
    )
    def sync_binding(
        binding_id: str, request_body: ProviderSyncRequest, request: Request
    ) -> ProviderSyncResult:
        return service.sync(
            mutation_actor(request), binding_id, request_body.source_id
        )

    return router
