from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_team_os.modules.knowledge import (
    KnowledgeProvider,
    KnowledgeProviderKind,
    ProviderActor,
    ProviderBindingCreate,
    ProviderNode,
    ProviderNodeKind,
    ProviderSnapshot,
    ProviderSpace,
    ProviderSyncRun,
    ProviderSyncStatus,
)
from agent_team_os.shared.hashes import sha256_json


class DeterministicProvider:
    def list_spaces(self, actor: ProviderActor) -> tuple[ProviderSpace, ...]:
        return (ProviderSpace(external_id="space-1", title=f"知识空间/{actor.provider_user_id}"),)

    def list_nodes(
        self, actor: ProviderActor, external_space_id: str
    ) -> tuple[ProviderNode, ...]:
        return (
            ProviderNode(
                external_id=f"doc-{actor.provider_user_id}",
                external_space_id=external_space_id,
                title="可验证文档",
                kind=ProviderNodeKind.DOCUMENT,
                provider_revision="rev-1",
            ),
        )

    def fetch_snapshot(self, actor: ProviderActor, source_id: str) -> ProviderSnapshot:
        content = {"source_id": source_id, "reader": actor.provider_user_id, "text": "内容"}
        return ProviderSnapshot(
            source_id=source_id,
            provider_revision="rev-1",
            content_type="application/json",
            normalized_content=content,
            normalized_text="内容",
            content_sha256=sha256_json(content),
            fetched_at=datetime(2026, 8, 23, tzinfo=UTC),
        )


def _exercise_provider(provider: KnowledgeProvider) -> ProviderSnapshot:
    actor = ProviderActor(product_user_id="user-1", provider_user_id="feishu-user-1")
    space = provider.list_spaces(actor)[0]
    node = provider.list_nodes(actor, space.external_id)[0]
    return provider.fetch_snapshot(actor, node.external_id)


def test_provider_port_preserves_external_identity_and_verified_snapshot() -> None:
    snapshot = _exercise_provider(DeterministicProvider())
    assert snapshot.provider_revision == "rev-1"
    assert snapshot.content_sha256 == sha256_json(snapshot.normalized_content)


@pytest.mark.parametrize(
    "credential_ref",
    ["plain-secret", "https://token.invalid", "env:lowercase", "keychain:bad value"],
)
def test_provider_binding_rejects_plaintext_credentials(credential_ref: str) -> None:
    with pytest.raises(ValidationError):
        ProviderBindingCreate(
            provider_kind=KnowledgeProviderKind.FEISHU,
            display_name="飞书研发知识库",
            external_space_id="space-1",
            credential_ref=credential_ref,
        )


@pytest.mark.parametrize("credential_ref", ["env:FEISHU_APP_SECRET", "keychain:feishu.app"])
def test_provider_binding_accepts_only_secret_references(credential_ref: str) -> None:
    binding = ProviderBindingCreate(
        provider_kind=KnowledgeProviderKind.FEISHU,
        display_name="飞书研发知识库",
        external_space_id="space-1",
        credential_ref=credential_ref,
    )
    assert binding.credential_ref == credential_ref


def test_snapshot_rejects_content_hash_mismatch() -> None:
    with pytest.raises(ValidationError):
        ProviderSnapshot(
            source_id="doc-1",
            provider_revision="rev-1",
            content_type="application/json",
            normalized_content={"text": "真实内容"},
            normalized_text="真实内容",
            content_sha256="0" * 64,
            fetched_at=datetime(2026, 8, 23, tzinfo=UTC),
        )


def test_sync_terminal_states_require_traceable_evidence() -> None:
    with pytest.raises(ValidationError):
        ProviderSyncRun(
            id="sync-1",
            binding_id="binding-1",
            source_id="doc-1",
            status=ProviderSyncStatus.SUCCEEDED,
        )
    with pytest.raises(ValidationError):
        ProviderSyncRun(
            id="sync-2",
            binding_id="binding-1",
            source_id="doc-1",
            status=ProviderSyncStatus.UNAVAILABLE,
        )

    completed_at = datetime(2026, 8, 23, tzinfo=UTC)
    succeeded = ProviderSyncRun(
        id="sync-3",
        binding_id="binding-1",
        source_id="doc-1",
        status=ProviderSyncStatus.SUCCEEDED,
        provider_revision="rev-1",
        snapshot_sha256="a" * 64,
        completed_at=completed_at,
    )
    assert succeeded.completed_at == completed_at
