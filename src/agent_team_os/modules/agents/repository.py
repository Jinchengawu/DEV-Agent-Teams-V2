from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ...shared.events import ProductEvent
from .domain import AgentProfile, AgentProfileDraft, AgentProfileRevision


class SQLiteAgentProfileRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(self, profile: AgentProfile, draft: AgentProfileDraft) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO agent_profiles(
                id,name,description,tags_json,latest_revision,version,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    profile.id,
                    profile.name,
                    profile.description,
                    _json(profile.tags),
                    profile.latest_revision,
                    profile.version,
                    profile.created_by,
                    profile.created_at.isoformat(),
                    profile.updated_at.isoformat(),
                ),
            )
            connection.execute(
                """INSERT INTO agent_profile_drafts(
                profile_id,spec_json,version,validation_status,validation_errors_json,
                updated_by,updated_at) VALUES(?,?,?,?,?,?,?)""",
                (
                    draft.profile_id,
                    draft.spec.model_dump_json(),
                    draft.version,
                    draft.validation_status,
                    _json(draft.validation_errors),
                    draft.updated_by,
                    draft.updated_at.isoformat(),
                ),
            )
            _append_event(
                connection,
                ProductEvent(
                    event_type="agent-profile.created",
                    aggregate_type="agent-profile",
                    aggregate_id=profile.id,
                    aggregate_version=profile.version,
                    payload={"draft_version": draft.version},
                ),
            )

    def list_profiles(self) -> tuple[AgentProfile, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,name,description,tags_json,latest_revision,version,created_by,
                created_at,updated_at FROM agent_profiles ORDER BY id"""
            ).fetchall()
        return tuple(_profile(row) for row in rows)

    def get_profile(self, profile_id: str) -> AgentProfile:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id,name,description,tags_json,latest_revision,version,created_by,
                created_at,updated_at FROM agent_profiles WHERE id=?""",
                (profile_id,),
            ).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return _profile(row)

    def get_draft(self, profile_id: str) -> AgentProfileDraft:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT profile_id,spec_json,version,validation_status,
                validation_errors_json,updated_by,updated_at
                FROM agent_profile_drafts WHERE profile_id=?""",
                (profile_id,),
            ).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return _draft(row)

    def compare_and_swap_draft(self, expected_version: int, updated: AgentProfileDraft) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE agent_profile_drafts SET spec_json=?,version=?,validation_status=?,
                validation_errors_json=?,updated_by=?,updated_at=?
                WHERE profile_id=? AND version=?""",
                (
                    updated.spec.model_dump_json(),
                    updated.version,
                    updated.validation_status,
                    _json(updated.validation_errors),
                    updated.updated_by,
                    updated.updated_at.isoformat(),
                    updated.profile_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            _append_event(
                connection,
                ProductEvent(
                    event_type="agent-profile.draft-updated",
                    aggregate_type="agent-profile",
                    aggregate_id=updated.profile_id,
                    aggregate_version=updated.version,
                    payload={"validation_status": updated.validation_status},
                ),
            )
        return True

    def publish(
        self,
        draft: AgentProfileDraft,
        revision: AgentProfileRevision,
    ) -> AgentProfileRevision:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT version,validation_status FROM agent_profile_drafts WHERE profile_id=?",
                (draft.profile_id,),
            ).fetchone()
            if current != (draft.version, "valid"):
                raise sqlite3.IntegrityError("agent profile draft changed before publication")
            connection.execute(
                """INSERT INTO agent_profile_revisions(
                profile_id,revision,spec_json,canonical_json,sha256,published_by,published_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    revision.profile_id,
                    revision.revision,
                    revision.spec.model_dump_json(),
                    revision.canonical_json,
                    revision.sha256,
                    revision.published_by,
                    revision.published_at.isoformat(),
                ),
            )
            connection.execute(
                """UPDATE agent_profiles SET name=?,description=?,tags_json=?,latest_revision=?,
                version=version+1,updated_at=? WHERE id=?""",
                (
                    draft.spec.name,
                    draft.spec.description,
                    _json(draft.spec.tags),
                    revision.revision,
                    revision.published_at.isoformat(),
                    draft.profile_id,
                ),
            )
            _append_event(
                connection,
                ProductEvent(
                    event_type="agent-profile.revision-published",
                    aggregate_type="agent-profile",
                    aggregate_id=revision.profile_id,
                    aggregate_version=revision.revision,
                    payload={"revision": revision.revision, "sha256": revision.sha256},
                ),
            )
        return revision

    def next_revision(self, profile_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(MAX(revision),0)+1
                FROM agent_profile_revisions WHERE profile_id=?""",
                (profile_id,),
            ).fetchone()
        return int(row[0])

    def list_revisions(self, profile_id: str) -> tuple[AgentProfileRevision, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT profile_id,revision,spec_json,canonical_json,sha256,published_by,
                published_at FROM agent_profile_revisions WHERE profile_id=? ORDER BY revision""",
                (profile_id,),
            ).fetchall()
        return tuple(_revision(row) for row in rows)

    def get_revision(self, profile_id: str, revision: int) -> AgentProfileRevision:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT profile_id,revision,spec_json,canonical_json,sha256,published_by,
                published_at FROM agent_profile_revisions WHERE profile_id=? AND revision=?""",
                (profile_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError(f"{profile_id}:{revision}")
        return _revision(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _profile(row: tuple[object, ...]) -> AgentProfile:
    keys = (
        "id",
        "name",
        "description",
        "tags",
        "latest_revision",
        "version",
        "created_by",
        "created_at",
        "updated_at",
    )
    values = dict(zip(keys, row, strict=True))
    values["tags"] = json.loads(str(values["tags"]))
    return AgentProfile.model_validate(values)


def _draft(row: tuple[object, ...]) -> AgentProfileDraft:
    keys = (
        "profile_id",
        "spec",
        "version",
        "validation_status",
        "validation_errors",
        "updated_by",
        "updated_at",
    )
    values = dict(zip(keys, row, strict=True))
    values["spec"] = json.loads(str(values["spec"]))
    values["validation_errors"] = json.loads(str(values["validation_errors"]))
    return AgentProfileDraft.model_validate(values)


def _revision(row: tuple[object, ...]) -> AgentProfileRevision:
    keys = (
        "profile_id",
        "revision",
        "spec",
        "canonical_json",
        "sha256",
        "published_by",
        "published_at",
    )
    values = dict(zip(keys, row, strict=True))
    values["spec"] = json.loads(str(values["spec"]))
    return AgentProfileRevision.model_validate(values)


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
