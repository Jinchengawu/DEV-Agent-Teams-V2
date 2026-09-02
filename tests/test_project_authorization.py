from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient

from agent_team_os.api import create_app
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
    UserCreate,
    UserPatch,
)
from agent_team_os.modules.knowledge import (
    KnowledgeAuthorizationResolver,
    SQLiteTenantKnowledgeRepository,
    TenantConnection,
    TenantKnowledgeManager,
    TenantProviderBinding,
)
from agent_team_os.modules.projects import (
    ProjectCatalog,
    ProjectCreate,
    ProjectKnowledgeSourceApprovalUpdate,
    ProjectMembershipUpdate,
    SQLiteProjectRepository,
)
from agent_team_os.shared.permissions import Role
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService

ORIGIN = "http://test"
ADMIN_PASSWORD = "secure-admin-2026"
VIEWER_PASSWORD = "secure-viewer-2026"
EDITOR_PASSWORD = "secure-editor-2026"


class DeterministicWorkspaceProvisioner:
    def provision(self, repository_ref: str) -> str:
        return f"seed:{repository_ref}"

    def reset(self, repository_ref: str) -> str:
        return f"reset:{repository_ref}"

    def revision(self, repository_ref: str) -> str:
        return f"head:{repository_ref}"


def _app(database: Path):  # type: ignore[no-untyped-def]
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    projects = ProjectCatalog(
        SQLiteProjectRepository(database),
        DeterministicWorkspaceProvisioner(),
    )
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(),
        executor=DeterministicCodeExecutor(),
        resolved_journey_sha256="a" * 64,
    )
    return create_app(coordinator, identity=identity, projects=projects)


async def _login(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_project_creator_is_persisted_as_the_initial_owner(tmp_path: Path) -> None:
    app = _app(tmp_path / "project-auth.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await _login(client)
        created = await client.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "alpha-project",
                "name": "Alpha Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        assert created.status_code == 201

        memberships = await client.get("/v1/projects/alpha-project/memberships")

    assert memberships.status_code == 200
    assert memberships.json() == [
        {
            "project_id": "alpha-project",
            "user_id": created.json()["project"]["created_by"],
            "role": "owner",
            "version": 1,
        }
    ]


@pytest.mark.anyio
async def test_project_owner_can_grant_a_versioned_membership(tmp_path: Path) -> None:
    app = _app(tmp_path / "project-auth.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await _login(client)
        viewer = await client.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "project-viewer",
                "display_name": "Project Viewer",
                "role": "viewer",
                "password": VIEWER_PASSWORD,
            },
        )
        assert viewer.status_code == 201
        created = await client.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "alpha-project",
                "name": "Alpha Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        assert created.status_code == 201

        granted = await client.put(
            f"/v1/projects/alpha-project/memberships/{viewer.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={"role": "viewer"},
        )

    assert granted.status_code == 200
    assert granted.json() == {
        "project_id": "alpha-project",
        "user_id": viewer.json()["id"],
        "role": "viewer",
        "version": 1,
    }


@pytest.mark.anyio
async def test_project_visibility_is_limited_to_membership(tmp_path: Path) -> None:
    app = _app(tmp_path / "project-auth.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        viewer = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "project-viewer",
                "display_name": "Project Viewer",
                "role": "viewer",
                "password": VIEWER_PASSWORD,
            },
        )
        assert viewer.status_code == 201
        for project_id in ("visible-project", "hidden-project"):
            response = await admin.post(
                "/v1/projects",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={
                    "id": project_id,
                    "name": project_id,
                    "default_pipeline_revision_id": "backend-delivery:1",
                },
            )
            assert response.status_code == 201
        granted = await admin.put(
            f"/v1/projects/visible-project/memberships/{viewer.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "viewer"},
        )
        assert granted.status_code == 200

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as viewer_client:
        login = await viewer_client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "project-viewer", "password": VIEWER_PASSWORD},
        )
        assert login.status_code == 200
        projects = await viewer_client.get("/v1/projects")
        visible = await viewer_client.get("/v1/projects/visible-project")
        hidden = await viewer_client.get("/v1/projects/hidden-project")

    assert [project["id"] for project in projects.json()] == ["visible-project"]
    assert visible.status_code == 200
    assert hidden.status_code == 403
    assert hidden.json()["code"] == "PROJECT_ACCESS_DENIED"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "resource_path",
    (
        "repositories",
        "pipeline-bindings",
        "deployment-access",
        "knowledge-sources",
        "knowledge-source-approvals",
        "memberships",
    ),
)
async def test_direct_project_child_resource_cannot_bypass_membership(
    tmp_path: Path,
    resource_path: str,
) -> None:
    app = _app(tmp_path / f"project-child-{resource_path}.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        viewer = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "child-resource-viewer",
                "display_name": "Child Resource Viewer",
                "role": "viewer",
                "password": VIEWER_PASSWORD,
            },
        )
        assert viewer.status_code == 201
        created = await admin.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "hidden-child-project",
                "name": "Hidden Child Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        assert created.status_code == 201

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as viewer_client:
        login = await viewer_client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "child-resource-viewer", "password": VIEWER_PASSWORD},
        )
        assert login.status_code == 200
        response = await viewer_client.get(f"/v1/projects/hidden-child-project/{resource_path}")

    assert response.status_code == 403
    assert response.json()["code"] == "PROJECT_ACCESS_DENIED"


@pytest.mark.anyio
async def test_global_editor_with_owner_membership_can_manage_members(tmp_path: Path) -> None:
    app = _app(tmp_path / "project-auth.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        created_users: dict[str, dict[str, object]] = {}
        for username, role, password in (
            ("project-owner", "editor", EDITOR_PASSWORD),
            ("project-viewer", "viewer", VIEWER_PASSWORD),
        ):
            response = await admin.post(
                "/v1/users",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={
                    "username": username,
                    "display_name": username,
                    "role": role,
                    "password": password,
                },
            )
            assert response.status_code == 201
            created_users[username] = response.json()
        project = await admin.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "owner-project",
                "name": "Owner Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        assert project.status_code == 201
        owner_grant = await admin.put(
            f"/v1/projects/owner-project/memberships/{created_users['project-owner']['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "owner"},
        )
        assert owner_grant.status_code == 200

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as owner:
        login = await owner.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "project-owner", "password": EDITOR_PASSWORD},
        )
        assert login.status_code == 200
        grant = await owner.put(
            f"/v1/projects/owner-project/memberships/{created_users['project-viewer']['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": owner.cookies["agent_team_os_csrf"],
            },
            json={"role": "viewer"},
        )

    assert grant.status_code == 200
    assert grant.json()["role"] == "viewer"


@pytest.mark.anyio
async def test_last_project_owner_cannot_be_demoted(tmp_path: Path) -> None:
    app = _app(tmp_path / "project-auth.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await _login(client)
        project = await client.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "last-owner-project",
                "name": "Last Owner Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        owner_id = project.json()["project"]["created_by"]
        demoted = await client.put(
            f"/v1/projects/last-owner-project/memberships/{owner_id}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["agent_team_os_csrf"],
            },
            json={"role": "viewer", "expected_version": 1},
        )

    assert demoted.status_code == 409
    assert demoted.json()["code"] == "PROJECT_LAST_OWNER_REQUIRED"


@pytest.mark.anyio
async def test_membership_revocation_immediately_removes_project_access(tmp_path: Path) -> None:
    app = _app(tmp_path / "project-auth.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        viewer = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "revoked-viewer",
                "display_name": "Revoked Viewer",
                "role": "viewer",
                "password": VIEWER_PASSWORD,
            },
        )
        project = await admin.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "revoked-project",
                "name": "Revoked Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        assert project.status_code == 201
        granted = await admin.put(
            f"/v1/projects/revoked-project/memberships/{viewer.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "viewer"},
        )
        assert granted.status_code == 200
        revoked = await admin.request(
            "DELETE",
            f"/v1/projects/revoked-project/memberships/{viewer.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"expected_version": 1},
        )
        assert revoked.status_code == 204

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as viewer_client:
        await viewer_client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "revoked-viewer", "password": VIEWER_PASSWORD},
        )
        denied = await viewer_client.get("/v1/projects/revoked-project")

    assert denied.status_code == 403
    assert denied.json()["code"] == "PROJECT_ACCESS_DENIED"


@pytest.mark.anyio
async def test_administrator_membership_bypass_is_auditable(tmp_path: Path) -> None:
    app = _app(tmp_path / "project-auth.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        await _login(client)

        legacy = await client.get("/v1/projects/legacy-default")
        audits = await client.get("/v1/projects/legacy-default/access-audits")

    assert legacy.status_code == 200
    assert audits.status_code == 200
    assert any(
        audit["capability"] == "project:read"
        and audit["resource"] == "project:legacy-default"
        and audit["reason"] == "read project detail"
        for audit in audits.json()
    )


@pytest.mark.anyio
async def test_administrator_project_role_bypass_is_auditable(tmp_path: Path) -> None:
    app = _app(tmp_path / "admin-role-bypass.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        owner = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "bypass-owner",
                "display_name": "Bypass Owner",
                "role": "editor",
                "password": EDITOR_PASSWORD,
            },
        )
        viewer = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "bypass-viewer",
                "display_name": "Bypass Viewer",
                "role": "viewer",
                "password": VIEWER_PASSWORD,
            },
        )
        project = await admin.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "admin-bypass-project",
                "name": "Admin Bypass Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        admin_id = project.json()["project"]["created_by"]
        assert (
            await admin.put(
                f"/v1/projects/admin-bypass-project/memberships/{owner.json()['id']}",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={"role": "owner"},
            )
        ).status_code == 200
        assert (
            await admin.put(
                f"/v1/projects/admin-bypass-project/memberships/{admin_id}",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={"role": "viewer", "expected_version": 1},
            )
        ).status_code == 200

        bypassed = await admin.put(
            f"/v1/projects/admin-bypass-project/memberships/{viewer.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "viewer"},
        )
        audits = await admin.get("/v1/projects/admin-bypass-project/access-audits")

    assert bypassed.status_code == 200
    assert audits.status_code == 200
    assert any(
        audit["capability"] == "project:membership-manage"
        and audit["resource"].endswith(viewer.json()["id"])
        for audit in audits.json()
    )


@pytest.mark.anyio
async def test_delivery_creation_requires_global_and_project_capability(tmp_path: Path) -> None:
    app = _app(tmp_path / "project-auth.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        editor = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "delivery-editor",
                "display_name": "Delivery Editor",
                "role": "editor",
                "password": EDITOR_PASSWORD,
            },
        )
        project = await admin.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "delivery-project",
                "name": "Delivery Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        assert project.status_code == 201

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as editor_client:
        await editor_client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "delivery-editor", "password": EDITOR_PASSWORD},
        )
        denied = await editor_client.post(
            "/v1/deliveries",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": editor_client.cookies["agent_team_os_csrf"],
            },
            json={"project_id": "delivery-project", "user_request": "受控交付"},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "PROJECT_ACCESS_DENIED"

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        granted = await admin.put(
            f"/v1/projects/delivery-project/memberships/{editor.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "editor"},
        )
        assert granted.status_code == 200

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as editor_client:
        await editor_client.post(
            "/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "delivery-editor", "password": EDITOR_PASSWORD},
        )
        accepted = await editor_client.post(
            "/v1/deliveries",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": editor_client.cookies["agent_team_os_csrf"],
            },
            json={"project_id": "delivery-project", "user_request": "受控交付"},
        )

    assert accepted.status_code == 202


@pytest.mark.anyio
async def test_delivery_direct_and_list_access_are_project_scoped(tmp_path: Path) -> None:
    app = _app(tmp_path / "delivery-resource-access.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        viewer = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "delivery-resource-viewer",
                "display_name": "Delivery Resource Viewer",
                "role": "viewer",
                "password": VIEWER_PASSWORD,
            },
        )
        assert viewer.status_code == 201
        for project_id in ("visible-delivery-project", "hidden-delivery-project"):
            assert (
                await admin.post(
                    "/v1/projects",
                    headers={
                        "Origin": ORIGIN,
                        "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                    },
                    json={
                        "id": project_id,
                        "name": project_id,
                        "default_pipeline_revision_id": "backend-delivery:1",
                    },
                )
            ).status_code == 201
        assert (
            await admin.put(
                f"/v1/projects/visible-delivery-project/memberships/{viewer.json()['id']}",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={"role": "viewer"},
            )
        ).status_code == 200
        created = await admin.post(
            "/v1/deliveries",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "project_id": "hidden-delivery-project",
                "user_request": "hidden delivery",
            },
        )
        assert created.status_code == 202

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as viewer_client:
        assert (
            await viewer_client.post(
                "/v1/auth/login",
                headers={"Origin": ORIGIN},
                json={
                    "username": "delivery-resource-viewer",
                    "password": VIEWER_PASSWORD,
                },
            )
        ).status_code == 200
        direct = await viewer_client.get(f"/v1/deliveries/{created.json()['id']}")
        listing = await viewer_client.get("/v1/deliveries")
        hidden_listing = await viewer_client.get(
            "/v1/deliveries", params={"project_id": "hidden-delivery-project"}
        )

    assert direct.status_code == 403
    assert direct.json()["code"] == "PROJECT_ACCESS_DENIED"
    assert created.json()["id"] not in {item["id"] for item in listing.json()}
    assert hidden_listing.status_code == 403
    assert hidden_listing.json()["code"] == "PROJECT_ACCESS_DENIED"


def test_unrelated_membership_change_does_not_revoke_existing_knowledge_epoch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge-epoch.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    administrator = identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    unrelated = identity.create_user(
        administrator,
        UserCreate(
            username="unrelated-viewer",
            display_name="Unrelated Viewer",
            role=Role.VIEWER,
            password=VIEWER_PASSWORD,
        ),
    )
    projects = ProjectCatalog(
        SQLiteProjectRepository(database),
        DeterministicWorkspaceProvisioner(),
    )
    projects.create(
        ProjectCreate(
            id="epoch-project",
            name="Epoch Project",
            default_pipeline_revision_id="backend-delivery:1",
        ),
        administrator.id,
    )
    resolver = KnowledgeAuthorizationResolver(
        identity=identity,
        projects=projects,
        tenant=TenantKnowledgeManager(SQLiteTenantKnowledgeRepository(database)),
    )
    access = resolver.initial_access_component(
        project_id="epoch-project",
        principal_id=administrator.id,
        bypass_receipt_id=None,
    )
    before = resolver.resolve(
        project_id="epoch-project",
        principal_id=administrator.id,
        frozen_access_component=access.model_dump(mode="json"),
    )

    projects.put_membership(
        "epoch-project",
        unrelated.id,
        ProjectMembershipUpdate(role="viewer"),
    )
    after = resolver.resolve(
        project_id="epoch-project",
        principal_id=administrator.id,
        frozen_access_component=access.model_dump(mode="json"),
    )

    assert after.authorization_epoch_hash == before.authorization_epoch_hash


def test_identity_profile_edit_does_not_revoke_existing_knowledge_epoch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "identity-auth-epoch.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    administrator = identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    editor = identity.create_user(
        administrator,
        UserCreate(
            username="epoch-editor",
            display_name="Epoch Editor",
            role=Role.EDITOR,
            password=EDITOR_PASSWORD,
        ),
    )
    projects = ProjectCatalog(
        SQLiteProjectRepository(database),
        DeterministicWorkspaceProvisioner(),
    )
    projects.create(
        ProjectCreate(
            id="identity-epoch-project",
            name="Identity Epoch Project",
            default_pipeline_revision_id="backend-delivery:1",
        ),
        editor.id,
    )
    resolver = KnowledgeAuthorizationResolver(
        identity=identity,
        projects=projects,
        tenant=TenantKnowledgeManager(SQLiteTenantKnowledgeRepository(database)),
    )
    access = resolver.initial_access_component(
        project_id="identity-epoch-project",
        principal_id=editor.id,
        bypass_receipt_id=None,
    )
    before = resolver.resolve(
        project_id="identity-epoch-project",
        principal_id=editor.id,
        frozen_access_component=access.model_dump(mode="json"),
    )

    updated = identity.patch_user(
        administrator,
        editor.id,
        UserPatch(expected_version=editor.version, display_name="Renamed Editor"),
    )
    after = resolver.resolve(
        project_id="identity-epoch-project",
        principal_id=editor.id,
        frozen_access_component=access.model_dump(mode="json"),
    )

    assert updated.version == editor.version + 1
    assert after.identity_authorization_version == before.identity_authorization_version
    assert after.authorization_epoch_hash == before.authorization_epoch_hash


def test_new_content_and_unrelated_approval_do_not_revoke_frozen_knowledge_scope(
    tmp_path: Path,
) -> None:
    database = tmp_path / "frozen-knowledge-scope.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    administrator = identity.bootstrap(BootstrapRequest(password=ADMIN_PASSWORD))
    projects = ProjectCatalog(
        SQLiteProjectRepository(database),
        DeterministicWorkspaceProvisioner(),
    )
    projects.create(
        ProjectCreate(
            id="frozen-scope-project",
            name="Frozen Scope Project",
            default_pipeline_revision_id="backend-delivery:1",
        ),
        administrator.id,
    )
    now = administrator.created_at
    connection = TenantConnection(
        id="connection-one",
        provider_kind="feishu",
        display_name="Connection One",
        app_id_ref="env:FEISHU_APP_ID",
        app_secret_ref="env:FEISHU_APP_SECRET",
        status="ready",
        authorization_version=1,
        version=1,
        created_by=administrator.id,
        created_at=now,
        updated_at=now,
        last_diagnosed_at=now,
    )
    bindings = {
        binding_id: TenantProviderBinding(
            id=binding_id,
            connection_id=connection.id,
            display_name=binding_id,
            external_space_id=f"space-{binding_id}",
            root_node_token=f"root-{binding_id}",
            status="ready",
            authorization_version=1,
            version=1,
            created_by=administrator.id,
            created_at=now,
            updated_at=now,
            last_permission_probe_at=now,
        )
        for binding_id in ("binding-one", "binding-two")
    }

    class FakeTenant:
        def __init__(self) -> None:
            self.repository = SQLiteTenantKnowledgeRepository(database)
            self.source_ids = {"binding-one": ("docx:one",), "binding-two": ("docx:two",)}

        def available_source_ids(self, binding_id: str) -> tuple[str, ...]:
            return self.source_ids[binding_id]

    fake_tenant = FakeTenant()
    assert fake_tenant.repository.create_connection(connection) is not None
    for binding in bindings.values():
        assert fake_tenant.repository.create_binding(binding, ()) is not None
    projects.configure_knowledge_binding_validator(lambda binding_id: bindings[binding_id])
    first = projects.put_knowledge_source_approval(
        "frozen-scope-project",
        "binding-one",
        ProjectKnowledgeSourceApprovalUpdate(enabled=True, rag_enabled=True),
        administrator.id,
    )
    resolver = KnowledgeAuthorizationResolver(
        identity=identity,
        projects=projects,
        tenant=cast(TenantKnowledgeManager, fake_tenant),
    )
    access = resolver.initial_access_component(
        project_id="frozen-scope-project",
        principal_id=administrator.id,
        bypass_receipt_id=None,
    )
    before = resolver.resolve(
        project_id="frozen-scope-project",
        principal_id=administrator.id,
        frozen_access_component=access.model_dump(mode="json"),
        frozen_approval_ids=(first.id,),
    )

    fake_tenant.source_ids["binding-one"] = ("docx:one", "docx:new-revision")
    projects.put_knowledge_source_approval(
        "frozen-scope-project",
        "binding-two",
        ProjectKnowledgeSourceApprovalUpdate(enabled=True, rag_enabled=True),
        administrator.id,
    )
    after = resolver.resolve(
        project_id="frozen-scope-project",
        principal_id=administrator.id,
        frozen_access_component=access.model_dump(mode="json"),
        frozen_approval_ids=(first.id,),
    )

    assert after.approvals == before.approvals
    assert after.authorization_epoch_hash == before.authorization_epoch_hash


@pytest.mark.anyio
async def test_identity_cannot_disable_the_last_effective_project_owner(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path / "last-effective-owner.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        owner = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "sole-project-owner",
                "display_name": "Sole Project Owner",
                "role": "editor",
                "password": EDITOR_PASSWORD,
            },
        )
        project = await admin.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "identity-guard-project",
                "name": "Identity Guard Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        admin_id = project.json()["project"]["created_by"]
        granted = await admin.put(
            f"/v1/projects/identity-guard-project/memberships/{owner.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "owner"},
        )
        assert granted.status_code == 200
        demoted = await admin.put(
            f"/v1/projects/identity-guard-project/memberships/{admin_id}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "viewer", "expected_version": 1},
        )
        assert demoted.status_code == 200

        disabled = await admin.patch(
            f"/v1/users/{owner.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"enabled": False, "expected_version": 1},
        )

    assert disabled.status_code == 409
    assert disabled.json()["code"] == "PROJECT_LAST_OWNER_REQUIRED"


@pytest.mark.anyio
async def test_identity_cannot_downgrade_the_last_effective_project_owner(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path / "last-effective-owner-role.sqlite")
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        owner = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": "role-guard-owner",
                "display_name": "Role Guard Owner",
                "role": "editor",
                "password": EDITOR_PASSWORD,
            },
        )
        project = await admin.post(
            "/v1/projects",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "id": "identity-role-guard-project",
                "name": "Identity Role Guard Project",
                "default_pipeline_revision_id": "backend-delivery:1",
            },
        )
        admin_id = project.json()["project"]["created_by"]
        assert (
            await admin.put(
                f"/v1/projects/identity-role-guard-project/memberships/{owner.json()['id']}",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={"role": "owner"},
            )
        ).status_code == 200
        assert (
            await admin.put(
                f"/v1/projects/identity-role-guard-project/memberships/{admin_id}",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={"role": "viewer", "expected_version": 1},
            )
        ).status_code == 200

        downgraded = await admin.patch(
            f"/v1/users/{owner.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "viewer", "expected_version": 1},
        )

    assert downgraded.status_code == 409
    assert downgraded.json()["code"] == "PROJECT_LAST_OWNER_REQUIRED"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target_role", "disable_first", "expected_code"),
    (
        ("viewer", False, "PROJECT_OWNER_NOT_ELIGIBLE"),
        ("editor", True, "PROJECT_MEMBER_USER_DISABLED"),
    ),
)
async def test_project_owner_membership_requires_an_effective_user(
    tmp_path: Path,
    target_role: str,
    disable_first: bool,
    expected_code: str,
) -> None:
    app = _app(tmp_path / f"owner-eligibility-{expected_code}.sqlite")
    password = VIEWER_PASSWORD if target_role == "viewer" else EDITOR_PASSWORD
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as admin:
        await _login(admin)
        target = await admin.post(
            "/v1/users",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={
                "username": f"ineligible-{target_role}",
                "display_name": "Ineligible Owner",
                "role": target_role,
                "password": password,
            },
        )
        assert target.status_code == 201
        if disable_first:
            disabled = await admin.patch(
                f"/v1/users/{target.json()['id']}",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={"enabled": False, "expected_version": 1},
            )
            assert disabled.status_code == 200
        assert (
            await admin.post(
                "/v1/projects",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
                },
                json={
                    "id": "owner-eligibility-project",
                    "name": "Owner Eligibility Project",
                    "default_pipeline_revision_id": "backend-delivery:1",
                },
            )
        ).status_code == 201

        granted = await admin.put(
            f"/v1/projects/owner-eligibility-project/memberships/{target.json()['id']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": admin.cookies["agent_team_os_csrf"],
            },
            json={"role": "owner"},
        )

    assert granted.status_code == 409
    assert granted.json()["code"] == expected_code
