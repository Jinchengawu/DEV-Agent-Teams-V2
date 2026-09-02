from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_team_os.delivery import (
    DeliveryCoordinator,
    DeliveryExecutionSnapshot,
    DeliveryKnowledgeContextSnapshot,
    DeliveryMethodSnapshot,
    InMemoryDeliveryRepository,
    KnowledgePreparationInputV1,
    KnowledgePreparationResult,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.artifacts import ArtifactReference
from agent_team_os.modules.knowledge import (
    DeliveryKnowledgeContextPreparationService,
    KnowledgeAuthorizationStampV1,
    MembershipAuthorizationComponent,
    SQLiteKnowledgeContextRepository,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


def _preparation_input(delivery_id: str) -> KnowledgePreparationInputV1:
    payload = {
        "delivery_id": delivery_id,
        "project_id": "project-one",
        "project_version": 1,
        "project_description_snapshot": {
            "uri": "artifact://sha256/" + "f" * 64,
            "sha256": "f" * 64,
            "media_type": ("application/vnd.agent-team-os.project-description-snapshot+json"),
            "size_bytes": 128,
        },
        "authorized_principal_id": "user-one",
        "delivery_goal": "实现受控知识上下文",
        "pipeline_revision_id": "knowledge-pipeline:1",
        "pipeline_revision_sha256": "a" * 64,
        "authorization_access_component": {
            "kind": "membership",
            "membership_id": "project-one:user-one",
            "version": 1,
        },
        "approved_knowledge_approval_ids": (),
        "stage_bindings": {
            "requirements": {
                "stage_path": "requirements",
                "acwm_artifact_slot": "knowledge-context-v1",
                "acwm_artifact_contract_version": "1.0.0",
                "acwm_artifact_contract_sha256": "9" * 64,
                "retrieval_policy_revision_id": "retrieval-v1",
                "required": True,
                "max_context_bytes": 16_384,
            }
        },
        "stage_responsibilities": {"requirements": "澄清需求边界"},
    }
    return KnowledgePreparationInputV1(
        **payload,
        input_sha256=sha256_json(payload),
    )


def _snapshot(preparation_input: KnowledgePreparationInputV1) -> DeliveryExecutionSnapshot:
    reference = ArtifactReference(
        uri="artifact://sha256/" + "b" * 64,
        sha256="b" * 64,
        media_type="application/vnd.agent-team-os.knowledge-context+json",
        size_bytes=12,
    )
    payload = {
        "project_id": preparation_input.project_id,
        "project_version": preparation_input.project_version,
        "team_template_revision_id": "team:1",
        "team_template_sha256": "c" * 64,
        "team_workcells": {},
        "pipeline_revision_id": preparation_input.pipeline_revision_id,
        "pipeline_revision_sha256": preparation_input.pipeline_revision_sha256,
        "workcell_stage_map": {},
        "release_contract_snapshot": (),
        "knowledge_context_bindings": preparation_input.stage_bindings,
        "resolved_provider_bindings": {},
        "workspaces": (),
        "method_snapshot": DeliveryMethodSnapshot(
            snapshot_id="methods:1",
            qualification_sha256="d" * 64,
            packages=(),
            method_entries={},
        ),
        "knowledge_contexts": {
            "requirements": DeliveryKnowledgeContextSnapshot(
                stage_path="requirements",
                artifact_reference=reference,
                citation_ids=("citation-1",),
                authorization_epoch_hash="e" * 64,
            )
        },
        "knowledge_authorization_stamp": {"authorization_epoch_hash": "e" * 64},
        "knowledge_preparation_input_sha256": preparation_input.input_sha256,
    }
    return DeliveryExecutionSnapshot(**payload, snapshot_sha256=sha256_json(payload))


class FakePreparer:
    def __init__(self, repository: InMemoryDeliveryRepository, *, fail: bool = False) -> None:
        self.repository = repository
        self.fail = fail
        self.cancelled: list[str] = []

    async def prepare(
        self, preparation_input: KnowledgePreparationInputV1
    ) -> KnowledgePreparationResult:
        persisted = self.repository.get(preparation_input.delivery_id)
        assert persisted is not None
        assert persisted.status == "preparing_context"
        assert persisted.delivery_execution_snapshot is None
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("KNOWLEDGE_PROVIDER_TEMPORARILY_UNAVAILABLE")
        return KnowledgePreparationResult(
            preparation_run_id="preparation-run-1",
            delivery_execution_snapshot=_snapshot(preparation_input),
        )

    def cancel(self, delivery_id: str) -> None:
        self.cancelled.append(delivery_id)


class FakePipelineExecution:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.advanced: list[str] = []

    def start(self, delivery):  # type: ignore[no-untyped-def]
        self.started.append(delivery.id)

    async def advance(self, delivery_id: str) -> None:
        self.advanced.append(delivery_id)


def _runtime_bindings() -> dict[str, dict[str, object]]:
    return {
        "hermes-pm": {
            "instance_id": "instance:hermes-pm",
            "instance_version": 1,
            "runtime_type": "deterministic",
            "identity": "deterministic-test",
        }
    }


def test_delivery_is_persisted_before_context_external_work_and_then_frozen() -> None:
    async def scenario() -> None:
        repository = InMemoryDeliveryRepository()
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=repository,
            resolved_journey_sha256="a" * 64,
        )
        preparer = FakePreparer(repository)
        pipeline = FakePipelineExecution()
        coordinator.configure_knowledge_context(preparer)
        coordinator._pipeline_execution = pipeline  # type: ignore[assignment]
        preparation_input = _preparation_input("delivery-one")

        created = coordinator.enqueue(
            delivery_id="delivery-one",
            project_id="project-one",
            workspace_id="project:project-one",
            user_request=preparation_input.delivery_goal,
            pipeline_revision_id=preparation_input.pipeline_revision_id,
            journey_binding_snapshot=_runtime_bindings(),
            resolved_journey_sha256="a" * 64,
            knowledge_preparation_input=preparation_input,
        )
        assert created.status == "preparing_context"
        assert created.delivery_execution_snapshot is None

        for _ in range(50):
            await asyncio.sleep(0.01)
            current = coordinator.get(created.id)
            if current.knowledge_preparation_run_id is not None:
                break
        else:
            raise AssertionError("knowledge preparation did not complete")

        assert current.status == "queued"
        assert current.delivery_execution_snapshot is not None
        assert current.delivery_execution_snapshot.knowledge_contexts["requirements"]
        assert pipeline.started == [created.id]
        assert pipeline.advanced == [created.id]

    asyncio.run(scenario())


def test_failed_context_preparation_keeps_auditable_failed_delivery_without_snapshot() -> None:
    async def scenario() -> None:
        repository = InMemoryDeliveryRepository()
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=repository,
            resolved_journey_sha256="a" * 64,
        )
        coordinator.configure_knowledge_context(FakePreparer(repository, fail=True))
        preparation_input = _preparation_input("delivery-failed")
        created = coordinator.enqueue(
            delivery_id="delivery-failed",
            project_id="project-one",
            workspace_id="project:project-one",
            user_request=preparation_input.delivery_goal,
            pipeline_revision_id=preparation_input.pipeline_revision_id,
            journey_binding_snapshot=_runtime_bindings(),
            resolved_journey_sha256="a" * 64,
            knowledge_preparation_input=preparation_input,
        )

        for _ in range(50):
            await asyncio.sleep(0.01)
            failed = coordinator.get(created.id)
            if failed.status == "failed":
                break
        else:
            raise AssertionError("failed preparation did not terminate")

        assert failed.error_code == "KNOWLEDGE_CONTEXT_PREPARATION_FAILED"
        assert failed.delivery_execution_snapshot is None
        assert [event.payload["status"] for event in coordinator.events(created.id)] == [
            "preparing_context",
            "failed",
        ]

    asyncio.run(scenario())


def test_preparation_retry_wait_cannot_be_acquired_before_due_time(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    repository = SQLiteKnowledgeContextRepository(database)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    preparation_input = _preparation_input("delivery-retry")
    run = repository.create_or_get(
        preparation_input,
        knowledge_binding_hash="7" * 64,
        now=now,
    )
    running = repository.acquire(
        run.id,
        lease_owner="worker-one",
        now=now,
        lease_ttl=timedelta(minutes=5),
    )
    retry_at = now + timedelta(seconds=30)
    repository.schedule_retry(
        running.id,
        error_code="KNOWLEDGE_OLLAMA_UNAVAILABLE",
        next_attempt_at=retry_at,
        now=now,
    )

    with pytest.raises(RuntimeError, match="KNOWLEDGE_CONTEXT_PREPARATION_NOT_ACQUIRABLE"):
        repository.acquire(
            run.id,
            lease_owner="worker-two",
            now=retry_at - timedelta(microseconds=1),
            lease_ttl=timedelta(minutes=5),
        )

    recovered = repository.acquire(
        run.id,
        lease_owner="worker-two",
        now=retry_at,
        lease_ttl=timedelta(minutes=5),
    )
    assert recovered.status == "running"
    assert recovered.attempt_count == 2
    assert recovered.lease_owner == "worker-two"


def test_preparation_running_lease_recovers_only_after_expiry(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    repository = SQLiteKnowledgeContextRepository(database)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    preparation_input = _preparation_input("delivery-restart")
    run = repository.create_or_get(
        preparation_input,
        knowledge_binding_hash="8" * 64,
        now=now,
    )
    lease_expires_at = now + timedelta(seconds=10)
    repository.acquire(
        run.id,
        lease_owner="interrupted-process",
        now=now,
        lease_ttl=timedelta(seconds=10),
    )

    with pytest.raises(RuntimeError, match="KNOWLEDGE_CONTEXT_PREPARATION_NOT_ACQUIRABLE"):
        repository.acquire(
            run.id,
            lease_owner="restarted-process",
            now=lease_expires_at - timedelta(microseconds=1),
            lease_ttl=timedelta(minutes=5),
        )

    recovered = repository.acquire(
        run.id,
        lease_owner="restarted-process",
        now=lease_expires_at,
        lease_ttl=timedelta(minutes=5),
    )
    assert recovered.status == "running"
    assert recovered.attempt_count == 2
    assert recovered.lease_owner == "restarted-process"


def test_preparation_retries_only_transient_failure_up_to_policy_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    repository = SQLiteKnowledgeContextRepository(database)
    stamp = KnowledgeAuthorizationStampV1(
        project_id="project-one",
        authorized_principal_id="user-one",
        identity_authorization_version=1,
        global_role="editor",
        project_authorization_version=1,
        access_component=MembershipAuthorizationComponent(
            membership_id="project-one:user-one",
            version=1,
        ),
        approvals=(),
        connections=(),
        authorization_epoch_hash="6" * 64,
    )

    class StableAuthorization:
        def resolve(self, **_kwargs: object) -> KnowledgeAuthorizationStampV1:
            return stamp

    service = DeliveryKnowledgeContextPreparationService(
        repository,
        authorization=StableAuthorization(),  # type: ignore[arg-type]
        projects=object(),  # type: ignore[arg-type]
        tenant=object(),  # type: ignore[arg-type]
        indexes=object(),  # type: ignore[arg-type]
        artifacts=object(),  # type: ignore[arg-type]
        snapshot_compiler=object(),  # type: ignore[arg-type]
        max_attempts=2,
        retry_base_delay_seconds=0,
    )

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise ProductError(
            code="KNOWLEDGE_OLLAMA_UNAVAILABLE",
            title="Ollama unavailable",
            detail="temporary local endpoint failure",
            repair="retry",
        )

    monkeypatch.setattr(service, "_prepare_stages", unavailable)

    with pytest.raises(ProductError) as failed:
        asyncio.run(service.prepare(_preparation_input("delivery-transient")))

    assert failed.value.code == "KNOWLEDGE_OLLAMA_UNAVAILABLE"
    run = repository.get_for_delivery("delivery-transient")
    assert run is not None
    assert run.status == "failed"
    assert run.attempt_count == 2
    assert run.error_code == "KNOWLEDGE_OLLAMA_UNAVAILABLE"
