from pathlib import Path

import pytest

from agent_team_os.delivery import DeliveryBuildIdentitySnapshot, DeliveryMethodSnapshot
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ProjectGitWorkspaces
from agent_team_os.modules.orchestration import (
    GraphCompilation,
    PipelineCatalog,
    PipelineCreate,
    SQLitePipelineRepository,
    WorkcellStageBinding,
)
from agent_team_os.modules.projects import ProjectCatalog, ProjectCreate, SQLiteProjectRepository
from agent_team_os.modules.workcells import (
    DeliveryExecutionSnapshotCompiler,
    ProjectWorkcellGovernance,
    SQLiteProjectWorkcellRepository,
    SQLiteTeamTemplateRepository,
    TeamTemplateCatalog,
)
from agent_team_os.shared.errors import ProductError


class Compiler:
    def compile(self, definition: dict[str, object]) -> GraphCompilation:
        return GraphCompilation(
            graph={"topological_order": ["design", "frontend", "backend", "qa"]},
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
                "deployment": {"id": deployment, "enabled": True},
                "runtime_identity": "deterministic-workcell",
                "binding": {"binding_fingerprint": "b" * 64},
            }
            for site, deployment in assignments.items()
        }


def test_delivery_snapshot_compiles_team_pipeline_provider_workspace_and_method_revisions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    project_repository = SQLiteProjectRepository(database)
    managed_git = ProjectGitWorkspaces(tmp_path / "managed-workspaces")
    teams = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database))
    governance = ProjectWorkcellGovernance(
        SQLiteProjectWorkcellRepository(database),
        teams=teams,
        projects=project_repository,
        managed_git=managed_git,
    )
    projects = ProjectCatalog(project_repository, managed_git, team_governance=governance)
    pipeline_catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=Compiler(),
        provider_binding_resolver=ProviderBindings(),
    )
    roles = ("design", "frontend", "backend", "qa")
    slot_names = ("main", "delegate_1", "delegate_2", "delegate_3")
    stage_map = {
        role: WorkcellStageBinding(
            workcell_key=role,
            slot_bindings={slot: f"{role}.{slot}" for slot in slot_names},
        )
        for role in roles
    }
    assignments = {
        site: f"deployment-{site.replace('.', '-')}"
        for binding in stage_map.values()
        for site in binding.slot_bindings.values()
    }
    pipeline = pipeline_catalog.create_pipeline(
        PipelineCreate(
            id="four-workcell-delivery",
            name="四 Workcell 交付",
            definition={
                "id": "four-workcell-delivery",
                "version": "1.0.0",
                "nodes": [
                    {
                        "kind": "stage",
                        "id": role,
                        "workflow_mode": "agentscope.workcell-team",
                        "bindings": {
                            "main": "workcell.lead",
                            "delegate_1": "workcell.delegate",
                            "delegate_2": "workcell.delegate",
                            "delegate_3": "workcell.delegate",
                        },
                        "output_validator": "workcell-result-v1",
                    }
                    for role in roles
                ],
                "edges": [],
            },
            agent_assignments=assignments,
            workcell_stage_map=stage_map,
            release_contract_snapshot=roles,
        ),
        created_by="admin",
    )
    validated = pipeline_catalog.validate_draft(
        pipeline.draft.id,
        expected_version=pipeline.draft.version,
    )
    revision = pipeline_catalog.publish_draft(
        pipeline.draft.id,
        expected_version=validated.version,
        published_by="admin",
    )
    pipeline_catalog.activate_revision(
        pipeline.pipeline.id,
        revision=revision.revision,
        expected_version=pipeline.pipeline.version,
        activated_by="admin",
    )
    projects.create(
        ProjectCreate(
            id="snapshot-project",
            name="Snapshot 项目",
            default_pipeline_revision_id="four-workcell-delivery:1",
            team_template_revision_id="software-delivery-team:1",
        ),
        "admin",
    )
    for role in roles:
        assignment = governance.create_workspace_binding(
            "snapshot-project",
            {
                "workcell_key": role,
                "kind": "git_repository_v1",
                "adapter_type": "managed-bare-git",
                "repository_uri": f"projects/snapshot-project/{role}",
                "verification_profile_id": "python-unittest-v1",
            },
        )
        verified = governance.verify_workspace(
            assignment.workspace_binding.id,
            expected_version=assignment.workspace_binding.version,
        )
        governance.qualify_verification_profile(verified.id, expected_version=verified.version)
    governance.activate("snapshot-project", expected_version=1)
    methods = DeliveryMethodSnapshot(
        snapshot_id="method-pack-set-v1:test",
        qualification_sha256="c" * 64,
        packages=(
            {
                "package_name": "bmad-method",
                "package_version": "6.11.0",
                "qualification_sha256": "d" * 64,
            },
            {
                "package_name": "bmad-method-test-architecture-enterprise",
                "package_version": "1.23.4",
                "qualification_sha256": "e" * 64,
            },
        ),
        method_entries={"bmad-build": {"qualification_sha256": "d" * 64}},
    )
    build_identity = DeliveryBuildIdentitySnapshot(
        product_revision="1" * 40,
        product_worktree_clean=True,
        acwm_version="0.5.1",
        acwm_revision="2" * 40,
        framework_lock_sha256="3" * 64,
        framework_dependency_status="ready",
        snapshot_sha256="4" * 64,
    )
    compiler = DeliveryExecutionSnapshotCompiler(
        governance=governance,
        projects=project_repository,
        pipelines=pipeline_catalog,
        method_snapshot=lambda: methods,
        build_identity=lambda: build_identity,
    )

    snapshot = compiler.compile("snapshot-project", "four-workcell-delivery:1")

    assert snapshot.team_template_revision_id == "software-delivery-team:1"
    assert snapshot.pipeline_revision_id == "four-workcell-delivery:1"
    assert snapshot.release_contract_snapshot == roles
    assert tuple(item.workcell_key for item in snapshot.workspaces) == roles
    assert len({item.repository_uri for item in snapshot.workspaces}) == 4
    assert snapshot.method_snapshot == methods
    assert snapshot.build_identity == build_identity
    assert snapshot.review_policies is not None
    assert set(snapshot.review_policies.workcells) == set(roles)
    assert (
        compiler.compile("snapshot-project", "four-workcell-delivery:1").snapshot_sha256
        == snapshot.snapshot_sha256
    )
    assert "credential_reference" not in snapshot.model_dump_json()
    old_hash = snapshot.snapshot_sha256
    first = governance.topology("snapshot-project").workspace_bindings[0]
    changed = governance.set_verification_profile(
        first.id,
        expected_version=first.version,
        profile_id="node-native-test-v1",
    )
    with pytest.raises(ProductError) as unqualified:
        compiler.compile("snapshot-project", "four-workcell-delivery:1")
    assert unqualified.value.code == "WORKCELL_VERIFICATION_PROFILE_REQUIRED"
    governance.qualify_verification_profile(changed.id, expected_version=changed.version)
    fresh = compiler.compile("snapshot-project", "four-workcell-delivery:1")
    assert fresh.snapshot_sha256 != old_hash
    assert snapshot.workspaces[0].verification_profile.profile.id == "python-unittest-v1"
    assert fresh.workspaces[0].verification_profile.profile.id == "node-native-test-v1"
