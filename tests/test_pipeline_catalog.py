from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.orchestration import (
    GraphCompilation,
    PipelineCatalog,
    PipelineCreate,
    SQLitePipelineRepository,
    create_pipeline_router,
)


class GraphCompiler:
    def compile(self, definition: dict[str, object]) -> GraphCompilation:
        return GraphCompilation(
            graph={
                "topological_order": ["plan", "delivery"],
                "entry_node_ids": ["plan"],
                "exit_node_ids": ["delivery"],
                "loops": [],
            },
            fingerprint="a" * 64,
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
