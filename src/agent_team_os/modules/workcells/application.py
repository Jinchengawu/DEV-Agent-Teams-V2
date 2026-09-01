from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from ...shared.ids import new_id
from .domain import (
    TeamTemplate,
    TeamTemplateCreate,
    TeamTemplateDraft,
    TeamTemplateDraftPatch,
    TeamTemplateRevision,
    TeamTemplateWithDraft,
)
from .repository import SQLiteTeamTemplateRepository


class TeamTemplateCatalog:
    """Own organization topology without duplicating Pipeline or Deployment facts."""

    def __init__(self, repository: SQLiteTeamTemplateRepository) -> None:
        self.repository = repository

    def create(
        self,
        request: TeamTemplateCreate,
        *,
        actor_id: str,
    ) -> TeamTemplateWithDraft:
        template = TeamTemplate(
            id=request.id,
            name=request.name,
            description=request.description,
            created_by=actor_id,
        )
        draft = TeamTemplateDraft(
            id=new_id(),
            template_id=template.id,
            name=request.name,
            description=request.description,
            workcells=request.workcells,
            topology=request.topology,
            created_by=actor_id,
        )
        try:
            self.repository.create(template, draft)
        except sqlite3.IntegrityError as error:
            raise ProductError(
                code="TEAM_TEMPLATE_ALREADY_EXISTS",
                title="TeamTemplate 已存在",
                detail=f"TeamTemplate {template.id} 已经存在。",
                repair="使用新的 TeamTemplate ID，或打开现有草稿。",
            ) from error
        return TeamTemplateWithDraft(template=template, draft=draft)

    def list(self) -> tuple[TeamTemplate, ...]:
        return self.repository.list_templates()

    def get_draft(self, draft_id: str) -> TeamTemplateDraft:
        return self.repository.get_draft(draft_id)

    def list_drafts(self, template_id: str) -> tuple[TeamTemplateDraft, ...]:
        self.repository.get_template(template_id)
        return self.repository.list_drafts(template_id)

    def patch(
        self,
        draft_id: str,
        request: TeamTemplateDraftPatch,
    ) -> TeamTemplateDraft:
        current = self.repository.get_draft(draft_id)
        self._require_version(current, request.expected_version)
        changes = request.model_dump(exclude_none=True, exclude={"expected_version"})
        updated = current.model_copy(
            update={
                **changes,
                "version": current.version + 1,
                "validation_status": "unknown",
                "validation_errors": (),
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap_draft(current.version, updated):
            latest = self.repository.get_draft(draft_id)
            self._raise_version_conflict(request.expected_version, latest.version)
        return updated

    def validate(self, draft_id: str, *, expected_version: int) -> TeamTemplateDraft:
        current = self.repository.get_draft(draft_id)
        self._require_version(current, expected_version)
        errors = _validation_errors(current)
        updated = current.model_copy(
            update={
                "version": current.version + 1,
                "validation_status": "invalid" if errors else "valid",
                "validation_errors": errors,
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap_draft(current.version, updated):
            latest = self.repository.get_draft(draft_id)
            self._raise_version_conflict(expected_version, latest.version)
        return updated

    def publish(
        self,
        draft_id: str,
        *,
        expected_version: int,
        actor_id: str,
    ) -> TeamTemplateRevision:
        draft = self.repository.get_draft(draft_id)
        self._require_version(draft, expected_version)
        if draft.validation_status != "valid":
            raise ProductError(
                code="TEAM_TEMPLATE_DRAFT_INVALID",
                title="TeamTemplate 草稿尚未通过校验",
                detail="组织拓扑草稿不能发布为不可变 Revision。",
                repair="先校验草稿并修复全部组织约束错误。",
            )
        revision_number = self.repository.next_revision(draft.template_id)
        payload = {
            "template_id": draft.template_id,
            "revision": revision_number,
            "name": draft.name,
            "description": draft.description,
            "workcells": [item.model_dump(mode="json") for item in draft.workcells],
            "topology": draft.topology.model_dump(mode="json"),
        }
        revision = TeamTemplateRevision(
            template_id=draft.template_id,
            revision=revision_number,
            name=draft.name,
            description=draft.description,
            workcells=draft.workcells,
            topology=draft.topology,
            sha256=sha256_json(payload),
            published_by=actor_id,
        )
        return self.repository.publish(draft, revision)

    def get_revision(self, template_id: str, revision: int) -> TeamTemplateRevision:
        return self.repository.get_revision(template_id, revision)

    @staticmethod
    def _require_version(draft: TeamTemplateDraft, expected: int) -> None:
        if draft.version != expected:
            TeamTemplateCatalog._raise_version_conflict(expected, draft.version)

    @staticmethod
    def _raise_version_conflict(expected: int, actual: int) -> None:
        raise ProductError(
            code="TEAM_TEMPLATE_DRAFT_VERSION_CONFLICT",
            title="TeamTemplate 草稿版本冲突",
            detail="组织拓扑草稿已被其他操作更新。",
            repair="刷新草稿后重新提交。",
            expected_version=expected,
            actual_version=actual,
        )


def _validation_errors(draft: TeamTemplateDraft) -> tuple[str, ...]:
    errors: list[str] = []
    workcell_keys = tuple(item.workcell_key for item in draft.workcells)
    if len(set(workcell_keys)) != len(workcell_keys):
        errors.append("TEAM_TEMPLATE_WORKCELL_KEY_DUPLICATE")
    node_keys = tuple(item.workcell_key for item in draft.topology.nodes)
    if len(set(node_keys)) != len(node_keys):
        errors.append("TEAM_TEMPLATE_TOPOLOGY_NODE_DUPLICATE")
    if set(node_keys) != set(workcell_keys):
        errors.append("TEAM_TEMPLATE_TOPOLOGY_NODE_MISMATCH")
    known = set(workcell_keys)
    for link in draft.topology.links:
        if (
            link.source_workcell_key not in known
            or link.target_workcell_key not in known
        ):
            errors.append("TEAM_TEMPLATE_TOPOLOGY_LINK_UNKNOWN_WORKCELL")
        if link.source_workcell_key == link.target_workcell_key:
            errors.append("TEAM_TEMPLATE_TOPOLOGY_SELF_LINK")
    return tuple(dict.fromkeys(errors))
