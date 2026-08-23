from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

import yaml
from pydantic import ValidationError

from ...shared.errors import ProductError
from ...shared.hashes import sha256_bytes
from .domain import (
    AgentProfile,
    AgentProfileCreate,
    AgentProfileDraft,
    AgentProfileDraftPatch,
    AgentProfileRevision,
    AgentProfileSpec,
    AgentProfileWithDraft,
    AgentSpecExport,
    AgentSpecImportRequest,
)
from .repository import SQLiteAgentProfileRepository


class AgentProfileCatalog:
    def __init__(self, repository: SQLiteAgentProfileRepository) -> None:
        self.repository = repository

    def create(self, request: AgentProfileCreate, *, actor_id: str) -> AgentProfileWithDraft:
        now = datetime.now(UTC)
        spec = request.spec
        profile = AgentProfile(
            id=spec.id,
            name=spec.name,
            description=spec.description,
            tags=spec.tags,
            latest_revision=None,
            version=1,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        draft = AgentProfileDraft(
            profile_id=spec.id,
            spec=spec,
            version=1,
            updated_by=actor_id,
            updated_at=now,
        )
        try:
            self.repository.create(profile, draft)
        except Exception as error:
            if "UNIQUE constraint failed" not in str(error):
                raise
            raise ProductError(
                code="AGENT_PROFILE_ALREADY_EXISTS",
                title="智能体角色已存在",
                detail=f"角色 {spec.id} 已经存在。",
                repair="更换角色 ID，或编辑现有角色草稿。",
            ) from error
        return AgentProfileWithDraft(profile=profile, draft=draft)

    def import_spec(
        self, request: AgentSpecImportRequest, *, actor_id: str
    ) -> AgentProfileWithDraft:
        try:
            raw = (
                json.loads(request.content)
                if request.format == "json"
                else yaml.safe_load(request.content)
            )
            spec = AgentProfileSpec.model_validate(raw)
        except (json.JSONDecodeError, yaml.YAMLError, ValidationError) as error:
            raise ProductError(
                code="AGENT_SPEC_IMPORT_INVALID",
                title="AgentSpec 导入失败",
                detail="导入内容不是有效的 AgentProfileSpec v1。",
                repair="检查 JSON/YAML 语法、字段名称和策略引用后重试。",
                status_code=422,
            ) from error
        return self.create(AgentProfileCreate(spec=spec), actor_id=actor_id)

    def list_profiles(self) -> tuple[AgentProfile, ...]:
        return self.repository.list_profiles()

    def get_draft(self, profile_id: str) -> AgentProfileDraft:
        try:
            return self.repository.get_draft(profile_id)
        except KeyError as error:
            raise self._profile_not_found(profile_id) from error

    def patch_draft(
        self, profile_id: str, request: AgentProfileDraftPatch, *, actor_id: str
    ) -> AgentProfileDraft:
        if request.spec.id != profile_id:
            raise ProductError(
                code="AGENT_PROFILE_ID_IMMUTABLE",
                title="角色 ID 不可修改",
                detail="草稿中的角色 ID 必须与 URL 中的角色 ID 一致。",
                repair="保留原角色 ID；如需新 ID，请创建新的角色。",
            )
        current = self.get_draft(profile_id)
        self._require_version(current, request.expected_version)
        updated = current.model_copy(
            update={
                "spec": request.spec,
                "version": current.version + 1,
                "validation_status": "unknown",
                "validation_errors": (),
                "updated_by": actor_id,
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap_draft(current.version, updated):
            self._raise_version_conflict(
                request.expected_version, self.get_draft(profile_id).version
            )
        return updated

    def validate_draft(
        self, profile_id: str, *, expected_version: int, actor_id: str
    ) -> AgentProfileDraft:
        current = self.get_draft(profile_id)
        self._require_version(current, expected_version)
        updated = current.model_copy(
            update={
                "version": current.version + 1,
                "validation_status": "valid",
                "validation_errors": (),
                "updated_by": actor_id,
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap_draft(current.version, updated):
            self._raise_version_conflict(
                expected_version, self.get_draft(profile_id).version
            )
        return updated

    def publish(
        self, profile_id: str, *, expected_version: int, actor_id: str
    ) -> AgentProfileRevision:
        draft = self.get_draft(profile_id)
        self._require_version(draft, expected_version)
        if draft.validation_status != "valid":
            raise ProductError(
                code="AGENT_PROFILE_NOT_VALIDATED",
                title="智能体角色尚未通过校验",
                detail="只有当前版本已校验的草稿才能发布。",
                repair="先执行角色校验，再发布不可变 Revision。",
            )
        canonical = _canonical_json(draft.spec)
        revision = AgentProfileRevision(
            profile_id=profile_id,
            revision=self.repository.next_revision(profile_id),
            spec=draft.spec,
            canonical_json=canonical,
            sha256=sha256_bytes(canonical.encode("utf-8")),
            published_by=actor_id,
            published_at=datetime.now(UTC),
        )
        return self.repository.publish(draft, revision)

    def list_revisions(self, profile_id: str) -> tuple[AgentProfileRevision, ...]:
        try:
            self.repository.get_profile(profile_id)
        except KeyError as error:
            raise self._profile_not_found(profile_id) from error
        return self.repository.list_revisions(profile_id)

    def get_revision(self, profile_id: str, revision: int) -> AgentProfileRevision:
        try:
            return self.repository.get_revision(profile_id, revision)
        except KeyError as error:
            raise ProductError(
                code="AGENT_PROFILE_REVISION_NOT_FOUND",
                title="智能体角色版本不存在",
                detail=f"角色 {profile_id} 没有 Revision {revision}。",
                repair="刷新 Revision 列表后重新选择。",
                status_code=404,
            ) from error

    def export_revision(
        self, profile_id: str, revision: int, *, format: Literal["json", "yaml"]
    ) -> AgentSpecExport:
        published = self.get_revision(profile_id, revision)
        if format == "json":
            content = json.dumps(
                published.spec.model_dump(mode="json"), ensure_ascii=False, indent=2
            )
        elif format == "yaml":
            content = yaml.safe_dump(
                published.spec.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            )
        return AgentSpecExport(
            format=format,
            content=content,
            canonical_json=published.canonical_json,
            sha256=published.sha256,
        )

    @staticmethod
    def _require_version(draft: AgentProfileDraft, expected_version: int) -> None:
        if draft.version != expected_version:
            AgentProfileCatalog._raise_version_conflict(expected_version, draft.version)

    @staticmethod
    def _raise_version_conflict(expected: int, actual: int) -> None:
        raise ProductError(
            code="AGENT_PROFILE_VERSION_CONFLICT",
            title="智能体角色版本冲突",
            detail="当前角色草稿已被其他操作更新。",
            repair="刷新角色详情后重新提交。",
            expected_version=expected,
            actual_version=actual,
        )

    @staticmethod
    def _profile_not_found(profile_id: str) -> ProductError:
        return ProductError(
            code="AGENT_PROFILE_NOT_FOUND",
            title="智能体角色不存在",
            detail=f"没有找到角色 {profile_id}。",
            repair="刷新角色列表后重新选择。",
            status_code=404,
        )


def _canonical_json(spec: AgentProfileSpec) -> str:
    return json.dumps(
        spec.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
