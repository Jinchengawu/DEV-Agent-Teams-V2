from pathlib import Path

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.orchestration import (
    GraphCompilation,
    PipelineCatalog,
    PipelineCreate,
    SQLitePipelineRepository,
    WorkcellStageBinding,
)


class Compiler:
    def compile(self, definition: dict[str, object]) -> GraphCompilation:
        return GraphCompilation(
            graph={"topological_order": ["frontend"]},
            fingerprint="a" * 64,
            capability_ids=(),
        )


class ProviderBindings:
    def snapshot(
        self,
        definition: dict[str, object],
        assignments: dict[str, str],
    ) -> dict[str, dict[str, object]]:
        del definition
        return {
            site: {
                "deployment": {"id": deployment},
                "binding": {"binding_fingerprint": character * 64},
            }
            for (site, deployment), character in zip(
                sorted(assignments.items()),
                "123456789abcdef",
                strict=False,
            )
        }


def _request(assignments: dict[str, str]) -> PipelineCreate:
    return PipelineCreate(
        id="workcell-pipeline",
        name="Workcell Pipeline",
        definition={
            "id": "workcell-pipeline",
            "version": "1.0.0",
            "nodes": [
                {
                    "kind": "stage",
                    "id": "frontend",
                    "workflow_mode": "agentscope.workcell-team",
                    "bindings": {
                        "main": "workcell.lead",
                        "delegate_1": "workcell.delegate",
                        "delegate_2": "workcell.delegate",
                        "delegate_3": "workcell.delegate",
                    },
                    "output_validator": "workcell-result-v1",
                }
            ],
            "edges": [],
        },
        agent_assignments=assignments,
        workcell_stage_map={
            "frontend": WorkcellStageBinding(
                workcell_key="frontend",
                slot_bindings={
                    slot: f"frontend.{slot}"
                    for slot in ("main", "delegate_1", "delegate_2", "delegate_3")
                },
            )
        },
        release_contract_snapshot=("frontend",),
    )


def test_pipeline_publication_freezes_workcell_map_release_contract_and_all_slots(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=Compiler(),
        provider_binding_resolver=ProviderBindings(),
    )
    assignments = {
        f"frontend.{slot}": f"deployment-{slot}"
        for slot in ("main", "delegate_1", "delegate_2", "delegate_3")
    }
    created = catalog.create_pipeline(_request(assignments), created_by="admin")
    validated = catalog.validate_draft(
        created.draft.id,
        expected_version=created.draft.version,
    )
    revision = catalog.publish_draft(
        created.draft.id,
        expected_version=validated.version,
        published_by="admin",
    )

    assert validated.validation_status == "valid"
    assert revision.release_contract_snapshot == ("frontend",)
    assert revision.workcell_stage_map["frontend"].workcell_key == "frontend"
    assert set(revision.workcell_stage_map["frontend"].slot_bindings) == {
        "main",
        "delegate_1",
        "delegate_2",
        "delegate_3",
    }
    assert catalog.get_revision("workcell-pipeline", 1) == revision


def test_pipeline_validation_rejects_missing_frozen_delegate_provider_binding(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=Compiler(),
        provider_binding_resolver=ProviderBindings(),
    )
    assignments = {
        f"frontend.{slot}": f"deployment-{slot}"
        for slot in ("main", "delegate_1", "delegate_2")
    }
    created = catalog.create_pipeline(_request(assignments), created_by="admin")

    validated = catalog.validate_draft(
        created.draft.id,
        expected_version=created.draft.version,
    )

    assert validated.validation_status == "invalid"
    assert validated.validation_errors == (
        "PIPELINE_WORKCELL_SLOT_BINDING_MISSING:frontend:delegate_3:frontend.delegate_3",
    )
