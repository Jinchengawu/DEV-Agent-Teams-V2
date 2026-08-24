from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class KnowledgeSearchHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str | None
    source_kind: str
    source_id: str
    title: str
    searchable_text: str
    revision: str
    content_sha256: str | None
    source_link: str


class KnowledgeSearchIndex:
    """Rebuildable cross-source index; source records remain authoritative."""

    def __init__(self, database: Path) -> None:
        self.database = database

    def search(
        self, project_id: str, query: str, *, include_global: bool = True
    ) -> tuple[KnowledgeSearchHit, ...]:
        self._rebuild(project_id, include_global)
        normalized = query.strip()
        where = "knowledge_search_fts MATCH ?" if normalized else "1=1"
        parameters = (normalized,) if normalized else ()
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                f"""SELECT project_id,source_kind,source_id,title,searchable_text,
                revision,content_sha256,source_link FROM knowledge_search_fts
                WHERE {where} ORDER BY rank,title""",  # noqa: S608 - where is fixed above
                parameters,
            ).fetchall()
        return tuple(
            KnowledgeSearchHit(
                project_id=None if row[0] == "" else str(row[0]),
                source_kind=str(row[1]),
                source_id=str(row[2]),
                title=str(row[3]),
                searchable_text=str(row[4]),
                revision=str(row[5]),
                content_sha256=None if row[6] in {None, ""} else str(row[6]),
                source_link=str(row[7]),
            )
            for row in rows
        )

    def _rebuild(self, project_id: str, include_global: bool) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM knowledge_search_fts")
            scope = (
                "(space.project_id=? OR space.scope_kind='global')"
                if include_global
                else "space.project_id=?"
            )
            connection.execute(
                f"""INSERT INTO knowledge_search_fts
                SELECT COALESCE(space.project_id,''),'wiki',document.id,document.title,
                revision.search_text,CAST(revision.revision AS TEXT),revision.content_sha256,
                '/projects/' || COALESCE(space.project_id,'global') ||
                '/knowledge?document_id=' || document.id
                FROM wiki_documents document
                JOIN wiki_spaces space ON space.id=document.space_id
                JOIN wiki_revisions revision ON revision.document_id=document.id
                    AND revision.revision=document.current_revision
                WHERE {scope}""",  # noqa: S608 - scope is fixed above
                (project_id,),
            )
            connection.execute(
                """INSERT INTO knowledge_search_fts
                SELECT project_id,'evidence',id,kind || ' · ' || delivery_id,payload_json,
                '1',COALESCE(content_sha256,''),
                '/projects/' || project_id || '/evidence?evidence_id=' || id
                FROM evidence_records WHERE project_id=?""",
                (project_id,),
            )
            connection.execute(
                """INSERT INTO knowledge_search_fts
                SELECT source.project_id,'provider-snapshot',snapshot.id,
                source.binding_id || ' · ' || snapshot.source_id,snapshot.normalized_text,
                snapshot.provider_revision,snapshot.content_sha256,
                COALESCE(snapshot.source_url,'')
                FROM project_knowledge_sources source
                JOIN projects project ON project.id=source.project_id
                JOIN knowledge_provider_snapshots snapshot ON snapshot.binding_id=source.binding_id
                WHERE source.project_id=? AND source.enabled=1
                AND (
                    project.lifecycle_status!='archived'
                    OR snapshot.fetched_at<=project.updated_at
                )""",
                (project_id,),
            )
