from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .application import WikiService
from .domain import KnowledgeActor


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
