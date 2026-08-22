from __future__ import annotations

from typing import Protocol

from .provider_domain import ProviderActor, ProviderNode, ProviderSnapshot, ProviderSpace


class KnowledgeProvider(Protocol):
    """External collaboration source; never a Delivery Evidence repository."""

    def list_spaces(self, actor: ProviderActor) -> tuple[ProviderSpace, ...]: ...

    def list_nodes(
        self, actor: ProviderActor, external_space_id: str
    ) -> tuple[ProviderNode, ...]: ...

    def fetch_snapshot(self, actor: ProviderActor, source_id: str) -> ProviderSnapshot: ...

