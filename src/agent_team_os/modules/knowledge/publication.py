from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ...shared.errors import ProductError
from ...shared.events import ProductEvent
from ...shared.hashes import Sha256, sha256_json
from ...shared.ids import new_id
from ..agents import ArtifactEnvelope
from ..artifacts import ArtifactStorageError, ContentAddressedArtifactStorage
from ..delivery import RoleDocumentPublicationRequest
from .domain import DocumentKind, RevisionProducerKind, RevisionProvenance


class KnowledgePublicationStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class KnowledgePublication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    publication_key: str
    project_id: str
    delivery_id: str
    node_id: str
    binding_site: str
    agent_run_id: str
    artifact_id: str
    artifact_key: str
    contract_id: str
    artifact_sha256: Sha256
    runtime_identity: str | None = None
    required: bool = True
    status: KnowledgePublicationStatus
    attempt_count: int = Field(ge=0)
    target_space_id: str | None = None
    target_document_id: str | None = None
    target_revision: int | None = Field(default=None, ge=1)
    expected_document_version: int | None = Field(default=None, ge=1)
    error_code: str | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


def _append_requested_event(
    connection: sqlite3.Connection, publication: KnowledgePublication
) -> None:
    event = ProductEvent(
        event_type="knowledge.publication-requested",
        aggregate_type="knowledge-publication",
        aggregate_id=publication.id,
        aggregate_version=publication.version,
        project_id=publication.project_id,
        payload={
            "project_id": publication.project_id,
            "delivery_id": publication.delivery_id,
            "node_id": publication.node_id,
            "binding_site": publication.binding_site,
            "contract_id": publication.contract_id,
            "artifact_id": publication.artifact_id,
            "artifact_key": publication.artifact_key,
            "artifact_sha256": publication.artifact_sha256,
        },
        occurred_at=publication.updated_at,
    )
    connection.execute(
        """INSERT INTO product_events(
        event_id,event_type,aggregate_type,aggregate_id,aggregate_version,project_id,
        payload_json,occurred_at) VALUES(?,?,?,?,?,?,?,?)""",
        (
            event.id,
            event.event_type,
            event.aggregate_type,
            event.aggregate_id,
            event.aggregate_version,
            event.project_id,
            json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
            event.occurred_at.isoformat(),
        ),
    )


class KnowledgePublicationLedger:
    """Durable publication state; ArtifactEnvelope remains the content authority."""

    def __init__(self, database: Path) -> None:
        self.database = database

    def register(self, request: RoleDocumentPublicationRequest) -> KnowledgePublication:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = self.register_on(connection, request)
            connection.commit()
            return publication

    def register_on(
        self,
        connection: sqlite3.Connection,
        request: RoleDocumentPublicationRequest,
    ) -> KnowledgePublication:
        existing_row = connection.execute(
            "SELECT * FROM knowledge_publications WHERE publication_key=?",
            (request.publication_key,),
        ).fetchone()
        if existing_row is not None:
            existing = self._publication(existing_row)
            if existing.artifact_sha256 == request.artifact_sha256:
                return existing
            now = datetime.now(UTC)
            connection.execute(
                """UPDATE knowledge_publications SET
                agent_run_id=?,artifact_id=?,artifact_sha256=?,runtime_identity=?,required=?,
                status='pending',error_code=NULL,version=version+1,updated_at=?,published_at=NULL
                WHERE id=? AND version=?""",
                (
                    request.agent_run_id,
                    request.artifact_id,
                    request.artifact_sha256,
                    request.runtime_identity,
                    int(request.required),
                    now.isoformat(),
                    existing.id,
                    existing.version,
                ),
            )
            updated = self._get_on(connection, existing.id)
            _append_requested_event(connection, updated)
            return updated
        now = datetime.now(UTC)
        publication = KnowledgePublication(
            id=new_id(),
            publication_key=request.publication_key,
            project_id=request.project_id,
            delivery_id=request.delivery_id,
            node_id=request.node_id,
            binding_site=request.binding_site,
            agent_run_id=request.agent_run_id,
            artifact_id=request.artifact_id,
            artifact_key=request.artifact_key,
            contract_id=request.contract_id,
            artifact_sha256=request.artifact_sha256,
            runtime_identity=request.runtime_identity,
            required=request.required,
            status=KnowledgePublicationStatus.PENDING,
            attempt_count=0,
            version=1,
            created_at=now,
            updated_at=now,
        )
        connection.execute(
            """INSERT INTO knowledge_publications(
            id,publication_key,project_id,delivery_id,pipeline_node_id,binding_site,
            agent_run_id,artifact_id,artifact_key,contract_id,artifact_sha256,runtime_identity,
            required,status,attempt_count,target_space_id,target_document_id,target_revision,
            expected_document_version,error_code,version,created_at,updated_at,published_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            self._values(publication),
        )
        _append_requested_event(connection, publication)
        return publication

    def get(self, publication_id: str) -> KnowledgePublication:
        with self._connect() as connection:
            return self._get_on(connection, publication_id)

    def list_for_delivery(self, delivery_id: str) -> tuple[KnowledgePublication, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_publications
                WHERE delivery_id=? ORDER BY created_at,id""",
                (delivery_id,),
            ).fetchall()
        return tuple(self._publication(row) for row in rows)

    def list_recoverable(self) -> tuple[KnowledgePublication, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_publications
                WHERE status IN ('pending','failed','publishing')
                ORDER BY created_at,id"""
            ).fetchall()
        return tuple(self._publication(row) for row in rows)

    def record_failure(
        self,
        publication_id: str,
        error_code: str,
        *,
        expected_version: int | None = None,
    ) -> KnowledgePublication:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = self._get_on(connection, publication_id)
            if expected_version is not None and publication.version != expected_version:
                raise ProductError(
                    code="KNOWLEDGE_PUBLICATION_VERSION_CONFLICT",
                    title="知识发布版本冲突",
                    detail="发布记录已被其他操作更新。",
                    repair="刷新发布状态后重试。",
                    expected_version=expected_version,
                    actual_version=publication.version,
                )
            now = datetime.now(UTC)
            connection.execute(
                """UPDATE knowledge_publications SET status='failed',attempt_count=attempt_count+1,
                error_code=?,version=version+1,updated_at=? WHERE id=? AND version=?""",
                (
                    error_code,
                    now.isoformat(),
                    publication.id,
                    publication.version,
                ),
            )
            connection.commit()
            return self.get(publication.id)

    def is_satisfied(self, delivery_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) FROM knowledge_publications
                WHERE delivery_id=? AND required=1 AND status!='published'""",
                (delivery_id,),
            ).fetchone()
        return row is not None and int(row[0]) == 0

    def has_recoverable(self, delivery_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM knowledge_publications
                WHERE delivery_id=? AND status IN ('pending','failed','publishing') LIMIT 1""",
                (delivery_id,),
            ).fetchone()
        return row is not None

    def has_publications(self, delivery_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM knowledge_publications WHERE delivery_id=? LIMIT 1",
                (delivery_id,),
            ).fetchone()
        return row is not None

    def _get_on(
        self, connection: sqlite3.Connection, publication_id: str
    ) -> KnowledgePublication:
        row = connection.execute(
            "SELECT * FROM knowledge_publications WHERE id=?", (publication_id,)
        ).fetchone()
        if row is None:
            raise KeyError(publication_id)
        return self._publication(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _values(publication: KnowledgePublication) -> tuple[object, ...]:
        return (
            publication.id,
            publication.publication_key,
            publication.project_id,
            publication.delivery_id,
            publication.node_id,
            publication.binding_site,
            publication.agent_run_id,
            publication.artifact_id,
            publication.artifact_key,
            publication.contract_id,
            publication.artifact_sha256,
            publication.runtime_identity,
            int(publication.required),
            publication.status.value,
            publication.attempt_count,
            publication.target_space_id,
            publication.target_document_id,
            publication.target_revision,
            publication.expected_document_version,
            publication.error_code,
            publication.version,
            publication.created_at.isoformat(),
            publication.updated_at.isoformat(),
            None if publication.published_at is None else publication.published_at.isoformat(),
        )

    @staticmethod
    def _publication(row: sqlite3.Row) -> KnowledgePublication:
        return KnowledgePublication(
            id=str(row["id"]),
            publication_key=str(row["publication_key"]),
            project_id=str(row["project_id"]),
            delivery_id=str(row["delivery_id"]),
            node_id=str(row["pipeline_node_id"]),
            binding_site=str(row["binding_site"]),
            agent_run_id=str(row["agent_run_id"]),
            artifact_id=str(row["artifact_id"]),
            artifact_key=str(row["artifact_key"]),
            contract_id=str(row["contract_id"]),
            artifact_sha256=Sha256.validate(str(row["artifact_sha256"])),
            runtime_identity=(
                None if row["runtime_identity"] is None else str(row["runtime_identity"])
            ),
            required=bool(row["required"]),
            status=KnowledgePublicationStatus(str(row["status"])),
            attempt_count=int(row["attempt_count"]),
            target_space_id=(
                None if row["target_space_id"] is None else str(row["target_space_id"])
            ),
            target_document_id=(
                None
                if row["target_document_id"] is None
                else str(row["target_document_id"])
            ),
            target_revision=(
                None if row["target_revision"] is None else int(row["target_revision"])
            ),
            expected_document_version=(
                None
                if row["expected_document_version"] is None
                else int(row["expected_document_version"])
            ),
            error_code=None if row["error_code"] is None else str(row["error_code"]),
            version=int(row["version"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            published_at=(
                None
                if row["published_at"] is None
                else datetime.fromisoformat(str(row["published_at"]))
            ),
        )


class _PublicationFailure(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _document_title(value: object, *, fallback: str) -> str:
    """Project an artifact field into the bounded, single-line Wiki title contract."""
    raw = str(value or fallback).strip()
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), fallback)
    normalized = " ".join(first_line.split()).strip() or fallback
    if len(normalized) <= 240:
        return normalized
    return normalized[:239] + "…"


class KnowledgePublisher:
    """Deterministically render persisted Agent artifacts into collaborative Wiki revisions."""

    CONTRACTS = {
        "requirement-artifact-v1": (
            DocumentKind.PRODUCT_REQUIREMENT,
            "product-manager",
        ),
        "task-contract-v1": (
            DocumentKind.DELIVERY_PLAN,
            "project-admin",
        ),
    }

    def __init__(
        self,
        database: Path,
        ledger: KnowledgePublicationLedger | None = None,
        artifact_storage: ContentAddressedArtifactStorage | None = None,
    ) -> None:
        self.database = database
        self.ledger = ledger or KnowledgePublicationLedger(database)
        self.artifact_storage = artifact_storage or ContentAddressedArtifactStorage(
            database.parent / "artifacts"
        )

    def publish(
        self, publication_id: str, *, expected_version: int | None = None
    ) -> KnowledgePublication:
        connection = self.ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            publication = self.ledger._get_on(connection, publication_id)
            if expected_version is not None and publication.version != expected_version:
                raise ProductError(
                    code="KNOWLEDGE_PUBLICATION_VERSION_CONFLICT",
                    title="知识发布版本冲突",
                    detail="发布记录已被其他操作更新。",
                    repair="刷新发布状态后重试。",
                    expected_version=expected_version,
                    actual_version=publication.version,
                )
            if publication.status == KnowledgePublicationStatus.PUBLISHED:
                connection.commit()
                return publication
            publication = self._mark_publishing(connection, publication)
            try:
                artifact = self._load_artifact(connection, publication)
                rendered = self._render(publication, artifact)
                document_id, revision, document_version = self._publish_revision(
                    connection, publication, rendered
                )
            except _PublicationFailure as failure:
                failed = self._mark_failed(connection, publication, failure.code)
                connection.commit()
                raise ProductError(
                    code=failure.code,
                    title="知识发布失败",
                    detail=failure.detail,
                    repair="修复发布目标冲突后，仅重试知识发布。",
                    status_code=409,
                    actual_version=failed.version,
                ) from failure
            published = self._mark_published(
                connection,
                publication,
                document_id=document_id,
                revision=revision,
                document_version=document_version,
            )
            connection.commit()
            return published
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def publish_required(self, delivery_id: str) -> bool:
        publications = self.ledger.list_for_delivery(delivery_id)
        for publication in publications:
            if publication.required and publication.status in {
                KnowledgePublicationStatus.PENDING,
                KnowledgePublicationStatus.PUBLISHING,
            }:
                self.publish(publication.id)
        return self.ledger.is_satisfied(delivery_id)

    def _mark_publishing(
        self, connection: sqlite3.Connection, publication: KnowledgePublication
    ) -> KnowledgePublication:
        now = datetime.now(UTC)
        cursor = connection.execute(
            """UPDATE knowledge_publications SET status='publishing',attempt_count=attempt_count+1,
            error_code=NULL,version=version+1,updated_at=? WHERE id=? AND version=?""",
            (now.isoformat(), publication.id, publication.version),
        )
        if cursor.rowcount != 1:
            raise ProductError(
                code="KNOWLEDGE_PUBLICATION_VERSION_CONFLICT",
                title="知识发布版本冲突",
                detail="发布记录已被其他执行器更新。",
                repair="刷新发布状态后重试。",
                expected_version=publication.version,
            )
        return self.ledger._get_on(connection, publication.id)

    def _load_artifact(
        self, connection: sqlite3.Connection, publication: KnowledgePublication
    ) -> ArtifactEnvelope:
        row = connection.execute(
            """SELECT status,artifact_envelopes_json FROM agent_runs WHERE id=?""",
            (publication.agent_run_id,),
        ).fetchone()
        if row is None or str(row["status"]) != "succeeded":
            raise _PublicationFailure(
                "KNOWLEDGE_ARTIFACT_UNAVAILABLE",
                "发布只能读取已经成功持久化的 AgentRun ArtifactEnvelope。",
            )
        artifacts = json.loads(str(row["artifact_envelopes_json"]))
        artifact = next(
            (
                ArtifactEnvelope.model_validate(item)
                for item in artifacts
                if isinstance(item, dict) and item.get("id") == publication.artifact_id
            ),
            None,
        )
        if artifact is None:
            raise _PublicationFailure(
                "KNOWLEDGE_ARTIFACT_UNAVAILABLE",
                "AgentRun 中不存在发布记录引用的 ArtifactEnvelope。",
            )
        if artifact.content is None and artifact.reference is not None:
            try:
                stored_content = self.artifact_storage.get_json(artifact.reference)
            except ArtifactStorageError as error:
                raise _PublicationFailure(
                    "KNOWLEDGE_ARTIFACT_UNAVAILABLE",
                    "ArtifactEnvelope 引用的内容寻址对象不可用或完整性校验失败。",
                ) from error
            if not isinstance(stored_content, dict):
                raise _PublicationFailure(
                    "KNOWLEDGE_ARTIFACT_INTEGRITY_FAILED",
                    "ArtifactEnvelope 引用的 JSON 不是对象。",
                )
            artifact = artifact.model_copy(
                update={"content": stored_content, "reference": None}
            )
        if (
            artifact.contract_id != publication.contract_id
            or artifact.artifact_key != publication.artifact_key
            or artifact.sha256 != publication.artifact_sha256
            or sha256_json(artifact.content) != artifact.sha256
        ):
            raise _PublicationFailure(
                "KNOWLEDGE_ARTIFACT_INTEGRITY_FAILED",
                "ArtifactEnvelope 的合同、artifact_key 或 SHA-256 与发布记录不一致。",
            )
        return artifact

    def _render(
        self, publication: KnowledgePublication, artifact: ArtifactEnvelope
    ) -> dict[str, object]:
        mapping = self.CONTRACTS.get(artifact.contract_id)
        if mapping is None:
            raise _PublicationFailure(
                "KNOWLEDGE_CONTRACT_NOT_PUBLISHABLE",
                "该 Artifact Contract 不属于首批可发布角色文档。",
            )
        document_kind, role_key = mapping
        if artifact.content is None:
            raise _PublicationFailure(
                "KNOWLEDGE_ARTIFACT_UNAVAILABLE",
                "ArtifactEnvelope 内容不可用。",
            )
        title, markdown = self._render_markdown(artifact.contract_id, artifact.content)
        return {
            "schema": "project-document-v1",
            "artifact_key": artifact.artifact_key,
            "title": title,
            "document_kind": document_kind.value,
            "role_key": role_key,
            "markdown": markdown,
        }

    @staticmethod
    def _render_markdown(
        contract_id: str, content: dict[str, object]
    ) -> tuple[str, str]:
        if contract_id == "requirement-artifact-v1":
            summary = str(content.get("summary") or "产品需求").strip()
            title = _document_title(summary, fallback="产品需求")
            sections = [f"# {title}"]
            if summary != title:
                sections.append(summary)
            non_goals = content.get("non_goals")
            if isinstance(non_goals, list | tuple) and non_goals:
                sections.append("## 非目标\n" + "\n".join(f"- {item}" for item in non_goals))
            risks = content.get("risks")
            if isinstance(risks, list | tuple) and risks:
                sections.append("## 风险\n" + "\n".join(f"- {item}" for item in risks))
            criteria = content.get("acceptance_criteria")
            if isinstance(criteria, list | tuple) and criteria:
                lines = []
                for item in criteria:
                    if isinstance(item, dict):
                        lines.append(
                            f"- {item.get('id', '')}: {item.get('statement', '')}".strip()
                        )
                if lines:
                    sections.append("## 验收标准\n" + "\n".join(lines))
            return title, "\n\n".join(sections)
        title = _document_title(content.get("title"), fallback="交付计划")
        instructions = str(content.get("instructions") or "")
        sections = [f"# {title}", instructions]
        acceptance_ids = content.get("acceptance_ids")
        if isinstance(acceptance_ids, list | tuple) and acceptance_ids:
            sections.append(
                "## 验收项\n" + "\n".join(f"- {item}" for item in acceptance_ids)
            )
        policy = content.get("system_policy")
        if isinstance(policy, dict):
            sections.append(
                "## 执行边界\n```json\n"
                + json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n```"
            )
        return title, "\n\n".join(section for section in sections if section)

    def _publish_revision(
        self,
        connection: sqlite3.Connection,
        publication: KnowledgePublication,
        rendered: dict[str, object],
    ) -> tuple[str, int, int]:
        space_id = self._ensure_project_space(connection, publication.project_id)
        document_id = publication.target_document_id or (
            "project-doc:" + hashlib.sha256(publication.publication_key.encode()).hexdigest()
        )
        row = connection.execute(
            "SELECT * FROM wiki_documents WHERE id=? OR (source_kind=? AND source_id=?)",
            (document_id, "agent-publication", publication.publication_key),
        ).fetchone()
        if row is None:
            return self._create_document(
                connection, publication, rendered, space_id, document_id
            )
        document_id = str(row["id"])
        if str(row["space_id"]) != space_id:
            raise _PublicationFailure(
                "KNOWLEDGE_PUBLICATION_TARGET_CONFLICT",
                "同一发布键已指向其他项目知识空间。",
            )
        current_revision = int(row["current_revision"])
        document_version = int(row["version"])
        latest = connection.execute(
            """SELECT content_sha256,provenance_json FROM wiki_revisions
            WHERE document_id=? AND revision=?""",
            (document_id, current_revision),
        ).fetchone()
        if latest is None:
            raise _PublicationFailure(
                "KNOWLEDGE_PUBLICATION_TARGET_INVALID",
                "目标 Wiki 文档缺少当前 Revision。",
            )
        provenance = json.loads(str(latest["provenance_json"]))
        if provenance.get("source_artifact_sha256") == publication.artifact_sha256:
            return document_id, current_revision, document_version
        if (
            publication.expected_document_version is None
            or document_version != publication.expected_document_version
        ):
            code = (
                "KNOWLEDGE_PUBLICATION_HUMAN_CONFLICT"
                if provenance.get("producer_kind") == RevisionProducerKind.HUMAN.value
                else "KNOWLEDGE_PUBLICATION_TARGET_CONFLICT"
            )
            raise _PublicationFailure(
                code,
                "目标文档已产生新的 Revision，自动发布不会覆盖现有内容。",
            )
        revision = current_revision + 1
        next_version = document_version + 1
        now = datetime.now(UTC)
        connection.execute(
            """UPDATE wiki_documents SET title=?,current_revision=?,version=?,updated_at=?
            WHERE id=? AND version=?""",
            (
                str(rendered["title"]),
                revision,
                next_version,
                now.isoformat(),
                document_id,
                document_version,
            ),
        )
        self._insert_revision(
            connection, publication, rendered, document_id, revision, now
        )
        self._append_document_event(
            connection,
            publication,
            "knowledge.document-revised",
            document_id,
            next_version,
            revision,
            sha256_json(rendered),
            now,
        )
        return document_id, revision, next_version

    def _create_document(
        self,
        connection: sqlite3.Connection,
        publication: KnowledgePublication,
        rendered: dict[str, object],
        space_id: str,
        document_id: str,
    ) -> tuple[str, int, int]:
        now = datetime.now(UTC)
        connection.execute(
            """INSERT INTO wiki_documents(
            id,space_id,parent_id,title,current_revision,version,created_by,created_at,
            updated_at,source_kind,source_id,document_kind,role_key,delivery_id,lifecycle_status
            ) VALUES(?,?,NULL,?,?,?,NULL,?,?,?,?,?,?,?,'active')""",
            (
                document_id,
                space_id,
                str(rendered["title"]),
                1,
                1,
                now.isoformat(),
                now.isoformat(),
                "agent-publication",
                publication.publication_key,
                str(rendered["document_kind"]),
                str(rendered["role_key"]),
                publication.delivery_id,
            ),
        )
        self._insert_revision(connection, publication, rendered, document_id, 1, now)
        self._append_document_event(
            connection,
            publication,
            "knowledge.document-created",
            document_id,
            1,
            1,
            sha256_json(rendered),
            now,
        )
        return document_id, 1, 1

    @staticmethod
    def _insert_revision(
        connection: sqlite3.Connection,
        publication: KnowledgePublication,
        rendered: dict[str, object],
        document_id: str,
        revision: int,
        now: datetime,
    ) -> None:
        provenance = RevisionProvenance(
            producer_kind=RevisionProducerKind.AGENT,
            producer_id=publication.runtime_identity or publication.agent_run_id,
            agent_run_id=publication.agent_run_id,
            binding_site=publication.binding_site,
            contract_id=publication.contract_id,
            artifact_id=publication.artifact_id,
            artifact_key=publication.artifact_key,
            runtime_identity=publication.runtime_identity,
            source_artifact_sha256=publication.artifact_sha256,
        )
        markdown = str(rendered["markdown"])
        connection.execute(
            """INSERT INTO wiki_revisions(
            document_id,revision,content_json,search_text,content_sha256,created_by,created_at,
            provenance_json,asset_references_json) VALUES(?,?,?,?,?,NULL,?,?,?)""",
            (
                document_id,
                revision,
                json.dumps(rendered, ensure_ascii=False, separators=(",", ":")),
                markdown,
                sha256_json(rendered),
                now.isoformat(),
                provenance.model_dump_json(),
                "[]",
            ),
        )
        connection.execute("DELETE FROM wiki_fts WHERE document_id=?", (document_id,))
        connection.execute(
            "INSERT INTO wiki_fts(document_id,space_id,title,content) "
            "SELECT id,space_id,title,? FROM wiki_documents WHERE id=?",
            (markdown, document_id),
        )

    @staticmethod
    def _ensure_project_space(connection: sqlite3.Connection, project_id: str) -> str:
        space_id = f"project-docs:{project_id}"
        row = connection.execute(
            "SELECT lifecycle_status FROM wiki_spaces WHERE id=?", (space_id,)
        ).fetchone()
        if row is not None:
            if str(row["lifecycle_status"]) != "active":
                raise _PublicationFailure(
                    "KNOWLEDGE_PROJECT_SPACE_ARCHIVED",
                    "项目标准文档空间已经归档。",
                )
            return space_id
        project = connection.execute(
            "SELECT name,lifecycle_status FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if project is None or str(project["lifecycle_status"]) != "active":
            raise _PublicationFailure(
                "KNOWLEDGE_PROJECT_SPACE_UNAVAILABLE",
                "项目不存在或不是 active 状态。",
            )
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """INSERT INTO wiki_spaces(
            id,name,description,version,created_by,created_at,updated_at,scope_kind,project_id,
            space_kind,lifecycle_status) VALUES(?,?,?,1,NULL,?,?,'project',?,
            'project-documents','active')""",
            (
                space_id,
                f"{project['name']} · 项目文档",
                "项目角色在交付过程中发布的可协作文档。",
                now,
                now,
                project_id,
            ),
        )
        return space_id

    def _mark_failed(
        self,
        connection: sqlite3.Connection,
        publication: KnowledgePublication,
        error_code: str,
    ) -> KnowledgePublication:
        now = datetime.now(UTC)
        connection.execute(
            """UPDATE knowledge_publications SET status='failed',error_code=?,
            version=version+1,updated_at=? WHERE id=? AND version=?""",
            (error_code, now.isoformat(), publication.id, publication.version),
        )
        return self.ledger._get_on(connection, publication.id)

    def _mark_published(
        self,
        connection: sqlite3.Connection,
        publication: KnowledgePublication,
        *,
        document_id: str,
        revision: int,
        document_version: int,
    ) -> KnowledgePublication:
        now = datetime.now(UTC)
        connection.execute(
            """UPDATE knowledge_publications SET status='published',target_space_id=?,
            target_document_id=?,target_revision=?,expected_document_version=?,error_code=NULL,
            version=version+1,updated_at=?,published_at=? WHERE id=? AND version=?""",
            (
                f"project-docs:{publication.project_id}",
                document_id,
                revision,
                document_version,
                now.isoformat(),
                now.isoformat(),
                publication.id,
                publication.version,
            ),
        )
        published = self.ledger._get_on(connection, publication.id)
        event = ProductEvent(
            event_type="knowledge.publication-published",
            aggregate_type="knowledge-publication",
            aggregate_id=published.id,
            aggregate_version=published.version,
            project_id=published.project_id,
            payload={
                "delivery_id": published.delivery_id,
                "document_id": document_id,
                "revision": revision,
                "artifact_sha256": published.artifact_sha256,
            },
            occurred_at=now,
        )
        self._append_product_event(connection, event)
        return published

    @staticmethod
    def _append_document_event(
        connection: sqlite3.Connection,
        publication: KnowledgePublication,
        event_type: str,
        document_id: str,
        document_version: int,
        revision: int,
        content_sha256: str,
        now: datetime,
    ) -> None:
        KnowledgePublisher._append_product_event(
            connection,
            ProductEvent(
                event_type=event_type,
                aggregate_type="wiki-document",
                aggregate_id=document_id,
                aggregate_version=document_version,
                project_id=publication.project_id,
                payload={
                    "space_id": f"project-docs:{publication.project_id}",
                    "revision": revision,
                    "content_sha256": content_sha256,
                },
                occurred_at=now,
            ),
        )

    @staticmethod
    def _append_product_event(
        connection: sqlite3.Connection, event: ProductEvent
    ) -> None:
        connection.execute(
            """INSERT INTO product_events(
            event_id,event_type,aggregate_type,aggregate_id,aggregate_version,project_id,
            payload_json,occurred_at) VALUES(?,?,?,?,?,?,?,?)""",
            (
                event.id,
                event.event_type,
                event.aggregate_type,
                event.aggregate_id,
                event.aggregate_version,
                event.project_id,
                json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
                event.occurred_at.isoformat(),
            ),
        )
