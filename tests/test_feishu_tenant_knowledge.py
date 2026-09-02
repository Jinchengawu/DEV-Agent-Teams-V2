from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.feishu import (
    EnvironmentSecretResolver,
    FeishuTenantKnowledgeProvider,
)
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
)
from agent_team_os.modules.knowledge import (
    KnowledgeDirectoryReconciler,
    KnowledgeProviderKind,
    KnowledgeSyncPolicy,
    KnowledgeSyncScheduler,
    KnowledgeSyncSupervisor,
    KnowledgeSyncWorker,
    ProviderFailure,
    ProviderNode,
    ProviderNodeKind,
    ProviderSnapshot,
    ProviderSpace,
    SQLiteTenantKnowledgeRepository,
    TenantConnection,
    TenantKnowledgeManager,
    TenantKnowledgeProviderResolver,
)
from agent_team_os.modules.projects import ProjectCatalog, SQLiteProjectRepository
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService

ORIGIN = "http://test"
ADMIN_PASSWORD = "secure-admin-2026"


class DeterministicTenantProvider:
    def __init__(self) -> None:
        self.fetch_count = 0

    def list_spaces(self) -> tuple[ProviderSpace, ...]:
        return (ProviderSpace(external_id="space-1", title="研发知识库"),)

    def list_nodes(self, external_space_id: str) -> tuple[ProviderNode, ...]:
        return (
            ProviderNode(
                external_id="root-node",
                external_space_id=external_space_id,
                source_id="docx:doc-1",
                title="架构规范",
                kind=ProviderNodeKind.DOCUMENT,
                provider_revision="rev-1",
            ),
        )

    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
        self.fetch_count += 1
        normalized = {"type": "feishu-docx-raw", "text": "# 架构规范\n不可共享仓库"}
        return ProviderSnapshot(
            source_id=source_id,
            provider_revision="rev-1",
            content_type="text/plain; charset=utf-8",
            normalized_content=normalized,
            normalized_text="# 架构规范\n不可共享仓库",
            content_sha256=sha256_json(normalized),
            source_url="https://example.invalid/wiki/doc-1",
            fetched_at=datetime(2026, 9, 2, tzinfo=UTC),
        )


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 2, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class ChangingRevisionProvider(DeterministicTenantProvider):
    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
        self.fetch_count += 1
        normalized = {
            "type": "feishu-docx-raw",
            "text": f"content-version-{self.fetch_count}",
        }
        return ProviderSnapshot(
            source_id=source_id,
            provider_revision="same-revision",
            content_type="text/plain; charset=utf-8",
            normalized_content=normalized,
            normalized_text=str(normalized["text"]),
            content_sha256=sha256_json(normalized),
            fetched_at=datetime(2026, 9, 2, tzinfo=UTC),
        )


class TransientTenantProvider(DeterministicTenantProvider):
    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
        if self.fetch_count == 0:
            self.fetch_count += 1
            raise ProviderFailure(
                "FEISHU_RATE_LIMITED",
                "try later",
                retry_after_seconds=1,
            )
        return super().fetch_snapshot(source_id)


class FailingSourceTenantProvider(DeterministicTenantProvider):
    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code

    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
        self.fetch_count += 1
        raise ProviderFailure(self.code, f"failed source {source_id}", unavailable=True)


class MutableDirectoryTenantProvider(DeterministicTenantProvider):
    def __init__(self) -> None:
        super().__init__()
        self.nodes = super().list_nodes("space-1")

    def list_nodes(self, external_space_id: str) -> tuple[ProviderNode, ...]:
        return tuple(
            node.model_copy(update={"external_space_id": external_space_id}) for node in self.nodes
        )


class ConcurrentTenantProvider(DeterministicTenantProvider):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def list_nodes(self, external_space_id: str) -> tuple[ProviderNode, ...]:
        return tuple(
            ProviderNode(
                external_id="root-node" if index == 0 else f"node-{index}",
                external_space_id=external_space_id,
                parent_external_id=None if index == 0 else "root-node",
                source_id=f"docx:doc-{index}",
                title=f"文档 {index}",
                kind=ProviderNodeKind.DOCUMENT,
                provider_revision="rev-1",
            )
            for index in range(3)
        )

    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.04)
            normalized = {"type": "feishu-docx-raw", "text": source_id}
            return ProviderSnapshot(
                source_id=source_id,
                provider_revision="rev-1",
                content_type="text/plain; charset=utf-8",
                normalized_content=normalized,
                normalized_text=source_id,
                content_sha256=sha256_json(normalized),
                fetched_at=datetime(2026, 9, 2, tzinfo=UTC),
            )
        finally:
            with self._lock:
                self.active -= 1


class DeterministicTenantResolver:
    def __init__(self, provider: DeterministicTenantProvider) -> None:
        self.provider = provider

    def resolve(self, _connection: TenantConnection) -> DeterministicTenantProvider:
        return self.provider


class DeterministicWorkspaceProvisioner:
    def provision(self, repository_ref: str) -> str:
        return f"seed:{repository_ref}"

    def reset(self, repository_ref: str) -> str:
        return f"reset:{repository_ref}"

    def revision(self, repository_ref: str) -> str:
        return f"head:{repository_ref}"


def _app(
    database: Path,
    *,
    resolver: TenantKnowledgeProviderResolver | None = None,
    clock: MutableClock | None = None,
):  # type: ignore[no-untyped-def]
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    tenant_knowledge = TenantKnowledgeManager(
        SQLiteTenantKnowledgeRepository(database),
        provider_resolver=resolver,
        artifact_storage=ContentAddressedArtifactStorage(database.parent / "artifacts"),
        clock=clock,
    )
    projects = ProjectCatalog(
        SQLiteProjectRepository(database),
        DeterministicWorkspaceProvisioner(),
    )
    projects.configure_knowledge_binding_validator(tenant_knowledge.require_binding)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )
    return create_app(
        coordinator,
        identity=identity,
        projects=projects,
        tenant_knowledge=tenant_knowledge,
    )


@pytest.mark.anyio
async def test_administrator_creates_tenant_connection_from_secret_references(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path / "tenant-knowledge.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        created = await client.post(
            "/v1/knowledge/connections",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={
                "provider_kind": "feishu",
                "display_name": "研发知识库",
                "app_id_ref": "env:FEISHU_APP_ID",
                "app_secret_ref": "env:FEISHU_APP_SECRET",
            },
        )

    assert created.status_code == 201
    assert created.json()["access_model"] == "tenant-service-principal-v1"
    assert created.json()["app_id_ref"] == "env:FEISHU_APP_ID"
    assert created.json()["app_secret_ref"] == "env:FEISHU_APP_SECRET"
    assert "app_secret" not in created.text.replace("app_secret_ref", "")


@pytest.mark.anyio
async def test_connection_diagnose_records_a_fresh_tenant_permission_probe(
    tmp_path: Path,
) -> None:
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(DeterministicTenantProvider()),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        created = await client.post(
            "/v1/knowledge/connections",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={
                "provider_kind": "feishu",
                "display_name": "研发知识库",
                "app_id_ref": "env:FEISHU_APP_ID",
                "app_secret_ref": "env:FEISHU_APP_SECRET",
            },
        )
        diagnosed = await client.post(
            f"/v1/knowledge/connections/{created.json()['id']}/diagnose",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
        )

    assert diagnosed.status_code == 200
    assert diagnosed.json()["status"] == "ready"
    assert diagnosed.json()["last_diagnosed_at"] is not None
    assert diagnosed.json()["last_error_code"] is None


@pytest.mark.anyio
async def test_ready_connection_binds_an_existing_space_and_discovers_nodes(
    tmp_path: Path,
) -> None:
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(DeterministicTenantProvider()),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        headers = {
            "Origin": ORIGIN,
            "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
        }
        connection = await client.post(
            "/v1/knowledge/connections",
            headers=headers,
            json={
                "provider_kind": "feishu",
                "display_name": "研发知识库",
                "app_id_ref": "env:FEISHU_APP_ID",
                "app_secret_ref": "env:FEISHU_APP_SECRET",
            },
        )
        diagnosed = await client.post(
            f"/v1/knowledge/connections/{connection.json()['id']}/diagnose",
            headers=headers,
        )
        assert diagnosed.json()["status"] == "ready"
        spaces = await client.get(
            f"/v1/knowledge/connections/{connection.json()['id']}/spaces"
        )
        binding = await client.post(
            "/v1/knowledge/provider-bindings-v2",
            headers=headers,
            json={
                "connection_id": connection.json()["id"],
                "display_name": "研发 Wiki",
                "external_space_id": "space-1",
                "root_node_token": "root-node",
            },
        )
        assert binding.status_code == 201
        nodes = await client.get(f"/v1/knowledge/provider-bindings-v2/{binding.json()['id']}/nodes")

    assert spaces.status_code == 200
    assert spaces.json() == [{"external_id": "space-1", "title": "研发知识库"}]
    assert binding.json()["status"] == "ready"
    assert binding.json()["authorization_version"] == 1
    assert binding.json()["last_permission_probe_at"] is not None
    assert [node["external_id"] for node in nodes.json()] == ["root-node"]


@pytest.mark.anyio
async def test_administrator_approves_a_tenant_binding_for_one_project(
    tmp_path: Path,
) -> None:
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(DeterministicTenantProvider()),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        headers = {
            "Origin": ORIGIN,
            "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
        }
        project = await client.post(
            "/v1/projects",
            headers=headers,
            json={
                "id": "knowledge-project",
                "name": "Knowledge Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        assert project.status_code == 201
        connection = await client.post(
            "/v1/knowledge/connections",
            headers=headers,
            json={
                "provider_kind": "feishu",
                "display_name": "研发知识库",
                "app_id_ref": "env:FEISHU_APP_ID",
                "app_secret_ref": "env:FEISHU_APP_SECRET",
            },
        )
        await client.post(
            f"/v1/knowledge/connections/{connection.json()['id']}/diagnose",
            headers=headers,
        )
        binding = await client.post(
            "/v1/knowledge/provider-bindings-v2",
            headers=headers,
            json={
                "connection_id": connection.json()["id"],
                "display_name": "研发 Wiki",
                "external_space_id": "space-1",
                "root_node_token": "root-node",
            },
        )

        approval = await client.put(
            f"/v1/projects/knowledge-project/knowledge-source-approvals/{binding.json()['id']}",
            headers=headers,
            json={"enabled": True, "rag_enabled": True},
        )

    assert approval.status_code == 200
    assert approval.json()["project_id"] == "knowledge-project"
    assert approval.json()["binding_id"] == binding.json()["id"]
    assert approval.json()["version"] == 1


@pytest.mark.anyio
async def test_sync_job_is_durable_content_addressed_and_idempotent(tmp_path: Path) -> None:
    provider = DeterministicTenantProvider()
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(provider),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        headers = {
            "Origin": ORIGIN,
            "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
        }
        await client.post(
            "/v1/projects",
            headers=headers,
            json={
                "id": "sync-project",
                "name": "Sync Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        connection = await client.post(
            "/v1/knowledge/connections",
            headers=headers,
            json={
                "provider_kind": "feishu",
                "display_name": "研发知识库",
                "app_id_ref": "env:FEISHU_APP_ID",
                "app_secret_ref": "env:FEISHU_APP_SECRET",
            },
        )
        await client.post(
            f"/v1/knowledge/connections/{connection.json()['id']}/diagnose",
            headers=headers,
        )
        binding = await client.post(
            "/v1/knowledge/provider-bindings-v2",
            headers=headers,
            json={
                "connection_id": connection.json()["id"],
                "display_name": "研发 Wiki",
                "external_space_id": "space-1",
                "root_node_token": "root-node",
            },
        )
        await client.put(
            f"/v1/projects/sync-project/knowledge-source-approvals/{binding.json()['id']}",
            headers=headers,
            json={"enabled": True, "rag_enabled": False},
        )
        project_nodes = await client.get(
            f"/v1/projects/sync-project/knowledge-bindings/{binding.json()['id']}/nodes"
        )
        request_body = {
            "binding_id": binding.json()["id"],
            "source_id": "docx:doc-1",
            "idempotency_key": "sync-project-doc-1-rev-1",
        }
        first = await client.post(
            "/v1/projects/sync-project/knowledge-sync-jobs",
            headers=headers,
            json=request_body,
        )
        second = await client.post(
            "/v1/projects/sync-project/knowledge-sync-jobs",
            headers=headers,
            json=request_body,
        )
        jobs = await client.get(
            "/v1/projects/sync-project/knowledge-sync-jobs",
            params={"binding_id": binding.json()["id"]},
        )
        snapshots = await client.get(
            "/v1/projects/sync-project/knowledge-snapshots",
            params={"binding_id": binding.json()["id"]},
        )

    assert first.status_code == 202
    assert project_nodes.status_code == 200
    assert [item["source_id"] for item in project_nodes.json()] == ["docx:doc-1"]
    assert first.json()["status"] == "succeeded"
    assert first.json()["snapshot_id"] is not None
    assert first.json()["snapshot_sha256"] is not None
    assert second.json()["id"] == first.json()["id"]
    assert provider.fetch_count == 1
    assert jobs.status_code == 200
    assert [item["id"] for item in jobs.json()] == [first.json()["id"]]
    assert snapshots.status_code == 200
    assert [item["id"] for item in snapshots.json()] == [first.json()["snapshot_id"]]


@pytest.mark.anyio
async def test_sync_requires_project_approved_source_scope(tmp_path: Path) -> None:
    provider = DeterministicTenantProvider()
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(provider),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        headers = {
            "Origin": ORIGIN,
            "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
        }
        await client.post(
            "/v1/projects",
            headers=headers,
            json={
                "id": "unapproved-project",
                "name": "Unapproved",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        connection = await client.post(
            "/v1/knowledge/connections",
            headers=headers,
            json={
                "provider_kind": "feishu",
                "display_name": "研发知识库",
                "app_id_ref": "env:FEISHU_APP_ID",
                "app_secret_ref": "env:FEISHU_APP_SECRET",
            },
        )
        await client.post(
            f"/v1/knowledge/connections/{connection.json()['id']}/diagnose",
            headers=headers,
        )
        binding = await client.post(
            "/v1/knowledge/provider-bindings-v2",
            headers=headers,
            json={
                "connection_id": connection.json()["id"],
                "display_name": "研发 Wiki",
                "external_space_id": "space-1",
                "root_node_token": "root-node",
            },
        )
        denied = await client.post(
            "/v1/projects/unapproved-project/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding.json()["id"],
                "source_id": "docx:doc-1",
                "idempotency_key": "not-approved",
            },
        )
        denied_nodes = await client.get(
            f"/v1/projects/unapproved-project/knowledge-bindings/{binding.json()['id']}/nodes"
        )
        denied_jobs = await client.get(
            "/v1/projects/unapproved-project/knowledge-sync-jobs",
            params={"binding_id": binding.json()["id"]},
        )
        denied_snapshots = await client.get(
            "/v1/projects/unapproved-project/knowledge-snapshots",
            params={"binding_id": binding.json()["id"]},
        )

    assert denied.status_code == 403
    assert denied.json()["code"] == "KNOWLEDGE_SOURCE_NOT_APPROVED"
    assert [response.status_code for response in (denied_nodes, denied_jobs, denied_snapshots)] == [
        403,
        403,
        403,
    ]
    assert {
        response.json()["code"] for response in (denied_nodes, denied_jobs, denied_snapshots)
    } == {"KNOWLEDGE_SOURCE_NOT_APPROVED"}
    assert provider.fetch_count == 0


@pytest.mark.anyio
async def test_sync_fails_closed_when_permission_probe_is_stale(tmp_path: Path) -> None:
    clock = MutableClock()
    provider = DeterministicTenantProvider()
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(provider),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(client, "stale-project")
        clock.advance(timedelta(minutes=31))
        response = await client.post(
            "/v1/projects/stale-project/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding_id,
                "source_id": "docx:doc-1",
                "idempotency_key": "stale-probe",
            },
        )

    assert response.status_code == 409
    assert response.json()["code"] == "KNOWLEDGE_PERMISSION_PROBE_STALE"
    assert provider.fetch_count == 0


@pytest.mark.anyio
async def test_sync_rejects_source_id_outside_cached_binding_scope(tmp_path: Path) -> None:
    provider = DeterministicTenantProvider()
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(provider),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(client, "scope-project")
        denied = await client.post(
            "/v1/projects/scope-project/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding_id,
                "source_id": "docx:not-in-scope",
                "idempotency_key": "scope-denied",
            },
        )

    assert denied.status_code == 403
    assert denied.json()["code"] == "KNOWLEDGE_SOURCE_SCOPE_DENIED"
    assert provider.fetch_count == 0


@pytest.mark.anyio
async def test_same_provider_revision_with_different_hash_is_quarantined(
    tmp_path: Path,
) -> None:
    provider = ChangingRevisionProvider()
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(provider),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(client, "conflict-project")
        first = await client.post(
            "/v1/projects/conflict-project/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding_id,
                "source_id": "docx:doc-1",
                "idempotency_key": "conflict-1",
            },
        )
        conflict = await client.post(
            "/v1/projects/conflict-project/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding_id,
                "source_id": "docx:doc-1",
                "idempotency_key": "conflict-2",
            },
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "KNOWLEDGE_PROVIDER_REVISION_HASH_CONFLICT"


@pytest.mark.anyio
async def test_retryable_provider_failure_enters_retry_wait_and_resumes(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    provider = TransientTenantProvider()
    app = _app(
        tmp_path / "tenant-knowledge.sqlite",
        resolver=DeterministicTenantResolver(provider),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(client, "retry-project")
        body = {
            "binding_id": binding_id,
            "source_id": "docx:doc-1",
            "idempotency_key": "retry-job",
        }
        first = await client.post(
            "/v1/projects/retry-project/knowledge-sync-jobs",
            headers=headers,
            json=body,
        )
        clock.advance(timedelta(seconds=3))
        resumed = await client.post(
            "/v1/projects/retry-project/knowledge-sync-jobs",
            headers=headers,
            json=body,
        )

    assert first.status_code == 202
    assert first.json()["status"] == "retry_wait"
    assert first.json()["error_code"] == "FEISHU_RATE_LIMITED"
    assert resumed.json()["status"] == "succeeded"
    assert provider.fetch_count == 2


@pytest.mark.anyio
async def test_expired_sync_lease_is_recovered_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "tenant-knowledge.sqlite"
    clock = MutableClock()
    provider = TransientTenantProvider()
    app = _app(
        database,
        resolver=DeterministicTenantResolver(provider),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(client, "lease-project")
        body = {
            "binding_id": binding_id,
            "source_id": "docx:doc-1",
            "idempotency_key": "lease-recovery",
        }
        waiting = await client.post(
            "/v1/projects/lease-project/knowledge-sync-jobs",
            headers=headers,
            json=body,
        )
        assert waiting.json()["status"] == "retry_wait"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """UPDATE knowledge_sync_jobs SET status='running',retry_at=NULL,
                lease_owner='dead-worker',lease_expires_at=? WHERE id=?""",
                (
                    (clock.now() - timedelta(seconds=1)).isoformat(),
                    waiting.json()["id"],
                ),
            )
        recovered = await client.post(
            "/v1/projects/lease-project/knowledge-sync-jobs",
            headers=headers,
            json=body,
        )

    assert recovered.status_code == 202
    assert recovered.json()["status"] == "succeeded"
    assert recovered.json()["attempt"] == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure_code", "expected_source_status"),
    (
        ("FEISHU_SOURCE_NOT_FOUND", "tombstoned"),
        ("FEISHU_SOURCE_PERMISSION_REVOKED", "quarantined"),
    ),
)
async def test_single_source_failure_is_isolated_without_degrading_connection(
    tmp_path: Path,
    failure_code: str,
    expected_source_status: str,
) -> None:
    database = tmp_path / f"source-isolation-{expected_source_status}.sqlite"
    app = _app(
        database,
        resolver=DeterministicTenantResolver(FailingSourceTenantProvider(failure_code)),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(
            client, f"source-{expected_source_status}"
        )
        response = await client.post(
            f"/v1/projects/source-{expected_source_status}/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding_id,
                "source_id": "docx:doc-1",
                "idempotency_key": f"source-{expected_source_status}",
            },
        )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == failure_code
    with sqlite3.connect(database) as connection:
        source_status = connection.execute(
            """SELECT status FROM knowledge_provider_source_heads_v2
            WHERE binding_id=? AND source_id='docx:doc-1'""",
            (binding_id,),
        ).fetchone()
        connection_status = connection.execute(
            """SELECT knowledge_connections.status,knowledge_connections.authorization_version
            FROM knowledge_connections JOIN knowledge_provider_bindings_v2
            ON knowledge_provider_bindings_v2.connection_id=knowledge_connections.id
            WHERE knowledge_provider_bindings_v2.id=?""",
            (binding_id,),
        ).fetchone()
    assert source_status == (expected_source_status,)
    assert connection_status == ("ready", 2)


@pytest.mark.anyio
async def test_persistent_tenant_authorization_failure_degrades_connection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "connection-revoked.sqlite"
    app = _app(
        database,
        resolver=DeterministicTenantResolver(
            FailingSourceTenantProvider("FEISHU_PERMISSION_REVOKED")
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(client, "connection-revoked")
        response = await client.post(
            "/v1/projects/connection-revoked/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding_id,
                "source_id": "docx:doc-1",
                "idempotency_key": "connection-revoked",
            },
        )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    with sqlite3.connect(database) as connection:
        connection_status = connection.execute(
            """SELECT knowledge_connections.status,knowledge_connections.authorization_version,
            knowledge_connections.last_error_code
            FROM knowledge_connections JOIN knowledge_provider_bindings_v2
            ON knowledge_provider_bindings_v2.connection_id=knowledge_connections.id
            WHERE knowledge_provider_bindings_v2.id=?""",
            (binding_id,),
        ).fetchone()
    assert connection_status == ("degraded", 3, "FEISHU_PERMISSION_REVOKED")


@pytest.mark.anyio
async def test_directory_reconciliation_tombstones_source_removed_from_scope(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source-reconciliation.sqlite"
    provider = MutableDirectoryTenantProvider()
    app = _app(
        database,
        resolver=DeterministicTenantResolver(provider),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(client, "source-reconcile")
        synced = await client.post(
            "/v1/projects/source-reconcile/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding_id,
                "source_id": "docx:doc-1",
                "idempotency_key": "source-before-removal",
            },
        )
        assert synced.json()["status"] == "succeeded"
        provider.nodes = ()
        refreshed = await client.post(
            f"/v1/knowledge/provider-bindings-v2/{binding_id}/diagnose",
            headers=headers,
        )

    assert refreshed.status_code == 200
    assert refreshed.json()["authorization_version"] == 2
    with sqlite3.connect(database) as connection:
        source_head = connection.execute(
            """SELECT status,authorization_version
            FROM knowledge_provider_source_heads_v2
            WHERE binding_id=? AND source_id='docx:doc-1'""",
            (binding_id,),
        ).fetchone()
    assert source_head == ("tombstoned", 2)


@pytest.mark.anyio
async def test_scheduler_only_enqueues_persistent_jobs_in_stable_poll_buckets(
    tmp_path: Path,
) -> None:
    database = tmp_path / "scheduled-sync.sqlite"
    clock = MutableClock()
    provider = DeterministicTenantProvider()
    app = _app(
        database,
        resolver=DeterministicTenantResolver(provider),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await _create_approved_source(client, "scheduled-project")

    manager, policy = _sync_runtime(database, provider, clock)
    scheduler = KnowledgeSyncScheduler(
        manager,
        SQLiteProjectRepository(database),
        policy=policy,
        clock=clock,
    )

    first = scheduler.enqueue_due()
    duplicate = scheduler.enqueue_due()
    clock.advance(timedelta(minutes=15))
    next_bucket = scheduler.enqueue_due()

    assert len(first) == 1
    assert first[0].status == "queued"
    assert first[0].attempt == 0
    assert first[0].max_attempts == 5
    assert duplicate == ()
    assert len(next_bucket) == 1
    assert next_bucket[0].idempotency_key != first[0].idempotency_key
    assert provider.fetch_count == 0


@pytest.mark.anyio
async def test_worker_resumes_due_retry_without_a_second_http_request(tmp_path: Path) -> None:
    database = tmp_path / "scheduled-retry.sqlite"
    clock = MutableClock()
    provider = TransientTenantProvider()
    app = _app(
        database,
        resolver=DeterministicTenantResolver(provider),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await _create_approved_source(client, "scheduled-retry")

    manager, policy = _sync_runtime(database, provider, clock)
    scheduler = KnowledgeSyncScheduler(
        manager,
        SQLiteProjectRepository(database),
        policy=policy,
        clock=clock,
    )
    worker = KnowledgeSyncWorker(manager, policy=policy, clock=clock)
    queued = scheduler.enqueue_due()

    first_attempt = await worker.run_once()
    clock.advance(timedelta(seconds=3))
    second_attempt = await worker.run_once()

    assert len(queued) == 1
    assert first_attempt[0].status == "retry_wait"
    assert second_attempt[0].status == "succeeded"
    assert second_attempt[0].attempt == 2
    assert provider.fetch_count == 2


@pytest.mark.anyio
async def test_supervisor_autonomously_schedules_and_runs_persistent_job(
    tmp_path: Path,
) -> None:
    database = tmp_path / "scheduled-supervisor.sqlite"
    clock = MutableClock()
    provider = DeterministicTenantProvider()
    app = _app(
        database,
        resolver=DeterministicTenantResolver(provider),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, _headers = await _create_approved_source(client, "scheduled-supervisor")

    manager, base_policy = _sync_runtime(database, provider, clock)
    policy = KnowledgeSyncPolicy(
        poll_interval=base_policy.poll_interval,
        directory_reconciliation_interval=base_policy.directory_reconciliation_interval,
        worker_concurrency=2,
        max_attempts=5,
        worker_batch_size=32,
        supervisor_tick_seconds=0.01,
    )
    supervisor = KnowledgeSyncSupervisor(
        KnowledgeSyncScheduler(
            manager,
            SQLiteProjectRepository(database),
            policy=policy,
            clock=clock,
        ),
        KnowledgeDirectoryReconciler(manager, policy=policy, clock=clock),
        KnowledgeSyncWorker(manager, policy=policy, clock=clock),
        policy=policy,
    )

    supervisor.start()
    for _ in range(100):
        jobs = manager.repository.list_sync_jobs("scheduled-supervisor", binding_id)
        if jobs and jobs[0].status == "succeeded":
            break
        await asyncio.sleep(0.01)
    await supervisor.stop()

    jobs = manager.repository.list_sync_jobs("scheduled-supervisor", binding_id)
    assert len(jobs) == 1
    assert jobs[0].status == "succeeded"
    assert provider.fetch_count == 1


@pytest.mark.anyio
async def test_worker_never_exceeds_configured_two_fetches(tmp_path: Path) -> None:
    database = tmp_path / "scheduled-concurrency.sqlite"
    clock = MutableClock()
    provider = ConcurrentTenantProvider()
    app = _app(
        database,
        resolver=DeterministicTenantResolver(provider),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await _create_approved_source(client, "scheduled-concurrency")

    manager, policy = _sync_runtime(database, provider, clock)
    scheduler = KnowledgeSyncScheduler(
        manager,
        SQLiteProjectRepository(database),
        policy=policy,
        clock=clock,
    )
    worker = KnowledgeSyncWorker(manager, policy=policy, clock=clock)

    assert len(scheduler.enqueue_due()) == 3
    completed = await worker.run_once()

    assert len(completed) == 3
    assert {job.status for job in completed} == {"succeeded"}
    assert provider.max_active == 2


@pytest.mark.anyio
async def test_source_freshness_and_daily_directory_reconciliation_fail_closed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "scheduled-reconciliation.sqlite"
    clock = MutableClock()
    provider = MutableDirectoryTenantProvider()
    app = _app(
        database,
        resolver=DeterministicTenantResolver(provider),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        binding_id, headers = await _create_approved_source(client, "scheduled-reconcile")
        synced = await client.post(
            "/v1/projects/scheduled-reconcile/knowledge-sync-jobs",
            headers=headers,
            json={
                "binding_id": binding_id,
                "source_id": "docx:doc-1",
                "idempotency_key": "initial-source-freshness",
            },
        )
        assert synced.json()["status"] == "succeeded"

    manager, policy = _sync_runtime(database, provider, clock)
    assert manager.available_source_ids(binding_id) == ("docx:doc-1",)
    clock.advance(timedelta(minutes=31))
    assert manager.available_source_ids(binding_id) == ()

    provider.nodes = ()
    clock.advance(timedelta(hours=24))
    reconciler = KnowledgeDirectoryReconciler(manager, policy=policy, clock=clock)
    reconciled = reconciler.reconcile_due()

    assert tuple(binding.id for binding in reconciled) == (binding_id,)
    with sqlite3.connect(database) as connection:
        source_status = connection.execute(
            """SELECT status FROM knowledge_provider_source_heads_v2
            WHERE binding_id=? AND source_id='docx:doc-1'""",
            (binding_id,),
        ).fetchone()
    assert source_status == ("tombstoned",)


async def _create_approved_source(
    client: AsyncClient, project_id: str
) -> tuple[str, dict[str, str]]:
    await client.post(
        "/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
    }
    await client.post(
        "/v1/projects",
        headers=headers,
        json={
            "id": project_id,
            "name": project_id,
            "default_pipeline_revision_id": "backend-delivery:1",
        },
    )
    connection = await client.post(
        "/v1/knowledge/connections",
        headers=headers,
        json={
            "provider_kind": "feishu",
            "display_name": project_id,
            "app_id_ref": f"env:FEISHU_{project_id.upper().replace('-', '_')}_APP_ID",
            "app_secret_ref": f"env:FEISHU_{project_id.upper().replace('-', '_')}_SECRET",
        },
    )
    await client.post(
        f"/v1/knowledge/connections/{connection.json()['id']}/diagnose",
        headers=headers,
    )
    binding = await client.post(
        "/v1/knowledge/provider-bindings-v2",
        headers=headers,
        json={
            "connection_id": connection.json()["id"],
            "display_name": project_id,
            "external_space_id": "space-1",
            "root_node_token": "root-node",
        },
    )
    await client.put(
        f"/v1/projects/{project_id}/knowledge-source-approvals/{binding.json()['id']}",
        headers=headers,
        json={"enabled": True, "rag_enabled": True},
    )
    return str(binding.json()["id"]), headers


def _sync_runtime(
    database: Path,
    provider: DeterministicTenantProvider,
    clock: MutableClock,
) -> tuple[TenantKnowledgeManager, KnowledgeSyncPolicy]:
    manager = TenantKnowledgeManager(
        SQLiteTenantKnowledgeRepository(database),
        provider_resolver=DeterministicTenantResolver(provider),
        artifact_storage=ContentAddressedArtifactStorage(database.parent / "scheduled-artifacts"),
        clock=clock,
    )
    return manager, KnowledgeSyncPolicy(
        poll_interval=timedelta(minutes=15),
        directory_reconciliation_interval=timedelta(hours=24),
        worker_concurrency=2,
        max_attempts=5,
        worker_batch_size=32,
    )


def test_feishu_tenant_provider_uses_app_service_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "tenant-app-secret")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/open-apis/auth/v3/tenant_access_token/internal":
            assert request.headers.get("authorization") is None
            assert request.read() == b'{"app_id":"cli_app_id","app_secret":"tenant-app-secret"}'
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        assert request.headers["Authorization"] == "Bearer tenant-token"
        if request.url.path == "/open-apis/wiki/v2/spaces":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"space_id": "space-1", "name": "研发知识库"}],
                        "has_more": False,
                    },
                },
            )
        return httpx.Response(404, json={"code": 404})

    client = httpx.Client(
        base_url="https://open.feishu.cn",
        transport=httpx.MockTransport(handler),
    )
    connection = TenantConnection(
        id="connection-1",
        provider_kind=KnowledgeProviderKind.FEISHU,
        display_name="研发知识库",
        app_id_ref="env:FEISHU_APP_ID",
        app_secret_ref="env:FEISHU_APP_SECRET",
        status="unverified",
        version=1,
        created_by="admin-1",
        created_at="2026-09-02T00:00:00Z",
        updated_at="2026-09-02T00:00:00Z",
    )
    provider = FeishuTenantKnowledgeProvider(
        connection,
        EnvironmentSecretResolver(),
        client=client,
    )

    spaces = provider.list_spaces()

    assert [space.external_id for space in spaces] == ["space-1"]
    assert [request.url.path for request in requests] == [
        "/open-apis/auth/v3/tenant_access_token/internal",
        "/open-apis/wiki/v2/spaces",
    ]


def test_feishu_tenant_provider_refreshes_token_once_after_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "tenant-app-secret")
    token_requests = 0
    space_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests, space_requests
        if request.url.path == "/open-apis/auth/v3/tenant_access_token/internal":
            token_requests += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": f"tenant-token-{token_requests}",
                    "expire": 7200,
                },
            )
        space_requests += 1
        if request.headers["Authorization"] == "Bearer tenant-token-1":
            return httpx.Response(401, json={"code": 99991663})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"items": [], "has_more": False},
            },
        )

    client = httpx.Client(
        base_url="https://open.feishu.cn",
        transport=httpx.MockTransport(handler),
    )
    connection = TenantConnection(
        id="connection-refresh",
        provider_kind=KnowledgeProviderKind.FEISHU,
        display_name="研发知识库",
        app_id_ref="env:FEISHU_APP_ID",
        app_secret_ref="env:FEISHU_APP_SECRET",
        status="ready",
        version=1,
        created_by="admin-1",
        created_at="2026-09-02T00:00:00Z",
        updated_at="2026-09-02T00:00:00Z",
    )
    provider = FeishuTenantKnowledgeProvider(
        connection,
        EnvironmentSecretResolver(),
        client=client,
    )

    assert provider.list_spaces() == ()
    assert token_requests == 2
    assert space_requests == 2


@pytest.mark.parametrize(
    ("status_code", "expected_code", "expected_unavailable", "expected_retry"),
    (
        (403, "FEISHU_SOURCE_PERMISSION_REVOKED", True, None),
        (404, "FEISHU_SOURCE_NOT_FOUND", False, None),
        (429, "FEISHU_RATE_LIMITED", False, 7.0),
        (503, "FEISHU_UNAVAILABLE", True, None),
    ),
)
def test_feishu_document_failure_is_classified_at_source_scope(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_code: str,
    expected_unavailable: bool,
    expected_retry: float | None,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "tenant-app-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        return httpx.Response(
            status_code,
            headers={"Retry-After": "7"} if status_code == 429 else None,
            json={"code": status_code},
        )

    provider = FeishuTenantKnowledgeProvider(
        TenantConnection(
            id="connection-source-error",
            provider_kind=KnowledgeProviderKind.FEISHU,
            display_name="研发知识库",
            app_id_ref="env:FEISHU_APP_ID",
            app_secret_ref="env:FEISHU_APP_SECRET",
            status="ready",
            version=1,
            created_by="admin-1",
            created_at="2026-09-02T00:00:00Z",
            updated_at="2026-09-02T00:00:00Z",
        ),
        EnvironmentSecretResolver(),
        client=httpx.Client(
            base_url="https://open.feishu.cn",
            transport=httpx.MockTransport(handler),
        ),
    )

    with pytest.raises(ProviderFailure) as failure:
        provider.fetch_snapshot("docx:doc-1")

    assert failure.value.code == expected_code
    assert failure.value.unavailable is expected_unavailable
    assert failure.value.retry_after_seconds == expected_retry
