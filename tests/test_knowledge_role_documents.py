from __future__ import annotations

import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.agents import AgentRunLedger, ArtifactEnvelope
from agent_team_os.modules.delivery import RoleDocumentPublicationRequest
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
    UserCreate,
)
from agent_team_os.modules.knowledge import (
    AssetReference,
    DocumentCreate,
    DocumentKind,
    DocumentPatch,
    KnowledgeActor,
    KnowledgePublicationLedger,
    KnowledgePublisher,
    KnowledgeSearchIndex,
    PermissionGrant,
    SQLiteWikiRepository,
    WikiAccess,
    WikiService,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.permissions import Role

ROOT = Path(__file__).parents[1]


def test_role_document_migration_creates_project_space_and_preserves_legacy_docs(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for source in sorted((ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 21:
            shutil.copy2(source, old_migrations / source.name)
    MigrationRunner(database, old_migrations).migrate()

    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO users(
            id,username,display_name,role,password_hash,enabled,version,created_at,updated_at
            ) VALUES('admin-1','admin-1','管理员','administrator','hash',1,1,
            CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO wiki_spaces(
            id,name,description,version,created_by,created_at,updated_at,scope_kind,project_id
            ) VALUES('manual-space','项目说明','旧人工知识',1,'admin-1',CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,'project','legacy-default')"""
        )
        connection.execute(
            """INSERT INTO wiki_documents(
            id,space_id,parent_id,title,current_revision,version,created_by,created_at,
            updated_at,source_kind,source_id
            ) VALUES('manual-doc','manual-space',NULL,'旧项目手册',1,1,'admin-1',
            CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'manual',NULL)"""
        )
        connection.execute(
            """INSERT INTO wiki_revisions(
            document_id,revision,content_json,search_text,content_sha256,created_by,created_at
            ) VALUES('manual-doc',1,'{"text":"保留我"}','保留我',?,'admin-1',CURRENT_TIMESTAMP)""",
            ("a" * 64,),
        )
        connection.execute(
            """INSERT INTO wiki_spaces(
            id,name,description,version,created_by,created_at,updated_at,scope_kind,project_id
            ) VALUES('system:delivery-evidence','交付证据归档','旧知识证据混合区',1,
            'admin-1',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'project','legacy-default')"""
        )

    assert 22 in MigrationRunner(database, ROOT / "migrations").migrate()

    with sqlite3.connect(database) as connection:
        standard_space = connection.execute(
            """SELECT space_kind,lifecycle_status FROM wiki_spaces
            WHERE id='project-docs:legacy-default'"""
        ).fetchone()
        legacy_space = connection.execute(
            """SELECT space_kind,lifecycle_status FROM wiki_spaces
            WHERE id='system:delivery-evidence'"""
        ).fetchone()
        mapping = connection.execute(
            """SELECT migrated_document_id,project_id
            FROM knowledge_legacy_document_mappings
            WHERE legacy_document_id='manual-doc'"""
        ).fetchone()
        copied = connection.execute(
            """SELECT space_id,document_kind,lifecycle_status,source_kind,source_id
            FROM wiki_documents WHERE id=?""",
            (mapping[0],),
        ).fetchone()
        original_count = connection.execute(
            "SELECT COUNT(*) FROM wiki_documents WHERE id='manual-doc'"
        ).fetchone()[0]
        copied_revision = connection.execute(
            """SELECT content_json FROM wiki_revisions
            WHERE document_id=? AND revision=1""",
            (mapping[0],),
        ).fetchone()[0]

    assert standard_space == ("project-documents", "active")
    assert legacy_space == ("legacy-archive", "archived")
    assert mapping[1] == "legacy-default"
    assert copied == (
        "project-docs:legacy-default",
        "project-general",
        "active",
        "legacy-migrated",
        "manual-doc",
    )
    assert original_count == 1
    assert copied_revision == '{"text":"保留我"}'


def test_manual_project_document_records_kind_role_delivery_and_human_provenance(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manual-role-document.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    user = identity.bootstrap(BootstrapRequest(password="secure-admin-2026"))
    actor = KnowledgeActor(user_id=user.id, role=user.role)
    wiki = WikiService(SQLiteWikiRepository(database))

    created = wiki.create_document(
        actor,
        DocumentCreate(
            space_id="project-docs:legacy-default",
            title="后端接口约定",
            document_kind=DocumentKind.BACKEND_API,
            role_key="backend-engineer",
            delivery_id="delivery-1",
            content={"markdown": "GET /health"},
            asset_references=(
                AssetReference(kind="external-link", url="https://example.com/api"),
            ),
        ),
    )

    listed = wiki.list_documents(
        actor,
        space_id="project-docs:legacy-default",
        document_kind=DocumentKind.BACKEND_API,
        role_key="backend-engineer",
        delivery_id="delivery-1",
    )
    revision = wiki.get_revision(actor, created.id, 1)

    assert listed == (created,)
    assert created.document_kind == DocumentKind.BACKEND_API
    assert revision.provenance.producer_kind == "human"
    assert revision.provenance.producer_id == user.id
    assert revision.asset_references[0].url == "https://example.com/api"


def test_publication_ledger_is_idempotent_and_keys_each_artifact_separately(
    tmp_path: Path,
) -> None:
    database = tmp_path / "publication-ledger.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    agent_runs = AgentRunLedger(database)
    run = agent_runs.start(
        delivery_id="delivery-1",
        pipeline_revision_id="backend-delivery:1",
        binding_site="requirements.product-manager",
        resolved_binding_hash="b" * 64,
        deployment_snapshot={"id": "deployment-1"},
        runtime_identity="codex-simulated-hermes",
    )
    content = {"summary": "增加 health endpoint"}
    artifact = ArtifactEnvelope(
        contract_id="requirement-artifact-v1",
        artifact_key="primary",
        content=content,
        sha256=sha256_json(content),
    )
    agent_runs.finish(run, status="succeeded", artifacts=(artifact,))
    ledger = KnowledgePublicationLedger(database)
    request = RoleDocumentPublicationRequest(
        project_id="legacy-default",
        delivery_id="delivery-1",
        node_id="requirements",
        binding_site=run.binding_site,
        agent_run_id=run.id,
        artifact_id=artifact.id,
        artifact_key=artifact.artifact_key,
        contract_id=artifact.contract_id,
        artifact_sha256=artifact.sha256,
        runtime_identity=run.runtime_identity,
    )

    first = ledger.register(request)
    duplicate = ledger.register(request)
    second = ledger.register(
        request.model_copy(
            update={
                "artifact_id": "artifact-detail",
                "artifact_key": "details",
                "artifact_sha256": "c" * 64,
            }
        )
    )

    assert duplicate == first
    assert second.id != first.id
    assert first.publication_key.endswith(":requirement-artifact-v1:primary")
    assert second.publication_key.endswith(":requirement-artifact-v1:details")
    assert ledger.list_for_delivery("delivery-1") == (first, second)
    with sqlite3.connect(database) as connection:
        payloads = tuple(
            json.loads(row[0])
            for row in connection.execute(
                """SELECT payload_json FROM product_events
                WHERE event_type='knowledge.publication-requested'
                ORDER BY sequence"""
            ).fetchall()
        )
    assert len(payloads) == 2
    assert all("content" not in payload for payload in payloads)


def test_publisher_renders_requirement_artifact_with_agent_provenance(
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge-publisher.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    content = {
        "summary": "增加 health endpoint",
        "non_goals": ["不扩展鉴权"],
        "risks": ["版本字段漂移"],
        "acceptance_criteria": [
            {"id": "AC-001", "statement": "返回 status=ok"}
        ],
    }
    artifact = ArtifactEnvelope(
        contract_id="requirement-artifact-v1",
        artifact_key="primary",
        content=content,
        sha256=sha256_json(content),
    )
    agent_runs = AgentRunLedger(database)
    running = agent_runs.start(
        delivery_id="delivery-1",
        pipeline_revision_id="backend-delivery:1",
        binding_site="requirements.product-manager",
        resolved_binding_hash="b" * 64,
        deployment_snapshot={"id": "pm-deployment"},
        runtime_identity="codex-simulated-hermes",
    )
    succeeded = agent_runs.finish(running, status="succeeded", artifacts=(artifact,))
    ledger = KnowledgePublicationLedger(database)
    pending = ledger.register(
        RoleDocumentPublicationRequest(
            project_id="legacy-default",
            delivery_id="delivery-1",
            node_id="requirements",
            binding_site=succeeded.binding_site,
            agent_run_id=succeeded.id,
            artifact_id=artifact.id,
            artifact_key=artifact.artifact_key,
            contract_id=artifact.contract_id,
            artifact_sha256=artifact.sha256,
            runtime_identity=succeeded.runtime_identity,
        )
    )

    published = KnowledgePublisher(database, ledger).publish(
        pending.id, expected_version=pending.version
    )
    document = SQLiteWikiRepository(database).get_document(published.target_document_id or "")
    revision = SQLiteWikiRepository(database).get_revision(document.id, 1)

    assert published.status == "published"
    assert document.document_kind == DocumentKind.PRODUCT_REQUIREMENT
    assert document.role_key == "product-manager"
    assert document.delivery_id == "delivery-1"
    assert revision.content["schema"] == "project-document-v1"
    assert revision.content["artifact_key"] == "primary"
    assert "AC-001" in revision.content["markdown"]
    assert revision.provenance.agent_run_id == succeeded.id
    assert revision.provenance.source_artifact_sha256 == artifact.sha256
    assert revision.provenance.runtime_identity == "codex-simulated-hermes"


def test_publisher_normalizes_long_multiline_requirement_summary_for_document_title() -> None:
    summary = "交付受控知识上下文\n" + "冻结引用与授权边界。" * 80

    title, markdown = KnowledgePublisher._render_markdown(
        "requirement-artifact-v1",
        {"summary": summary},
    )

    assert title == "交付受控知识上下文"
    assert summary in markdown


def test_unified_search_applies_wiki_acl_before_returning_title_or_summary(
    tmp_path: Path,
) -> None:
    database = tmp_path / "actor-aware-search.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    identities = IdentityService(SQLiteIdentityRepository(database))
    admin_user = identities.bootstrap(BootstrapRequest(password="secure-admin-2026"))
    viewer_user = identities.create_user(
        admin_user,
        UserCreate(
            username="viewer-search",
            display_name="搜索访客",
            role=Role.VIEWER,
            password="secure-viewer-2026",
        ),
    )
    admin = KnowledgeActor(user_id=admin_user.id, role=admin_user.role)
    viewer = KnowledgeActor(user_id=viewer_user.id, role=viewer_user.role)
    wiki = WikiService(SQLiteWikiRepository(database))
    document = wiki.create_document(
        admin,
        DocumentCreate(
            space_id="project-docs:legacy-default",
            title="机密后端接口",
            document_kind=DocumentKind.BACKEND_API,
            content={"markdown": "secret-health-token 不应泄露"},
        ),
    )
    wiki.put_permission(
        admin,
        PermissionGrant(
            resource_kind="wiki-document",
            resource_id=document.id,
            user_id=viewer.user_id,
            access=WikiAccess.NONE,
        ),
    )
    search = KnowledgeSearchIndex(database)

    admin_hits = search.search(
        admin,
        "legacy-default",
        "secret-health-token",
        wiki=wiki,
        can_read_evidence=False,
    )
    viewer_hits = search.search(
        viewer,
        "legacy-default",
        "secret-health-token",
        wiki=wiki,
        can_read_evidence=False,
    )

    assert admin_hits[0].title == "机密后端接口"
    assert "secret-health-token" in admin_hits[0].summary
    assert viewer_hits == ()


def test_project_search_rebuilds_are_transactional_and_never_cross_projects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "project-search-isolation.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    identities = IdentityService(SQLiteIdentityRepository(database))
    user = identities.bootstrap(BootstrapRequest(password="secure-admin-2026"))
    actor = KnowledgeActor(user_id=user.id, role=user.role)
    with sqlite3.connect(database) as connection:
        for project_id, name in (("pj1", "项目一"), ("pj2", "项目二")):
            connection.execute(
                """INSERT INTO projects(
                id,slug,name,description,lifecycle_status,version,created_by,created_at,updated_at
                ) VALUES(?,?,?,?, 'active',1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (project_id, project_id, name, "搜索隔离", user.id),
            )
    wiki = WikiService(SQLiteWikiRepository(database))
    for project_id, name in (("pj1", "项目一"), ("pj2", "项目二")):
        wiki.reconcile_project_space(project_id, name, "active", actor_id=user.id)
        wiki.create_document(
            actor,
            DocumentCreate(
                space_id=f"project-docs:{project_id}",
                title=f"{name}接口",
                content={"markdown": f"shared-health {project_id}"},
            ),
        )
    search = KnowledgeSearchIndex(database)

    def find(project_id: str) -> tuple[str, ...]:
        return tuple(
            hit.title
            for hit in search.search(
                actor,
                project_id,
                "shared-health",
                wiki=wiki,
                can_read_evidence=False,
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(find, ("pj1", "pj2") * 12))

    for project_id, titles in zip(("pj1", "pj2") * 12, results, strict=True):
        assert titles == (("项目一接口" if project_id == "pj1" else "项目二接口"),)


def test_publication_same_hash_is_noop_and_new_hash_appends_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "publication-revision.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    agent_runs = AgentRunLedger(database)
    publications = KnowledgePublicationLedger(database)
    publisher = KnowledgePublisher(database, publications)

    def register(summary: str) -> tuple[object, object]:
        content = {
            "summary": summary,
            "acceptance_criteria": [
                {"id": "AC-001", "statement": "返回 status=ok"}
            ],
        }
        artifact = ArtifactEnvelope(
            contract_id="requirement-artifact-v1",
            artifact_key="primary",
            content=content,
            sha256=sha256_json(content),
        )
        running = agent_runs.start(
            delivery_id="delivery-1",
            pipeline_revision_id="backend-delivery:1",
            binding_site="requirements.product-manager",
            resolved_binding_hash="a" * 64,
            deployment_snapshot={"id": "pm"},
            runtime_identity="codex-simulated-hermes",
        )
        succeeded = agent_runs.finish(
            running, status="succeeded", artifacts=(artifact,)
        )
        publication = publications.register(
            RoleDocumentPublicationRequest(
                project_id="legacy-default",
                delivery_id="delivery-1",
                node_id="requirements",
                binding_site=succeeded.binding_site,
                agent_run_id=succeeded.id,
                artifact_id=artifact.id,
                artifact_key=artifact.artifact_key,
                contract_id=artifact.contract_id,
                artifact_sha256=artifact.sha256,
                runtime_identity=succeeded.runtime_identity,
            )
        )
        return publication, artifact

    initial, _artifact = register("初始需求")
    first = publisher.publish(initial.id, expected_version=initial.version)
    same = publisher.publish(first.id, expected_version=first.version)
    changed, changed_artifact = register("补充后的需求")
    second = publisher.publish(changed.id, expected_version=changed.version)
    revisions = SQLiteWikiRepository(database).list_revisions(
        second.target_document_id or ""
    )

    assert same == first
    assert second.target_document_id == first.target_document_id
    assert second.target_revision == 2
    assert len(revisions) == 2
    assert revisions[0].provenance.source_artifact_sha256 == changed_artifact.sha256


def test_agent_republication_conflicts_after_human_revision_without_overwrite(
    tmp_path: Path,
) -> None:
    database = tmp_path / "publication-human-conflict.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    identities = IdentityService(SQLiteIdentityRepository(database))
    user = identities.bootstrap(BootstrapRequest(password="secure-admin-2026"))
    actor = KnowledgeActor(user_id=user.id, role=user.role)
    agent_runs = AgentRunLedger(database)
    publications = KnowledgePublicationLedger(database)
    publisher = KnowledgePublisher(database, publications)

    def register(summary: str):  # type: ignore[no-untyped-def]
        content = {
            "summary": summary,
            "acceptance_criteria": [
                {"id": "AC-001", "statement": "返回 status=ok"}
            ],
        }
        artifact = ArtifactEnvelope(
            contract_id="requirement-artifact-v1",
            content=content,
            sha256=sha256_json(content),
        )
        run = agent_runs.start(
            delivery_id="delivery-1",
            pipeline_revision_id="backend-delivery:1",
            binding_site="requirements.product-manager",
            resolved_binding_hash="a" * 64,
            deployment_snapshot={"id": "pm"},
            runtime_identity="codex-simulated-hermes",
        )
        run = agent_runs.finish(run, status="succeeded", artifacts=(artifact,))
        return publications.register(
            RoleDocumentPublicationRequest(
                project_id="legacy-default",
                delivery_id="delivery-1",
                node_id="requirements",
                binding_site=run.binding_site,
                agent_run_id=run.id,
                artifact_id=artifact.id,
                artifact_key=artifact.artifact_key,
                contract_id=artifact.contract_id,
                artifact_sha256=artifact.sha256,
                runtime_identity=run.runtime_identity,
            )
        )

    first_pending = register("初始需求")
    first = publisher.publish(
        first_pending.id, expected_version=first_pending.version
    )
    wiki = WikiService(SQLiteWikiRepository(database))
    document = wiki.get_document(actor, first.target_document_id or "")
    edited = wiki.patch_document(
        actor,
        document.id,
        DocumentPatch(
            expected_version=document.version,
            content={"markdown": "人工确认后的正式需求，不允许 Agent 覆盖。"},
        ),
    )
    changed = register("Agent 再次生成的需求")

    with pytest.raises(ProductError) as conflict:
        publisher.publish(changed.id, expected_version=changed.version)

    latest = wiki.get_revision(actor, edited.id, edited.current_revision)
    failed = publications.get(changed.id)
    assert conflict.value.code == "KNOWLEDGE_PUBLICATION_HUMAN_CONFLICT"
    assert failed.status == "failed"
    assert failed.error_code == conflict.value.code
    assert latest.content == {"markdown": "人工确认后的正式需求，不允许 Agent 覆盖。"}
    assert latest.provenance.producer_kind == "human"


def test_concurrent_publication_retry_creates_one_document_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "publication-concurrency.sqlite"
    MigrationRunner(database, ROOT / "migrations").migrate()
    content = {
        "title": "交付计划",
        "instructions": "实现 health endpoint",
        "acceptance_ids": ["AC-001"],
        "system_policy": {"allowed_paths": ["src/**", "tests/**"]},
    }
    artifact = ArtifactEnvelope(
        contract_id="task-contract-v1",
        content=content,
        sha256=sha256_json(content),
    )
    agent_runs = AgentRunLedger(database)
    run = agent_runs.start(
        delivery_id="delivery-1",
        pipeline_revision_id="backend-delivery:1",
        binding_site="tasking.project-admin",
        resolved_binding_hash="a" * 64,
        deployment_snapshot={"id": "project-admin"},
        runtime_identity="codex-simulated-hermes",
    )
    run = agent_runs.finish(run, status="succeeded", artifacts=(artifact,))
    publications = KnowledgePublicationLedger(database)
    pending = publications.register(
        RoleDocumentPublicationRequest(
            project_id="legacy-default",
            delivery_id="delivery-1",
            node_id="tasking",
            binding_site=run.binding_site,
            agent_run_id=run.id,
            artifact_id=artifact.id,
            artifact_key=artifact.artifact_key,
            contract_id=artifact.contract_id,
            artifact_sha256=artifact.sha256,
            runtime_identity=run.runtime_identity,
        )
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(
            executor.map(
                lambda _index: KnowledgePublisher(database, publications).publish(
                    pending.id
                ),
                range(24),
            )
        )

    assert {item.target_document_id for item in results} == {
        results[0].target_document_id
    }
    assert publications.get(pending.id).attempt_count == 1
    document_id = results[0].target_document_id or ""
    assert len(SQLiteWikiRepository(database).list_revisions(document_id)) == 1
    assert len(
        SQLiteWikiRepository(database).list_documents(
            "project-docs:legacy-default"
        )
    ) == 1
