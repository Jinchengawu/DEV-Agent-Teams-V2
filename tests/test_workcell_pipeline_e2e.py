from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_team_os.control_plane import (
    AgentInstanceCreate,
    ControlPlaneService,
    HealthResult,
)
from agent_team_os.delivery import (
    DeliveryBuildIdentitySnapshot,
    DeliveryExecutionSnapshot,
    DeliveryKnowledgeContextSnapshot,
    DeliveryMethodSnapshot,
    DeliveryRun,
    DeliveryWorkspaceSnapshot,
    KnowledgePreparationInputV1,
    SQLiteDeliveryRepository,
)
from agent_team_os.infrastructure.acwm import (
    ACWMGraphCompiler,
    ACWMPipelineGraphRuntime,
    AgentDeploymentBindingResolver,
    ControlPlaneBindingResolver,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import (
    ExternalForwardGitRemote,
    ExternalGitBinding,
    ExternalGitWorkspaceManager,
)
from agent_team_os.journey import load_agent_workcell_knowledge_delivery_definition
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentProfileCatalog,
    AgentRunLedger,
    AgentRuntimeDispatcher,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
    ensure_builtin_workcell_agent_deployments,
)
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.delivery import (
    BackendDeliveryPipelinePolicy,
    PipelineExecutionModule,
    PlanningRoleTurnRuntimeAdapter,
)
from agent_team_os.modules.extensions import (
    RuntimeExtensionCatalog,
    SQLiteRuntimeExtensionRepository,
)
from agent_team_os.modules.knowledge import (
    AuthorizationApprovalComponent,
    AuthorizationConnectionComponent,
    KnowledgeAuthorizationStampV1,
    KnowledgeContextRuntimeGuard,
    KnowledgeContextStageResult,
    MembershipAuthorizationComponent,
    SQLiteKnowledgeContextRepository,
)
from agent_team_os.modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    PipelineRevision,
    PipelineRunLedger,
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
)
from agent_team_os.modules.releases import (
    ExternalForwardReleaseCoordinator,
    ExternalReleaseCatalog,
    GitHubPRReceiptCreate,
    SQLiteExternalReleaseRepository,
    WorkspaceCandidateV2,
)
from agent_team_os.modules.releases.acceptance_application import (
    ReleaseAcceptanceVerifierV2,
)
from agent_team_os.modules.releases.acceptance_domain import ReleaseAcceptanceReportV2
from agent_team_os.modules.workcells import (
    CommandWorkcellMachineVerifier,
    SQLiteTeamTemplateRepository,
    SQLiteWorkcellExecutionRepository,
    TeamTemplateCatalog,
    WorkcellAgentInvocation,
    WorkcellAgentOutput,
    WorkcellExecutionModule,
    WorkcellMethodContext,
    WorkcellStageDriver,
    builtin_knowledge_context_bindings,
    builtin_release_contract,
    builtin_workcell_stage_map,
)
from agent_team_os.shared.hashes import Sha256, sha256_json
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


class ReadyProbe:
    async def check(
        self,
        runtime_type: str,
        connection: dict[str, str],
    ) -> HealthResult:
        del connection
        identity = (
            "hermes-acp:acceptance-test"
            if runtime_type == "hermes-acp"
            else "codex-cli:acceptance-test"
        )
        return HealthResult(status="ready", identity=identity, latency_ms=1)


class StaticMethodRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def activate(self, snapshot: DeliveryMethodSnapshot) -> Iterator[WorkcellMethodContext]:
        self.root.mkdir(parents=True, exist_ok=True)
        yield WorkcellMethodContext(
            control_workspace=self.root,
            environment={"CODEX_HOME": str(self.root / "codex-home")},
            method_entries=snapshot.method_entries,
        )


class FourRepositoryAgent:
    def __init__(self, runtime_identity: str = "deterministic-test") -> None:
        self.runtime_identity = runtime_identity

    async def run(self, invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
        if invocation.phase == "planning":
            content = {
                "assignments": json.loads(invocation.instruction.split("：", 1)[1])
            }
        elif invocation.phase == "synthesis":
            content = {"status": "passed", "workcell": invocation.workcell_key}
        elif invocation.workspace_access == "workspace_write":
            source, test = {
                "design": ("design/candidate.md", "tests/test_design_candidate.py"),
                "frontend": ("src/candidate.txt", "tests/test_frontend_candidate.py"),
                "backend": ("src/candidate.txt", "tests/test_backend_candidate.py"),
                "qa": ("reports/candidate.md", "tests/test_qa_candidate.py"),
            }[invocation.workcell_key]
            source_path = invocation.workspace / source
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                f"{invocation.workcell_key} candidate\n",
                encoding="utf-8",
            )
            test_path = invocation.workspace / test
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(
                "import unittest\n\n"
                "class CandidateTest(unittest.TestCase):\n"
                "    def test_candidate(self) -> None:\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            content = {"changed_files": [source, test]}
        elif invocation.workspace_access == "candidate_read":
            content = {"blocking_findings": [], "method_id": invocation.method_id}
        else:
            content = {
                "artifact": invocation.method_id,
                "acceptance_coverage": ["AC-1"],
            }
        return WorkcellAgentOutput(
            runtime_identity=self.runtime_identity,
            content=content,
        )


class HermesFixturePlanning(DeterministicPlanningService):
    evidence_identity = "hermes-acp:acceptance-test"


class KnowledgePolicies:
    def validate(self, retrieval_policy_revision_id: str, max_context_bytes: int) -> None:
        assert retrieval_policy_revision_id == "gate-retrieval-v1"
        assert max_context_bytes == 65_536


class StaticKnowledgeAuthorization:
    def __init__(self, stamp: KnowledgeAuthorizationStampV1) -> None:
        self.stamp = stamp

    def resolve(self, **_kwargs: object) -> KnowledgeAuthorizationStampV1:
        return self.stamp


RepositorySet = dict[str, tuple[Path, str]]


class PRSurface:
    def ensure(
        self,
        candidate: WorkspaceCandidateV2,
        _binding: ExternalGitBinding,
    ) -> GitHubPRReceiptCreate:
        ordinal = {"design": 1, "frontend": 2, "backend": 3, "qa": 4}[
            candidate.workcell_key
        ]
        return GitHubPRReceiptCreate(
            pull_request_id=ordinal,
            url=f"https://github.com/deterministic/{candidate.workcell_key}/pull/{ordinal}",
            head_branch=candidate.candidate_branch,
            head_candidate_sha=candidate.candidate_revision,
            state="open",
        )


def test_four_repository_workcell_pipeline_and_forward_only_release(
    tmp_path: Path,
) -> None:
    asyncio.run(_run_four_repository_pipeline(tmp_path))


async def _run_four_repository_pipeline(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, root / "migrations").migrate()
    remotes = {role: _remote(tmp_path, role) for role in builtin_release_contract()}
    _project_and_workspaces(database, remotes)
    control_plane = ControlPlaneService(
        database,
        config_root=root / "config",
        probe=ReadyProbe(),
    )
    control_plane.import_builtin_journey(
        planning_identity="deterministic-test",
        execution_identity="deterministic-test",
    )
    hermes_instance = control_plane.create_instance(
        AgentInstanceCreate(
            name="Hermes ACP acceptance fixture",
            runtime_type="hermes-acp",
            connection={"command": "hermes"},
        )
    )
    codex_instance = control_plane.create_instance(
        AgentInstanceCreate(
            name="Codex CLI acceptance fixture",
            runtime_type="codex-cli",
            connection={"command": "codex"},
        )
    )
    hermes_instance = await control_plane.check_instance(hermes_instance.id)
    codex_instance = await control_plane.check_instance(codex_instance.id)
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    providers = ProviderManifestCatalog()
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database),
        profiles,
        control_plane,
        providers,
        extensions=RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database)),
    )
    assignments = ensure_builtin_workcell_agent_deployments(
        profiles,
        deployments,
        planning_instance_id=hermes_instance.id,
        execution_instance_id=codex_instance.id,
    )
    pipelines = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=ACWMGraphCompiler(),
        binding_resolver=ControlPlaneBindingResolver(
            control_plane.get_binding,
            control_plane.get_instance,
        ),
        provider_binding_resolver=AgentDeploymentBindingResolver(deployments, providers),
        definition_policy=BackendDeliveryPipelinePolicy(),
        knowledge_binding_policy=KnowledgePolicies(),
    )
    knowledge_bindings = builtin_knowledge_context_bindings("gate-retrieval-v1")
    created = pipelines.create_pipeline(
        PipelineCreate(
            id="agent-workcell-delivery-r2",
            name="Knowledge-enabled Agent Workcell Delivery",
            definition=load_agent_workcell_knowledge_delivery_definition(root / "config"),
            agent_assignments=assignments,
            workcell_stage_map=builtin_workcell_stage_map(),
            release_contract_snapshot=builtin_release_contract(),
            knowledge_context_bindings=knowledge_bindings,
        ),
        created_by="system",
    )
    validated = pipelines.validate_draft(
        created.draft.id,
        expected_version=created.draft.version,
    )
    revision = pipelines.publish_draft(
        validated.id,
        expected_version=validated.version,
        published_by="system",
    )
    artifacts = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    knowledge_guard, preparation_input, preparation_run_id, snapshot = (
        _prepare_knowledge_snapshot(
            database=database,
            artifacts=artifacts,
            revision=revision,
            remotes=remotes,
        )
    )
    delivery = DeliveryRun(
        id="delivery-four-repositories",
        project_id="project-four-repositories",
        workspace_id="project:project-four-repositories",
        user_request="实现四仓可验证交付",
        status="queued",
        version=1,
        pipeline_run_id="pipeline-run-four-repositories",
        pipeline_revision_id="agent-workcell-delivery-r2:1",
        resolved_pipeline_sha256=Sha256.validate(revision.fingerprint),
        resolved_journey_sha256=Sha256.validate("1" * 64),
        resolved_provider_bindings=revision.resolved_provider_bindings,
        delivery_execution_snapshot=snapshot,
        knowledge_preparation_input=preparation_input,
        knowledge_preparation_run_id=preparation_run_id,
        evidence_identity="deterministic-test",
        planning_identity="deterministic-test",
    )
    delivery_repository = SQLiteDeliveryRepository(database)
    delivery_repository.save(delivery)
    kernel = WorkcellExecutionModule(
        SQLiteWorkcellExecutionRepository(database),
        artifact_storage=artifacts,
    )
    release_repository = SQLiteExternalReleaseRepository(database)
    bindings = {
        f"workspace-{role}": ExternalGitBinding(remote_uri=str(remote))
        for role, (remote, _base) in remotes.items()
    }
    driver = WorkcellStageDriver(
        kernel=kernel,
        artifacts=artifacts,
        methods=StaticMethodRuntime(tmp_path / "method-runtime"),
        agent=FourRepositoryAgent("codex-cli:acceptance-test"),
        workspaces=ExternalGitWorkspaceManager(tmp_path / "workcell-runtime"),
        binding_resolver=bindings.__getitem__,
        verifier=CommandWorkcellMachineVerifier(
            lambda _workcell: (
                (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"),
            )
        ),
        releases=ExternalReleaseCatalog(release_repository),
        pull_requests=PRSurface(),
        knowledge_guard=knowledge_guard,
    )
    external_release = ExternalForwardReleaseCoordinator(
        release_repository,
        ExternalForwardGitRemote(bindings.__getitem__),
    )
    planning = HermesFixturePlanning()
    executor = DeterministicCodeExecutor()
    execution = PipelineExecutionModule(
        planning=planning,
        executor=executor,
        verifier=None,
        applier=None,
        repository=delivery_repository,
        catalog=pipelines,
        runs=PipelineRunLedger(
            SQLitePipelineRunRepository(database),
            ACWMPipelineGraphRuntime(),
        ),
        agent_runs=AgentRunLedger(database),
        runtime_dispatcher=AgentRuntimeDispatcher(
            (PlanningRoleTurnRuntimeAdapter(planning, adapter_id="hermes.acp"),)
        ),
        workcell_stage_driver=driver,
        external_release=external_release,
        knowledge_runtime_guard=knowledge_guard,
    )
    execution.start(delivery)
    await execution.advance(delivery.id)
    planned = _delivery(delivery_repository, delivery.id)
    assert planned.status == "awaiting_plan_decision", planned.error_code
    assert planned.plan_gate is not None
    await execution.decide_plan(
        planned,
        decision="approve",
        expected_version=planned.version,
        expected_subject_sha256=planned.plan_gate.subject_sha256,
    )
    designed = _delivery(delivery_repository, delivery.id)
    assert designed.status == "awaiting_design_decision"
    assert designed.design_gate is not None
    await execution.decide_design(
        designed,
        decision="approve",
        expected_version=designed.version,
        expected_subject_sha256=designed.design_gate.subject_sha256,
    )
    gated = _delivery(delivery_repository, delivery.id)
    assert gated.status == "awaiting_candidate_decision", (
        gated.error_code,
        [
            (tree.workcell_run.stage_path, tree.workcell_run.status, tree.workcell_run.error_code)
            for tree in kernel.list_delivery(delivery.id)
        ],
    )
    assert gated.candidate_gate is not None
    assert set(gated.workcell_candidates) == set(builtin_release_contract())
    assert gated.release_bundle_v2_sha256 is not None
    assert len(kernel.list_delivery(delivery.id)) == 5

    await execution.decide_candidate(
        gated,
        decision="accept",
        expected_version=gated.version,
        expected_subject_sha256=gated.candidate_gate.subject_sha256,
    )

    completed = _delivery(delivery_repository, delivery.id)
    manifest = release_repository.get_manifest(delivery.project_id)
    assert completed.status == "completed"
    assert manifest is not None
    assert completed.release_manifest_v2_sha256 == manifest.manifest_sha256
    assert len(manifest.repositories) == 4
    for role, (remote, _base) in remotes.items():
        assert _git(remote, "rev-parse", "refs/heads/main") == (
            completed.workcell_candidates[role].candidate_revision
        )

    completed_snapshot = completed.delivery_execution_snapshot
    assert completed_snapshot is not None
    build_identity = completed_snapshot.build_identity
    assert build_identity is not None
    persisted_preparation = SQLiteKnowledgeContextRepository(database).get(
        preparation_run_id
    )
    assert persisted_preparation.preparation_input == preparation_input
    assert persisted_preparation.authorization_stamp == _knowledge_stamp()
    assert persisted_preparation.final_snapshot == completed_snapshot
    assert {
        item.stage_path
        for item in SQLiteKnowledgeContextRepository(database).list_stage_results(
            preparation_run_id
        )
    } == set(completed_snapshot.knowledge_context_bindings)
    for stage_path in completed_snapshot.knowledge_context_bindings:
        assert knowledge_guard.admit(completed, stage_path) is not None
    verifier = ReleaseAcceptanceVerifierV2(
        database=database,
        project_root=root,
        artifact_root=tmp_path / "artifacts",
        remote=ExternalForwardGitRemote(bindings.__getitem__),
        knowledge_guard=knowledge_guard,
        current_build_identity=lambda: build_identity,
    )
    acceptance = verifier.verify(
        project_id=delivery.project_id,
        delivery_id=delivery.id,
    )
    assert acceptance.status == "passed", [
        (check.code, check.detail)
        for check in acceptance.checks
        if check.status == "failed"
    ]
    assert (acceptance.fail, acceptance.warn, acceptance.skipped) == (0, 0, 0)
    checks = {check.code: check.status for check in acceptance.checks}
    assert {
        "CANDIDATE_EVIDENCE_VERIFIED",
        "BUILD_IDENTITY_VERIFIED",
        "DELIVERY_TERMINAL_VERIFIED",
        "HERMES_PLANNING_ATTEMPTS_VERIFIED",
        "CODEX_WORKCELL_ATTEMPTS_VERIFIED",
        "WORKCELL_RESULTS_VERIFIED",
        "KNOWLEDGE_CONTEXTS_VERIFIED",
        "RELEASE_BUNDLE_VERIFIED",
        "REMOTE_MAIN_VERIFIED",
        "RELEASE_MANIFEST_VERIFIED",
        "RELEASE_HEALTH_VERIFIED",
    } <= {code for code, status in checks.items() if status == "passed"}

    with sqlite3.connect(database) as connection:
        planning_attempt_row = connection.execute(
            "SELECT aa.id FROM agent_attempts aa "
            "JOIN agent_runs ar ON ar.id=aa.agent_run_id "
            "WHERE ar.delivery_id=? AND ar.binding_site='requirements.actor'",
            (delivery.id,),
        ).fetchone()
        assert planning_attempt_row is not None
        planning_attempt_id = str(planning_attempt_row[0])
        connection.execute(
            "UPDATE agent_attempts SET phase='delegate' WHERE id=?",
            (planning_attempt_id,),
        )
    planning_tampered = verifier.verify(
        project_id=delivery.project_id,
        delivery_id=delivery.id,
    )
    assert (
        _check_status(planning_tampered, "HERMES_PLANNING_ATTEMPTS_VERIFIED")
        == "failed"
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agent_attempts SET phase='legacy' WHERE id=?",
            (planning_attempt_id,),
        )

    qa_preparation = next(
        tree
        for tree in kernel.list_delivery(delivery.id)
        if tree.workcell_run.stage_path == "qa-preparation-repair/qa-preparation"
    )
    assert qa_preparation.result_validation is not None
    original_validation_sha = qa_preparation.result_validation.sha256
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE workcell_result_validations SET sha256=? WHERE workcell_run_id=?",
            ("f" * 64, qa_preparation.workcell_run.id),
        )
    result_tampered = verifier.verify(
        project_id=delivery.project_id,
        delivery_id=delivery.id,
    )
    assert _check_status(result_tampered, "WORKCELL_RESULTS_VERIFIED") == "failed"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE workcell_result_validations SET sha256=? WHERE workcell_run_id=?",
            (original_validation_sha, qa_preparation.workcell_run.id),
        )
        snapshot_row = connection.execute(
            "SELECT workcell_snapshot_json,workcell_snapshot_sha256 "
            "FROM workcell_runs WHERE id=?",
            (qa_preparation.workcell_run.id,),
        ).fetchone()
        assert snapshot_row is not None
        original_snapshot_json, original_snapshot_sha = snapshot_row
        forged_snapshot = json.loads(str(original_snapshot_json))
        forged_snapshot["team_template_revision_id"] = "forged-team:9"
        connection.execute(
            "UPDATE workcell_runs SET workcell_snapshot_json=?,"
            "workcell_snapshot_sha256=? WHERE id=?",
            (
                json.dumps(forged_snapshot, sort_keys=True, separators=(",", ":")),
                sha256_json(forged_snapshot),
                qa_preparation.workcell_run.id,
            ),
        )
    snapshot_tampered = verifier.verify(
        project_id=delivery.project_id,
        delivery_id=delivery.id,
    )
    assert _check_status(snapshot_tampered, "WORKCELL_TERMINALS_VERIFIED") == "failed"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE workcell_runs SET workcell_snapshot_json=?,"
            "workcell_snapshot_sha256=? WHERE id=?",
            (
                original_snapshot_json,
                original_snapshot_sha,
                qa_preparation.workcell_run.id,
            ),
        )
        assert qa_preparation.workcell_run.main_agent_run_id is not None
        synthesis_row = connection.execute(
            "SELECT id FROM agent_attempts "
            "WHERE agent_run_id=? AND phase='synthesis'",
            (qa_preparation.workcell_run.main_agent_run_id,),
        ).fetchone()
        assert synthesis_row is not None
        synthesis_attempt_id = str(synthesis_row[0])
        connection.execute(
            "UPDATE agent_attempts SET phase='legacy' WHERE id=?",
            (synthesis_attempt_id,),
        )
    attempt_tampered = verifier.verify(
        project_id=delivery.project_id,
        delivery_id=delivery.id,
    )
    assert _check_status(attempt_tampered, "CODEX_WORKCELL_ATTEMPTS_VERIFIED") == "failed"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agent_attempts SET phase='synthesis' WHERE id=?",
            (synthesis_attempt_id,),
        )
        connection.execute(
            "DELETE FROM knowledge_context_stage_results "
            "WHERE preparation_run_id=? AND stage_path='requirements'",
            (preparation_run_id,),
        )
    context_tampered = verifier.verify(
        project_id=delivery.project_id,
        delivery_id=delivery.id,
    )
    assert _check_status(context_tampered, "KNOWLEDGE_CONTEXTS_VERIFIED") == "failed"


def _prepare_knowledge_snapshot(
    *,
    database: Path,
    artifacts: ContentAddressedArtifactStorage,
    revision: PipelineRevision,
    remotes: RepositorySet,
) -> tuple[
    KnowledgeContextRuntimeGuard,
    KnowledgePreparationInputV1,
    str,
    DeliveryExecutionSnapshot,
]:
    stamp = _knowledge_stamp()
    stage_bindings = {
        key: value.model_dump(mode="json")
        for key, value in revision.knowledge_context_bindings.items()
    }
    project_description = artifacts.put_json(
        {
            "contract_id": "project-description-snapshot-v1",
            "project_id": "project-four-repositories",
            "project_version": 1,
            "name": "Four repos",
            "description": "Deterministic Release Acceptance fixture",
        },
        media_type="application/vnd.agent-team-os.project-description-snapshot+json",
    )
    preparation_payload = {
        "delivery_id": "delivery-four-repositories",
        "project_id": "project-four-repositories",
        "project_version": 1,
        "project_description_snapshot": project_description.model_dump(mode="json"),
        "authorized_principal_id": stamp.authorized_principal_id,
        "delivery_goal": "实现四仓可验证交付",
        "pipeline_revision_id": f"{revision.pipeline_id}:{revision.revision}",
        "pipeline_revision_sha256": revision.fingerprint,
        "authorization_access_component": stamp.access_component.model_dump(mode="json"),
        "approved_knowledge_approval_ids": tuple(
            item.approval_id for item in stamp.approvals
        ),
        "stage_bindings": stage_bindings,
        "stage_responsibilities": {
            stage_path: f"Verify {stage_path}" for stage_path in stage_bindings
        },
    }
    preparation_input = KnowledgePreparationInputV1.model_validate(
        {
            **preparation_payload,
            "input_sha256": sha256_json(preparation_payload),
        }
    )
    contexts: dict[str, DeliveryKnowledgeContextSnapshot] = {}
    for stage_path, binding in sorted(stage_bindings.items()):
        reference = artifacts.put_json(
            {
                "contract_id": "knowledge-context-v1",
                "contract_version": "1.0.0",
                "trust_class": "external-collaborative",
                "instruction_authority": "none",
                "delivery_id": preparation_input.delivery_id,
                "project_id": preparation_input.project_id,
                "stage_path": stage_path,
                "query_sha256": sha256_json({"stage_path": stage_path}),
                "retrieval_policy_revision_id": binding[
                    "retrieval_policy_revision_id"
                ],
                "approved_scope": [
                    item.model_dump(mode="json") for item in stamp.approvals
                ],
                "authorization_stamp": stamp.model_dump(mode="json"),
                "retrievals": [
                    {
                        "binding_id": "feishu-binding-acceptance",
                        "approval_id": "approval-acceptance",
                        "hits": [],
                    }
                ],
                "citation_ids": [],
            },
            media_type="application/vnd.agent-team-os.knowledge-context+json",
        )
        contexts[stage_path] = DeliveryKnowledgeContextSnapshot(
            stage_path=stage_path,
            artifact_reference=reference,
            citation_ids=(),
            authorization_epoch_hash=stamp.authorization_epoch_hash,
        )
    snapshot = _snapshot(
        database,
        revision,
        remotes,
        contexts=contexts,
        stamp=stamp,
        preparation_input=preparation_input,
    )
    repository = SQLiteKnowledgeContextRepository(database)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    run = repository.create_or_get(
        preparation_input,
        knowledge_binding_hash=sha256_json(stage_bindings),
        now=now,
    )
    repository.acquire(
        run.id,
        lease_owner="acceptance-fixture",
        now=now,
        lease_ttl=timedelta(minutes=5),
    )
    for stage_path, context in contexts.items():
        repository.put_stage_result(
            KnowledgeContextStageResult(
                preparation_run_id=run.id,
                stage_path=stage_path,
                query_sha256=sha256_json({"stage_path": stage_path}),
                retrieval_policy_revision_id=str(
                    stage_bindings[stage_path]["retrieval_policy_revision_id"]
                ),
                context=context,
                created_at=now,
            )
        )
    completed = repository.succeed(
        run.id,
        stamp=stamp,
        final_snapshot_json=snapshot.model_dump_json(),
        now=now,
    )
    assert completed.status == "succeeded"
    guard = KnowledgeContextRuntimeGuard(
        authorization=StaticKnowledgeAuthorization(stamp),  # type: ignore[arg-type]
        artifacts=artifacts,
    )
    return guard, preparation_input, run.id, snapshot


def _knowledge_stamp() -> KnowledgeAuthorizationStampV1:
    access = MembershipAuthorizationComponent(
        membership_id="project-four-repositories:evaluator",
        version=1,
    )
    approvals = (
        AuthorizationApprovalComponent(
            approval_id="approval-acceptance",
            approval_version=1,
            binding_id="feishu-binding-acceptance",
            binding_authorization_version=1,
            approved_source_scope_sha256=Sha256.validate("a" * 64),
        ),
    )
    connections = (
        AuthorizationConnectionComponent(
            connection_id="feishu-connection-acceptance",
            authorization_version=1,
        ),
    )
    payload = {
        "policy_id": "best-effort-revoke-v1",
        "global_identity_policy_revision": 1,
        "project_id": "project-four-repositories",
        "authorized_principal_id": "evaluator",
        "identity_authorization_version": 1,
        "global_role": "editor",
        "project_authorization_version": 1,
        "access_component": access.model_dump(mode="json"),
        "approvals": [item.model_dump(mode="json") for item in approvals],
        "connections": [item.model_dump(mode="json") for item in connections],
    }
    return KnowledgeAuthorizationStampV1(
        policy_id="best-effort-revoke-v1",
        global_identity_policy_revision=1,
        project_id="project-four-repositories",
        authorized_principal_id="evaluator",
        identity_authorization_version=1,
        global_role="editor",
        project_authorization_version=1,
        access_component=access,
        approvals=approvals,
        connections=connections,
        authorization_epoch_hash=sha256_json(payload),
    )


def _snapshot(
    database: Path,
    revision: PipelineRevision,
    remotes: RepositorySet,
    *,
    contexts: dict[str, DeliveryKnowledgeContextSnapshot],
    stamp: KnowledgeAuthorizationStampV1,
    preparation_input: KnowledgePreparationInputV1,
) -> DeliveryExecutionSnapshot:
    team = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database)).get_revision(
        "software-delivery-team",
        1,
    )
    methods = {
        method
        for stage in revision.workcell_stage_map.values()
        for method in stage.delegate_methods.values()
    }
    method_snapshot = DeliveryMethodSnapshot(
        snapshot_id="method-pack-set-v1:deterministic",
        qualification_sha256=Sha256.validate("2" * 64),
        packages=(),
        method_entries={method: {"fixture": True} for method in methods},
    )
    workspaces = tuple(
        DeliveryWorkspaceSnapshot(
            workcell_key=role,
            workspace_binding_id=f"workspace-{role}",
            kind="git_repository_v1",
            adapter_type="external-git",
            repository_uri=str(remote),
            base_revision=base,
            verification_sha256=Sha256.validate(character * 64),
        )
        for (role, (remote, base)), character in zip(
            remotes.items(),
            "3456",
            strict=True,
        )
    )
    build_payload = {
        "product_revision": "7" * 40,
        "product_worktree_clean": True,
        "acwm_version": "0.5.1",
        "acwm_revision": "8" * 40,
        "framework_lock_sha256": "9" * 64,
        "framework_dependency_status": "ready",
    }
    build_identity = DeliveryBuildIdentitySnapshot(
        product_revision="7" * 40,
        product_worktree_clean=True,
        acwm_version="0.5.1",
        acwm_revision="8" * 40,
        framework_lock_sha256=Sha256.validate("9" * 64),
        framework_dependency_status="ready",
        snapshot_sha256=sha256_json(build_payload),
    )
    compiled_at = datetime(2026, 9, 2, tzinfo=UTC)
    payload = {
        "project_id": "project-four-repositories",
        "project_version": 1,
        "team_template_revision_id": "software-delivery-team:1",
        "team_template_sha256": team.sha256,
        "team_workcells": {
            item.workcell_key: item.model_dump(mode="json") for item in team.workcells
        },
        "pipeline_revision_id": f"{revision.pipeline_id}:{revision.revision}",
        "pipeline_revision_sha256": revision.fingerprint,
        "workcell_stage_map": {
            key: value.model_dump(mode="json")
            for key, value in revision.workcell_stage_map.items()
        },
        "release_contract_snapshot": revision.release_contract_snapshot,
        "knowledge_context_bindings": {
            key: value.model_dump(mode="json")
            for key, value in revision.knowledge_context_bindings.items()
        },
        "resolved_provider_bindings": revision.resolved_provider_bindings,
        "workspaces": workspaces,
        "method_snapshot": method_snapshot,
        "build_identity": build_identity,
        "knowledge_contexts": contexts,
        "knowledge_authorization_stamp": stamp.model_dump(mode="json"),
        "knowledge_preparation_input_sha256": preparation_input.input_sha256,
        "compiled_at": compiled_at,
    }
    serialized = DeliveryExecutionSnapshot.model_validate(
        {**payload, "snapshot_sha256": Sha256.validate("f" * 64)}
    ).model_dump(mode="json", exclude={"snapshot_sha256"})
    return DeliveryExecutionSnapshot.model_validate(
        {**payload, "snapshot_sha256": sha256_json(serialized)}
    )


def _check_status(report: ReleaseAcceptanceReportV2, code: str) -> str:
    return next(check.status for check in report.checks if check.code == code)


def _project_and_workspaces(database: Path, remotes: RepositorySet) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO projects(
            id,slug,name,description,lifecycle_status,version,created_by,created_at,updated_at)
            VALUES('project-four-repositories','project-four-repositories','Four repos','',
            'active',1,'test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        for role, (remote, _base) in remotes.items():
            connection.execute(
                """INSERT INTO workspace_bindings(
                id,project_id,kind,adapter_type,repository_uri,credential_reference,status,
                verification_sha256,verification_json,error_code,version,created_at,updated_at)
                VALUES(?, 'project-four-repositories','git_repository_v1','external-git',?,
                NULL,'ready',?,'{}',NULL,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (f"workspace-{role}", str(remote), sha256_json({"role": role})),
            )


def _remote(tmp_path: Path, role: str) -> tuple[Path, str]:
    remote = tmp_path / f"{role}.git"
    seed = tmp_path / f"{role}-seed"
    _run("git", "init", "--bare", "--initial-branch=main", str(remote))
    _run("git", "init", "--initial-branch=main", str(seed))
    source = {
        "design": "design/system.md",
        "frontend": "src/index.txt",
        "backend": "src/service.py",
        "qa": "reports/README.md",
    }[role]
    source_path = seed / source
    source_path.parent.mkdir(parents=True)
    source_path.write_text(f"{role} seed\n", encoding="utf-8")
    tests = seed / "tests"
    tests.mkdir()
    (tests / "test_seed.py").write_text(
        "import unittest\n\n"
        "class SeedTest(unittest.TestCase):\n"
        "    def test_seed(self) -> None:\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    _run("git", "add", "--all", cwd=seed)
    _run(
        "git",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "seed",
        cwd=seed,
    )
    _run("git", "remote", "add", "origin", str(remote), cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)
    return remote, _git(remote, "rev-parse", "refs/heads/main")


def _delivery(repository: SQLiteDeliveryRepository, delivery_id: str) -> DeliveryRun:
    delivery = repository.get(delivery_id)
    assert delivery is not None
    return delivery


def _git(repository: Path, *arguments: str) -> str:
    return _run("git", "--git-dir", str(repository), *arguments).strip()


def _run(*arguments: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
