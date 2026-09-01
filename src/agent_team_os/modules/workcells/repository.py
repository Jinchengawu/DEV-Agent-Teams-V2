from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ...shared.events import ProductEvent
from .domain import (
    TeamTemplate,
    TeamTemplateDraft,
    TeamTemplateRevision,
)


class SQLiteTeamTemplateRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(self, template: TeamTemplate, draft: TeamTemplateDraft) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO team_templates(
                id,name,description,latest_revision,version,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    template.id,
                    template.name,
                    template.description,
                    template.latest_revision,
                    template.version,
                    template.created_by,
                    template.created_at.isoformat(),
                    template.updated_at.isoformat(),
                ),
            )
            self._insert_draft(connection, draft)
            _append_event(
                connection,
                ProductEvent(
                    event_type="team-template.created",
                    aggregate_type="team-template",
                    aggregate_id=template.id,
                    aggregate_version=template.version,
                    payload={"draft_id": draft.id},
                ),
            )

    def list_templates(self) -> tuple[TeamTemplate, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {_TEMPLATE_COLUMNS} FROM team_templates ORDER BY id"  # noqa: S608
            ).fetchall()
        return tuple(_template(row) for row in rows)

    def get_template(self, template_id: str) -> TeamTemplate:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_TEMPLATE_COLUMNS} FROM team_templates WHERE id=?",  # noqa: S608
                (template_id,),
            ).fetchone()
        if row is None:
            raise KeyError(template_id)
        return _template(row)

    def get_draft(self, draft_id: str) -> TeamTemplateDraft:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_DRAFT_COLUMNS} FROM team_template_drafts WHERE id=?",  # noqa: S608
                (draft_id,),
            ).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return _draft(row)

    def list_drafts(self, template_id: str) -> tuple[TeamTemplateDraft, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_DRAFT_COLUMNS} FROM team_template_drafts
                WHERE template_id=? ORDER BY updated_at DESC,id""",  # noqa: S608
                (template_id,),
            ).fetchall()
        return tuple(_draft(row) for row in rows)

    def compare_and_swap_draft(
        self,
        expected_version: int,
        draft: TeamTemplateDraft,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE team_template_drafts SET
                name=?,description=?,workcells_json=?,topology_json=?,version=?,
                validation_status=?,validation_errors_json=?,updated_at=?
                WHERE id=? AND version=?""",
                (
                    draft.name,
                    draft.description,
                    _json([item.model_dump(mode="json") for item in draft.workcells]),
                    _json(draft.topology.model_dump(mode="json")),
                    draft.version,
                    draft.validation_status,
                    _json(draft.validation_errors),
                    draft.updated_at.isoformat(),
                    draft.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            _append_event(
                connection,
                ProductEvent(
                    event_type="team-template.draft-validated",
                    aggregate_type="team-template",
                    aggregate_id=draft.template_id,
                    aggregate_version=draft.version,
                    payload={
                        "draft_id": draft.id,
                        "validation_status": draft.validation_status,
                    },
                ),
            )
        return True

    def publish(
        self,
        draft: TeamTemplateDraft,
        revision: TeamTemplateRevision,
    ) -> TeamTemplateRevision:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT version,validation_status FROM team_template_drafts WHERE id=?",
                (draft.id,),
            ).fetchone()
            if current != (draft.version, "valid"):
                raise sqlite3.IntegrityError("team template draft changed before publication")
            connection.execute(
                """INSERT INTO team_template_revisions(
                template_id,revision,name,description,workcells_json,topology_json,sha256,
                published_by,published_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    revision.template_id,
                    revision.revision,
                    revision.name,
                    revision.description,
                    _json([item.model_dump(mode="json") for item in revision.workcells]),
                    _json(revision.topology.model_dump(mode="json")),
                    revision.sha256,
                    revision.published_by,
                    revision.published_at.isoformat(),
                ),
            )
            connection.execute(
                """UPDATE team_templates SET latest_revision=?,version=version+1,
                updated_at=? WHERE id=?""",
                (revision.revision, revision.published_at.isoformat(), revision.template_id),
            )
            _append_event(
                connection,
                ProductEvent(
                    event_type="team-template.revision-published",
                    aggregate_type="team-template",
                    aggregate_id=revision.template_id,
                    aggregate_version=revision.revision,
                    payload={"revision": revision.revision, "sha256": revision.sha256},
                ),
            )
        return revision

    def next_revision(self, template_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(MAX(revision),0)+1 FROM team_template_revisions
                WHERE template_id=?""",
                (template_id,),
            ).fetchone()
        return int(row[0])

    def get_revision(self, template_id: str, revision: int) -> TeamTemplateRevision:
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT {_REVISION_COLUMNS} FROM team_template_revisions
                WHERE template_id=? AND revision=?""",  # noqa: S608
                (template_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError(f"{template_id}:{revision}")
        return _revision(row)

    @staticmethod
    def _insert_draft(
        connection: sqlite3.Connection,
        draft: TeamTemplateDraft,
    ) -> None:
        connection.execute(
            """INSERT INTO team_template_drafts(
            id,template_id,name,description,workcells_json,topology_json,version,
            validation_status,validation_errors_json,created_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                draft.id,
                draft.template_id,
                draft.name,
                draft.description,
                _json([item.model_dump(mode="json") for item in draft.workcells]),
                _json(draft.topology.model_dump(mode="json")),
                draft.version,
                draft.validation_status,
                _json(draft.validation_errors),
                draft.created_by,
                draft.created_at.isoformat(),
                draft.updated_at.isoformat(),
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


_TEMPLATE_COLUMNS = "id,name,description,latest_revision,version,created_by,created_at,updated_at"
_DRAFT_COLUMNS = (
    "id,template_id,name,description,workcells_json,topology_json,version,"
    "validation_status,validation_errors_json,created_by,created_at,updated_at"
)
_REVISION_COLUMNS = (
    "template_id,revision,name,description,workcells_json,topology_json,sha256,"
    "published_by,published_at"
)


def _template(row: tuple[object, ...]) -> TeamTemplate:
    return TeamTemplate.model_validate(
        dict(zip(_TEMPLATE_COLUMNS.split(","), row, strict=True))
    )


def _draft(row: tuple[object, ...]) -> TeamTemplateDraft:
    values = dict(zip(_DRAFT_COLUMNS.split(","), row, strict=True))
    values["workcells"] = json.loads(str(values.pop("workcells_json")))
    values["topology"] = json.loads(str(values.pop("topology_json")))
    values["validation_errors"] = json.loads(
        str(values.pop("validation_errors_json"))
    )
    return TeamTemplateDraft.model_validate(values)


def _revision(row: tuple[object, ...]) -> TeamTemplateRevision:
    values = dict(zip(_REVISION_COLUMNS.split(","), row, strict=True))
    values["workcells"] = json.loads(str(values.pop("workcells_json")))
    values["topology"] = json.loads(str(values.pop("topology_json")))
    return TeamTemplateRevision.model_validate(values)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
            _json(event.payload),
            event.occurred_at.isoformat(),
        ),
    )
