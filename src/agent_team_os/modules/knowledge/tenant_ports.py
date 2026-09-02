from __future__ import annotations

from typing import Protocol

from .provider_domain import ProviderNode, ProviderSnapshot, ProviderSpace
from .tenant_domain import TenantConnection


class TenantKnowledgeProvider(Protocol):
    def list_spaces(self) -> tuple[ProviderSpace, ...]: ...
    def list_nodes(self, external_space_id: str) -> tuple[ProviderNode, ...]: ...
    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot: ...


class TenantKnowledgeProviderResolver(Protocol):
    def resolve(self, connection: TenantConnection) -> TenantKnowledgeProvider: ...
