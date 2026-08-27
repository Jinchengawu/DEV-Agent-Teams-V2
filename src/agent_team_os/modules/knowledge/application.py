from __future__ import annotations

from collections.abc import Callable, Iterable

from pydantic import JsonValue

from ...shared.clock import Clock, SystemClock
from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from ...shared.ids import new_id
from ...shared.permissions import Role
from .domain import (
    AssetReference,
    Comment,
    CommentCreate,
    CommentPatch,
    Document,
    DocumentCreate,
    DocumentKind,
    DocumentPatch,
    KnowledgeActor,
    KnowledgeLifecycleStatus,
    PermissionGrant,
    Revision,
    RevisionProducerKind,
    RevisionProvenance,
    Space,
    SpaceCreate,
    SpaceKind,
    WikiAccess,
)
from .ports import CompareAndSwapResult, WikiRepository

ACCESS_LEVEL = {
    WikiAccess.NONE: 0,
    WikiAccess.READ: 1,
    WikiAccess.COMMENT: 2,
    WikiAccess.EDIT: 3,
    WikiAccess.ADMIN: 4,
}
ROLE_MAX_ACCESS = {
    Role.VIEWER: WikiAccess.READ,
    Role.EDITOR: WikiAccess.EDIT,
    Role.ADMINISTRATOR: WikiAccess.ADMIN,
}


class WikiService:
    def __init__(
        self,
        repository: WikiRepository,
        clock: Clock | None = None,
        project_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock or SystemClock()
        self.project_guard = project_guard

    def reconcile_project_space(
        self,
        project_id: str,
        project_name: str,
        lifecycle_status: str,
        *,
        actor_id: str | None = None,
    ) -> Space:
        now = self.clock.now()
        return self.repository.reconcile_project_space(
            Space(
                id=f"project-docs:{project_id}",
                scope_kind="project",
                project_id=project_id,
                space_kind=SpaceKind.PROJECT_DOCUMENTS,
                lifecycle_status=(
                    KnowledgeLifecycleStatus.ARCHIVED
                    if lifecycle_status == "archived"
                    else KnowledgeLifecycleStatus.ACTIVE
                ),
                name=f"{project_name} · 项目文档",
                description="项目角色在交付过程中发布的可协作文档。",
                version=1,
                created_by=actor_id,
                created_at=now,
                updated_at=now,
            )
        )

    def create_space(self, actor: KnowledgeActor, request: SpaceCreate) -> Space:
        self._require_role(actor, Role.ADMINISTRATOR)
        if request.project_id is not None and self.project_guard is not None:
            self.project_guard(request.project_id)
        now = self.clock.now()
        return self.repository.create_space(
            Space(
                id=new_id(),
                scope_kind=request.scope_kind,
                project_id=request.project_id,
                name=request.name,
                description=request.description,
                version=1,
                created_by=actor.user_id,
                created_at=now,
                updated_at=now,
            )
        )

    def list_spaces(
        self,
        actor: KnowledgeActor,
        project_id: str | None = None,
        include_global: bool = True,
        include_archived: bool = False,
    ) -> tuple[Space, ...]:
        if include_archived:
            self._require_role(actor, Role.ADMINISTRATOR)
        return tuple(
            space
            for space in self.repository.list_spaces()
            if self._effective_access(actor, space.id, None) != WikiAccess.NONE
            and (
                include_archived
                or space.lifecycle_status != KnowledgeLifecycleStatus.ARCHIVED
            )
            and (
                project_id is None
                or space.project_id == project_id
                or (include_global and space.scope_kind == "global")
            )
        )

    def create_document(self, actor: KnowledgeActor, request: DocumentCreate) -> Document:
        space = self._space(request.space_id)
        self._assert_project_writable(space.id)
        self._require_access(actor, space.id, None, WikiAccess.EDIT)
        if request.parent_id is not None:
            self._validate_parent(request.space_id, "", request.parent_id)
        now = self.clock.now()
        document_id = new_id()
        document = Document(
            id=document_id,
            space_id=space.id,
            parent_id=request.parent_id,
            title=request.title,
            current_revision=1,
            version=1,
            source_kind="manual",
            document_kind=request.document_kind,
            role_key=request.role_key,
            delivery_id=request.delivery_id,
            created_by=actor.user_id,
            created_at=now,
            updated_at=now,
        )
        revision = self._revision(
            document,
            1,
            request.content,
            actor.user_id,
            request.asset_references,
        )
        return self.repository.create_document(document, revision)

    def list_documents(
        self,
        actor: KnowledgeActor,
        space_id: str | None = None,
        document_kind: DocumentKind | None = None,
        role_key: str | None = None,
        delivery_id: str | None = None,
        source_kind: str | None = None,
        include_archived: bool = False,
    ) -> tuple[Document, ...]:
        if include_archived:
            self._require_role(actor, Role.ADMINISTRATOR)
        return tuple(
            document
            for document in self.repository.list_documents(space_id)
            if self._effective_access(actor, document.space_id, document.id) != WikiAccess.NONE
            and (
                include_archived
                or document.lifecycle_status != KnowledgeLifecycleStatus.ARCHIVED
            )
            and (document_kind is None or document.document_kind == document_kind)
            and (role_key is None or document.role_key == role_key)
            and (delivery_id is None or document.delivery_id == delivery_id)
            and (source_kind is None or document.source_kind == source_kind)
        )

    def get_document(self, actor: KnowledgeActor, document_id: str) -> Document:
        document = self._document(document_id)
        self._require_access(actor, document.space_id, document.id, WikiAccess.READ)
        return document

    def get_revision(
        self, actor: KnowledgeActor, document_id: str, revision_number: int
    ) -> Revision:
        document = self.get_document(actor, document_id)
        revision = self.repository.get_revision(document.id, revision_number)
        if revision is None:
            raise ProductError(
                code="WIKI_REVISION_NOT_FOUND",
                title="文档版本不存在",
                detail="指定的文档版本已不存在。",
                repair="刷新版本列表后重试。",
                status_code=404,
            )
        return revision

    def list_revisions(self, actor: KnowledgeActor, document_id: str) -> tuple[Revision, ...]:
        self.get_document(actor, document_id)
        return self.repository.list_revisions(document_id)

    def patch_document(
        self, actor: KnowledgeActor, document_id: str, request: DocumentPatch
    ) -> Document:
        current = self._document(document_id)
        space = self._space(current.space_id)
        if space.project_id is not None and self.project_guard is not None:
            self.project_guard(space.project_id)
        self._require_access(actor, current.space_id, current.id, WikiAccess.EDIT)
        if current.source_kind not in {"manual", "legacy-migrated", "agent-publication"}:
            raise ProductError(
                code="WIKI_SYSTEM_DOCUMENT_IMMUTABLE",
                title="系统证据不可编辑",
                detail="该文档来自系统证据，不允许人工覆盖。",
                repair="根据来源交付或证据生成新版本。",
            )
        if current.version != request.expected_version:
            raise self._version_conflict(request.expected_version, current.version)
        next_parent = (
            request.parent_id if "parent_id" in request.model_fields_set else current.parent_id
        )
        if next_parent == current.id:
            raise self._invalid_parent()
        if next_parent is not None:
            self._validate_parent(current.space_id, current.id, next_parent)
        previous = self.get_revision(actor, current.id, current.current_revision)
        content = previous.content if request.content is None else request.content
        asset_references = (
            previous.asset_references
            if request.asset_references is None
            else request.asset_references
        )
        now = self.clock.now()
        updated = current.model_copy(
            update={
                "title": request.title or current.title,
                "parent_id": next_parent,
                "current_revision": current.current_revision + 1,
                "version": current.version + 1,
                "updated_at": now,
            }
        )
        revision = self._revision(
            updated,
            updated.current_revision,
            content,
            actor.user_id,
            asset_references,
        )
        result = self.repository.compare_and_swap_document(
            request.expected_version, updated, revision
        )
        if result != CompareAndSwapResult.UPDATED:
            latest = self.repository.get_document(document_id)
            raise self._version_conflict(
                request.expected_version, None if latest is None else latest.version
            )
        return updated

    def restore_revision(
        self,
        actor: KnowledgeActor,
        document_id: str,
        revision_number: int,
        expected_version: int,
    ) -> Document:
        source = self.get_revision(actor, document_id, revision_number)
        return self.patch_document(
            actor,
            document_id,
            DocumentPatch(expected_version=expected_version, content=source.content),
        )

    def search(self, actor: KnowledgeActor, query: str) -> tuple[Document, ...]:
        found: list[Document] = []
        for document_id in self.repository.search_document_ids(query):
            document = self.repository.get_document(document_id)
            if (
                document is not None
                and self._effective_access(actor, document.space_id, document.id) != WikiAccess.NONE
                and document.lifecycle_status != KnowledgeLifecycleStatus.ARCHIVED
            ):
                found.append(document)
        return tuple(found)

    def add_comment(
        self, actor: KnowledgeActor, document_id: str, request: CommentCreate
    ) -> Comment:
        document = self._document(document_id)
        self._assert_project_writable(document.space_id)
        self._require_access(actor, document.space_id, document.id, WikiAccess.COMMENT)
        now = self.clock.now()
        return self.repository.create_comment(
            Comment(
                id=new_id(),
                document_id=document.id,
                parent_id=request.parent_id,
                body=request.body,
                author_id=actor.user_id,
                resolved=False,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )

    def list_comments(self, actor: KnowledgeActor, document_id: str) -> tuple[Comment, ...]:
        self.get_document(actor, document_id)
        return self.repository.list_comments(document_id)

    def patch_comment(
        self, actor: KnowledgeActor, comment_id: str, request: CommentPatch
    ) -> Comment:
        comment = self.repository.get_comment(comment_id)
        if comment is None:
            raise ProductError(
                code="WIKI_COMMENT_NOT_FOUND",
                title="评论不存在",
                detail="指定的评论已不存在。",
                repair="刷新评论列表后重试。",
                status_code=404,
            )
        document = self._document(comment.document_id)
        self._assert_project_writable(document.space_id)
        access = self._effective_access(actor, document.space_id, document.id)
        can_change = (
            comment.author_id == actor.user_id
            or ACCESS_LEVEL[access] >= ACCESS_LEVEL[WikiAccess.EDIT]
        )
        if not can_change:
            raise self._permission_denied()
        if comment.version != request.expected_version:
            raise self._version_conflict(request.expected_version, comment.version)
        if request.resolved is not None and ACCESS_LEVEL[access] < ACCESS_LEVEL[WikiAccess.EDIT]:
            raise self._permission_denied()
        updated = comment.model_copy(
            update={
                "body": request.body or comment.body,
                "resolved": comment.resolved if request.resolved is None else request.resolved,
                "version": comment.version + 1,
                "updated_at": self.clock.now(),
            }
        )
        result = self.repository.compare_and_swap_comment(request.expected_version, updated)
        if result != CompareAndSwapResult.UPDATED:
            raise self._version_conflict(request.expected_version, None)
        return updated

    def put_permission(self, actor: KnowledgeActor, grant: PermissionGrant) -> PermissionGrant:
        self._require_role(actor, Role.ADMINISTRATOR)
        if grant.resource_kind not in {"wiki-space", "wiki-document"}:
            raise ProductError(
                code="WIKI_PERMISSION_RESOURCE_INVALID",
                title="权限目标无效",
                detail="只能为知识空间或文档设置权限。",
                repair="选择有效的知识资源后重试。",
                status_code=422,
            )
        space_id = (
            grant.resource_id
            if grant.resource_kind == "wiki-space"
            else self._document(grant.resource_id).space_id
        )
        self._assert_project_writable(space_id)
        return self.repository.put_permission(grant)

    def _assert_project_writable(self, space_id: str) -> None:
        space = self._space(space_id)
        if space.lifecycle_status == KnowledgeLifecycleStatus.ARCHIVED:
            raise ProductError(
                code="WIKI_ARCHIVED_READ_ONLY",
                title="归档知识只读",
                detail="归档空间不允许新增或修改内容。",
                repair="在当前项目文档空间中创建新文档。",
                status_code=409,
            )
        if space.project_id is not None and self.project_guard is not None:
            self.project_guard(space.project_id)

    def _effective_access(
        self, actor: KnowledgeActor, space_id: str, document_id: str | None
    ) -> WikiAccess:
        maximum = ROLE_MAX_ACCESS[actor.role]
        explicit = None
        if document_id is not None:
            explicit = self.repository.get_permission("wiki-document", document_id, actor.user_id)
        if explicit is None:
            explicit = self.repository.get_permission("wiki-space", space_id, actor.user_id)
        requested = maximum if explicit is None else explicit
        return min((maximum, requested), key=lambda access: ACCESS_LEVEL[access])

    def _require_access(
        self,
        actor: KnowledgeActor,
        space_id: str,
        document_id: str | None,
        required: WikiAccess,
    ) -> None:
        actual = self._effective_access(actor, space_id, document_id)
        if ACCESS_LEVEL[actual] < ACCESS_LEVEL[required]:
            raise self._permission_denied()

    @staticmethod
    def _require_role(actor: KnowledgeActor, role: Role) -> None:
        if actor.role != role:
            raise WikiService._permission_denied()

    def _space(self, space_id: str) -> Space:
        space = self.repository.get_space(space_id)
        if space is None:
            raise ProductError(
                code="WIKI_SPACE_NOT_FOUND",
                title="知识空间不存在",
                detail="指定的知识空间已不存在。",
                repair="刷新知识空间列表后重试。",
                status_code=404,
            )
        return space

    def _document(self, document_id: str) -> Document:
        document = self.repository.get_document(document_id)
        if document is None:
            raise ProductError(
                code="WIKI_DOCUMENT_NOT_FOUND",
                title="文档不存在",
                detail="指定的文档已不存在。",
                repair="刷新文档列表后重试。",
                status_code=404,
            )
        return document

    def _validate_parent(self, space_id: str, document_id: str, parent_id: str) -> None:
        visited = {document_id} if document_id else set()
        candidate_id: str | None = parent_id
        while candidate_id is not None:
            if candidate_id in visited:
                raise self._invalid_parent()
            visited.add(candidate_id)
            candidate = self._document(candidate_id)
            if candidate.space_id != space_id:
                raise self._invalid_parent()
            candidate_id = candidate.parent_id

    def _revision(
        self,
        document: Document,
        revision_number: int,
        content: JsonValue,
        actor_id: str,
        asset_references: tuple[AssetReference, ...] = (),
    ) -> Revision:
        now = self.clock.now()
        return Revision(
            document_id=document.id,
            revision=revision_number,
            content=content,
            search_text=" ".join(_text_fragments(content)),
            content_sha256=sha256_json(content),
            provenance=RevisionProvenance(
                producer_kind=RevisionProducerKind.HUMAN,
                producer_id=actor_id,
            ),
            asset_references=asset_references,
            created_by=actor_id,
            created_at=now,
        )

    @staticmethod
    def _permission_denied() -> ProductError:
        return ProductError(
            code="WIKI_PERMISSION_DENIED",
            title="知识权限不足",
            detail="当前账户无权访问或修改该知识资源。",
            repair="联系空间管理员调整权限。",
            status_code=403,
        )

    @staticmethod
    def _version_conflict(expected: int, actual: int | None) -> ProductError:
        return ProductError(
            code="WIKI_VERSION_CONFLICT",
            title="文档版本冲突",
            detail="文档或评论已被其他操作更新。",
            repair="刷新最新版本后重新提交。",
            expected_version=expected,
            actual_version=actual,
        )

    @staticmethod
    def _invalid_parent() -> ProductError:
        return ProductError(
            code="WIKI_PARENT_INVALID",
            title="文档父节点无效",
            detail="父文档不能是自身，且必须位于同一知识空间。",
            repair="选择同一空间内的其他文档，或将其设为根文档。",
            status_code=422,
        )


def _text_fragments(value: JsonValue) -> Iterable[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            yield stripped
    elif isinstance(value, list):
        for item in value:
            yield from _text_fragments(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _text_fragments(item)
