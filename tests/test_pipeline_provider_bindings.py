import asyncio
from pathlib import Path
from typing import cast

from agent_team_os.control_plane import ControlPlaneService
from agent_team_os.delivery import (
    DeliveryCoordinator,
    InMemoryDeliveryRepository,
    SQLiteDeliveryRepository,
)
from agent_team_os.infrastructure.acwm import (
    ACWMGraphCompiler,
    ACWMPipelineGraphRuntime,
    AgentDeploymentBindingResolver,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.journey import load_backend_delivery_definition
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRunLedger,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
    ensure_builtin_agent_deployments,
)
from agent_team_os.modules.delivery import BackendDeliveryPipelinePolicy
from agent_team_os.modules.knowledge import (
    KnowledgePublicationLedger,
    KnowledgePublisher,
    SQLiteWikiRepository,
)
from agent_team_os.modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
)
from agent_team_os.modules.projects import (
    ProjectCatalog,
    ProjectLeaseDeliveryRepository,
    SQLiteProjectRepository,
)
from agent_team_os.testing import (
    DeterministicCandidateVerifier,
    DeterministicCodeExecutor,
    DeterministicPlanningService,
)


class _UnusedProvisioner:
    def provision(self, repository_ref: str) -> str:
        raise AssertionError(repository_ref)

    def reset(self, repository_ref: str) -> str:
        raise AssertionError(repository_ref)


class _FailingDocumentPublisher:
    def __init__(self, publications: KnowledgePublicationLedger) -> None:
        self.publications = publications

    def publish_required(self, delivery_id: str) -> bool:
        for publication in self.publications.list_for_delivery(delivery_id):
            if publication.status == "pending":
                self.publications.record_failure(
                    publication.id,
                    "WIKI_TEMPORARILY_UNAVAILABLE",
                    expected_version=publication.version,
                )
        return False


def _catalog(tmp_path: Path) -> tuple[PipelineCatalog, dict[str, str]]:
    root = Path(__file__).parents[1]
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, root / "migrations").migrate()
    instances = ControlPlaneService(database, config_root=root / "config")
    instances.import_builtin_journey(
        planning_identity="codex-simulated-hermes",
        execution_identity="codex-cli",
    )
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    providers = ProviderManifestCatalog()
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database), profiles, instances, providers
    )
    assignments = ensure_builtin_agent_deployments(profiles, deployments)
    return (
        PipelineCatalog(
            SQLitePipelineRepository(database),
            graph_compiler=ACWMGraphCompiler(),
            provider_binding_resolver=AgentDeploymentBindingResolver(
                deployments, providers
            ),
            definition_policy=BackendDeliveryPipelinePolicy(),
        ),
        assignments,
    )


def test_pipeline_publication_freezes_provider_bindings_by_stage_site(
    tmp_path: Path,
) -> None:
    catalog, assignments = _catalog(tmp_path)
    definition = load_backend_delivery_definition(Path(__file__).parents[1] / "config")
    created = catalog.create_pipeline(
        PipelineCreate(
            id="provider-delivery",
            name="Provider 交付",
            definition=definition,
            agent_assignments=assignments,
        ),
        created_by="test",
    )
    validated = catalog.validate_draft(
        created.draft.id, expected_version=created.draft.version
    )
    revision = catalog.publish_draft(
        created.draft.id,
        expected_version=validated.version,
        published_by="test",
    )

    assert validated.validation_status == "valid"
    assert revision.binding_model == "provider-v1"
    assert revision.binding_snapshot == {}
    assert set(revision.resolved_provider_bindings) == set(assignments)
    for snapshot in revision.resolved_provider_bindings.values():
        deployment = cast(dict[str, object], snapshot["deployment"])
        binding = cast(dict[str, object], snapshot["binding"])
        assert deployment["enabled"] is True
        assert len(cast(str, binding["binding_fingerprint"])) == 64


def test_pipeline_validation_rejects_missing_deployment_assignment(
    tmp_path: Path,
) -> None:
    catalog, assignments = _catalog(tmp_path)
    assignments.pop("tasking.actor")
    created = catalog.create_pipeline(
        PipelineCreate(
            id="missing-assignment",
            name="缺失分配",
            definition=load_backend_delivery_definition(
                Path(__file__).parents[1] / "config"
            ),
            agent_assignments=assignments,
        ),
        created_by="test",
    )

    validated = catalog.validate_draft(
        created.draft.id, expected_version=created.draft.version
    )

    assert validated.validation_status == "invalid"
    assert "PROVIDER_ASSIGNMENT_MISSING" in validated.validation_errors[0]


def test_provider_pipeline_records_frozen_agent_runs_and_typed_artifacts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        catalog, assignments = _catalog(tmp_path)
        created = catalog.create_pipeline(
            PipelineCreate(
                id="provider-runtime",
                name="Provider 运行",
                definition=load_backend_delivery_definition(
                    Path(__file__).parents[1] / "config"
                ),
                agent_assignments=assignments,
            ),
            created_by="test",
        )
        validated = catalog.validate_draft(
            created.draft.id, expected_version=created.draft.version
        )
        revision = catalog.publish_draft(
            created.draft.id,
            expected_version=validated.version,
            published_by="test",
        )
        database = tmp_path / "agent-team-os.sqlite"
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        agent_runs = AgentRunLedger(database)
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=InMemoryDeliveryRepository(),
            resolved_journey_sha256=revision.fingerprint,
        )
        coordinator.configure_pipeline_runtime(catalog, runs, agent_runs)
        delivery = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="增加健康检查",
            pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
            resolved_provider_bindings=revision.resolved_provider_bindings,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )
        for _ in range(100):
            current = coordinator.get(delivery.id)
            if current.status == "awaiting_plan_decision":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("provider pipeline did not reach plan gate")

        recorded = agent_runs.list(delivery.id)
        assert [item.binding_site for item in recorded] == [
            "requirements.actor",
            "tasking.actor",
        ]
        assert all(item.status == "succeeded" for item in recorded)
        assert all(item.runtime_identity == "codex-simulated-hermes" for item in recorded)
        assert [item.artifact_envelopes[0].contract_id for item in recorded] == [
            "requirement-artifact-v1",
            "task-contract-v1",
        ]
        for item in recorded:
            snapshot = revision.resolved_provider_bindings[item.binding_site]
            binding = cast(dict[str, object], snapshot["binding"])
            assert item.resolved_binding_hash == binding["binding_fingerprint"]

    asyncio.run(scenario())


def test_recovery_publishes_pending_role_documents_without_rerunning_agents(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        catalog, assignments = _catalog(tmp_path)
        created_pipeline = catalog.create_pipeline(
            PipelineCreate(
                id="publication-recovery",
                name="角色文档发布恢复",
                definition=load_backend_delivery_definition(
                    Path(__file__).parents[1] / "config"
                ),
                agent_assignments=assignments,
            ),
            created_by="test",
        )
        validated = catalog.validate_draft(
            created_pipeline.draft.id,
            expected_version=created_pipeline.draft.version,
        )
        revision = catalog.publish_draft(
            created_pipeline.draft.id,
            expected_version=validated.version,
            published_by="test",
        )
        database = tmp_path / "agent-team-os.sqlite"
        repository = SQLiteDeliveryRepository(database)
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        agent_runs = AgentRunLedger(database)
        publications = KnowledgePublicationLedger(database)
        interrupted = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=repository,
            resolved_journey_sha256=revision.fingerprint,
        )
        interrupted.configure_pipeline_runtime(
            catalog,
            runs,
            agent_runs,
            publications=publications,
            publication_barrier=publications,
        )
        delivery = interrupted.enqueue(
            workspace_id="backend-demo",
            user_request="增加健康检查并发布产品需求与交付计划",
            pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
            resolved_provider_bindings=revision.resolved_provider_bindings,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )
        for _ in range(100):
            current = interrupted.get(delivery.id)
            graph = runs.get_for_delivery(delivery.id)
            states = {
                str(node["node_id"]): str(node["status"])
                for node in graph.snapshot["nodes"]
            }
            if states.get("tasking") == "succeeded":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("role stages did not commit")

        assert current.status == "planning"
        assert current.plan_gate is None
        assert states["approve-plan"] == "ready"
        assert [item.status for item in agent_runs.list(delivery.id)] == [
            "succeeded",
            "succeeded",
        ]
        assert [item.status for item in publications.list_for_delivery(delivery.id)] == [
            "pending",
            "pending",
        ]

        restarted = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            repository=repository,
            resolved_journey_sha256=revision.fingerprint,
        )
        restarted.configure_pipeline_runtime(
            catalog,
            runs,
            agent_runs,
            publications=publications,
            publication_barrier=publications,
            document_publisher=KnowledgePublisher(database, publications),
        )
        await restarted.recover()

        recovered = restarted.get(delivery.id)
        assert recovered.status == "awaiting_plan_decision"
        assert recovered.plan_gate is not None
        assert len(agent_runs.list(delivery.id)) == 2
        assert [item.status for item in publications.list_for_delivery(delivery.id)] == [
            "published",
            "published",
        ]
        documents = SQLiteWikiRepository(database).list_documents(
            "project-docs:legacy-default"
        )
        assert {item.document_kind for item in documents} == {
            "product-requirement",
            "delivery-plan",
        }

    asyncio.run(scenario())


def test_publication_failure_keeps_delivery_and_lease_nonterminal_until_retry(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        catalog, assignments = _catalog(tmp_path)
        pipeline = catalog.create_pipeline(
            PipelineCreate(
                id="publication-failure",
                name="知识发布失败",
                definition=load_backend_delivery_definition(
                    Path(__file__).parents[1] / "config"
                ),
                agent_assignments=assignments,
            ),
            created_by="test",
        )
        valid = catalog.validate_draft(
            pipeline.draft.id, expected_version=pipeline.draft.version
        )
        revision = catalog.publish_draft(
            pipeline.draft.id,
            expected_version=valid.version,
            published_by="test",
        )
        database = tmp_path / "agent-team-os.sqlite"
        projects = ProjectCatalog(
            SQLiteProjectRepository(database), _UnusedProvisioner()
        )
        repository = ProjectLeaseDeliveryRepository(
            SQLiteDeliveryRepository(database), projects
        )
        runs = PipelineRunLedger(
            SQLitePipelineRunRepository(database), ACWMPipelineGraphRuntime()
        )
        agent_runs = AgentRunLedger(database)
        publications = KnowledgePublicationLedger(database)
        coordinator = DeliveryCoordinator(
            planning=DeterministicPlanningService(),
            executor=DeterministicCodeExecutor(),
            verifier=DeterministicCandidateVerifier(),
            repository=repository,
            resolved_journey_sha256=revision.fingerprint,
        )
        coordinator.configure_pipeline_runtime(
            catalog,
            runs,
            agent_runs,
            publications=publications,
            publication_barrier=publications,
            document_publisher=_FailingDocumentPublisher(publications),
        )
        delivery = coordinator.enqueue(
            workspace_id="backend-demo",
            user_request="知识发布失败时不得重跑 Agent",
            pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
            resolved_provider_bindings=revision.resolved_provider_bindings,
            resolved_journey_sha256=revision.fingerprint,
            resolved_pipeline_sha256=revision.fingerprint,
        )
        for _ in range(100):
            failed_publications = publications.list_for_delivery(delivery.id)
            if len(failed_publications) == 2 and all(
                item.status == "failed" for item in failed_publications
            ):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("publication failure was not recorded")

        blocked = coordinator.get(delivery.id)
        graph = runs.get_for_delivery(delivery.id)
        gate = next(
            node
            for node in graph.snapshot["nodes"]
            if node["node_id"] == "approve-plan"
        )
        assert blocked.status == "planning"
        assert blocked.error_code is None
        assert blocked.plan_gate is None
        assert gate["status"] == "ready"
        assert projects.repository.active_delivery_id("legacy-default") == delivery.id
        assert len(agent_runs.list(delivery.id)) == 2

        publisher = KnowledgePublisher(database, publications)
        for failed in failed_publications:
            publisher.publish(failed.id, expected_version=failed.version)
        await coordinator.resume_publications(delivery.id)

        resumed = coordinator.get(delivery.id)
        assert resumed.status == "awaiting_plan_decision"
        assert resumed.plan_gate is not None
        assert projects.repository.active_delivery_id("legacy-default") == delivery.id
        assert len(agent_runs.list(delivery.id)) == 2

        await coordinator.decide_plan(
            delivery.id,
            decision="approve",
            expected_version=resumed.version,
            expected_subject_sha256=resumed.plan_gate.subject_sha256,
        )
        for _ in range(100):
            candidate = coordinator.get(delivery.id)
            if candidate.status == "awaiting_candidate_decision":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("candidate Gate did not open")
        documents = SQLiteWikiRepository(database).list_documents(
            "project-docs:legacy-default"
        )
        assert {item.document_kind for item in documents} == {
            "product-requirement",
            "delivery-plan",
        }
        assert len(publications.list_for_delivery(delivery.id)) == 2

    asyncio.run(scenario())
