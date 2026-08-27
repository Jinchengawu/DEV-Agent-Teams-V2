from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator, SQLiteDeliveryRepository
from agent_team_os.infrastructure.database import (
    LegacyDatabaseImporter,
    MigrationRunner,
)
from agent_team_os.infrastructure.database.migration import MigrationChecksumError
from agent_team_os.modules.board import BoardProjector
from agent_team_os.modules.evidence import (
    EvidenceKind,
    EvidenceLedger,
    EvidenceStatus,
    SQLiteEvidenceRepository,
)
from agent_team_os.modules.settings import (
    AppSettingsPatch,
    SettingsManager,
    SQLiteSettingsRepository,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.events import ProductEvent
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService

ROOT = Path(__file__).parents[1]


def _database(tmp_path: Path) -> tuple[Path, MigrationRunner]:
    database = tmp_path / "agent-team-os.sqlite"
    runner = MigrationRunner(database, ROOT / "migrations")
    assert runner.migrate() == (*range(1, 19), 20, 21)
    return database, runner


def test_migrations_are_idempotent_and_checksums_are_enforced(tmp_path: Path) -> None:
    database, runner = _database(tmp_path)
    assert runner.migrate() == ()


def test_missing_legacy_journey_hash_is_audited_and_failed_closed(tmp_path: Path) -> None:
    database, runner = _database(tmp_path)
    original = json.dumps(
        {
            "id": "legacy-missing-journey",
            "status": "planning",
            "version": 1,
            "created_at": "2026-08-20T00:00:00+00:00",
            "updated_at": "2026-08-20T00:00:00+00:00",
        }
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO deliveries(id,snapshot_json) VALUES(?,?)",
            ("legacy-missing-journey", original),
        )
        connection.execute("DELETE FROM schema_migrations WHERE version=11")

    assert runner.migrate() == (11,)
    with sqlite3.connect(database) as connection:
        repaired_json = connection.execute(
            "SELECT snapshot_json FROM deliveries WHERE id='legacy-missing-journey'"
        ).fetchone()[0]
        audit = connection.execute(
            """SELECT original_sha256,original_json,migration_action
            FROM legacy_snapshot_audit WHERE aggregate_id='legacy-missing-journey'"""
        ).fetchone()
    repaired = json.loads(repaired_json)
    assert repaired["resolved_journey_sha256"] is None
    assert repaired["status"] == "failed"
    assert repaired["error_code"] == "LEGACY_INCOMPLETE_EVIDENCE"
    assert audit == (
        hashlib.sha256(original.encode("utf-8")).hexdigest(),
        original,
        "normalize-missing-journey-sha256",
    )
    copied = tmp_path / "migrations"
    copied.mkdir()
    for source in (ROOT / "migrations").glob("*.sql"):
        (copied / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    first = copied / "0001_baseline.sql"
    first.write_text(first.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")
    with pytest.raises(MigrationChecksumError):
        MigrationRunner(database, copied).migrate()


def test_legacy_invalid_active_delivery_is_preserved_and_failed_closed(
    tmp_path: Path,
) -> None:
    database, runner = _database(tmp_path)
    legacy = tmp_path / "preview.sqlite"
    snapshot = {
        "id": "delivery-legacy",
        "status": "awaiting_plan_decision",
        "resolved_journey_sha256": "0" * 64,
    }
    with sqlite3.connect(legacy) as connection:
        connection.execute(
            "CREATE TABLE deliveries(id TEXT PRIMARY KEY,snapshot_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO deliveries VALUES(?,?)", (snapshot["id"], json.dumps(snapshot))
        )

    LegacyDatabaseImporter(runner, tmp_path / "backups").import_if_present(
        legacy, tmp_path / "missing-control.sqlite"
    )

    with sqlite3.connect(database) as connection:
        imported = json.loads(
            connection.execute("SELECT snapshot_json FROM deliveries").fetchone()[0]
        )
        audit = connection.execute(
            "SELECT original_json,migration_action FROM legacy_snapshot_audit"
        ).fetchone()
    assert imported["status"] == "failed"
    assert imported["error_code"] == "LEGACY_INCOMPLETE_EVIDENCE"
    assert json.loads(audit[0]) == snapshot
    assert audit[1] == "failed-invalid-active"


def test_evidence_ledger_never_marks_zero_hash_as_verified(tmp_path: Path) -> None:
    database, _runner = _database(tmp_path)
    ledger = EvidenceLedger(SQLiteEvidenceRepository(database))
    records = ledger.sync_delivery(
        {
            "id": "delivery-1",
            "created_at": "2026-08-22T00:00:00+00:00",
            "planning_identity": "codex-simulated-hermes",
            "execution_identity": "codex-cli",
            "journey_revision_id": None,
            "resolved_journey_sha256": "0" * 64,
            "candidate": {
                "candidate_revision": "a" * 40,
                "diff_sha256": "b" * 64,
                "unified_diff": "+real change",
            },
        }
    )
    journey = next(item for item in records if item.kind == EvidenceKind.JOURNEY)
    diff = next(item for item in records if item.kind == EvidenceKind.DIFF)
    assert journey.status == EvidenceStatus.INVALID
    assert journey.content_sha256 is None
    assert diff.status == EvidenceStatus.VERIFIED
    assert ledger.verify(journey.id).status == EvidenceStatus.INVALID


def test_evidence_ledger_prefers_pinned_pipeline_revision(tmp_path: Path) -> None:
    database, _runner = _database(tmp_path)
    ledger = EvidenceLedger(SQLiteEvidenceRepository(database))

    records = ledger.sync_delivery(
        {
            "id": "delivery-pipeline",
            "created_at": "2026-08-23T00:00:00+00:00",
            "planning_identity": "codex-simulated-hermes",
            "pipeline_revision_id": "backend-delivery:3",
            "resolved_pipeline_sha256": "d" * 64,
            "journey_revision_id": None,
            "resolved_journey_sha256": "a" * 64,
        }
    )

    pipeline = next(item for item in records if item.kind == EvidenceKind.JOURNEY)
    assert pipeline.status == EvidenceStatus.VERIFIED
    assert pipeline.source_kind == "pipeline-revision"
    assert pipeline.source_id == "backend-delivery:3"
    assert pipeline.content_sha256 == "d" * 64


def test_settings_use_compare_and_swap_and_keep_security_policy_locked(
    tmp_path: Path,
) -> None:
    database, _runner = _database(tmp_path)
    manager = SettingsManager(SQLiteSettingsRepository(database))
    initial = manager.get()
    updated = manager.patch(
        AppSettingsPatch(expected_version=initial.version, evidence_retention_days=14)
    )
    assert updated.version == 2
    assert updated.evidence_retention_days == 14
    assert updated.allowed_paths == ("src/**", "tests/**")
    with pytest.raises(ProductError) as raised:
        manager.patch(AppSettingsPatch(expected_version=1, evidence_retention_days=21))
    assert raised.value.code == "SETTINGS_VERSION_CONFLICT"


def test_board_projection_rebuild_is_deterministic() -> None:
    events = (
        ProductEvent(
            id="event-1",
            event_type="delivery.awaiting_plan_decision",
            aggregate_type="delivery",
            aggregate_id="delivery-1",
            aggregate_version=2,
            payload={
                "status": "awaiting_plan_decision",
                "title": "实现健康检查",
                "acceptance_ids": ["AC-001"],
            },
        ),
        ProductEvent(
            id="event-2",
            event_type="delivery.executing",
            aggregate_type="delivery",
            aggregate_id="delivery-1",
            aggregate_version=3,
            payload={
                "status": "executing",
                "title": "实现健康检查",
                "acceptance_ids": ["AC-001"],
                "execution_identity": "codex-cli",
            },
        ),
    )

    first = BoardProjector().rebuild(events)
    rebuilt = BoardProjector().rebuild(tuple(reversed(events)))

    assert first.projection_sha256 == rebuilt.projection_sha256
    assert first.items[0].column == "executing"
    assert first.items[0].version == 3


def test_foundation_interfaces_expose_events_evidence_and_problem_details(
    tmp_path: Path,
) -> None:
    database, _runner = _database(tmp_path)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        repository=SQLiteDeliveryRepository(database),
        resolved_journey_sha256="a" * 64,
    )
    evidence = EvidenceLedger(SQLiteEvidenceRepository(database))
    settings = SettingsManager(SQLiteSettingsRepository(database))
    with TestClient(create_app(coordinator, evidence=evidence, settings=settings)) as client:
        created = client.post(
            "/v1/deliveries",
            json={"workspace_id": "backend-demo", "user_request": "Add a health check."},
        )
        delivery_id = created.json()["id"]
        for _attempt in range(100):
            delivery = client.get(f"/v1/deliveries/{delivery_id}").json()
            if delivery["status"] == "awaiting_plan_decision":
                break
            time.sleep(0.01)
        events = client.get(f"/v1/deliveries/{delivery_id}/events")
        evidence_response = client.get(f"/v1/deliveries/{delivery_id}/evidence")
        initial_settings = client.get("/v1/settings").json()
        changed = client.patch(
            "/v1/settings",
            json={
                "expected_version": initial_settings["version"],
                "evidence_retention_days": 12,
            },
        )
        conflict = client.patch(
            "/v1/settings",
            json={
                "expected_version": initial_settings["version"],
                "evidence_retention_days": 13,
            },
        )

    assert events.status_code == 200
    assert [event["event_type"] for event in events.json()] == [
        "delivery.queued",
        "delivery.planning",
        "delivery.awaiting_plan_decision",
    ]
    assert evidence_response.status_code == 200
    assert any(item["kind"] == "journey" for item in evidence_response.json())
    assert changed.json()["evidence_retention_days"] == 12
    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/problem+json")
    assert conflict.json()["code"] == "SETTINGS_VERSION_CONFLICT"
