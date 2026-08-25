from __future__ import annotations

from collections.abc import Callable, Iterable

from pydantic import JsonValue

from ...shared.clock import Clock, SystemClock
from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from ...shared.ids import new_id
from ...shared.permissions import Role
from .domain import (
    Comment,
    CommentCreate,
    CommentPatch,
    Document,
    DocumentCreate,
    DocumentPatch,
    KnowledgeActor,
    KnowledgeDerivation,
    KnowledgeDerivationCreate,
    KnowledgeDerivationResult,
    PermissionGrant,
    Revision,
    Space,
    SpaceCreate,
    SystemKnowledgeArtifact,
    WikiAccess,
)
from .ports import CompareAndSwapResult, WikiRepository
from .search import ResolvedKnowledgeSource

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
    SYSTEM_SPACE_ID = "system:delivery-evidence"

    def __init__(
        self,
        repository: WikiRepository,
        clock: Clock | None = None,
        project_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock or SystemClock()
        self.project_guard = project_guard

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
        self, actor: KnowledgeActor, project_id: str | None = None, include_global: bool = True
    ) -> tuple[Space, ...]:
        return tuple(
            space
            for space in self.repository.list_spaces()
            if self._effective_access(actor, space.id, None) != WikiAccess.NONE
            and (
                project_id is None
                or space.project_id == project_id
                or (include_global and space.scope_kind == "global")
            )
        )

    def create_document(self, actor: KnowledgeActor, request: DocumentCreate) -> Document:
        space = self._space(request.space_id)
        if space.project_id is not None and self.project_guard is not None:
            self.project_guard(space.project_id)
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
            created_by=actor.user_id,
            created_at=now,
            updated_at=now,
        )
        revision = self._revision(document, 1, request.content, actor.user_id)
        return self.repository.create_document(document, revision)

    def derive_source(
        self,
        actor: KnowledgeActor,
        request: KnowledgeDerivationCreate,
        source: ResolvedKnowledgeSource,
    ) -> KnowledgeDerivationResult:
        space = self._space(request.target_space_id)
        if space.project_id != request.project_id or source.project_id != request.project_id:
            raise ProductError(
                code="KNOWLEDGE_SOURCE_PROJECT_MISMATCH",
                title="知识来源不属于当前项目",
                detail="来源、目标知识空间和当前项目必须一致。",
                repair="切换到来源所属项目并重新选择目标空间。",
                status_code=409,
            )
        if source.content_sha256 != request.expected_source_sha256:
            raise ProductError(
                code="KNOWLEDGE_SOURCE_VERSION_CONFLICT",
                title="知识来源版本已变化",
                detail="提炼请求引用的来源哈希已不是当前版本。",
                repair="刷新知识动态后重新发起提炼。",
                status_code=409,
            )
        if self.project_guard is not None:
            self.project_guard(request.project_id)
        self._require_access(actor, space.id, None, WikiAccess.EDIT)
        now = self.clock.now()
        document = Document(
            id=new_id(),
            space_id=space.id,
            title=request.title,
            current_revision=1,
            version=1,
            source_kind="manual",
            created_by=actor.user_id,
            created_at=now,
            updated_at=now,
        )
        content: JsonValue = {
            "format": "markdown",
            "text": (
                f"# {request.title}\n\n"
                f"> 来源：{source.source_kind} · {source.source_id} · Revision {source.revision}\n"
                f"> SHA-256：{source.content_sha256}\n\n"
                f"```json\n{source.content_text}\n```\n"
            ),
        }
        revision = self._revision(document, 1, content, actor.user_id)
        derivation = KnowledgeDerivation(
            document_id=document.id,
            project_id=request.project_id,
            target_space_id=space.id,
            source_kind=source.source_kind,
            source_id=source.source_id,
            source_revision=source.revision,
            source_sha256=source.content_sha256,
            created_by=actor.user_id,
            created_at=now,
        )
        persisted, persisted_derivation, created = self.repository.create_derived_document(
            document, revision, derivation
        )
        if persisted_derivation.source_sha256 != source.content_sha256:
            raise ProductError(
                code="KNOWLEDGE_DERIVATION_SOURCE_CONFLICT",
                title="知识提炼来源冲突",
                detail="该来源已经按另一个不可变哈希提炼。",
                repair="打开已有 Wiki 并核对其来源链。",
                status_code=409,
            )
        return KnowledgeDerivationResult(
            document=persisted, derivation=persisted_derivation, created=created
        )

    def sync_system_artifacts(
        self,
        actor: KnowledgeActor,
        artifacts: tuple[SystemKnowledgeArtifact, ...],
    ) -> tuple[Document, ...]:
        if not artifacts:
            return ()
        now = self.clock.now()
        space = self.repository.ensure_system_space(
            Space(
                id=self.SYSTEM_SPACE_ID,
                name="交付证据归档",
                description="由交付闭环自动生成的不可人工覆盖知识。",
                version=1,
                created_by=actor.user_id,
                created_at=now,
                updated_at=now,
            )
        )
        synced: list[Document] = []
        for artifact in artifacts:
            now = self.clock.now()
            document = Document(
                id=new_id(),
                space_id=space.id,
                title=artifact.title,
                current_revision=1,
                version=1,
                source_kind="delivery-evidence",
                source_id=artifact.source_id,
                created_by=actor.user_id,
                created_at=now,
                updated_at=now,
            )
            revision = self._revision(document, 1, artifact.content, actor.user_id)
            persisted = self.repository.ensure_system_document(document, revision)
            persisted_revision = self.repository.get_revision(
                persisted.id, persisted.current_revision
            )
            if (
                persisted_revision is None
                or persisted_revision.content_sha256 != revision.content_sha256
            ):
                raise ProductError(
                    code="WIKI_SYSTEM_SOURCE_CONFLICT",
                    title="系统知识来源冲突",
                    detail="同一交付产物标识对应了不同内容。",
                    repair="检查交付审计链，不要覆盖已归档证据。",
                    status_code=409,
                )
            synced.append(persisted)
        return tuple(synced)

    def list_documents(
        self, actor: KnowledgeActor, space_id: str | None = None
    ) -> tuple[Document, ...]:
        return tuple(
            document
            for document in self.repository.list_documents(space_id)
            if self._effective_access(actor, document.space_id, document.id) != WikiAccess.NONE
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
        if current.source_kind != "manual":
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
        revision = self._revision(updated, updated.current_revision, content, actor.user_id)
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
    ) -> Revision:
        now = self.clock.now()
        return Revision(
            document_id=document.id,
            revision=revision_number,
            content=content,
            search_text=" ".join(_text_fragments(content)),
            content_sha256=sha256_json(content),
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
