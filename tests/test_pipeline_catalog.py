from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.orchestration import (
    GraphCompilation,
    PipelineCatalog,
    PipelineCreate,
    PipelineDraftPatch,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
    create_pipeline_router,
)
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class GraphCompiler:
    def compile(self, definition: dict[str, object]) -> GraphCompilation:
        fingerprint = "b" * 64 if definition.get("version") == "4.1.0" else "a" * 64
        return GraphCompilation(
            graph={
                "topological_order": ["plan", "delivery"],
                "entry_node_ids": ["plan"],
                "exit_node_ids": ["delivery"],
                "loops": [],
            },
            fingerprint=fingerprint,
            capability_ids=("codex-backend", "hermes-pm"),
        )


class BindingResolver:
    def snapshot(
        self, capability_ids: tuple[str, ...]
    ) -> dict[str, dict[str, object]]:
        return {
            capability_id: {
                "instance_id": f"instance:{capability_id}",
                "instance_version": 1,
                "identity": capability_id,
            }
            for capability_id in capability_ids
        }


class RejectingProductPolicy:
    def validate(self, definition: dict[str, object]) -> tuple[str, ...]:
        return ("DELIVERY_PIPELINE_MISSING_GATE:candidate-change",)


def _catalog(tmp_path: Path) -> PipelineCatalog:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    return PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=GraphCompiler(),
        binding_resolver=BindingResolver(),
    )


def test_catalog_creates_multiple_pipelines_with_independent_drafts(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)

    backend = catalog.create_pipeline(
        PipelineCreate(
            id="backend-delivery",
            name="后端交付",
            description="从需求到候选提交",
            definition={"id": "backend-delivery", "version": "4.0.0", "nodes": [], "edges": []},
        ),
        created_by="admin",
    )
    review = catalog.create_pipeline(
        PipelineCreate(
            id="release-review",
            name="发布审查",
            description="并行完成安全与架构审查",
            definition={"id": "release-review", "version": "4.0.0", "nodes": [], "edges": []},
        ),
        created_by="admin",
    )

    assert [pipeline.id for pipeline in catalog.list_pipelines()] == [
        "backend-delivery",
        "release-review",
    ]
    assert backend.draft.pipeline_id == "backend-delivery"
    assert review.draft.pipeline_id == "release-review"
    assert backend.draft.id != review.draft.id
    assert backend.pipeline.active_revision is None
    assert catalog.list_drafts("backend-delivery") == (backend.draft,)


def test_catalog_validation_combines_acwm_and_product_compatibility(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=GraphCompiler(),
        binding_resolver=BindingResolver(),
        definition_policy=RejectingProductPolicy(),
    )
    created = catalog.create_pipeline(
        PipelineCreate(
            id="invalid-delivery",
            name="缺少候选审批",
            definition={
                "id": "invalid-delivery",
                "version": "4.0.0",
                "nodes": [],
                "edges": [],
            },
        ),
        created_by="admin",
    )

    validated = catalog.validate_draft(
        created.draft.id, expected_version=created.draft.version
    )

    assert validated.validation_status == "invalid"
    assert validated.validation_errors == (
        "DELIVERY_PIPELINE_MISSING_GATE:candidate-change",
    )


def test_catalog_bootstraps_builtin_pipeline_once_and_activates_revision(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    request = PipelineCreate(
        id="backend-delivery",
        name="内置后端交付闭环",
        definition={
            "id": "backend-delivery",
            "version": "4.0.0",
            "nodes": [],
            "edges": [],
        },
    )

    first = catalog.ensure_builtin_pipeline(request, actor_id="system")
    second = catalog.ensure_builtin_pipeline(request, actor_id="system")

    assert first.id == "backend-delivery"
    assert first.active_revision == 1
    assert first.version == 2
    assert second == first
    assert len(catalog.list_pipelines()) == 1


def test_catalog_recovers_builtin_pipeline_left_inactive_after_create(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    request = PipelineCreate(
        id="backend-delivery",
        name="内置后端交付闭环",
        definition={
            "id": "backend-delivery",
            "version": "4.0.0",
            "nodes": [],
            "edges": [],
        },
    )
    created = catalog.create_pipeline(request, created_by="system")

    recovered = catalog.ensure_builtin_pipeline(request, actor_id="system")

    assert created.pipeline.active_revision is None
    assert recovered.active_revision == 1
    assert recovered.version == 2


def test_catalog_publishes_new_builtin_revision_when_definition_changes(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    original = PipelineCreate(
        id="backend-delivery",
        name="内置后端交付闭环",
        definition={"id": "backend-delivery", "version": "4.0.0", "nodes": []},
    )
    first = catalog.ensure_builtin_pipeline(original, actor_id="system")
    upgraded = original.model_copy(
        update={
            "definition": {
                "id": "backend-delivery",
                "version": "4.1.0",
                "nodes": [{"id": "repair", "kind": "loop"}],
            }
        }
    )

    second = catalog.ensure_builtin_pipeline(upgraded, actor_id="system")

    assert first.active_revision == 1
    assert second.active_revision == 2
    assert second.version == 3
    assert catalog.get_revision("backend-delivery", 2).definition == upgraded.definition


def test_catalog_publishes_an_immutable_compiled_pipeline_revision(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_pipeline(
        PipelineCreate(
            id="backend-delivery",
            name="后端交付",
            definition={
                "id": "backend-delivery",
                "version": "4.0.0",
                "nodes": [{"kind": "stage", "id": "plan"}],
                "edges": [],
            },
        ),
        created_by="admin",
    )

    validated = catalog.validate_draft(
        created.draft.id, expected_version=created.draft.version
    )
    revision = catalog.publish_draft(
        created.draft.id,
        expected_version=validated.version,
        published_by="admin",
    )

    assert validated.validation_status == "valid"
    assert revision.pipeline_id == "backend-delivery"
    assert revision.revision == 1
    assert revision.fingerprint == "a" * 64
    assert revision.compiled_graph["topological_order"] == ["plan", "delivery"]
    assert revision.binding_snapshot["codex-backend"]["instance_version"] == 1
    assert catalog.get_revision("backend-delivery", 1) == revision


def test_pipeline_http_interface_creates_validates_and_publishes(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    app = FastAPI()
    app.include_router(create_pipeline_router(catalog, actor_id=lambda _request: "admin"))

    with TestClient(app) as client:
        created = client.post(
            "/v1/pipelines",
            json={
                "id": "release-review",
                "name": "发布审查",
                "description": "",
                "definition": {
                    "id": "release-review",
                    "version": "4.0.0",
                    "nodes": [],
                    "edges": [],
                },
            },
        )
        draft = created.json()["draft"]
        validated = client.post(
            f"/v1/pipeline-drafts/{draft['id']}/validate",
            json={"expected_version": draft["version"]},
        )
        published = client.post(
            f"/v1/pipeline-drafts/{draft['id']}/publish",
            json={"expected_version": validated.json()["version"]},
        )

    assert created.status_code == 201
    assert validated.status_code == 200
    assert published.status_code == 201
    assert published.json()["pipeline_id"] == "release-review"


def test_catalog_activates_a_published_revision_with_pipeline_cas(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_pipeline(
        PipelineCreate(
            id="backend-delivery",
            name="后端交付",
            definition={"id": "backend-delivery", "version": "4.0.0", "nodes": [], "edges": []},
        ),
        created_by="admin",
    )
    validated = catalog.validate_draft(
        created.draft.id, expected_version=created.draft.version
    )
    revision = catalog.publish_draft(
        created.draft.id,
        expected_version=validated.version,
        published_by="admin",
    )

    activated = catalog.activate_revision(
        "backend-delivery",
        revision=revision.revision,
        expected_version=created.pipeline.version,
        activated_by="admin",
    )

    assert activated.active_revision == 1
    assert activated.version == 2


def test_catalog_updates_a_pipeline_draft_with_cas_and_invalidates_validation(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_pipeline(
        PipelineCreate(
            id="release-review",
            name="发布审查",
            definition={"id": "release-review", "version": "4.0.0", "nodes": [], "edges": []},
        ),
        created_by="admin",
    )
    validated = catalog.validate_draft(
        created.draft.id, expected_version=created.draft.version
    )

    updated = catalog.patch_draft(
        created.draft.id,
        PipelineDraftPatch(
            expected_version=validated.version,
            definition={
                "id": "release-review",
                "version": "4.0.1",
                "nodes": [{"kind": "stage", "id": "review"}],
                "edges": [],
            },
            layout={"review": {"x": 40, "y": 80}},
        ),
    )

    assert updated.version == validated.version + 1
    assert updated.validation_status == "unknown"
    assert updated.validation_errors == ()
    assert updated.layout["review"] == {"x": 40, "y": 80}


def test_delivery_pins_an_explicit_pipeline_revision(tmp_path: Path) -> None:
    class RuntimeCompiler:
        def compile(self, definition: dict[str, object]) -> GraphCompilation:
            return GraphCompilation(
                graph={
                    "id": definition["id"],
                    "topological_order": ["plan", "delivery"],
                    "entry_node_ids": ["plan"],
                    "exit_node_ids": ["delivery"],
                    "nodes": [],
                    "edges": [],
                    "loops": [],
                    "fingerprint": "b" * 64,
                },
                fingerprint="b" * 64,
                capability_ids=(
                    "codex-backend",
                    "hermes-pm",
                    "hermes-project-admin",
                ),
            )

    class RuntimeBindings:
        def snapshot(
            self, capability_ids: tuple[str, ...]
        ) -> dict[str, dict[str, object]]:
            return {
                capability_id: {
                    "instance_id": f"instance:{capability_id}",
                    "instance_version": 1,
                    "runtime_type": "codex-cli",
                    "identity": "deterministic-test",
                }
                for capability_id in capability_ids
            }

    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=RuntimeCompiler(),
        binding_resolver=RuntimeBindings(),
    )
    created = catalog.create_pipeline(
        PipelineCreate(
            id="backend-delivery",
            name="后端交付",
            definition={
                "id": "backend-delivery",
                "version": "4.0.0",
                "nodes": [],
                "edges": [],
            },
        ),
        created_by="admin",
    )
    validated = catalog.validate_draft(
        created.draft.id, expected_version=created.draft.version
    )
    revision = catalog.publish_draft(
        created.draft.id,
        expected_version=validated.version,
        published_by="admin",
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )

    class Runtime:
        def create(
            self, run_id: str, compiled_graph: dict[str, object]
        ) -> dict[str, object]:
            return {
                "id": run_id,
                "graph": compiled_graph,
                "status": "running",
                "version": 1,
                "nodes": [],
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
            return snapshot

    pipeline_runs = PipelineRunLedger(
        SQLitePipelineRunRepository(database), Runtime()
    )
    with TestClient(
        create_app(
            coordinator, pipeline_catalog=catalog, pipeline_runs=pipeline_runs
        )
    ) as client:
        response = client.post(
            "/v1/deliveries",
            json={
                "workspace_id": "backend-demo",
                "user_request": "增加健康检查",
                "pipeline_revision_id": (
                    f"{revision.pipeline_id}:{revision.revision}"
                ),
            },
        )
        graph_run = client.get(
            f"/v1/deliveries/{response.json()['id']}/pipeline-run"
        )

    assert response.status_code == 202, response.json()
    assert response.json()["pipeline_revision_id"] == "backend-delivery:1"
    assert response.json()["resolved_pipeline_sha256"] == "b" * 64
    assert graph_run.status_code == 200
    assert graph_run.json()["id"] == response.json()["pipeline_run_id"]
    assert (
        response.json()["journey_binding_snapshot"]["codex-backend"]["identity"]
        == "deterministic-test"
    )
