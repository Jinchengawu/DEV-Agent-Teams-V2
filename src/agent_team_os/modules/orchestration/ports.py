from __future__ import annotations

from typing import Protocol

from ...shared.events import ProductEvent
from .domain import (
    GraphCompilation,
    Pipeline,
    PipelineDraft,
    PipelineRevision,
    PipelineRunRecord,
)


class JourneyGraphCompiler(Protocol):
    def compile(self, definition: dict[str, object]) -> GraphCompilation: ...


class CapabilityBindingResolver(Protocol):
    def snapshot(
        self, capability_ids: tuple[str, ...]
    ) -> dict[str, dict[str, object]]: ...


class PipelineGraphRuntime(Protocol):
    def create(
        self, run_id: str, compiled_graph: dict[str, object]
    ) -> dict[str, object]: ...

    def transition(
        self,
        snapshot: dict[str, object],
        *,
        command: str,
        node_id: str,
        activated_conditions: tuple[str, ...] = (),
        exit_condition_met: bool | None = None,
    ) -> dict[str, object]: ...


class PipelineRunRepository(Protocol):
    def create(self, run: PipelineRunRecord, event: ProductEvent) -> None: ...

    def get(self, run_id: str) -> PipelineRunRecord: ...

    def get_for_delivery(self, delivery_id: str) -> PipelineRunRecord: ...

    def compare_and_swap(
        self, expected_version: int, run: PipelineRunRecord, event: ProductEvent
    ) -> bool: ...

    def list_events(self, run_id: str) -> tuple[ProductEvent, ...]: ...


class PipelineRepository(Protocol):
    def create(self, pipeline: Pipeline, draft: PipelineDraft) -> None: ...

    def list_pipelines(self) -> tuple[Pipeline, ...]: ...

    def get_pipeline(self, pipeline_id: str) -> Pipeline: ...

    def get_draft(self, draft_id: str) -> PipelineDraft: ...

    def list_drafts(self, pipeline_id: str) -> tuple[PipelineDraft, ...]: ...

    def compare_and_swap_draft(
        self, expected_version: int, updated: PipelineDraft
    ) -> bool: ...

    def publish(
        self,
        draft: PipelineDraft,
        *,
        compiled_graph: dict[str, object],
        binding_snapshot: dict[str, dict[str, object]],
        fingerprint: str,
        published_by: str,
    ) -> PipelineRevision: ...

    def get_revision(self, pipeline_id: str, revision: int) -> PipelineRevision: ...

    def compare_and_swap_pipeline(
        self, expected_version: int, updated: Pipeline, *, activated_by: str
    ) -> bool: ...
