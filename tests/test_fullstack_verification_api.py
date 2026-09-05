"""实际四仓配置通过公共 API 资格化，再由交付编译器冻结；没有执行 Agent 或远端写入。"""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_delivery_execution_snapshot import Compiler, ProviderBindings
from test_fullstack_verification import PROFILES, TEMPLATES, git

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator, DeliveryMethodSnapshot
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ProjectGitWorkspaces
from agent_team_os.modules.orchestration import (
    PipelineCatalog,
    PipelineCreate,
    SQLitePipelineRepository,
    WorkcellStageBinding,
)
from agent_team_os.modules.projects import ProjectCatalog, SQLiteProjectRepository
from agent_team_os.modules.workcells import (
    DeliveryExecutionSnapshotCompiler,
    ProjectWorkcellGovernance,
    SQLiteProjectWorkcellRepository,
    SQLiteTeamTemplateRepository,
    TeamTemplateCatalog,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.verification import VerificationQualificationV2
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


def _pipeline(database):
    catalog = PipelineCatalog(
        SQLitePipelineRepository(database),
        graph_compiler=Compiler(),
        provider_binding_resolver=ProviderBindings(),
    )
    slots = ("main", "delegate_1", "delegate_2", "delegate_3")
    mapping = {
        role: WorkcellStageBinding(
            workcell_key=role, slot_bindings={slot: f"{role}.{slot}" for slot in slots}
        )
        for role in PROFILES
    }
    pipeline = catalog.create_pipeline(
        PipelineCreate(
            id="actual-four-stack",
            name="四仓资格冻结专项",
            definition={
                "id": "actual-four-stack",
                "version": "1.0.0",
                "nodes": [
                    {
                        "kind": "stage",
                        "id": role,
                        "workflow_mode": "agentscope.workcell-team",
                        "bindings": {slot: "workcell.delegate" for slot in slots},
                        "output_validator": "workcell-result-v1",
                    }
                    for role in PROFILES
                ],
                "edges": [],
            },
            agent_assignments={
                site: "deployment-" + site.replace(".", "-")
                for stage in mapping.values()
                for site in stage.slot_bindings.values()
            },
            workcell_stage_map=mapping,
            release_contract_snapshot=tuple(PROFILES),
        ),
        created_by="admin",
    )
    validated = catalog.validate_draft(pipeline.draft.id, expected_version=pipeline.draft.version)
    revision = catalog.publish_draft(
        pipeline.draft.id, expected_version=validated.version, published_by="admin"
    )
    catalog.activate_revision(
        pipeline.pipeline.id,
        revision=revision.revision,
        expected_version=pipeline.pipeline.version,
        activated_by="admin",
    )
    return catalog


def _commit_and_push(workspace):
    git(workspace, "add", ".")
    git(
        workspace,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "commit",
        "-m",
        "Local qualification configuration",
    )
    git(workspace, "push", "origin", "main")


def test_actual_four_repository_profiles_freeze_from_api_and_refuse_configuration_drift(tmp_path):
    database = tmp_path / "api.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    project_repository = SQLiteProjectRepository(database)
    managed_git = ProjectGitWorkspaces(tmp_path / "managed")
    teams = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database))
    governance = ProjectWorkcellGovernance(
        SQLiteProjectWorkcellRepository(database),
        teams=teams,
        projects=project_repository,
        managed_git=managed_git,
    )
    projects = ProjectCatalog(project_repository, managed_git, team_governance=governance)
    pipelines = _pipeline(database)
    methods = DeliveryMethodSnapshot(
        snapshot_id="local-method-fixture",
        qualification_sha256="a" * 64,
        packages=(),
        method_entries={},
    )
    compiler = DeliveryExecutionSnapshotCompiler(
        governance=governance,
        projects=project_repository,
        pipelines=pipelines,
        method_snapshot=lambda: methods,
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    workspaces, bindings = {}, {}
    with TestClient(
        create_app(
            coordinator, projects=projects, team_templates=teams, project_workcells=governance
        )
    ) as client:
        created = client.post(
            "/v1/projects",
            json={
                "id": "actual-profiles",
                "name": "实际四仓资格",
                "default_pipeline_revision_id": "actual-four-stack:1",
                "team_template_revision_id": "software-delivery-team:1",
            },
        )
        assert created.status_code == 201, created.text
        for role, profile_id in PROFILES.items():
            uri = f"projects/actual-profiles/{role}"
            remote = managed_git.remote_uri(uri)
            workspace = tmp_path / ("source-" + role)
            git(tmp_path, "clone", remote, str(workspace))
            shutil.copytree(
                TEMPLATES / role,
                workspace,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("node_modules", "dist", "__pycache__", "*.pyc"),
            )
            marker = tmp_path / ("unexpected-code-" + role)
            (workspace / "sitecustomize.py").write_text(
                f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')\n"
            )
            _commit_and_push(workspace)
            created_binding = client.post(
                "/v1/projects/actual-profiles/workspace-bindings",
                json={
                    "workcell_key": role,
                    "kind": "git_repository_v1",
                    "adapter_type": "managed-bare-git",
                    "repository_uri": uri,
                },
            )
            assert created_binding.status_code == 201, created_binding.text
            binding = created_binding.json()["workspace_binding"]
            endpoint = f"/v1/workspace-bindings/{binding['id']}"
            verified = client.post(
                endpoint + "/verify", json={"expected_version": binding["version"]}
            )
            assert verified.status_code == 200, verified.text
            mismatch = client.put(
                endpoint + "/verification-profile",
                json={
                    "expected_version": verified.json()["version"],
                    "verification_profile_id": PROFILES["qa" if role != "qa" else "design"],
                },
            )
            assert mismatch.status_code == 409
            assert mismatch.json()["code"] == "WORKCELL_VERIFICATION_PROFILE_ROLE_MISMATCH"
            selected = client.put(
                endpoint + "/verification-profile",
                json={
                    "expected_version": verified.json()["version"],
                    "verification_profile_id": profile_id,
                },
            )
            assert selected.status_code == 200, selected.text
            qualified = client.post(
                endpoint + "/verification-profile/qualify",
                json={
                    "expected_version": selected.json()["version"],
                },
            )
            assert qualified.status_code == 200, qualified.text
            qualification = VerificationQualificationV2.model_validate(
                qualified.json()["verification_profile"]
            )
            assert qualification.profile.id == profile_id
            assert qualification.workspace_files
            assert qualification.dependencies
            assert not marker.exists()
            bindings[role], workspaces[role] = qualified.json(), workspace
        activated = client.post(
            "/v1/projects/actual-profiles/team-activate", json={"expected_version": 1}
        )
        assert activated.status_code == 200, activated.text
        snapshot = compiler.compile("actual-profiles", "actual-four-stack:1")
        assert tuple(item.verification_profile.profile.id for item in snapshot.workspaces) == tuple(
            PROFILES.values()
        )
        assert len({item.base_revision for item in snapshot.workspaces}) == 4
        assert (
            compiler.compile("actual-profiles", "actual-four-stack:1").snapshot_sha256
            == snapshot.snapshot_sha256
        )

        # main 内容变化先使 Git 资格失效；重新 Git Verify 后旧 Profile 资格仍须拒绝。
        config = workspaces["frontend"] / "tsconfig.json"
        config.write_text(config.read_text() + "\n")
        _commit_and_push(workspaces["frontend"])
        with pytest.raises(ProductError):
            compiler.compile("actual-profiles", "actual-four-stack:1")
        binding = bindings["frontend"]
        endpoint = f"/v1/workspace-bindings/{binding['id']}"
        verified = client.post(endpoint + "/verify", json={"expected_version": binding["version"]})
        assert verified.status_code == 200, verified.text
        with pytest.raises(ProductError):
            compiler.compile("actual-profiles", "actual-four-stack:1")
        qualified = client.post(
            endpoint + "/verification-profile/qualify",
            json={
                "expected_version": verified.json()["version"],
            },
        )
        assert qualified.status_code == 200, qualified.text
        fresh = compiler.compile("actual-profiles", "actual-four-stack:1")
        assert fresh.snapshot_sha256 != snapshot.snapshot_sha256
        assert (
            fresh.workspaces[1].verification_profile != snapshot.workspaces[1].verification_profile
        )
