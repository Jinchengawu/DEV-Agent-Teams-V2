from pathlib import Path

import pytest

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.orchestration import (
    PipelineRevision,
    PipelineRunLedger,
    SQLitePipelineRunRepository,
)
from agent_team_os.shared.errors import ProductError


class FakeGraphRuntime:
    def create(self, run_id: str, compiled_graph: dict[str, object]) -> dict[str, object]:
        return {
            "id": run_id,
            "status": "running",
            "version": 1,
            "graph": compiled_graph,
            "nodes": [{"node_id": "plan", "status": "ready", "attempt": 0}],
            "edges": [],
        }

    def transition(
        self,
        snapshot: dict[str, object],
        *,
        command: str,
        node_id: str,
        body_node_id: str | None = None,
        activated_conditions: tuple[str, ...] = (),
        exit_condition_met: bool | None = None,
    ) -> dict[str, object]:
        version = int(snapshot["version"])
        status = "running" if command == "start" else "completed"
        node_status = "running" if command == "start" else "succeeded"
        return {
            **snapshot,
            "status": status,
            "version": version + 1,
            "nodes": [
                {"node_id": node_id, "status": node_status, "attempt": 1}
            ],
        }


def _revision() -> PipelineRevision:
    return PipelineRevision(
        pipeline_id="backend-delivery",
        revision=3,
        definition={"id": "backend-delivery"},
        compiled_graph={
            "id": "backend-delivery",
            "fingerprint": "c" * 64,
            "topological_order": ["plan"],
        },
        binding_snapshot={},
        fingerprint="c" * 64,
        published_by="admin",
    )


def test_pipeline_run_is_durable_and_transitions_with_cas(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    repository = SQLitePipelineRunRepository(database)
    ledger = PipelineRunLedger(repository, FakeGraphRuntime())

    started = ledger.start(delivery_id="delivery-1", revision=_revision())
    running = ledger.transition(
        started.id,
        command="start",
        node_id="plan",
        expected_version=started.version,
    )
    completed = ledger.transition(
        started.id,
        command="succeed",
        node_id="plan",
        expected_version=running.version,
    )

    recovered = PipelineRunLedger(
        SQLitePipelineRunRepository(database), FakeGraphRuntime()
    ).get_for_delivery("delivery-1")
    assert started.pipeline_revision_id == "backend-delivery:3"
    assert started.graph_fingerprint == "c" * 64
    assert running.version == 2
    assert completed.status == "completed"
    assert recovered == completed
    assert [event.event_type for event in repository.list_events(started.id)] == [
        "pipeline-run.created",
        "pipeline-node.started",
        "pipeline-node.succeeded",
    ]


def test_pipeline_run_rejects_stale_transition(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    ledger = PipelineRunLedger(SQLitePipelineRunRepository(database), FakeGraphRuntime())
    started = ledger.start(delivery_id="delivery-1", revision=_revision())
    ledger.transition(
        started.id,
        command="start",
        node_id="plan",
        expected_version=started.version,
    )

    with pytest.raises(ProductError) as raised:
        ledger.transition(
            started.id,
            command="start",
            node_id="plan",
            expected_version=started.version,
        )

    assert raised.value.code == "PIPELINE_RUN_VERSION_CONFLICT"
