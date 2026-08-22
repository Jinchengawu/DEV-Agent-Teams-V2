from __future__ import annotations

from typing import Protocol

from .domain import GraphCompilation, Pipeline, PipelineDraft, PipelineRevision


class JourneyGraphCompiler(Protocol):
    def compile(self, definition: dict[str, object]) -> GraphCompilation: ...


class CapabilityBindingResolver(Protocol):
    def snapshot(
        self, capability_ids: tuple[str, ...]
    ) -> dict[str, dict[str, object]]: ...


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
