from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ...shared.errors import ProductError
from ...shared.ids import new_id
from .domain import (
    Pipeline,
    PipelineCreate,
    PipelineDraft,
    PipelineDraftPatch,
    PipelineRevision,
    PipelineWithDraft,
)
from .ports import CapabilityBindingResolver, JourneyGraphCompiler, PipelineRepository


class PipelineCatalog:
    def __init__(
        self,
        repository: PipelineRepository,
        *,
        graph_compiler: JourneyGraphCompiler | None = None,
        binding_resolver: CapabilityBindingResolver | None = None,
    ) -> None:
        self.repository = repository
        self.graph_compiler = graph_compiler
        self.binding_resolver = binding_resolver

    def create_pipeline(
        self, request: PipelineCreate, *, created_by: str
    ) -> PipelineWithDraft:
        pipeline = Pipeline(
            id=request.id,
            name=request.name,
            description=request.description,
            created_by=created_by,
        )
        draft = PipelineDraft(
            id=new_id(),
            pipeline_id=pipeline.id,
            name=request.name,
            definition=request.definition,
            layout=request.layout,
            input_schema=request.input_schema,
            created_by=created_by,
        )
        try:
            self.repository.create(pipeline, draft)
        except sqlite3.IntegrityError as error:
            raise ProductError(
                code="PIPELINE_ALREADY_EXISTS",
                title="流水线已存在",
                detail=f"流水线 {pipeline.id} 已经存在。",
                repair="更换流水线 ID，或打开现有流水线创建新草稿。",
            ) from error
        return PipelineWithDraft(pipeline=pipeline, draft=draft)

    def list_pipelines(self) -> tuple[Pipeline, ...]:
        return self.repository.list_pipelines()

    def validate_draft(self, draft_id: str, *, expected_version: int) -> PipelineDraft:
        draft = self.repository.get_draft(draft_id)
        self._require_version(draft, expected_version)
        if self.graph_compiler is None:
            raise RuntimeError("Journey Graph compiler is not configured")
        errors: tuple[str, ...] = ()
        try:
            self.graph_compiler.compile(draft.definition)
        except ValueError as error:
            errors = (str(error),)
        updated = draft.model_copy(
            update={
                "version": draft.version + 1,
                "validation_status": "invalid" if errors else "valid",
                "validation_errors": errors,
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap_draft(draft.version, updated):
            latest = self.repository.get_draft(draft_id)
            self._raise_version_conflict(expected_version, latest.version)
        return updated

    def patch_draft(self, draft_id: str, request: PipelineDraftPatch) -> PipelineDraft:
        draft = self.repository.get_draft(draft_id)
        self._require_version(draft, request.expected_version)
        changes = request.model_dump(exclude_none=True, exclude={"expected_version"})
        updated = draft.model_copy(
            update={
                **changes,
                "version": draft.version + 1,
                "validation_status": "unknown",
                "validation_errors": (),
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap_draft(draft.version, updated):
            latest = self.repository.get_draft(draft_id)
            self._raise_version_conflict(request.expected_version, latest.version)
        return updated

    def publish_draft(
        self, draft_id: str, *, expected_version: int, published_by: str
    ) -> PipelineRevision:
        draft = self.repository.get_draft(draft_id)
        self._require_version(draft, expected_version)
        if draft.validation_status != "valid":
            raise ProductError(
                code="PIPELINE_DRAFT_INVALID",
                title="流水线草稿尚未通过校验",
                detail="当前草稿不能发布为不可变版本。",
                repair="先执行 ACWM 图校验并修复全部错误。",
            )
        if self.graph_compiler is None or self.binding_resolver is None:
            raise RuntimeError("Pipeline publication adapters are not configured")
        compilation = self.graph_compiler.compile(draft.definition)
        bindings = self.binding_resolver.snapshot(compilation.capability_ids)
        return self.repository.publish(
            draft,
            compiled_graph=compilation.graph,
            binding_snapshot=bindings,
            fingerprint=compilation.fingerprint,
            published_by=published_by,
        )

    def get_revision(self, pipeline_id: str, revision: int) -> PipelineRevision:
        return self.repository.get_revision(pipeline_id, revision)

    def get_draft(self, draft_id: str) -> PipelineDraft:
        return self.repository.get_draft(draft_id)

    def activate_revision(
        self,
        pipeline_id: str,
        *,
        revision: int,
        expected_version: int,
        activated_by: str,
    ) -> Pipeline:
        pipeline = self.repository.get_pipeline(pipeline_id)
        if pipeline.version != expected_version:
            raise ProductError(
                code="PIPELINE_VERSION_CONFLICT",
                title="流水线版本冲突",
                detail="流水线已被其他操作更新。",
                repair="刷新流水线后重新激活目标版本。",
                expected_version=expected_version,
                actual_version=pipeline.version,
            )
        self.repository.get_revision(pipeline_id, revision)
        updated = pipeline.model_copy(
            update={
                "active_revision": revision,
                "version": pipeline.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap_pipeline(
            pipeline.version, updated, activated_by=activated_by
        ):
            latest = self.repository.get_pipeline(pipeline_id)
            raise ProductError(
                code="PIPELINE_VERSION_CONFLICT",
                title="流水线版本冲突",
                detail="流水线已被其他操作更新。",
                repair="刷新流水线后重新激活目标版本。",
                expected_version=expected_version,
                actual_version=latest.version,
            )
        return updated

    @staticmethod
    def _require_version(draft: PipelineDraft, expected_version: int) -> None:
        if draft.version != expected_version:
            PipelineCatalog._raise_version_conflict(expected_version, draft.version)

    @staticmethod
    def _raise_version_conflict(expected: int, actual: int) -> None:
        raise ProductError(
            code="PIPELINE_DRAFT_VERSION_CONFLICT",
            title="流水线草稿版本冲突",
            detail="当前草稿已被其他操作更新。",
            repair="刷新草稿后重新提交。",
            expected_version=expected,
            actual_version=actual,
        )
