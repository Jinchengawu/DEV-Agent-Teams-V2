from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.agents import AgentRunLedger, ArtifactEnvelope
from agent_team_os.modules.delivery import RoleDocumentPublicationRequest
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
    UserCreate,
)
from agent_team_os.modules.knowledge import (
    KnowledgePublicationLedger,
    KnowledgePublisher,
    SQLiteWikiRepository,
    WikiService,
)
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService

ORIGIN = "http://test"
ADMIN_PASSWORD = "secure-admin-2026"
VIEWER_PASSWORD = "secure-viewer-2026"


def _app(database: Path) -> FastAPI:
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    administrator = identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    identity.create_user(
        administrator,
        UserCreate(
            username="viewer",
            display_name="只读审计员",
            role="viewer",
            password=VIEWER_PASSWORD,
        ),
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )
    publications = KnowledgePublicationLedger(database)
    return create_app(
        coordinator,
        identity=identity,
        knowledge=WikiService(SQLiteWikiRepository(database)),
        knowledge_publications=publications,
        knowledge_publisher=KnowledgePublisher(database, publications),
    )


async def _login(client: AsyncClient, username: str, password: str) -> None:
    response = await client.post(
        "/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def _mutation_headers(client: AsyncClient) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
    }


async def _wait_for_delivery(
    client: AsyncClient, delivery_id: str, expected_status: str
) -> dict[str, object]:
    for _ in range(200):
        response = await client.get(f"/v1/deliveries/{delivery_id}")
        assert response.status_code == 200
        delivery = response.json()
        if delivery["status"] == expected_status:
            return delivery
        await asyncio.sleep(0.01)
    raise AssertionError(f"delivery did not reach {expected_status}")


@pytest.mark.anyio
async def test_wiki_routes_use_session_rbac_and_real_repository(tmp_path: Path) -> None:
    app = _app(tmp_path / "wiki-api.sqlite")

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as anonymous:
        response = await anonymous.get("/v1/wiki/spaces")
        assert response.status_code == 401

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as administrator:
        await _login(administrator, "admin", ADMIN_PASSWORD)
        space = await administrator.post(
            "/v1/wiki/spaces",
            headers=_mutation_headers(administrator),
            json={"name": "交付手册", "description": "可追溯的交付知识"},
        )
        assert space.status_code == 201
        document = await administrator.post(
            "/v1/wiki/documents",
            headers=_mutation_headers(administrator),
            json={
                "space_id": space.json()["id"],
                "title": "候选版本审查",
                "content": {"text": "只接受已验证证据"},
            },
        )
        assert document.status_code == 201

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as viewer:
        await _login(viewer, "viewer", VIEWER_PASSWORD)
        listed = await viewer.get("/v1/wiki/documents")
        assert listed.status_code == 200
        assert listed.json()[0]["title"] == "候选版本审查"

        denied = await viewer.patch(
            f"/v1/wiki/documents/{document.json()['id']}",
            headers=_mutation_headers(viewer),
            json={"expected_version": 1, "title": "越权修改"},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "IDENTITY_PERMISSION_DENIED"


@pytest.mark.anyio
async def test_wiki_get_does_not_project_delivery_artifacts_into_documents(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path / "wiki-read-is-pure.sqlite")

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as administrator:
        await _login(administrator, "admin", ADMIN_PASSWORD)
        created_response = await administrator.post(
            "/v1/deliveries",
            headers=_mutation_headers(administrator),
            json={
                "workspace_id": "backend-demo",
                "user_request": "确认知识读取不会隐式写入交付产物。",
            },
        )
        assert created_response.status_code == 202
        await _wait_for_delivery(
            administrator,
            created_response.json()["id"],
            "awaiting_plan_decision",
        )

        listed_spaces = await administrator.get("/v1/wiki/spaces")
        assert listed_spaces.status_code == 200
        documents_response = await administrator.get(
            "/v1/wiki/documents?space_id=system:delivery-evidence"
        )
        assert documents_response.status_code == 200
        assert documents_response.json() == []
        assert all(
            space["id"] != "system:delivery-evidence"
            for space in listed_spaces.json()
        )


@pytest.mark.anyio
async def test_publication_query_and_retry_require_wiki_edit_and_expected_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "publication-http.sqlite"
    app = _app(database)

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin, "admin", ADMIN_PASSWORD)
        created = await admin.post(
            "/v1/deliveries",
            headers=_mutation_headers(admin),
            json={"workspace_id": "backend-demo", "user_request": "发布需求文档"},
        )
        assert created.status_code == 202
        delivery_id = created.json()["id"]
        content = {
            "summary": "发布需求文档",
            "acceptance_criteria": [
                {"id": "AC-001", "statement": "可查询发布状态"}
            ],
        }
        artifact = ArtifactEnvelope(
            contract_id="requirement-artifact-v1",
            artifact_key="primary",
            content=content,
            sha256=sha256_json(content),
        )
        agent_runs = AgentRunLedger(database)
        running = agent_runs.start(
            delivery_id=delivery_id,
            pipeline_revision_id="backend-delivery:1",
            binding_site="requirements.product-manager",
            resolved_binding_hash="a" * 64,
            deployment_snapshot={"id": "pm"},
            runtime_identity="codex-simulated-hermes",
        )
        succeeded = agent_runs.finish(
            running, status="succeeded", artifacts=(artifact,)
        )
        publications = KnowledgePublicationLedger(database)
        pending = publications.register(
            RoleDocumentPublicationRequest(
                project_id="legacy-default",
                delivery_id=delivery_id,
                node_id="requirements",
                binding_site=succeeded.binding_site,
                agent_run_id=succeeded.id,
                artifact_id=artifact.id,
                artifact_key=artifact.artifact_key,
                contract_id=artifact.contract_id,
                artifact_sha256=artifact.sha256,
                runtime_identity=succeeded.runtime_identity,
            )
        )

        listed = await admin.get(
            f"/v1/deliveries/{delivery_id}/knowledge-publications"
        )
        assert listed.status_code == 200
        assert listed.json()[0]["status"] == "pending"

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as viewer:
        await _login(viewer, "viewer", VIEWER_PASSWORD)
        denied = await viewer.post(
            f"/v1/knowledge/publications/{pending.id}/retry",
            headers=_mutation_headers(viewer),
            json={"expected_version": pending.version},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "IDENTITY_PERMISSION_DENIED"

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin, "admin", ADMIN_PASSWORD)
        retried = await admin.post(
            f"/v1/knowledge/publications/{pending.id}/retry",
            headers=_mutation_headers(admin),
            json={"expected_version": pending.version},
        )
        assert retried.status_code == 200
        assert retried.json()["status"] == "published"
        assert retried.json()["target_document_id"]
