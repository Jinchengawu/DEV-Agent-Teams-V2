import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_team_os.infrastructure.database.migration import MigrationRunner
from agent_team_os.modules.knowledge.context_domain import KnowledgeContextStageResult
from agent_team_os.modules.knowledge.context_repository import SQLiteKnowledgeContextRepository
from agent_team_os.modules.knowledge.query_plan import (
    QueryBudget,
    QueryInputQualification,
    QueryPlan,
    QueryUnit,
)
from agent_team_os.modules.knowledge.query_repository import (
    QueryExecutionIdentity,
    QuerySafeError,
    SQLiteKnowledgeQueryRepository,
)
from agent_team_os.shared.hashes import Sha256


@pytest.mark.parametrize("failed", [False, True])
def test_receipts_are_fenced_immutable_and_complete_only(tmp_path: Path, failed: bool) -> None:
    database = tmp_path / "fixture.sqlite"
    MigrationRunner(database, Path("migrations")).migrate()
    repo = SQLiteKnowledgeQueryRepository(database)
    identity = QueryExecutionIdentity(
        preparation_run_id="run",
        stage_path="stage",
        binding_id="binding",
        input_sha256="a" * 64,
        index_sha256="b" * 64,
        policy_sha256="c" * 64,
        qualification_sha256="d" * 64,
        budget_sha256="e" * 64,
        authorization_epoch_hash="f" * 64,
    )
    plan = QueryPlan(
        source_sha256=identity.input_sha256,
        source_characters=2,
        qualification_sha256=identity.qualification_sha256,
        binding_sha256=Sha256("a" * 64),
        budget_sha256=identity.budget_sha256,
        units=tuple(
            QueryUnit(
                ordinal=i,
                source_sha256=identity.input_sha256,
                start=i,
                primary_start=i,
                end=i + 1,
                input_sha256=Sha256("a" * 64),
                tokens=1,
                utf8_bytes=1,
            )
            for i in range(2)
        ),
        total_tokens=2,
        total_bytes=2,
    )
    repo.freeze_plan(identity, plan=plan)
    repo.freeze_plan(identity, plan=plan)
    assert repo.get_stage_plan("run", "stage", "binding").plan_sha256 == plan.plan_sha256
    assert repo.get_stage_plan("other", "stage", "binding") is None
    with pytest.raises(RuntimeError, match="IMMUTABLE"):
        repo.freeze_plan(identity.model_copy(update={"index_sha256": Sha256("8" * 64)}), plan=plan)
    with pytest.raises(RuntimeError, match="IDENTITY"):
        repo.freeze_plan(identity.model_copy(update={"input_sha256": Sha256("9" * 64)}), plan=plan)
    now = datetime.now(UTC)
    fence = repo.claim(identity.execution_key, owner="first", now=now, ttl=timedelta(seconds=1))
    with pytest.raises(RuntimeError, match="LEASE"):
        repo.claim(identity.execution_key, owner="second", now=now, ttl=timedelta(seconds=1))
    later = now + timedelta(seconds=2)
    next_fence = repo.claim(
        identity.execution_key, owner="second", now=later, ttl=timedelta(seconds=20)
    )
    with pytest.raises(RuntimeError, match="FENCE"):
        repo.release(identity.execution_key, fence=fence, now=later)
    with pytest.raises(RuntimeError, match="FENCE"):
        repo.put_unit_receipt(
            identity.execution_key, fence=fence, ordinal=0, result_sha256="2" * 64, now=later
        )
    repo.put_unit_receipt(
        identity.execution_key, fence=next_fence, ordinal=0, result_sha256="2" * 64, now=later
    )
    # 重开 Repository 模拟进程重启；既有 unit 可复用且不可被覆写。
    repo = SQLiteKnowledgeQueryRepository(database)
    repo.put_unit_receipt(
        identity.execution_key, fence=next_fence, ordinal=0, result_sha256="2" * 64, now=later
    )
    with pytest.raises(RuntimeError, match="IMMUTABLE"):
        repo.put_unit_receipt(
            identity.execution_key, fence=next_fence, ordinal=0, result_sha256="3" * 64, now=later
        )
    assert repo.unit_receipts(identity.execution_key) == {0: "2" * 64}
    with pytest.raises(RuntimeError, match="INCOMPLETE"):
        repo.complete(identity.execution_key, fence=next_fence, artifact_sha256="4" * 64, now=later)
    repo.put_unit_receipt(
        identity.execution_key, fence=next_fence, ordinal=1, result_sha256="3" * 64, now=later
    )
    if failed:
        error = QuerySafeError(
            code="KNOWLEDGE_QUERY_INPUT_LIMIT",
            category="input_limit",
            correlation_id=uuid4(),
            http_status=400,
            unit=0,
            plan_sha256=plan.plan_sha256,
            qualification_sha256=identity.qualification_sha256,
            budget_sha256=identity.budget_sha256,
            measured_tokens=1,
            allowed_tokens=2,
            measured_bytes=1,
            allowed_bytes=2,
            attempt=1,
            occurred_at=later,
            duration_ms=5,
        )
        repo.put_safe_error(identity.execution_key, fence=next_fence, error=error, now=later)
        repo.put_safe_error(identity.execution_key, fence=next_fence, error=error, now=later)
        assert repo.list_safe_errors("run") == (error,)
        assert repo.list_safe_errors("other") == ()
        with pytest.raises(ValidationError):
            QuerySafeError.model_validate(error.model_dump() | {"provider_body": "secret-sentinel"})
        with pytest.raises(RuntimeError, match="FAILED"):
            repo.complete(
                identity.execution_key, fence=next_fence, artifact_sha256="4" * 64, now=later
            )
        assert repo.get_for_execution(identity).artifact_sha256 is None
        with sqlite3.connect(database) as connection:
            payload = connection.execute(
                "SELECT payload_json FROM knowledge_query_safe_errors"
            ).fetchone()[0]
            assert "secret-sentinel" not in payload
        return
    repo.complete(identity.execution_key, fence=next_fence, artifact_sha256="4" * 64, now=later)
    assert repo.get_for_execution(identity).artifact_sha256 == "4" * 64
    with sqlite3.connect(database) as connection:
        connection.execute("""INSERT INTO knowledge_context_preparation_runs
            (id,delivery_id,input_sha256,knowledge_binding_hash,input_json,status,attempt_count,
             created_at,updated_at)
             VALUES('run','delivery','input','binding','{}','running',0,'now','now')""")
    result = KnowledgeContextStageResult.model_validate(
        {
            "preparation_run_id": "run",
            "stage_path": "stage",
            "query_sha256": identity.input_sha256,
            "retrieval_policy_revision_id": "policy",
            "created_at": later,
            "context": {
                "stage_path": "stage",
                "citation_ids": [],
                "authorization_epoch_hash": identity.authorization_epoch_hash,
                "artifact_reference": {
                    "uri": "artifact://sha256/" + "6" * 64,
                    "sha256": "6" * 64,
                    "media_type": "application/json",
                    "size_bytes": 2,
                },
            },
        }
    )
    contexts = SQLiteKnowledgeContextRepository(database)
    with pytest.raises(RuntimeError, match="BINDINGS_INCOMPLETE"):
        contexts.put_stage_result(result, query_binding_ids=("binding", "missing"))
    assert repo.get_for_execution(identity).final_context_sha256 is None
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TRIGGER reject_context BEFORE INSERT
            ON knowledge_context_stage_results BEGIN SELECT RAISE(ABORT, 'fixture'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        contexts.put_stage_result(result, query_binding_ids=("binding",))
    assert repo.get_for_execution(identity).final_context_sha256 is None
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER reject_context")
    contexts.put_stage_result(result, query_binding_ids=("binding",))
    contexts.put_stage_result(result, query_binding_ids=("binding",))
    assert repo.get_for_execution(identity).final_context_sha256 == "6" * 64
    assert len(contexts.list_stage_results("run")) == 1
    with pytest.raises(RuntimeError, match="FENCE"):
        repo.complete(identity.execution_key, fence=next_fence, artifact_sha256="5" * 64, now=later)


@pytest.mark.parametrize("status", ["queued", "leased", "running", "retry_wait"])
def test_upgrade_blocks_active_preparation_without_modification(
    tmp_path: Path, status: str
) -> None:
    old = tmp_path / "old"
    old.mkdir()
    for source in Path("migrations").glob("*.sql"):
        if int(source.name[:4]) < 47:
            shutil.copyfile(source, old / source.name)
    database = tmp_path / "fixture.sqlite"
    MigrationRunner(database, old).migrate()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO knowledge_context_preparation_runs
            (id,delivery_id,input_sha256,knowledge_binding_hash,input_json,status,attempt_count,
             created_at,updated_at)
             VALUES('run','delivery','input','binding','{}',?,0,'now','now')""",
            (status,),
        )
    with pytest.raises(RuntimeError, match="UPGRADE_ACTIVE_PREPARATION"):
        MigrationRunner(database, Path("migrations")).migrate()
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT status FROM knowledge_context_preparation_runs").fetchone()[
                0
            ]
            == status
        )
        assert (
            connection.execute("SELECT 1 FROM schema_migrations WHERE version=47").fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='knowledge_query_plans'"
            ).fetchone()
            is None
        )


def test_upgrade_blocks_active_delivery_lease(tmp_path: Path) -> None:
    old = tmp_path / "old"
    old.mkdir()
    for source in Path("migrations").glob("*.sql"):
        if int(source.name[:4]) < 47:
            shutil.copyfile(source, old / source.name)
    database = tmp_path / "fixture.sqlite"
    MigrationRunner(database, old).migrate()
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO project_delivery_leases VALUES('legacy-default','d','now')")
    with pytest.raises(RuntimeError, match="UPGRADE_ACTIVE_DELIVERY"):
        MigrationRunner(database, Path("migrations")).migrate()


def test_qualification_roundtrip_integrity_and_no_legacy_backfill(tmp_path: Path) -> None:
    database = tmp_path / "fixture.sqlite"
    runner = MigrationRunner(database, Path("migrations"))
    runner.migrate()
    assert runner.migrate() == ()
    repo = SQLiteKnowledgeQueryRepository(database)
    h = Sha256("a" * 64)
    with pytest.raises(KeyError):
        repo.get_qualification(h)
    qualification = QueryInputQualification(
        status="blocked",
        evidence_kind="deterministic",
        base_qualification_sha256=h,
        model_digest="fixture",
        embedding_adapter_revision="fixture",
        measurement_identity_sha256=h,
        tokenizer_assets_sha256=h,
        tokenizer_contract="fixture",
        capacity_evidence_sha256=h,
        qualification_receipt_sha256=h,
        budget=QueryBudget(
            unit_tokens=8,
            unit_bytes=32,
            max_units=10,
            total_tokens=80,
            total_bytes=320,
            max_duration_ms=30000,
            max_cache_bytes=10000,
            overlap_tokens=0,
            overlap_bytes=0,
        ),
    )
    repo.put_qualification(qualification)
    repo.put_qualification(qualification)
    assert repo.get_qualification(qualification.qualification_sha256) == qualification
    assert repo.get_qualification(qualification.qualification_sha256).status == "blocked"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE knowledge_query_qualifications SET payload_json=?",
            (qualification.model_copy(update={"model_digest": "tampered"}).model_dump_json(),),
        )
    with pytest.raises(RuntimeError, match="INTEGRITY"):
        repo.get_qualification(qualification.qualification_sha256)


@pytest.mark.parametrize("status", ["failed", "cancelled", "succeeded"])
def test_upgrade_preserves_terminal_preparation(tmp_path: Path, status: str) -> None:
    old = tmp_path / "old"
    old.mkdir()
    for source in Path("migrations").glob("*.sql"):
        if int(source.name[:4]) < 47:
            shutil.copyfile(source, old / source.name)
    database = tmp_path / "fixture.sqlite"
    MigrationRunner(database, old).migrate()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO knowledge_context_preparation_runs
            (id,delivery_id,input_sha256,knowledge_binding_hash,input_json,status,attempt_count,
             created_at,updated_at)
             VALUES('run','delivery','input','binding','{}',?,0,'now','now')""",
            (status,),
        )
        before = connection.execute("SELECT * FROM knowledge_context_preparation_runs").fetchall()
    assert MigrationRunner(database, Path("migrations")).migrate() == (47,)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT * FROM knowledge_context_preparation_runs").fetchall()
            == before
        )
        assert (
            connection.execute("SELECT count(*) FROM knowledge_query_qualifications").fetchone()[0]
            == 0
        )
