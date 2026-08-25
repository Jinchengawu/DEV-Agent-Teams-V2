from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.evidence import (
    EvidenceKind,
    EvidenceLedger,
    EvidenceRecord,
    EvidenceStatus,
    SQLiteEvidenceRepository,
)
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
)
from agent_team_os.modules.knowledge import (
    KnowledgeSearchIndex,
    SQLiteWikiRepository,
    WikiService,
)
from agent_team_os.testing import (
    DeterministicCodeExecutor,
    DeterministicPlanningService,
)

ROOT = Path(__file__).parents[1]


def test_project_knowledge_activity_lists_existing_delivery_evidence(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO evidence_records(
            id,project_id,delivery_id,kind,source_kind,source_id,producer_identity,
            content_sha256,status,payload_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "evidence-1",
                "legacy-default",
                "delivery-1",
                "candidate",
                "delivery",
                "delivery-1:candidate",
                "codex-cli",
                "c" * 64,
                "verified",
                json.dumps({"changed_files": ["src/health.py", "tests/test_health.py"]}),
                "2026-08-04T00:00:00+00:00",
            ),
        )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )
    with TestClient(
        create_app(coordinator, knowledge_search=KnowledgeSearchIndex(database))
    ) as client:
        response = client.get("/v1/knowledge/activity?project_id=legacy-default")

    assert response.status_code == 200
    assert response.json() == [
        {
            "project_id": "legacy-default",
            "source_kind": "evidence",
            "source_id": "evidence-1",
            "delivery_id": "delivery-1",
            "title": "候选变更 · delivery-1",
            "summary": "src/health.py、tests/test_health.py",
            "revision": "1",
            "content_sha256": "c" * 64,
            "occurred_at": "2026-08-04T00:00:00Z",
            "source_link": "/projects/legacy-default/evidence?evidence_id=evidence-1",
        }
    ]


def test_verified_evidence_can_be_idempotently_derived_into_editable_wiki(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    identity.bootstrap(BootstrapRequest(password="admin-password-2026"))
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))
    record = EvidenceRecord(
        id="evidence-derived",
        project_id="legacy-default",
        delivery_id="delivery-derived",
        kind=EvidenceKind.VERIFICATION,
        source_kind="verification-log",
        source_id="delivery-derived:verification",
        producer_identity="codex-cli",
        content_sha256="d" * 64,
        status=EvidenceStatus.VERIFIED,
        payload={"command": "python -m unittest", "exit_code": 0},
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
        verified_at=datetime(2026, 8, 5, tzinfo=UTC),
    )
    evidence.repository.append(record)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )
    app = create_app(
        coordinator,
        identity=identity,
        evidence=evidence,
        knowledge=WikiService(SQLiteWikiRepository(database)),
        knowledge_search=KnowledgeSearchIndex(database),
    )
    with TestClient(app, base_url="http://test") as client:
        login = client.post(
            "/v1/auth/login",
            headers={"Origin": "http://test"},
            json={"username": "admin", "password": "admin-password-2026"},
        )
        headers = {
            "Origin": "http://test",
            "X-CSRF-Token": login.cookies["agent_team_os_csrf"],
        }
        space = client.post(
            "/v1/wiki/spaces",
            headers=headers,
            json={
                "name": "项目经验",
                "scope_kind": "project",
                "project_id": "legacy-default",
            },
        ).json()
        payload = {
            "project_id": "legacy-default",
            "source_kind": "evidence",
            "source_id": record.id,
            "expected_source_sha256": record.content_sha256,
            "target_space_id": space["id"],
            "title": "健康检查验证经验",
        }
        first = client.post("/v1/knowledge/derivations", headers=headers, json=payload)
        second = client.post("/v1/knowledge/derivations", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["document"]["id"] == second.json()["document"]["id"]
    derivation = first.json()["derivation"]
    assert derivation.pop("created_at")
    assert derivation.pop("created_by")
    assert derivation == {
        "document_id": first.json()["document"]["id"],
        "project_id": "legacy-default",
        "target_space_id": space["id"],
        "source_kind": "evidence",
        "source_id": record.id,
        "source_revision": "1",
        "source_sha256": "d" * 64,
    }
