from __future__ import annotations

from typing import Protocol

from .domain import KnowledgeActor
from .provider_domain import (
    ProviderActor,
    ProviderBinding,
    ProviderNode,
    ProviderSnapshot,
    ProviderSnapshotRecord,
    ProviderSpace,
    ProviderSyncRun,
)


class ProviderFailure(RuntimeError):
    def __init__(self, code: str, detail: str, *, unavailable: bool = False) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.unavailable = unavailable


class KnowledgeProvider(Protocol):
    """External collaboration source; never a Delivery Evidence repository."""

    def list_spaces(self, actor: ProviderActor) -> tuple[ProviderSpace, ...]: ...

    def list_nodes(
        self, actor: ProviderActor, external_space_id: str
    ) -> tuple[ProviderNode, ...]: ...

    def fetch_snapshot(self, actor: ProviderActor, source_id: str) -> ProviderSnapshot: ...


class KnowledgeProviderResolver(Protocol):
    def resolve(self, binding: ProviderBinding) -> KnowledgeProvider: ...


class ProviderActorResolver(Protocol):
    def resolve(
        self, binding: ProviderBinding, actor: KnowledgeActor
    ) -> ProviderActor: ...


class ProviderKnowledgeRepository(Protocol):
    def create_binding(self, binding: ProviderBinding) -> ProviderBinding | None: ...

    def get_binding(self, binding_id: str) -> ProviderBinding | None: ...

    def list_bindings(self) -> tuple[ProviderBinding, ...]: ...

    def begin_sync(self, run: ProviderSyncRun) -> None: ...

    def complete_sync(
        self, run: ProviderSyncRun, snapshot: ProviderSnapshotRecord
    ) -> ProviderSnapshotRecord | None: ...

    def fail_sync(self, run: ProviderSyncRun) -> None: ...

    def get_snapshot(self, snapshot_id: str) -> ProviderSnapshotRecord | None: ...

    def list_sync_runs(self, binding_id: str) -> tuple[ProviderSyncRun, ...]: ...
