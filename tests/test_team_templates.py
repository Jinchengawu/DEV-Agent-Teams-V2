from pathlib import Path

from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.workcells import (
    SQLiteTeamTemplateRepository,
    TeamTemplateCatalog,
    ensure_builtin_software_delivery_team,
)
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


def test_builtin_software_delivery_team_is_idempotent_and_has_four_workcells(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    catalog = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database))

    first = ensure_builtin_software_delivery_team(catalog)
    second = ensure_builtin_software_delivery_team(catalog)

    assert first == second
    assert first.revision_id == "software-delivery-team:1"
    assert [item.workcell_key for item in first.workcells] == [
        "design",
        "frontend",
        "backend",
        "qa",
    ]
    assert all(item.primary_workspace.kind == "git_repository_v1" for item in first.workcells)
    assert first.topology.links


def test_team_template_draft_validates_and_publishes_immutable_organization_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    catalog = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database))
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
    )

    with TestClient(create_app(coordinator, team_templates=catalog)) as client:
        created = client.post(
            "/v1/team-templates",
            json={
                "id": "custom-software-team",
                "name": "四仓软件交付团队",
                "description": "组织职责与委派上限，不定义 Stage 顺序。",
                "workcells": [
                    {
                        "workcell_key": key,
                        "name": name,
                        "responsibility": responsibility,
                        "primary_workspace": {"kind": "git_repository_v1"},
                        "delegate_purposes": ["workspace_write", "review"],
                        "delegation_policy": {
                            "max_children": 3,
                            "max_concurrency": 2,
                            "max_writers": 1,
                            "max_depth": 1,
                            "wall_clock_budget_seconds": 900,
                        },
                    }
                    for key, name, responsibility in (
                        ("design", "Design", "设计契约"),
                        ("frontend", "Frontend", "前端实现"),
                        ("backend", "Backend", "后端实现"),
                        ("qa", "QA", "测试交付"),
                    )
                ],
                "topology": {
                    "nodes": [
                        {"workcell_key": key, "x": index * 240, "y": 120}
                        for index, key in enumerate(
                            ("design", "frontend", "backend", "qa")
                        )
                    ],
                    "links": [
                        {
                            "source_workcell_key": "design",
                            "target_workcell_key": "frontend",
                            "label": "artifact",
                        },
                        {
                            "source_workcell_key": "design",
                            "target_workcell_key": "backend",
                            "label": "artifact",
                        },
                    ],
                },
            },
        )
        assert created.status_code == 201
        draft = created.json()["draft"]

        validated = client.post(
            f"/v1/team-template-drafts/{draft['id']}/validate",
            json={"expected_version": draft["version"]},
        )
        assert validated.status_code == 200
        assert validated.json()["validation_status"] == "valid"

        published = client.post(
            f"/v1/team-template-drafts/{draft['id']}/publish",
            json={"expected_version": validated.json()["version"]},
        )
        assert published.status_code == 201
        revision = published.json()
        assert revision["revision"] == 1
        assert len(revision["sha256"]) == 64
        assert [item["workcell_key"] for item in revision["workcells"]] == [
            "design",
            "frontend",
            "backend",
            "qa",
        ]

        fetched = client.get(
            "/v1/team-templates/custom-software-team/revisions/1"
        )
        assert fetched.status_code == 200
        assert fetched.json() == revision
