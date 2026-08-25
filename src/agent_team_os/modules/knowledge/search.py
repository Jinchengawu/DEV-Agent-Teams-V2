from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

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
                    source_link=(
                        f"/projects/{project_id}/evidence?evidence_id={row[0]}"
                    ),
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
                    source_link=(
                        f"/projects/{project_id}/knowledge?document_id={row[1]}"
                    ),
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
        "verification": "机器验证",
        "plan-gate": "计划审批",
        "candidate-gate": "候选审批",
        "apply-receipt": "应用回执",
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
