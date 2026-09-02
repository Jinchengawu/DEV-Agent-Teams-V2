import json
import sqlite3
from pathlib import Path

import pytest

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.agents import AgentRunLedger, ArtifactEnvelope
from agent_team_os.modules.artifacts import (
    ArtifactStorageError,
    ContentAddressedArtifactStorage,
)
from agent_team_os.shared.hashes import sha256_json


def test_content_addressed_artifact_storage_deduplicates_and_detects_tampering(
    tmp_path: Path,
) -> None:
    store = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    content = {"title": "界面设计规范", "tokens": {"primary": "#1677ff"}}

    first = store.put_json(content)
    second = store.put_json(content)

    assert first == second
    assert first.uri == f"artifact://sha256/{first.sha256}"
    assert store.get_json(first) == content
    assert len(tuple((tmp_path / "artifacts" / "sha256").glob("*/*"))) == 1

    object_path = store.path_for(first)
    object_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ArtifactStorageError, match="ARTIFACT_CONTENT_HASH_MISMATCH"):
        store.get_json(first)


def test_agent_run_ledger_persists_only_artifact_reference_metadata(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    store = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    ledger = AgentRunLedger(database, artifact_storage=store)
    run = ledger.start(
        delivery_id="delivery-1",
        pipeline_revision_id="fullstack:1",
        binding_site="frontend.actor",
        resolved_binding_hash="b" * 64,
        deployment_snapshot={"id": "frontend-deployment"},
        runtime_identity="codex-cli",
    )
    payload = {"unified_diff": "+" + "x" * 20_000}
    completed = ledger.finish(
        run,
        status="succeeded",
        artifacts=(
            ArtifactEnvelope(
                contract_id="frontend-candidate-v1",
                content=payload,
                sha256=sha256_json(payload),
            ),
        ),
    )

    envelope = completed.artifact_envelopes[0]
    assert envelope.content is None
    assert envelope.reference is not None
    assert envelope.reference.size_bytes > 20_000
    assert store.get_json(envelope.reference) == payload

    with sqlite3.connect(database) as connection:
        raw = connection.execute(
            "SELECT artifact_envelopes_json FROM agent_runs WHERE id=?", (run.id,)
        ).fetchone()[0]
    persisted = json.loads(raw)
    assert "unified_diff" not in raw
    assert persisted[0]["reference"]["uri"].startswith("artifact://sha256/")


def test_agent_run_ledger_records_attempt_error_code(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    ledger = AgentRunLedger(database)
    run = ledger.start(
        delivery_id="delivery-revoked",
        pipeline_revision_id="pipeline:1",
        binding_site="design.main",
        resolved_binding_hash="a" * 64,
        deployment_snapshot={},
        runtime_identity="codex-cli",
    )

    ledger.finish(
        run,
        status="failed",
        error_code="KNOWLEDGE_AUTHORIZATION_REVOKED",
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status,error_code FROM agent_attempts WHERE id=?",
            (run.attempt_id,),
        ).fetchone()
    assert row == ("failed", "KNOWLEDGE_AUTHORIZATION_REVOKED")
