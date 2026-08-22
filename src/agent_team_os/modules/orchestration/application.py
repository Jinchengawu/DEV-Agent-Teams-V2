from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ...shared.errors import ProductError
from ...shared.events import ProductEvent
from ...shared.ids import new_id
from .domain import (
    Pipeline,
    PipelineCreate,
    PipelineDraft,
    PipelineDraftPatch,
    PipelineRevision,
    PipelineRunRecord,
    PipelineWithDraft,
)
from .ports import (
    CapabilityBindingResolver,
    JourneyGraphCompiler,
    PipelineGraphRuntime,
    PipelineRepository,
    PipelineRunRepository,
)

_COMMAND_EVENT_SUFFIX = {
    "start": "started",
    "succeed": "succeeded",
    "start-loop-iteration": "loop-iteration-started",
    "complete-loop-iteration": "loop-iteration-completed",
    "start-loop-body-node": "loop-body-node-started",
    "succeed-loop-body-node": "loop-body-node-succeeded",
}


class PipelineRunLedger:
    def __init__(
        self, repository: PipelineRunRepository, runtime: PipelineGraphRuntime
    ) -> None:
        self.repository = repository
        self.runtime = runtime

    def start(
        self, *, delivery_id: str, revision: PipelineRevision, run_id: str | None = None
    ) -> PipelineRunRecord:
        resolved_run_id = run_id or new_id()
        snapshot = self.runtime.create(resolved_run_id, revision.compiled_graph)
        run = PipelineRunRecord(
            id=resolved_run_id,
            delivery_id=delivery_id,
            pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
            graph_fingerprint=revision.fingerprint,
            status=self._status(snapshot),
            version=self._version(snapshot),
            snapshot=snapshot,
        )
        self.repository.create(
            run,
            self._event(run, "pipeline-run.created", {"delivery_id": delivery_id}),
        )
        return run

    def get(self, run_id: str) -> PipelineRunRecord:
        return self.repository.get(run_id)

    def get_for_delivery(self, delivery_id: str) -> PipelineRunRecord:
        return self.repository.get_for_delivery(delivery_id)

    def transition(
        self,
        run_id: str,
        *,
        command: str,
        node_id: str,
        expected_version: int,
        body_node_id: str | None = None,
        activated_conditions: tuple[str, ...] = (),
        exit_condition_met: bool | None = None,
    ) -> PipelineRunRecord:
        current = self.repository.get(run_id)
        if current.version != expected_version:
            self._raise_version_conflict(expected_version, current.version)
        snapshot = self.runtime.transition(
            current.snapshot,
            command=command,
            node_id=node_id,
            body_node_id=body_node_id,
            activated_conditions=activated_conditions,
            exit_condition_met=exit_condition_met,
        )
        updated = current.model_copy(
            update={
                "status": self._status(snapshot),
                "version": self._version(snapshot),
                "snapshot": snapshot,
                "updated_at": datetime.now(UTC),
            }
        )
        event_type = f"pipeline-node.{_COMMAND_EVENT_SUFFIX.get(command, command)}"
        if not self.repository.compare_and_swap(
            current.version,
            updated,
            self._event(
                updated,
                event_type,
                {
                    "node_id": node_id,
                    **(
                        {"body_node_id": body_node_id}
                        if body_node_id is not None
                        else {}
                    ),
                },
            ),
        ):
            latest = self.repository.get(run_id)
            self._raise_version_conflict(expected_version, latest.version)
        return updated

    @staticmethod
    def _event(
        run: PipelineRunRecord, event_type: str, payload: dict[str, object]
    ) -> ProductEvent:
        return ProductEvent(
            event_type=event_type,
            aggregate_type="pipeline-run",
            aggregate_id=run.id,
            aggregate_version=run.version,
            payload=payload,
        )

    @staticmethod
    def _status(snapshot: dict[str, object]) -> str:
        value = snapshot.get("status")
        if not isinstance(value, str) or not value:
            raise ValueError("ACWM GraphRun snapshot is missing status")
        return value

    @staticmethod
    def _version(snapshot: dict[str, object]) -> int:
        value = snapshot.get("version")
        if not isinstance(value, int) or value < 1:
            raise ValueError("ACWM GraphRun snapshot is missing version")
        return value

    @staticmethod
    def _raise_version_conflict(expected: int, actual: int) -> None:
        raise ProductError(
            code="PIPELINE_RUN_VERSION_CONFLICT",
            title="流水线运行版本冲突",
            detail="节点运行状态已被其他执行器更新。",
            repair="刷新运行快照后重新提交节点转换。",
            expected_version=expected,
            actual_version=actual,
        )


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

    def resolve_revision(self, reference: str) -> PipelineRevision:
        pipeline_id, separator, raw_revision = reference.rpartition(":")
        if not separator or not pipeline_id:
            raise ProductError(
                code="PIPELINE_REVISION_REFERENCE_INVALID",
                title="流水线版本引用无效",
                detail="流水线版本必须使用 pipeline-id:revision 格式。",
                repair="从已发布版本列表重新选择流水线版本。",
            )
        try:
            revision = int(raw_revision)
        except ValueError as error:
            raise ProductError(
                code="PIPELINE_REVISION_REFERENCE_INVALID",
                title="流水线版本引用无效",
                detail="流水线版本号必须是正整数。",
                repair="从已发布版本列表重新选择流水线版本。",
            ) from error
        if revision < 1:
            raise ProductError(
                code="PIPELINE_REVISION_REFERENCE_INVALID",
                title="流水线版本引用无效",
                detail="流水线版本号必须是正整数。",
                repair="从已发布版本列表重新选择流水线版本。",
            )
        try:
            return self.repository.get_revision(pipeline_id, revision)
        except KeyError as error:
            raise ProductError(
                code="PIPELINE_REVISION_NOT_FOUND",
                title="流水线版本不存在",
                detail=f"没有找到已发布版本 {reference}。",
                repair="刷新流水线目录并选择仍然存在的不可变版本。",
            ) from error

    def get_draft(self, draft_id: str) -> PipelineDraft:
        return self.repository.get_draft(draft_id)

    def list_drafts(self, pipeline_id: str) -> tuple[PipelineDraft, ...]:
        self.repository.get_pipeline(pipeline_id)
        return self.repository.list_drafts(pipeline_id)

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
