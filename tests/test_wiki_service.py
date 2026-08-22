from __future__ import annotations

from pathlib import Path

import pytest

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
    UserCreate,
)
from agent_team_os.modules.knowledge import (
    CommentCreate,
    CommentPatch,
    Document,
    DocumentCreate,
    DocumentPatch,
    KnowledgeActor,
    PermissionGrant,
    Revision,
    SpaceCreate,
    SQLiteWikiRepository,
    SystemKnowledgeArtifact,
    WikiAccess,
    WikiService,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.permissions import Role


def _services(tmp_path: Path) -> tuple[WikiService, KnowledgeActor, KnowledgeActor, KnowledgeActor]:
    database = tmp_path / "wiki.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identities = IdentityService(SQLiteIdentityRepository(database))
    admin_user = identities.bootstrap(
        BootstrapRequest(password="secure-admin-2026")
    )
    editor_user = identities.create_user(
        admin_user,
        UserCreate(
            username="editor",
            display_name="内容编辑",
            role=Role.EDITOR,
            password="secure-editor-2026",
        ),
    )
    viewer_user = identities.create_user(
        admin_user,
        UserCreate(
            username="viewer",
            display_name="只读访问者",
            role=Role.VIEWER,
            password="secure-viewer-2026",
        ),
    )
    return (
        WikiService(SQLiteWikiRepository(database)),
        KnowledgeActor(user_id=admin_user.id, role=admin_user.role),
        KnowledgeActor(user_id=editor_user.id, role=editor_user.role),
        KnowledgeActor(user_id=viewer_user.id, role=viewer_user.role),
    )


def test_versioned_document_cas_search_restore_and_cycle_guard(tmp_path: Path) -> None:
    wiki, admin, editor, viewer = _services(tmp_path)
    space = wiki.create_space(admin, SpaceCreate(name="交付手册"))
    document = wiki.create_document(
        editor,
        DocumentCreate(
            space_id=space.id,
            title="后端交付流程",
            content={"type": "doc", "content": [{"text": "真实 Diff 与机器测试"}]},
        ),
    )
    first = wiki.get_revision(viewer, document.id, 1)
    assert first.content_sha256 == sha256_json(first.content)
    assert wiki.search(viewer, "机器测试") == (document,)

    updated = wiki.patch_document(
        editor,
        document.id,
        DocumentPatch(
            expected_version=1,
            content={"type": "doc", "content": [{"text": "候选版本验证"}]},
        ),
    )
    assert updated.current_revision == 2
    assert len(wiki.list_revisions(viewer, document.id)) == 2
    with pytest.raises(ProductError, match="已被其他操作更新") as conflict:
        wiki.patch_document(
            editor,
            document.id,
            DocumentPatch(expected_version=1, title="过期修改"),
        )
    assert conflict.value.code == "WIKI_VERSION_CONFLICT"

    restored = wiki.restore_revision(editor, document.id, 1, expected_version=2)
    assert restored.current_revision == 3
    assert wiki.get_revision(viewer, document.id, 3).content_sha256 == first.content_sha256

    child = wiki.create_document(
        editor,
        DocumentCreate(
            space_id=space.id,
            parent_id=document.id,
            title="子流程",
            content={"text": "子节点"},
        ),
    )
    with pytest.raises(ProductError) as cycle:
        wiki.patch_document(
            editor,
            document.id,
            DocumentPatch(expected_version=restored.version, parent_id=child.id),
        )
    assert cycle.value.code == "WIKI_PARENT_INVALID"


def test_permissions_narrow_roles_and_comments_are_versioned(tmp_path: Path) -> None:
    wiki, admin, editor, viewer = _services(tmp_path)
    space = wiki.create_space(admin, SpaceCreate(name="证据规范"))
    document = wiki.create_document(
        editor,
        DocumentCreate(space_id=space.id, title="证据", content={"text": "SHA-256"}),
    )
    wiki.put_permission(
        admin,
        PermissionGrant(
            resource_kind="wiki-document",
            resource_id=document.id,
            user_id=viewer.user_id,
            access=WikiAccess.ADMIN,
        ),
    )
    with pytest.raises(ProductError) as cannot_comment:
        wiki.add_comment(viewer, document.id, CommentCreate(body="只读者评论"))
    assert cannot_comment.value.code == "WIKI_PERMISSION_DENIED"

    comment = wiki.add_comment(editor, document.id, CommentCreate(body="需要补充来源"))
    resolved = wiki.patch_comment(
        editor,
        comment.id,
        CommentPatch(expected_version=1, resolved=True),
    )
    assert resolved.resolved is True
    assert resolved.version == 2

    wiki.put_permission(
        admin,
        PermissionGrant(
            resource_kind="wiki-space",
            resource_id=space.id,
            user_id=editor.user_id,
            access=WikiAccess.READ,
        ),
    )
    with pytest.raises(ProductError) as narrowed:
        wiki.patch_document(
            editor,
            document.id,
            DocumentPatch(expected_version=1, title="无权修改"),
        )
    assert narrowed.value.code == "WIKI_PERMISSION_DENIED"


def test_system_evidence_document_cannot_be_overwritten(tmp_path: Path) -> None:
    wiki, admin, editor, _viewer = _services(tmp_path)
    space = wiki.create_space(admin, SpaceCreate(name="系统证据"))
    now = wiki.clock.now()
    repository = wiki.repository
    document = Document(
        id="system-evidence-1",
        space_id=space.id,
        title="交付证据",
        current_revision=1,
        version=1,
        source_kind="evidence",
        source_id="evidence-1",
        created_by=admin.user_id,
        created_at=now,
        updated_at=now,
    )
    repository.create_document(
        document,
        Revision(
            document_id=document.id,
            revision=1,
            content={"evidence_id": "evidence-1"},
            search_text="交付证据",
            content_sha256=sha256_json({"evidence_id": "evidence-1"}),
            created_by=admin.user_id,
            created_at=now,
        ),
    )

    with pytest.raises(ProductError) as immutable:
        wiki.patch_document(
            editor,
            document.id,
            DocumentPatch(expected_version=1, title="伪造证据"),
        )
    assert immutable.value.code == "WIKI_SYSTEM_DOCUMENT_IMMUTABLE"


def test_delivery_artifact_sync_is_idempotent_and_source_immutable(tmp_path: Path) -> None:
    wiki, admin, _editor, viewer = _services(tmp_path)
    artifact = SystemKnowledgeArtifact(
        source_id="delivery-1:requirement",
        title="增加 health 接口 · 需求",
        content={"summary": "增加 health 接口", "acceptance": ["status=ok"]},
    )

    first = wiki.sync_system_artifacts(admin, (artifact,))
    second = wiki.sync_system_artifacts(admin, (artifact,))

    assert first == second
    assert first[0].source_kind == "delivery-evidence"
    assert wiki.search(viewer, "health") == first

    with pytest.raises(ProductError) as conflict:
        wiki.sync_system_artifacts(
            admin,
            (
                artifact.model_copy(
                    update={"content": {"summary": "伪造的同源内容"}}
                ),
            ),
        )
    assert conflict.value.code == "WIKI_SYSTEM_SOURCE_CONFLICT"
