from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from agent_team_os.delivery import (
    DeliveryRun,
    ProjectExecutionSnapshot,
    SQLiteDeliveryRepository,
)
from agent_team_os.git_sandbox import SandboxPolicy
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ProjectGitWorkspaces
from agent_team_os.modules.projects import (
    Project,
    ProjectBindingUpdate,
    ProjectCatalog,
    ProjectCreate,
    ProjectDeploymentUpdate,
    ProjectKnowledgeSourceUpdate,
    ProjectLeaseDeliveryRepository,
    ProjectWorkspace,
    SQLiteProjectRepository,
)
from agent_team_os.shared.errors import ProductError

ROOT = Path(__file__).parents[1]


class Provisioner:
    def __init__(self) -> None:
        self.fail = False
        self.fail_refs: set[str] = set()
        self.revisions: dict[str, str] = {}

    def provision(self, repository_ref: str) -> str:
        if self.fail or repository_ref in self.fail_refs:
            raise RuntimeError("git unavailable")
        return self.revisions.setdefault(repository_ref, "a" * 40)

    def reset(self, repository_ref: str) -> str:
        return self.revisions[repository_ref]

    def revision(self, repository_ref: str) -> str:
        return self.revisions[repository_ref]


def catalog(tmp_path: Path) -> tuple[ProjectCatalog, Provisioner]:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    provisioner = Provisioner()
    return ProjectCatalog(SQLiteProjectRepository(database), provisioner), provisioner


def test_project_provisions_workspace_and_freezes_execution_context(tmp_path: Path) -> None:
    projects, _ = catalog(tmp_path)
    created = projects.create(
        ProjectCreate(
            id="pj1",
            name="项目一",
            default_pipeline_revision_id="backend-delivery:3",
            deployment_ids=("pm", "backend"),
        ),
        "admin",
    )
    assert created.project.lifecycle_status == "active"
    assert created.workspace.workspace_id == "project:pj1"
    context = projects.prepare_delivery("pj1", "delivery-1", None)
    assert context.pipeline_revision_id == "backend-delivery:3"
    assert context.deployment_ids == ("backend", "pm")
    delivery_repository = ProjectLeaseDeliveryRepository(
        SQLiteDeliveryRepository(projects.repository.database), projects
    )
    first = DeliveryRun(
        id="delivery-1",
        project_id="pj1",
        workspace_id=context.workspace_id,
        project_execution_snapshot=ProjectExecutionSnapshot(**context.model_dump()),
        user_request="测试项目租约",
        status="queued",
        version=1,
        resolved_journey_sha256="a" * 64,
        evidence_identity="deterministic-test",
        planning_identity="deterministic-test",
    )
    delivery_repository.save(first)
    second_context = projects.prepare_delivery("pj1", "delivery-2", None)
    second = first.model_copy(
        update={
            "id": "delivery-2",
            "project_execution_snapshot": ProjectExecutionSnapshot(**second_context.model_dump()),
        }
    )
    with pytest.raises(ProductError) as active_conflict:
        delivery_repository.save(second)
    assert active_conflict.value.code == "PROJECT_ACTIVE_DELIVERY_CONFLICT"
    delivery_repository.save(
        first.model_copy(update={"status": "failed", "version": first.version + 1})
    )
    delivery_repository.save(second)


def test_project_provisions_fullstack_repository_set_and_freezes_it(tmp_path: Path) -> None:
    projects, provisioner = catalog(tmp_path)
    projects.create(
        ProjectCreate(
            id="pj-fullstack",
            name="全栈项目",
            default_pipeline_revision_id="fullstack-delivery:1",
            repository_mode="fullstack",
        ),
        "admin",
    )

    upgraded = projects.get("pj-fullstack")

    assert tuple(repository.role for repository in upgraded.repositories) == (
        "backend",
        "design",
        "frontend",
        "qa",
    )
    assert all(repository.status == "ready" for repository in upgraded.repositories)
    assert len({repository.workspace_ref for repository in upgraded.repositories}) == 4
    assert len({repository.repository_ref for repository in upgraded.repositories}) == 4

    provisioner.revisions["projects/pj-fullstack/frontend"] = "f" * 40
    context = projects.prepare_delivery("pj-fullstack", "delivery-fullstack", None)
    assert tuple(repository.role for repository in context.repositories) == (
        "backend",
        "design",
        "frontend",
        "qa",
    )
    assert len(context.repository_set_sha256) == 64
    assert (
        next(
            repository.seed_revision
            for repository in context.repositories
            if repository.role == "frontend"
        )
        == "f" * 40
    )
    assert (
        projects.prepare_delivery(
            "pj-fullstack", "delivery-fullstack-2", None
        ).repository_set_sha256
        == context.repository_set_sha256
    )


def test_fullstack_delivery_is_blocked_until_every_repository_is_ready(
    tmp_path: Path,
) -> None:
    projects, provisioner = catalog(tmp_path)
    provisioner.fail_refs.add("projects/pj-incomplete/frontend")
    created = projects.create(
        ProjectCreate(
            id="pj-incomplete",
            name="仓库不完整项目",
            default_pipeline_revision_id="fullstack-product-delivery:1",
            repository_mode="fullstack",
        ),
        "admin",
    )

    assert created.project.lifecycle_status == "active"
    assert next(item for item in created.repositories if item.role == "frontend").status == "failed"
    with pytest.raises(ProductError) as blocked:
        projects.prepare_delivery("pj-incomplete", "delivery", None)
    assert blocked.value.code == "PROJECT_REPOSITORY_NOT_READY"
    assert "frontend" in blocked.value.detail

    provisioner.fail_refs.clear()
    retried = projects.provision_fullstack("pj-incomplete")
    assert all(item.status == "ready" for item in retried.repositories)
    assert len(projects.prepare_delivery("pj-incomplete", "delivery", None).repositories) == 4


def test_project_provision_failure_is_retryable_and_never_falls_back(tmp_path: Path) -> None:
    projects, provisioner = catalog(tmp_path)
    provisioner.fail = True
    created = projects.create(
        ProjectCreate(id="pj2", name="项目二", default_pipeline_revision_id="backend-delivery:3"),
        "admin",
    )
    assert created.project.lifecycle_status == "provision_failed"
    assert created.workspace.error_code == "PROJECT_WORKSPACE_PROVISION_FAILED"
    with pytest.raises(ProductError) as blocked:
        projects.prepare_delivery("pj2", "delivery", None)
    assert blocked.value.code == "PROJECT_WORKSPACE_NOT_READY"
    provisioner.fail = False
    retried = projects.retry_workspace("pj2")
    assert retried.project.lifecycle_status == "active"
    assert retried.workspace.provision_attempt == 2


def test_interrupted_project_provisioning_resumes_idempotently(tmp_path: Path) -> None:
    projects, _ = catalog(tmp_path)
    project = Project(
        id="pj-crash",
        slug="pj-crash",
        name="崩溃恢复项目",
        lifecycle_status="provisioning",
        version=1,
        created_by="admin",
    )
    workspace = ProjectWorkspace(
        project_id=project.id,
        workspace_id="project:pj-crash",
        repository_ref="projects/pj-crash",
        status="provisioning",
        provision_attempt=1,
    )
    projects.repository.create(project, workspace)
    projects.recover_provisioning()
    recovered = projects.get(project.id)
    assert recovered.project.lifecycle_status == "active"
    assert recovered.workspace.status == "ready"
    projects.recover_provisioning()
    assert projects.get(project.id).project.version == recovered.project.version


def test_orphan_delivery_lease_is_removed_during_restart_reconciliation(tmp_path: Path) -> None:
    projects, _ = catalog(tmp_path)
    projects.create(
        ProjectCreate(id="pj-lease", name="租约恢复", default_pipeline_revision_id="delivery:1"),
        "admin",
    )
    projects.repository.acquire_lease("pj-lease", "missing-delivery")
    repository = ProjectLeaseDeliveryRepository(
        SQLiteDeliveryRepository(projects.repository.database), projects
    )
    repository.reconcile_leases()
    assert projects.get("pj-lease").active_delivery_id is None


def test_initial_delivery_lease_aggregate_and_event_roll_back_together(tmp_path: Path) -> None:
    projects, _ = catalog(tmp_path)
    projects.create(
        ProjectCreate(id="pj-uow", name="事务项目", default_pipeline_revision_id="delivery:1"),
        "admin",
    )
    context = projects.prepare_delivery("pj-uow", "delivery-uow", None)
    inner = SQLiteDeliveryRepository(projects.repository.database)
    repository = ProjectLeaseDeliveryRepository(inner, projects)
    delivery = DeliveryRun(
        id="delivery-uow",
        project_id="pj-uow",
        workspace_id=context.workspace_id,
        project_execution_snapshot=ProjectExecutionSnapshot(**context.model_dump()),
        user_request="验证首次事务",
        status="queued",
        version=1,
        resolved_journey_sha256="a" * 64,
        evidence_identity="deterministic-test",
        planning_identity="deterministic-test",
    )
    with sqlite3.connect(projects.repository.database) as connection:
        connection.execute(
            """CREATE TRIGGER fail_initial_event BEFORE INSERT ON product_events
            BEGIN SELECT RAISE(ABORT,'event insert failed'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        repository.save(delivery)
    assert inner.get(delivery.id) is None
    assert projects.get("pj-uow").active_delivery_id is None


def test_delivery_project_index_cannot_drift_from_frozen_snapshot(tmp_path: Path) -> None:
    projects, _ = catalog(tmp_path)
    projects.create(
        ProjectCreate(id="pj-index", name="索引一致性", default_pipeline_revision_id="delivery:1"),
        "admin",
    )
    context = projects.prepare_delivery("pj-index", "delivery-index", None)
    repository = ProjectLeaseDeliveryRepository(
        SQLiteDeliveryRepository(projects.repository.database), projects
    )
    delivery = DeliveryRun(
        id="delivery-index",
        project_id="legacy-default",
        workspace_id=context.workspace_id,
        project_execution_snapshot=ProjectExecutionSnapshot(**context.model_dump()),
        user_request="伪造项目索引",
        status="queued",
        version=1,
        resolved_journey_sha256="a" * 64,
        evidence_identity="deterministic-test",
        planning_identity="deterministic-test",
    )
    with pytest.raises(ValueError, match="project index"):
        repository.save(delivery)
    assert projects.get("pj-index").active_delivery_id is None


def test_archived_project_is_read_only_and_binding_cas_is_enforced(tmp_path: Path) -> None:
    projects, _ = catalog(tmp_path)
    created = projects.create(
        ProjectCreate(id="pj3", name="项目三", default_pipeline_revision_id="backend-delivery:1"),
        "admin",
    )
    binding = created.pipeline_bindings[0]
    with pytest.raises(ProductError) as conflict:
        projects.put_pipeline_binding(
            "pj3",
            ProjectBindingUpdate(
                pipeline_revision_id="backend-delivery:1",
                is_default=True,
                expected_version=binding.version + 1,
            ),
        )
    assert conflict.value.code == "PROJECT_BINDING_VERSION_CONFLICT"
    projects.put_deployment_access("pj3", ProjectDeploymentUpdate(deployment_id="backend"))
    source = projects.put_knowledge_source(
        "pj3", ProjectKnowledgeSourceUpdate(binding_id="feishu-team", source_scope="space-1")
    )
    assert source.version == 1
    archived = projects.archive("pj3", created.project.version)
    assert archived.lifecycle_status == "archived"
    with pytest.raises(ProductError) as error:
        projects.put_deployment_access("pj3", ProjectDeploymentUpdate(deployment_id="pm"))
    assert error.value.code == "PROJECT_ARCHIVED"
    with pytest.raises(ProductError):
        projects.put_knowledge_source("pj3", ProjectKnowledgeSourceUpdate(binding_id="feishu-team"))


def test_project_git_candidates_and_main_revisions_are_fully_isolated(tmp_path: Path) -> None:
    workspaces = ProjectGitWorkspaces(tmp_path / "workspaces")
    first_base = workspaces.provision("projects/pj1")
    second_base = workspaces.provision("projects/pj2")
    first = workspaces.for_workspace("project:pj1")
    second = workspaces.for_workspace("project:pj2")
    first_tree = first.create_worktree("delivery-pj1", first_base)
    (first_tree / "src" / "project.py").write_text("PROJECT = 'pj1'\n", encoding="utf-8")
    candidate = first.create_candidate(
        "delivery-pj1", base_revision=first_base, policy=SandboxPolicy()
    )
    receipt = first.apply_candidate(candidate)
    assert receipt.after_revision == candidate.candidate_revision
    assert first.main_revision() == candidate.candidate_revision
    assert second.main_revision() == second_base
    assert second.main_revision() != first.main_revision()


def test_fullstack_repository_roles_have_isolated_git_main_refs(tmp_path: Path) -> None:
    workspaces = ProjectGitWorkspaces(tmp_path / "workspaces")
    frontend_base = workspaces.provision("projects/pj-fullstack/frontend")
    backend_base = workspaces.provision("projects/pj-fullstack/backend")
    frontend = workspaces.for_workspace("project:pj-fullstack:frontend")
    backend = workspaces.for_workspace("project:pj-fullstack:backend")

    frontend_seed = frontend.create_worktree("inspect-frontend", frontend_base)
    assert (frontend_seed / "src" / "index.html").is_file()
    assert not (frontend_seed / "src" / "service.py").exists()

    frontend_tree = frontend.create_worktree("delivery-frontend", frontend_base)
    (frontend_tree / "src" / "project.py").write_text("PROJECT = 'frontend'\n", encoding="utf-8")
    candidate = frontend.create_candidate(
        "delivery-frontend", base_revision=frontend_base, policy=SandboxPolicy()
    )
    frontend.apply_candidate(candidate)

    assert frontend.main_revision() == candidate.candidate_revision
    assert backend.main_revision() == backend_base
    assert backend.main_revision() != frontend.main_revision()


def test_existing_v031_database_migrates_to_default_project_with_audit(tmp_path: Path) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for source in sorted((ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 18:
            shutil.copy2(source, old_migrations / source.name)
    MigrationRunner(database, old_migrations).migrate()
    original = {"id": "legacy-delivery", "workspace_id": "backend-demo", "status": "completed"}
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO deliveries(id,snapshot_json) VALUES(?,?)",
            ("legacy-delivery", json.dumps(original)),
        )
        connection.execute(
            """INSERT INTO product_events(
            event_id,event_type,aggregate_type,aggregate_id,aggregate_version,payload_json,occurred_at
            ) VALUES(
            'event-1','delivery.completed','delivery','legacy-delivery',1,'{}',CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            """INSERT INTO evidence_records(
            id,delivery_id,kind,source_kind,source_id,producer_identity,status,payload_json,created_at
            ) VALUES('evidence-1','legacy-delivery','journey','delivery','legacy-delivery',
            'legacy','verified','{}',CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO agent_runs(
            id,delivery_id,pipeline_revision_id,binding_site,resolved_binding_hash,
            deployment_snapshot_json,attempt_id,runtime_identity,status,
            artifact_envelopes_json,created_at,updated_at
            ) VALUES(
            'legacy-agent-run','legacy-delivery','backend-delivery:1','implementation',
            ?, '{}','legacy-attempt','codex-cli:legacy','succeeded','[]',
            '2026-01-01T00:00:00+00:00','2026-01-01T00:01:00+00:00'
            )""",
            ("b" * 64,),
        )
    assert MigrationRunner(database, ROOT / "migrations").migrate() == tuple(range(19, 45))
    with sqlite3.connect(database) as connection:
        snapshot, project_id = connection.execute(
            "SELECT snapshot_json,project_id FROM deliveries WHERE id='legacy-delivery'"
        ).fetchone()
        assert json.loads(snapshot)["project_id"] == "legacy-default"
        assert project_id == "legacy-default"
        audit = connection.execute(
            """SELECT original_json,original_sha256,normalized_sha256
            FROM project_migration_audit WHERE aggregate_id='legacy-delivery'"""
        ).fetchone()
        report = connection.execute(
            """SELECT delivery_count,event_count,evidence_count,source_index_sha256
            FROM project_migration_reports WHERE migration_id='0019-project-governance'"""
        ).fetchone()
        repositories = connection.execute(
            """SELECT role,workspace_ref,repository_ref,status
            FROM project_repositories WHERE project_id='legacy-default'"""
        ).fetchall()
        team_projection = connection.execute(
            """SELECT template_id,template_revision,status
            FROM project_team_bindings WHERE project_id='legacy-default'"""
        ).fetchone()
        workspace_projection = connection.execute(
            """SELECT pw.workcell_key,wb.adapter_type,wb.repository_uri,wb.status
            FROM project_workcell_bindings pw
            JOIN workspace_bindings wb ON wb.id=pw.workspace_binding_id
            WHERE pw.project_id='legacy-default'"""
        ).fetchall()
        migrated_run = connection.execute(
            """SELECT parent_agent_run_id,root_agent_run_id,depth,run_role,
            delegate_purpose,workspace_access,slot_key
            FROM agent_runs WHERE id='legacy-agent-run'"""
        ).fetchone()
        migrated_attempt = connection.execute(
            """SELECT id,agent_run_id,phase,ordinal,provider_binding_hash,
            runtime_identity,status,started_at,finished_at
            FROM agent_attempts WHERE id='legacy-attempt'"""
        ).fetchone()
    assert json.loads(audit[0]) == original
    assert audit[1] != audit[2]
    assert report[:3] == (1, 1, 1)
    assert len(report[3]) == 64
    assert repositories == [("backend", "backend-demo", "legacy/backend-demo", "ready")]
    assert team_projection == ("software-delivery-team", 1, "legacy_projected")
    assert workspace_projection == [
        ("backend", "managed-bare-git", "legacy/backend-demo", "ready")
    ]
    assert migrated_run == (
        None,
        "legacy-agent-run",
        0,
        "main",
        None,
        "legacy",
        None,
    )
    assert migrated_attempt == (
        "legacy-attempt",
        "legacy-agent-run",
        "legacy",
        1,
        "b" * 64,
        "codex-cli:legacy",
        "succeeded",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:01:00+00:00",
    )
    assert MigrationRunner(database, ROOT / "migrations").migrate() == ()
