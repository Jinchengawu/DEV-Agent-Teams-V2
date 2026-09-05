"""读取当前 Workcell 已登记的输出；不能由任意 Store SHA 获得读取权限。"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from ...shared.errors import ProductError
from ..artifacts import ArtifactReference, ArtifactStorageError
from .execution_application import WorkcellExecutionModule

MAX_ARTIFACT_PREVIEW_BYTES = 1024 * 1024


class WorkcellArtifactPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: ArtifactReference
    content: str


def read_workcell_artifact(
    execution: WorkcellExecutionModule, *, delivery_id: str, run_id: str, sha256: str
) -> WorkcellArtifactPreview:
    try:
        tree = execution.tree(run_id)
    except ProductError as error:
        if error.status_code == 404:
            raise _unavailable() from error
        raise
    if tree.workcell_run.delivery_id != delivery_id:
        raise _unavailable()
    references = [
        envelope.reference
        for agent in tree.agent_runs
        for envelope in agent.artifact_envelopes
        if envelope.reference is not None
    ]
    references.extend(review.artifact_reference for review in tree.reviews)
    if tree.result is not None:
        references.extend(tree.result.output_artifact_references)
    reference = next((item for item in references if item.sha256 == sha256), None)
    if reference is None:
        raise _unavailable()
    json_media = reference.media_type == "application/json" or (
        reference.media_type.startswith("application/") and reference.media_type.endswith("+json")
    )
    if not json_media and reference.media_type != "text/plain":
        raise ProductError(
            code="WORKCELL_ARTIFACT_PREVIEW_UNSUPPORTED", title="该产物不支持正文预览",
            detail="只允许 JSON 或纯文本产物。", repair="通过产物合同的专用界面查看。",
            status_code=415,
        )
    try:
        payload = execution.artifact_storage.get_bytes(
            reference, max_bytes=MAX_ARTIFACT_PREVIEW_BYTES
        )
        content = payload.decode("utf-8")
        if json_media:
            json.loads(content)
    except (ArtifactStorageError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductError(
            code=getattr(error, "code", "ARTIFACT_TEXT_INVALID"),
            title="产物正文无法验证", detail="登记内容的大小、编码或 Hash 不一致。",
            repair="保留当前证据并检查产物存储；不能据此批准交付。", status_code=409,
        ) from error
    return WorkcellArtifactPreview(reference=reference, content=content)


def _unavailable() -> ProductError:
    return ProductError(
        code="WORKCELL_ARTIFACT_NOT_FOUND", title="产物不存在",
        detail="当前交付与 Workcell 没有登记该输出产物。",
        repair="刷新当前 Workcell 证据列表。", status_code=404,
    )
