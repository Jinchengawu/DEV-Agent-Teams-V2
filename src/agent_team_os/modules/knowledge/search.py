from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from .domain import KnowledgeActor

if TYPE_CHECKING:
    from .application import WikiService


class KnowledgeSearchHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    group: str
    source_kind: str
    source_id: str
    title: str
    summary: str
    revision: str
    content_sha256: str | None
    source_link: str


class KnowledgeActivityItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    source_kind: str
    source_id: str
    delivery_id: str | None = None
    title: str
    summary: str
    revision: str
    content_sha256: str | None
    occurred_at: datetime
    source_link: str


class ResolvedKnowledgeSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    source_kind: str
    source_id: str
    title: str
    revision: str
    content_sha256: str
    content_text: str


class KnowledgeSearchIndex:
    """Actor-scoped, rebuildable discovery projection; sources remain authoritative."""

    def __init__(self, database: Path) -> None:
        self.database = database

    def search(
        self,
        actor: KnowledgeActor,
        project_id: str,
        query: str,
        *,
        wiki: WikiService,
        include_global: bool = True,
        can_read_evidence: bool,
        provider_snapshot_authorizer: Callable[[str], bool] | None = None,
    ) -> tuple[KnowledgeSearchHit, ...]:
        wiki_space_ids = {
            space.id
            for space in wiki.list_spaces(
                actor,
                project_id=project_id,
                include_global=include_global,
            )
        }
        wiki_document_ids = tuple(
            document.id
            for document in wiki.list_documents(actor)
            if document.space_id in wiki_space_ids
        )
        provider_snapshot_ids = self._authorized_provider_snapshots(
            project_id, provider_snapshot_authorizer
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._replace_projection(
                connection,
                project_id,
                wiki_document_ids=wiki_document_ids,
                include_evidence=can_read_evidence,
                provider_snapshot_ids=provider_snapshot_ids,
            )
            rows = self._query(connection, project_id, query)
            connection.commit()
        return tuple(self._hit(row) for row in rows)

    def activity(
        self,
        project_id: str,
        *,
        include_global: bool = True,
        source_kind: str | None = None,
        delivery_id: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> tuple[KnowledgeActivityItem, ...]:
        """Read a chronological cross-source projection without mutating source records."""
        items: list[KnowledgeActivityItem] = []
        with sqlite3.connect(self.database) as connection:
            evidence_rows = connection.execute(
                """SELECT id,delivery_id,kind,payload_json,content_sha256,created_at
                FROM evidence_records WHERE project_id=? ORDER BY created_at DESC,id DESC""",
                (project_id,),
            ).fetchall()
            wiki_scope = (
                "(space.project_id=? OR space.scope_kind='global')"
                if include_global
                else "space.project_id=?"
            )
            wiki_rows = connection.execute(
                f"""SELECT COALESCE(space.project_id,''),document.id,document.title,
                revision.search_text,document.current_revision,revision.content_sha256,
                document.updated_at
                FROM wiki_documents document
                JOIN wiki_spaces space ON space.id=document.space_id
                JOIN wiki_revisions revision ON revision.document_id=document.id
                    AND revision.revision=document.current_revision
                WHERE {wiki_scope}
                ORDER BY document.updated_at DESC,document.id DESC""",  # noqa: S608
                (project_id,),
            ).fetchall()
            provider_rows = connection.execute(
                """SELECT snapshot.id,source.binding_id,snapshot.source_id,
                snapshot.normalized_text,snapshot.provider_revision,snapshot.content_sha256,
                snapshot.source_url,snapshot.fetched_at
                FROM project_knowledge_sources source
                JOIN projects project ON project.id=source.project_id
                JOIN knowledge_provider_snapshots snapshot ON snapshot.binding_id=source.binding_id
                WHERE source.project_id=? AND source.enabled=1
                AND (
                    project.lifecycle_status!='archived'
                    OR snapshot.fetched_at<=project.updated_at
                )
                ORDER BY snapshot.fetched_at DESC,snapshot.id DESC""",
                (project_id,),
            ).fetchall()

        for row in evidence_rows:
            kind = str(row[2])
            payload = _json_object(str(row[3]))
            items.append(
                KnowledgeActivityItem(
                    project_id=project_id,
                    source_kind="evidence",
                    source_id=str(row[0]),
                    delivery_id=str(row[1]),
                    title=f"{_evidence_kind_label(kind)} · {row[1]}",
                    summary=_evidence_summary(payload),
                    revision="1",
                    content_sha256=None if row[4] in {None, ""} else str(row[4]),
                    occurred_at=_datetime(str(row[5])),
                    source_link=f"/projects/{project_id}/evidence?evidence_id={row[0]}",
                )
            )
        for row in wiki_rows:
            scoped_project = None if str(row[0]) == "" else str(row[0])
            items.append(
                KnowledgeActivityItem(
                    project_id=scoped_project,
                    source_kind="wiki",
                    source_id=str(row[1]),
                    title=str(row[2]),
                    summary=str(row[3])[:240],
                    revision=str(row[4]),
                    content_sha256=str(row[5]),
                    occurred_at=_datetime(str(row[6])),
                    source_link=f"/projects/{project_id}/knowledge?document_id={row[1]}",
                )
            )
        for row in provider_rows:
            items.append(
                KnowledgeActivityItem(
                    project_id=project_id,
                    source_kind="provider-snapshot",
                    source_id=str(row[0]),
                    title=f"{row[1]} · {row[2]}",
                    summary=str(row[3])[:240],
                    revision=str(row[4]),
                    content_sha256=str(row[5]),
                    occurred_at=_datetime(str(row[7])),
                    source_link="" if row[6] is None else str(row[6]),
                )
            )

        filtered = (
            item
            for item in items
            if (source_kind is None or item.source_kind == source_kind)
            and (delivery_id is None or item.delivery_id == delivery_id)
            and (before is None or item.occurred_at < before)
        )
        return tuple(
            sorted(filtered, key=lambda item: item.occurred_at, reverse=True)[:limit]
        )

    def resolve_source(
        self, project_id: str, source_kind: str, source_id: str
    ) -> ResolvedKnowledgeSource | None:
        with sqlite3.connect(self.database) as connection:
            if source_kind == "evidence":
                row = connection.execute(
                    """SELECT id,delivery_id,kind,payload_json,content_sha256,status
                    FROM evidence_records WHERE project_id=? AND id=?""",
                    (project_id, source_id),
                ).fetchone()
                if row is None or str(row[5]) != "verified" or row[4] in {None, ""}:
                    return None
                return ResolvedKnowledgeSource(
                    project_id=project_id,
                    source_kind=source_kind,
                    source_id=str(row[0]),
                    title=f"{_evidence_kind_label(str(row[2]))} · {row[1]}",
                    revision="1",
                    content_sha256=str(row[4]),
                    content_text=json.dumps(
                        _json_object(str(row[3])), ensure_ascii=False, indent=2
                    ),
                )
            if source_kind == "provider-snapshot":
                row = connection.execute(
                    """SELECT snapshot.id,source.binding_id,snapshot.source_id,
                    snapshot.provider_revision,snapshot.content_sha256,snapshot.normalized_text
                    FROM project_knowledge_sources source
                    JOIN knowledge_provider_snapshots snapshot
                        ON snapshot.binding_id=source.binding_id
                    WHERE source.project_id=? AND source.enabled=1 AND snapshot.id=?""",
                    (project_id, source_id),
                ).fetchone()
                if row is None:
                    return None
                return ResolvedKnowledgeSource(
                    project_id=project_id,
                    source_kind=source_kind,
                    source_id=str(row[0]),
                    title=f"{row[1]} · {row[2]}",
                    revision=str(row[3]),
                    content_sha256=str(row[4]),
                    content_text=str(row[5]),
                )
        return None

    def _authorized_provider_snapshots(
        self,
        project_id: str,
        authorizer: Callable[[str], bool] | None,
    ) -> tuple[str, ...]:
        if authorizer is None:
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT snapshot.id
                FROM project_knowledge_sources source
                JOIN projects project ON project.id=source.project_id
                JOIN knowledge_provider_snapshots snapshot
                    ON snapshot.binding_id=source.binding_id
                WHERE source.project_id=? AND source.enabled=1
                AND (
                    project.lifecycle_status!='archived'
                    OR snapshot.fetched_at<=project.updated_at
                )
                ORDER BY snapshot.id""",
                (project_id,),
            ).fetchall()
        allowed: list[str] = []
        for row in rows:
            snapshot_id = str(row[0])
            try:
                if authorizer(snapshot_id):
                    allowed.append(snapshot_id)
            except Exception:
                continue
        return tuple(allowed)

    @staticmethod
    def _replace_projection(
        connection: sqlite3.Connection,
        project_id: str,
        *,
        wiki_document_ids: tuple[str, ...],
        include_evidence: bool,
        provider_snapshot_ids: tuple[str, ...],
    ) -> None:
        connection.execute(
            "DELETE FROM knowledge_search_projection WHERE project_id=?", (project_id,)
        )
        connection.execute(
            "DELETE FROM knowledge_search_fts_v2 WHERE project_id=?", (project_id,)
        )
        connection.execute(
            """CREATE TEMP TABLE IF NOT EXISTS knowledge_search_authorized(
            source_kind TEXT NOT NULL,source_id TEXT NOT NULL,
            PRIMARY KEY(source_kind,source_id))"""
        )
        connection.execute("DELETE FROM knowledge_search_authorized")
        connection.executemany(
            "INSERT INTO knowledge_search_authorized(source_kind,source_id) VALUES('wiki',?)",
            ((document_id,) for document_id in wiki_document_ids),
        )
        connection.executemany(
            """INSERT INTO knowledge_search_authorized(source_kind,source_id)
            VALUES('provider-snapshot',?)""",
            ((snapshot_id,) for snapshot_id in provider_snapshot_ids),
        )
        connection.execute(
            """INSERT INTO knowledge_search_projection(
            project_id,source_kind,source_id,title,summary,searchable_text,revision,
            content_sha256,source_link)
            SELECT ?,'wiki',document.id,document.title,
            substr(revision.search_text,1,280),revision.search_text,
            CAST(revision.revision AS TEXT),revision.content_sha256,
            '/projects/' || ? || '/knowledge?document_id=' || document.id
            FROM wiki_documents document
            JOIN wiki_spaces space ON space.id=document.space_id
            JOIN wiki_revisions revision ON revision.document_id=document.id
                AND revision.revision=document.current_revision
            JOIN knowledge_search_authorized authorized
                ON authorized.source_kind='wiki' AND authorized.source_id=document.id
            WHERE document.lifecycle_status!='archived'
            AND space.lifecycle_status!='archived'""",
            (project_id, project_id),
        )
        if include_evidence:
            connection.execute(
                """INSERT INTO knowledge_search_projection(
                project_id,source_kind,source_id,title,summary,searchable_text,revision,
                content_sha256,source_link)
                SELECT project_id,'evidence',id,kind || ' · ' || delivery_id,
                kind || ' · ' || status,payload_json,'1',COALESCE(content_sha256,''),
                '/projects/' || project_id || '/evidence?evidence_id=' || id
                FROM evidence_records WHERE project_id=?""",
                (project_id,),
            )
        connection.execute(
            """INSERT INTO knowledge_search_projection(
            project_id,source_kind,source_id,title,summary,searchable_text,revision,
            content_sha256,source_link)
            SELECT DISTINCT ?, 'provider-snapshot',snapshot.id,
            source.binding_id || ' · ' || snapshot.source_id,
            substr(snapshot.normalized_text,1,280),snapshot.normalized_text,
            snapshot.provider_revision,snapshot.content_sha256,
            COALESCE(snapshot.source_url,'')
            FROM project_knowledge_sources source
            JOIN knowledge_provider_snapshots snapshot ON snapshot.binding_id=source.binding_id
            JOIN knowledge_search_authorized authorized
                ON authorized.source_kind='provider-snapshot'
                AND authorized.source_id=snapshot.id
            WHERE source.project_id=? AND source.enabled=1""",
            (project_id, project_id),
        )
        connection.execute(
            """INSERT INTO knowledge_search_fts_v2(
            project_id,source_kind,source_id,title,summary,searchable_text,revision,
            content_sha256,source_link)
            SELECT project_id,source_kind,source_id,title,summary,searchable_text,revision,
            COALESCE(content_sha256,''),source_link
            FROM knowledge_search_projection WHERE project_id=?""",
            (project_id,),
        )

    @staticmethod
    def _query(
        connection: sqlite3.Connection, project_id: str, query: str
    ) -> tuple[sqlite3.Row, ...]:
        normalized = query.strip()
        if not normalized:
            rows = connection.execute(
                """SELECT project_id,source_kind,source_id,title,summary,revision,
                content_sha256,source_link FROM knowledge_search_projection
                WHERE project_id=? ORDER BY source_kind,title,source_id""",
                (project_id,),
            ).fetchall()
            return tuple(rows)
        phrase = " ".join(
            f'"{token.replace(chr(34), "")}"' for token in normalized.split()
        )
        try:
            rows = connection.execute(
                """SELECT project_id,source_kind,source_id,title,summary,revision,
                content_sha256,source_link FROM knowledge_search_fts_v2
                WHERE knowledge_search_fts_v2 MATCH ? AND project_id=?
                ORDER BY rank,title,source_id""",
                (phrase, project_id),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            contains = f"%{normalized}%"
            rows = connection.execute(
                """SELECT project_id,source_kind,source_id,title,summary,revision,
                content_sha256,source_link FROM knowledge_search_projection
                WHERE project_id=? AND (
                    title LIKE ? OR summary LIKE ? OR searchable_text LIKE ?
                ) ORDER BY source_kind,title,source_id""",
                (project_id, contains, contains, contains),
            ).fetchall()
        return tuple(rows)

    @staticmethod
    def _hit(row: sqlite3.Row) -> KnowledgeSearchHit:
        source_kind = str(row["source_kind"])
        group = {
            "wiki": "project-document",
            "evidence": "evidence",
            "provider-snapshot": "external-source",
        }.get(source_kind, "external-source")
        return KnowledgeSearchHit(
            project_id=str(row["project_id"]),
            group=group,
            source_kind=source_kind,
            source_id=str(row["source_id"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            revision=str(row["revision"]),
            content_sha256=(
                None
                if row["content_sha256"] in {None, ""}
                else str(row["content_sha256"])
            ),
            source_link=str(row["source_link"]),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _evidence_kind_label(kind: str) -> str:
    return {
        "journey": "流水线快照",
        "requirement": "需求分析",
        "task": "任务合同",
        "candidate": "候选变更",
        "diff": "代码差异",
        "verification": "机器验证",
        "plan-gate": "计划审批",
        "design-gate": "设计审批",
        "candidate-gate": "候选审批",
        "apply-receipt": "应用回执",
        "release-bundle": "全栈发布包",
        "release-manifest": "发布清单",
        "evaluation-report": "评测报告",
    }.get(kind, f"交付证据（{kind}）")


def _evidence_summary(payload: dict[str, Any]) -> str:
    changed_files = payload.get("changed_files")
    if isinstance(changed_files, list):
        values = [str(value) for value in changed_files if str(value).strip()]
        if values:
            return "、".join(values)[:240]
    for key in ("summary", "user_request", "command", "result"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:240]
