from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ProjectGitWorkspaces
from agent_team_os.modules.projects import ProjectCatalog, SQLiteProjectRepository
from agent_team_os.modules.workcells import (
    ProjectWorkcellGovernance,
    SQLiteProjectWorkcellRepository,
    SQLiteTeamTemplateRepository,
    TeamTemplateCatalog,
)
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


def test_project_team_onboarding_requires_four_independent_verified_workspaces(
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
    projects = ProjectCatalog(
        project_repository,
        managed_git,
        team_governance=governance,
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
    )

    with TestClient(
        create_app(
            coordinator,
            projects=projects,
            team_templates=teams,
            project_workcells=governance,
        )
    ) as client:
        created = client.post(
            "/v1/projects",
            json={
                "id": "workcell-onboarding",
                "name": "四仓项目",
                "default_pipeline_revision_id": "fullstack-product-delivery:1",
                "team_template_revision_id": "software-delivery-team:1",
            },
        )
        assert created.status_code == 201
        assert created.json()["project"]["lifecycle_status"] == "provisioning"
        assert created.json()["repositories"] == []

        topology = client.get("/v1/projects/workcell-onboarding/workcells")
        assert topology.status_code == 200
        assert topology.json()["team_binding"]["status"] == "provisioning"
        assert topology.json()["workspace_bindings"] == []
        profiles = client.get(
            "/v1/verification-profiles", params={"project_id": "workcell-onboarding"}
        )
        assert profiles.status_code == 200
        assert {profile["id"] for profile in profiles.json()} == {
            "python-unittest-v1",
            "node-native-test-v1",
        }

        workspace_ids: list[str] = []
        for workcell_key in ("design", "frontend", "backend", "qa"):
            bound = client.post(
                "/v1/projects/workcell-onboarding/workspace-bindings",
                json={
                    "workcell_key": workcell_key,
                    "kind": "git_repository_v1",
                    "adapter_type": "managed-bare-git",
                    "repository_uri": f"projects/workcell-onboarding/{workcell_key}",
                },
            )
            assert bound.status_code == 201
            workspace = bound.json()["workspace_binding"]
            assert workspace["status"] == "pending"
            workspace_ids.append(workspace["id"])

            verified = client.post(
                f"/v1/workspace-bindings/{workspace['id']}/verify",
                json={"expected_version": workspace["version"]},
            )
            assert verified.status_code == 200
            assert verified.json()["status"] == "ready"
            assert len(verified.json()["verification_sha256"]) == 64
            selected = client.put(
                f"/v1/workspace-bindings/{workspace['id']}/verification-profile",
                json={
                    "expected_version": verified.json()["version"],
                    "verification_profile_id": "python-unittest-v1",
                },
            )
            assert selected.status_code == 200
            qualified = client.post(
                f"/v1/workspace-bindings/{workspace['id']}/verification-profile/qualify",
                json={"expected_version": selected.json()["version"]},
            )
            assert qualified.status_code == 200
            assert qualified.json()["verification_profile"]["profile"]["id"] == "python-unittest-v1"

        activated = client.post(
            "/v1/projects/workcell-onboarding/team-activate",
            json={"expected_version": topology.json()["team_binding"]["version"]},
        )
        assert activated.status_code == 200
        assert activated.json()["team_binding"]["status"] == "active"
        assert activated.json()["project_status"] == "active"
        assert {
            item["workcell_key"]: item["workspace_binding_id"]
            for item in activated.json()["workcell_bindings"]
        } == dict(zip(("design", "frontend", "backend", "qa"), workspace_ids, strict=True))
        assert len({item["repository_uri"] for item in activated.json()["workspace_bindings"]}) == 4
        first = activated.json()["workspace_bindings"][0]
        changed = client.put(
            f"/v1/workspace-bindings/{first['id']}/verification-profile",
            json={
                "expected_version": first["version"],
                "verification_profile_id": "node-native-test-v1",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["verification_profile"] is None
        stale = client.put(
            f"/v1/workspace-bindings/{first['id']}/verification-profile",
            json={
                "expected_version": first["version"],
                "verification_profile_id": "python-unittest-v1",
            },
        )
        assert stale.status_code == 409
        project_repository.acquire_lease("workcell-onboarding", "delivery-active")
        blocked = client.put(
            f"/v1/workspace-bindings/{first['id']}/verification-profile",
            json={
                "expected_version": changed.json()["version"],
                "verification_profile_id": "python-unittest-v1",
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "WORKCELL_VERIFICATION_PROFILE_DELIVERY_ACTIVE"


def test_team_activation_fails_closed_when_a_required_workspace_is_unverified(
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

    from agent_team_os.modules.projects import ProjectCreate

    projects.create(
        ProjectCreate(
            id="incomplete-team",
            name="不完整四仓",
            default_pipeline_revision_id="fullstack-product-delivery:1",
            team_template_revision_id="software-delivery-team:1",
        ),
        "admin",
    )
    governance.create_workspace_binding(
        "incomplete-team",
        {
            "workcell_key": "design",
            "kind": "git_repository_v1",
            "adapter_type": "managed-bare-git",
            "repository_uri": "projects/incomplete-team/design",
        },
    )

    try:
        governance.activate("incomplete-team", expected_version=1)
    except Exception as error:
        assert getattr(error, "code", None) == "PROJECT_WORKCELL_BINDINGS_INCOMPLETE"
    else:  # pragma: no cover - explicit fail-closed assertion
        raise AssertionError("activation unexpectedly succeeded")
