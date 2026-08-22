from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from ...shared.events import ProductEvent
from ...shared.hashes import Sha256
from .domain import Comment, Document, PermissionGrant, Revision, Space, WikiAccess
from .ports import CompareAndSwapResult

JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class SQLiteWikiRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create_space(self, space: Space) -> Space:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO wiki_spaces(
                id,name,description,version,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    space.id,
                    space.name,
                    space.description,
                    space.version,
                    space.created_by,
                    space.created_at.isoformat(),
                    space.updated_at.isoformat(),
                ),
            )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.space-created",
                    aggregate_type="wiki-space",
                    aggregate_id=space.id,
                    aggregate_version=space.version,
                    payload={"name": space.name},
                    occurred_at=space.updated_at,
                ),
            )
            connection.commit()
        return space

    def ensure_system_space(self, space: Space) -> Space:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM wiki_spaces WHERE id=?", (space.id,)
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._space(existing)
            connection.execute(
                """INSERT INTO wiki_spaces(
                id,name,description,version,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    space.id,
                    space.name,
                    space.description,
                    space.version,
                    space.created_by,
                    space.created_at.isoformat(),
                    space.updated_at.isoformat(),
                ),
            )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.space-created",
                    aggregate_type="wiki-space",
                    aggregate_id=space.id,
                    aggregate_version=space.version,
                    payload={"name": space.name},
                    occurred_at=space.updated_at,
                ),
            )
            connection.commit()
        return space

    def get_space(self, space_id: str) -> Space | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM wiki_spaces WHERE id=?", (space_id,)
            ).fetchone()
        return None if row is None else self._space(row)

    def list_spaces(self) -> tuple[Space, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM wiki_spaces ORDER BY name,id"
            ).fetchall()
        return tuple(self._space(row) for row in rows)

    def create_document(self, document: Document, revision: Revision) -> Document:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO wiki_documents(
                id,space_id,parent_id,title,current_revision,version,created_by,created_at,
                updated_at,source_kind,source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    document.id,
                    document.space_id,
                    document.parent_id,
                    document.title,
                    document.current_revision,
                    document.version,
                    document.created_by,
                    document.created_at.isoformat(),
                    document.updated_at.isoformat(),
                    document.source_kind,
                    document.source_id,
                ),
            )
            self._insert_revision(connection, revision, document)
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.document-created",
                    aggregate_type="wiki-document",
                    aggregate_id=document.id,
                    aggregate_version=document.version,
                    payload={
                        "space_id": document.space_id,
                        "revision": revision.revision,
                        "content_sha256": revision.content_sha256,
                    },
                    occurred_at=document.updated_at,
                ),
            )
            connection.commit()
        return document

    def ensure_system_document(
        self, document: Document, revision: Revision
    ) -> Document:
        if document.source_id is None:
            raise ValueError("System document requires a source ID")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM wiki_documents WHERE source_kind=? AND source_id=?",
                (document.source_kind, document.source_id),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._document(existing)
            connection.execute(
                """INSERT INTO wiki_documents(
                id,space_id,parent_id,title,current_revision,version,created_by,created_at,
                updated_at,source_kind,source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    document.id,
                    document.space_id,
                    document.parent_id,
                    document.title,
                    document.current_revision,
                    document.version,
                    document.created_by,
                    document.created_at.isoformat(),
                    document.updated_at.isoformat(),
                    document.source_kind,
                    document.source_id,
                ),
            )
            self._insert_revision(connection, revision, document)
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.document-created",
                    aggregate_type="wiki-document",
                    aggregate_id=document.id,
                    aggregate_version=document.version,
                    payload={
                        "space_id": document.space_id,
                        "revision": revision.revision,
                        "content_sha256": revision.content_sha256,
                    },
                    occurred_at=document.updated_at,
                ),
            )
            connection.commit()
        return document

    def get_document(self, document_id: str) -> Document | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM wiki_documents WHERE id=?", (document_id,)
            ).fetchone()
        return None if row is None else self._document(row)

    def get_document_by_source(self, source_kind: str, source_id: str) -> Document | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM wiki_documents WHERE source_kind=? AND source_id=?",
                (source_kind, source_id),
            ).fetchone()
        return None if row is None else self._document(row)

    def list_documents(self, space_id: str | None = None) -> tuple[Document, ...]:
        with self._connect() as connection:
            if space_id is None:
                rows = connection.execute(
                    "SELECT * FROM wiki_documents ORDER BY updated_at DESC,id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM wiki_documents WHERE space_id=? "
                    "ORDER BY updated_at DESC,id",
                    (space_id,),
                ).fetchall()
        return tuple(self._document(row) for row in rows)

    def get_revision(self, document_id: str, revision: int) -> Revision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM wiki_revisions WHERE document_id=? AND revision=?",
                (document_id, revision),
            ).fetchone()
        return None if row is None else self._revision(row)

    def list_revisions(self, document_id: str) -> tuple[Revision, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM wiki_revisions WHERE document_id=? ORDER BY revision DESC",
                (document_id,),
            ).fetchall()
        return tuple(self._revision(row) for row in rows)

    def compare_and_swap_document(
        self, expected_version: int, document: Document, revision: Revision
    ) -> CompareAndSwapResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT version FROM wiki_documents WHERE id=?", (document.id,)
            ).fetchone()
            if current is None:
                connection.rollback()
                return CompareAndSwapResult.NOT_FOUND
            if int(current["version"]) != expected_version:
                connection.rollback()
                return CompareAndSwapResult.VERSION_CONFLICT
            connection.execute(
                """UPDATE wiki_documents SET parent_id=?,title=?,current_revision=?,version=?,
                updated_at=? WHERE id=? AND version=?""",
                (
                    document.parent_id,
                    document.title,
                    document.current_revision,
                    document.version,
                    document.updated_at.isoformat(),
                    document.id,
                    expected_version,
                ),
            )
            self._insert_revision(connection, revision, document)
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.document-revised",
                    aggregate_type="wiki-document",
                    aggregate_id=document.id,
                    aggregate_version=document.version,
                    payload={
                        "revision": revision.revision,
                        "content_sha256": revision.content_sha256,
                    },
                    occurred_at=document.updated_at,
                ),
            )
            connection.commit()
        return CompareAndSwapResult.UPDATED

    def search_document_ids(self, query: str) -> tuple[str, ...]:
        phrase = " ".join(f'"{token.replace(chr(34), "")}"' for token in query.split())
        if not phrase:
            return ()
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    "SELECT document_id FROM wiki_fts WHERE wiki_fts MATCH ? ORDER BY rank",
                    (phrase,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                contains = f"%{query}%"
                rows = connection.execute(
                    """SELECT document_id FROM wiki_fts
                    WHERE title LIKE ? OR content LIKE ? ORDER BY rowid DESC""",
                    (contains, contains),
                ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def create_comment(self, comment: Comment) -> Comment:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO wiki_comments(
                id,document_id,parent_id,body,author_id,resolved,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    comment.id,
                    comment.document_id,
                    comment.parent_id,
                    comment.body,
                    comment.author_id,
                    int(comment.resolved),
                    comment.version,
                    comment.created_at.isoformat(),
                    comment.updated_at.isoformat(),
                ),
            )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.comment-created",
                    aggregate_type="wiki-comment",
                    aggregate_id=comment.id,
                    aggregate_version=comment.version,
                    payload={"document_id": comment.document_id},
                    occurred_at=comment.updated_at,
                ),
            )
            connection.commit()
        return comment

    def list_comments(self, document_id: str) -> tuple[Comment, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM wiki_comments WHERE document_id=? ORDER BY created_at,id",
                (document_id,),
            ).fetchall()
        return tuple(self._comment(row) for row in rows)

    def get_comment(self, comment_id: str) -> Comment | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM wiki_comments WHERE id=?", (comment_id,)
            ).fetchone()
        return None if row is None else self._comment(row)

    def compare_and_swap_comment(
        self, expected_version: int, comment: Comment
    ) -> CompareAndSwapResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                """UPDATE wiki_comments SET body=?,resolved=?,version=?,updated_at=?
                WHERE id=? AND version=?""",
                (
                    comment.body,
                    int(comment.resolved),
                    comment.version,
                    comment.updated_at.isoformat(),
                    comment.id,
                    expected_version,
                ),
            )
            if result.rowcount != 1:
                exists = connection.execute(
                    "SELECT 1 FROM wiki_comments WHERE id=?", (comment.id,)
                ).fetchone()
                connection.rollback()
                return (
                    CompareAndSwapResult.VERSION_CONFLICT
                    if exists
                    else CompareAndSwapResult.NOT_FOUND
                )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.comment-updated",
                    aggregate_type="wiki-comment",
                    aggregate_id=comment.id,
                    aggregate_version=comment.version,
                    payload={"document_id": comment.document_id, "resolved": comment.resolved},
                    occurred_at=comment.updated_at,
                ),
            )
            connection.commit()
        return CompareAndSwapResult.UPDATED

    def get_permission(
        self, resource_kind: str, resource_id: str, user_id: str
    ) -> WikiAccess | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT access FROM wiki_permissions
                WHERE resource_kind=? AND resource_id=? AND user_id=?""",
                (resource_kind, resource_id, user_id),
            ).fetchone()
        return None if row is None else WikiAccess(str(row[0]))

    def put_permission(self, grant: PermissionGrant) -> PermissionGrant:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO wiki_permissions(resource_kind,resource_id,user_id,access)
                VALUES(?,?,?,?) ON CONFLICT(resource_kind,resource_id,user_id)
                DO UPDATE SET access=excluded.access""",
                (grant.resource_kind, grant.resource_id, grant.user_id, grant.access.value),
            )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="knowledge.permission-updated",
                    aggregate_type=grant.resource_kind,
                    aggregate_id=grant.resource_id,
                    aggregate_version=1,
                    payload={"user_id": grant.user_id, "access": grant.access.value},
                ),
            )
            connection.commit()
        return grant

    def _insert_revision(
        self, connection: sqlite3.Connection, revision: Revision, document: Document
    ) -> None:
        connection.execute(
            """INSERT INTO wiki_revisions(
            document_id,revision,content_json,search_text,content_sha256,created_by,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (
                revision.document_id,
                revision.revision,
                json.dumps(revision.content, ensure_ascii=False, separators=(",", ":")),
                revision.search_text,
                revision.content_sha256,
                revision.created_by,
                revision.created_at.isoformat(),
            ),
        )
        connection.execute("DELETE FROM wiki_fts WHERE document_id=?", (document.id,))
        connection.execute(
            "INSERT INTO wiki_fts(document_id,space_id,title,content) VALUES(?,?,?,?)",
            (document.id, document.space_id, document.title, revision.search_text),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _space(row: sqlite3.Row) -> Space:
        return Space(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            version=int(row["version"]),
            created_by=str(row["created_by"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _document(row: sqlite3.Row) -> Document:
        return Document(
            id=str(row["id"]),
            space_id=str(row["space_id"]),
            parent_id=None if row["parent_id"] is None else str(row["parent_id"]),
            title=str(row["title"]),
            current_revision=int(row["current_revision"]),
            version=int(row["version"]),
            source_kind=str(row["source_kind"]),
            source_id=None if row["source_id"] is None else str(row["source_id"]),
            created_by=str(row["created_by"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _revision(row: sqlite3.Row) -> Revision:
        return Revision(
            document_id=str(row["document_id"]),
            revision=int(row["revision"]),
            content=JSON_ADAPTER.validate_json(str(row["content_json"])),
            search_text=str(row["search_text"]),
            content_sha256=Sha256.validate(str(row["content_sha256"])),
            created_by=str(row["created_by"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _comment(row: sqlite3.Row) -> Comment:
        return Comment(
            id=str(row["id"]),
            document_id=str(row["document_id"]),
            parent_id=None if row["parent_id"] is None else str(row["parent_id"]),
            body=str(row["body"]),
            author_id=str(row["author_id"]),
            resolved=bool(row["resolved"]),
            version=int(row["version"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _append_event(connection: sqlite3.Connection, event: ProductEvent) -> None:
        connection.execute(
            """INSERT INTO product_events(
            event_id,event_type,aggregate_type,aggregate_id,aggregate_version,payload_json,occurred_at)
            VALUES(?,?,?,?,?,?,?)""",
            (
                event.id,
                event.event_type,
                event.aggregate_type,
                event.aggregate_id,
                event.aggregate_version,
                json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
                event.occurred_at.isoformat(),
            ),
        )
