from pathlib import Path

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.orchestration import (
    GraphCompilation,
    KnowledgeContextBinding,
    PipelineCatalog,
    PipelineCreate,
    PipelineDraftPatch,
    SQLitePipelineRepository,
)


class Compiler:
    def __init__(self, *, exposes_acwm_contract: bool = True) -> None:
        self.exposes_acwm_contract = exposes_acwm_contract

    def compile(self, definition: dict[str, object]) -> GraphCompilation:
        del definition
        return GraphCompilation(
            graph={"topological_order": ["requirements"]},
            fingerprint="a" * 64,
            capability_ids=(),
            stage_input_artifact_contracts=(
                {
                    "requirements": (
                        {
                            "id": "knowledge-context-v1",
                            "version": "1.0.0",
                            "sha256": "d" * 64,
                        },
                    )
                }
                if self.exposes_acwm_contract
                else {}
            ),
        )


class ProviderBindings:
    def __init__(self, *, exposes_knowledge_contract: bool = True) -> None:
        self.exposes_knowledge_contract = exposes_knowledge_contract

    def snapshot(
        self,
        definition: dict[str, object],
        assignments: dict[str, str],
    ) -> dict[str, dict[str, object]]:
        del definition
        contracts: list[dict[str, object]] = []
        if self.exposes_knowledge_contract:
            contracts.append({"id": "knowledge-context-v1", "version": "1.0.0"})
        return {
            site: {
                "deployment": {"id": deployment},
                "binding": {"binding_fingerprint": "1" * 64},
                "provider_input_contracts": contracts,
            }
            for site, deployment in assignments.items()
        }


class KnowledgePolicies:
    def __init__(self, known: tuple[str, ...] = ("retrieval-v1",)) -> None:
        self.known = known

    def validate(self, retrieval_policy_revision_id: str, max_context_bytes: int) -> None:
        assert max_context_bytes > 0
        if retrieval_policy_revision_id not in self.known:
            raise ValueError("KNOWLEDGE_RETRIEVAL_POLICY_NOT_FOUND")


def _request() -> PipelineCreate:
    return PipelineCreate(
        id="knowledge-pipeline",
        name="Knowledge Pipeline",
        definition={
            "id": "knowledge-pipeline",
            "version": "1.0.0",
            "nodes": [
                {
                    "kind": "stage",
                    "id": "requirements",
                    "workflow_mode": "agentscope.role-turn",
                    "bindings": {"actor": "hermes-pm"},
                    "output_validator": "requirement-artifact-v1",
                }
            ],
            "edges": [],
        },
        agent_assignments={"requirements.actor": "deployment-hermes"},
        knowledge_context_bindings={
            "requirements": KnowledgeContextBinding(
                stage_path="requirements",
                acwm_artifact_slot="knowledge-context-v1",
                acwm_artifact_contract_version="1.0.0",
                acwm_artifact_contract_sha256="d" * 64,
                retrieval_policy_revision_id="retrieval-v1",
                required=True,
                max_context_bytes=16_384,
            )
        },
    )


def _catalog(
    tmp_path: Path,
    *,
    exposes_knowledge_contract: bool = True,
    exposes_acwm_contract: bool = True,
    policies: KnowledgePolicies | None = None,
) -> PipelineCatalog:
    database = tmp_path / "knowledge-pipeline.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    return PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=Compiler(exposes_acwm_contract=exposes_acwm_contract),
        provider_binding_resolver=ProviderBindings(
            exposes_knowledge_contract=exposes_knowledge_contract
        ),
        knowledge_binding_policy=policies or KnowledgePolicies(),
    )


def test_pipeline_publication_freezes_knowledge_context_binding(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_pipeline(_request(), created_by="admin")

    validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)
    revision = catalog.publish_draft(
        created.draft.id,
        expected_version=validated.version,
        published_by="admin",
    )

    assert validated.validation_status == "valid"
    assert revision.knowledge_context_bindings["requirements"].required is True
    assert (
        catalog.get_revision("knowledge-pipeline", 1).knowledge_context_bindings
        == revision.knowledge_context_bindings
    )


def test_pipeline_patch_preserves_http_deserialized_knowledge_bindings(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_pipeline(_request(), created_by="admin")
    raw_binding = next(iter(_request().knowledge_context_bindings.values())).model_dump(
        mode="json"
    )

    patched = catalog.patch_draft(
        created.draft.id,
        PipelineDraftPatch.model_validate(
            {
                "expected_version": created.draft.version,
                "knowledge_context_bindings": {"requirements": raw_binding},
            }
        ),
    )

    assert isinstance(
        patched.knowledge_context_bindings["requirements"],
        KnowledgeContextBinding,
    )
    assert catalog.get_draft(patched.id).knowledge_context_bindings == (
        patched.knowledge_context_bindings
    )

def test_pipeline_rejects_missing_provider_input_contract(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, exposes_knowledge_contract=False)
    created = catalog.create_pipeline(_request(), created_by="admin")

    validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)

    assert validated.validation_status == "invalid"
    assert validated.validation_errors == (
        "PIPELINE_KNOWLEDGE_PROVIDER_INPUT_CONTRACT_MISSING:requirements:requirements.actor:knowledge-context-v1",
    )


def test_pipeline_rejects_provider_claim_without_acwm_stage_contract(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, exposes_acwm_contract=False)
    created = catalog.create_pipeline(_request(), created_by="admin")

    validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)

    assert validated.validation_status == "invalid"
    assert validated.validation_errors == (
        "PIPELINE_KNOWLEDGE_ACWM_ARTIFACT_CONTRACT_MISSING:requirements:knowledge-context-v1:1.0.0",
    )


def test_pipeline_rejects_unknown_retrieval_policy(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, policies=KnowledgePolicies(known=()))
    created = catalog.create_pipeline(_request(), created_by="admin")

    validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)

    assert validated.validation_status == "invalid"
    assert validated.validation_errors == (
        "KNOWLEDGE_RETRIEVAL_POLICY_NOT_FOUND:requirements:retrieval-v1",
    )


def test_legacy_pipeline_remains_valid_without_knowledge_runtime(tmp_path: Path) -> None:
    database = tmp_path / "legacy-pipeline.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=Compiler(),
        provider_binding_resolver=ProviderBindings(),
    )
    created = catalog.create_pipeline(
        PipelineCreate(
            id="legacy-pipeline",
            name="Legacy Pipeline",
            definition={
                "id": "legacy-pipeline",
                "version": "1.0.0",
                "nodes": [],
                "edges": [],
            },
        ),
        created_by="admin",
    )

    validated = catalog.validate_draft(created.draft.id, expected_version=created.draft.version)

    assert validated.validation_status == "valid"
    assert validated.knowledge_context_bindings == {}
