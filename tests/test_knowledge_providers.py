from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
    UserCreate,
)
from agent_team_os.modules.knowledge import (
    KnowledgeActor,
    KnowledgeProviderKind,
    ProviderActor,
    ProviderBinding,
    ProviderBindingCreate,
    ProviderFailure,
    ProviderKnowledgeManager,
    ProviderNode,
    ProviderSnapshot,
    ProviderSpace,
    ProviderSyncStatus,
    SQLiteProviderKnowledgeRepository,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.permissions import Role


class DeterministicProvider:
    def __init__(self, snapshot: ProviderSnapshot) -> None:
        self.snapshot = snapshot
        self.failure: ProviderFailure | None = None
        self.fetches: list[tuple[ProviderActor, str]] = []

    def list_spaces(self, actor: ProviderActor) -> tuple[ProviderSpace, ...]:
        return (ProviderSpace(external_id="space-feishu", title="交付知识"),)

    def list_nodes(
        self, actor: ProviderActor, external_space_id: str
    ) -> tuple[ProviderNode, ...]:
        return ()

    def fetch_snapshot(
        self, actor: ProviderActor, source_id: str
    ) -> ProviderSnapshot:
        self.fetches.append((actor, source_id))
        if self.failure is not None:
            raise self.failure
        return self.snapshot.model_copy(update={"source_id": source_id})


class DeterministicResolver:
    def __init__(self, provider: DeterministicProvider) -> None:
        self.provider = provider
        self.bindings: list[ProviderBinding] = []

    def resolve(self, binding: ProviderBinding) -> DeterministicProvider:
        self.bindings.append(binding)
        return self.provider


class DeterministicActorResolver:
    def __init__(self) -> None:
        self.product_user_override: str | None = None

    def resolve(
        self, binding: ProviderBinding, actor: KnowledgeActor
    ) -> ProviderActor:
        return ProviderActor(
            product_user_id=self.product_user_override or actor.user_id,
            provider_user_id=f"feishu-{actor.user_id}",
        )


def _snapshot(revision: str, content: dict[str, object]) -> ProviderSnapshot:
    return ProviderSnapshot(
        source_id="doc-feishu",
        provider_revision=revision,
        content_type="application/json",
        normalized_content=content,
        normalized_text=" ".join(str(value) for value in content.values()),
        content_sha256=sha256_json(content),
        source_url="https://example.invalid/wiki/doc-feishu",
        fetched_at=datetime(2026, 8, 23, 2, 0, tzinfo=UTC),
    )


def _manager(
    tmp_path: Path,
) -> tuple[
    ProviderKnowledgeManager,
    SQLiteProviderKnowledgeRepository,
    DeterministicProvider,
    DeterministicActorResolver,
    KnowledgeActor,
    KnowledgeActor,
    KnowledgeActor,
]:
    database = tmp_path / "providers.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identities = IdentityService(SQLiteIdentityRepository(database))
    admin_user = identities.bootstrap(BootstrapRequest(password="secure-admin-2026"))
    editor_user = identities.create_user(
        admin_user,
        UserCreate(
            username="provider-editor",
            display_name="同步编辑",
            role=Role.EDITOR,
            password="secure-editor-2026",
        ),
    )
    viewer_user = identities.create_user(
        admin_user,
        UserCreate(
            username="provider-viewer",
            display_name="只读用户",
            role=Role.VIEWER,
            password="secure-viewer-2026",
        ),
    )
    provider = DeterministicProvider(_snapshot("rev-1", {"title": "交付手册"}))
    actor_resolver = DeterministicActorResolver()
    repository = SQLiteProviderKnowledgeRepository(database)
    manager = ProviderKnowledgeManager(
        repository, DeterministicResolver(provider), actor_resolver
    )
    return (
        manager,
        repository,
        provider,
        actor_resolver,
        KnowledgeActor(user_id=admin_user.id, role=admin_user.role),
        KnowledgeActor(user_id=editor_user.id, role=editor_user.role),
        KnowledgeActor(user_id=viewer_user.id, role=viewer_user.role),
    )


def _create_binding(
    manager: ProviderKnowledgeManager, admin: KnowledgeActor
) -> ProviderBinding:
    return manager.create_binding(
        admin,
        ProviderBindingCreate(
            provider_kind=KnowledgeProviderKind.FEISHU,
            display_name="飞书交付知识",
            external_space_id="space-feishu",
            credential_ref="env:FEISHU_APP_SECRET",
        ),
    )


def test_binding_requires_reference_admin_and_redacts_event(tmp_path: Path) -> None:
    manager, _repository, _provider, _actor_resolver, admin, editor, _viewer = _manager(
        tmp_path
    )
    with pytest.raises(ValidationError):
        ProviderBindingCreate(
            provider_kind=KnowledgeProviderKind.FEISHU,
            display_name="不安全绑定",
            external_space_id="space-unsafe",
            credential_ref="plaintext-secret",
        )
    with pytest.raises(ProductError) as denied:
        _create_binding(manager, editor)
    assert denied.value.code == "KNOWLEDGE_PROVIDER_PERMISSION_DENIED"

    binding = _create_binding(manager, admin)
    assert binding.credential_ref == "env:FEISHU_APP_SECRET"
    with pytest.raises(ProductError) as conflict:
        _create_binding(manager, admin)
    assert conflict.value.code == "KNOWLEDGE_PROVIDER_BINDING_CONFLICT"

    with sqlite3.connect(tmp_path / "providers.sqlite") as connection:
        payload_json = connection.execute(
            """SELECT payload_json FROM product_events
            WHERE event_type='knowledge.source-linked'"""
        ).fetchone()[0]
    assert "FEISHU_APP_SECRET" not in str(payload_json)
    assert "credential" not in json.loads(str(payload_json))


def test_sync_reuses_revision_snapshot_and_records_new_content(tmp_path: Path) -> None:
    manager, repository, provider, _actor_resolver, admin, editor, _viewer = _manager(
        tmp_path
    )
    binding = _create_binding(manager, admin)

    first = manager.sync(editor, binding.id, "doc-feishu")
    second = manager.sync(editor, binding.id, "doc-feishu")
    assert first.run.status == ProviderSyncStatus.SUCCEEDED
    assert first.snapshot is not None
    assert second.snapshot is not None
    assert second.snapshot.id == first.snapshot.id

    provider.snapshot = _snapshot("rev-2", {"title": "交付手册", "version": 2})
    changed = manager.sync(editor, binding.id, "doc-feishu")
    assert changed.snapshot is not None
    assert changed.snapshot.id != first.snapshot.id
    assert changed.snapshot.content_sha256 != first.snapshot.content_sha256
    assert len(repository.list_sync_runs(binding.id)) == 3

    with sqlite3.connect(tmp_path / "providers.sqlite") as connection:
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM knowledge_provider_snapshots"
        ).fetchone()[0]
        synced_events = connection.execute(
            """SELECT COUNT(*) FROM product_events
            WHERE event_type='knowledge.document-synced'"""
        ).fetchone()[0]
    assert snapshot_count == 2
    assert synced_events == 3


def test_revision_conflict_unavailable_and_permission_fail_closed(tmp_path: Path) -> None:
    manager, repository, provider, actor_resolver, admin, editor, viewer = _manager(
        tmp_path
    )
    binding = _create_binding(manager, admin)
    manager.sync(editor, binding.id, "doc-feishu")

    provider.snapshot = _snapshot("rev-1", {"title": "被篡改内容"})
    with pytest.raises(ProductError) as conflict:
        manager.sync(editor, binding.id, "doc-feishu")
    assert conflict.value.code == "KNOWLEDGE_PROVIDER_REVISION_CONFLICT"
    assert repository.list_sync_runs(binding.id)[0].error_code == conflict.value.code

    provider.failure = ProviderFailure(
        "FEISHU_PERMISSION_REVOKED",
        "provider access revoked",
        unavailable=True,
    )
    unavailable = manager.sync(editor, binding.id, "doc-feishu")
    assert unavailable.run.status == ProviderSyncStatus.UNAVAILABLE
    assert unavailable.run.error_code == "FEISHU_PERMISSION_REVOKED"
    assert unavailable.snapshot is None

    run_count_before_mismatch = len(repository.list_sync_runs(binding.id))
    actor_resolver.product_user_override = admin.user_id
    with pytest.raises(ProductError) as actor_mismatch:
        manager.sync(editor, binding.id, "doc-feishu")
    assert actor_mismatch.value.code == "KNOWLEDGE_PROVIDER_ACTOR_MISMATCH"
    assert len(repository.list_sync_runs(binding.id)) == run_count_before_mismatch
    with pytest.raises(ProductError) as viewer_denied:
        manager.sync(viewer, binding.id, "doc-feishu")
    assert viewer_denied.value.code == "KNOWLEDGE_PROVIDER_PERMISSION_DENIED"
