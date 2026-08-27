from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

from ...shared.events import ProductEvent
from .domain import Pipeline, PipelineDraft, PipelineRevision, PipelineRunRecord


class SQLitePipelineRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(self, pipeline: Pipeline, draft: PipelineDraft) -> None:
        event = ProductEvent(
            event_type="pipeline.created",
            aggregate_type="pipeline",
            aggregate_id=pipeline.id,
            aggregate_version=pipeline.version,
            payload={"draft_id": draft.id, "name": pipeline.name},
        )
        with sqlite3.connect(self.database, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO pipelines(
                id,name,description,active_revision,version,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    pipeline.id,
                    pipeline.name,
                    pipeline.description,
                    pipeline.active_revision,
                    pipeline.version,
                    pipeline.created_by,
                    pipeline.created_at.isoformat(),
                    pipeline.updated_at.isoformat(),
                ),
            )
            connection.execute(
                """INSERT INTO pipeline_drafts(
                id,pipeline_id,name,definition_json,layout_json,input_schema_json,
                agent_assignments_json,version,
                validation_status,validation_errors_json,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    draft.id,
                    draft.pipeline_id,
                    draft.name,
                    _json(draft.definition),
                    _json(draft.layout),
                    _json(draft.input_schema),
                    _json(draft.agent_assignments),
                    draft.version,
                    draft.validation_status,
                    _json(draft.validation_errors),
                    draft.created_by,
                    draft.created_at.isoformat(),
                    draft.updated_at.isoformat(),
                ),
            )
            connection.execute(
                """INSERT INTO product_events(
                event_id,event_type,aggregate_type,aggregate_id,aggregate_version,payload_json,
                occurred_at) VALUES(?,?,?,?,?,?,?)""",
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

    def list_pipelines(self) -> tuple[Pipeline, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT id,name,description,active_revision,version,created_by,
                created_at,updated_at
                FROM pipelines ORDER BY id"""
            ).fetchall()
        return tuple(
            Pipeline.model_validate(dict(zip(_PIPELINE_FIELDS, row, strict=True)))
            for row in rows
        )

    def get_pipeline(self, pipeline_id: str) -> Pipeline:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT id,name,description,active_revision,version,created_by,
                created_at,updated_at
                FROM pipelines WHERE id=?""",
                (pipeline_id,),
            ).fetchone()
        if row is None:
            raise KeyError(pipeline_id)
        return Pipeline.model_validate(dict(zip(_PIPELINE_FIELDS, row, strict=True)))

    def get_draft(self, draft_id: str) -> PipelineDraft:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT id,pipeline_id,name,definition_json,layout_json,input_schema_json,
                agent_assignments_json,version,validation_status,validation_errors_json,
                created_by,created_at,updated_at
                FROM pipeline_drafts WHERE id=?""",
                (draft_id,),
            ).fetchone()
        if row is None:
            raise KeyError(draft_id)
        values = dict(zip(_DRAFT_FIELDS, row, strict=True))
        for field in (
            "definition", "layout", "input_schema", "agent_assignments", "validation_errors"
        ):
            values[field] = json.loads(str(values[field]))
        return PipelineDraft.model_validate(values)

    def list_drafts(self, pipeline_id: str) -> tuple[PipelineDraft, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT id,pipeline_id,name,definition_json,layout_json,input_schema_json,
                agent_assignments_json,version,validation_status,validation_errors_json,
                created_by,created_at,updated_at
                FROM pipeline_drafts WHERE pipeline_id=? ORDER BY updated_at DESC,id""",
                (pipeline_id,),
            ).fetchall()
        drafts: list[PipelineDraft] = []
        for row in rows:
            values = dict(zip(_DRAFT_FIELDS, row, strict=True))
            for field in (
                "definition", "layout", "input_schema", "agent_assignments", "validation_errors"
            ):
                values[field] = json.loads(str(values[field]))
            drafts.append(PipelineDraft.model_validate(values))
        return tuple(drafts)

    def compare_and_swap_draft(
        self, expected_version: int, updated: PipelineDraft
    ) -> bool:
        event = ProductEvent(
            event_type=(
                "pipeline.draft-updated"
                if updated.validation_status == "unknown"
                else "pipeline.draft-validated"
            ),
            aggregate_type="pipeline",
            aggregate_id=updated.pipeline_id,
            aggregate_version=updated.version,
            payload={
                "draft_id": updated.id,
                "validation_status": updated.validation_status,
            },
        )
        with sqlite3.connect(self.database, timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE pipeline_drafts SET name=?,definition_json=?,layout_json=?,
                input_schema_json=?,agent_assignments_json=?,version=?,validation_status=?,
                validation_errors_json=?,
                updated_at=?
                WHERE id=? AND version=?""",
                (
                    updated.name,
                    _json(updated.definition),
                    _json(updated.layout),
                    _json(updated.input_schema),
                    _json(updated.agent_assignments),
                    updated.version,
                    updated.validation_status,
                    _json(updated.validation_errors),
                    updated.updated_at.isoformat(),
                    updated.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            _append_event(connection, event)
        return True


    def publish(
        self,
        draft: PipelineDraft,
        *,
        compiled_graph: dict[str, object],
        binding_snapshot: dict[str, dict[str, object]],
        binding_model: Literal["legacy-v0", "provider-v1"],
        resolved_provider_bindings: dict[str, dict[str, object]],
        fingerprint: str,
        published_by: str,
    ) -> PipelineRevision:
        with sqlite3.connect(self.database, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT version,validation_status FROM pipeline_drafts WHERE id=?",
                (draft.id,),
            ).fetchone()
            if current != (draft.version, "valid"):
                raise sqlite3.IntegrityError("pipeline draft changed before publication")
            row = connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM pipeline_revisions WHERE pipeline_id=?",
                (draft.pipeline_id,),
            ).fetchone()
            revision = PipelineRevision(
                pipeline_id=draft.pipeline_id,
                revision=int(row[0]),
                definition=draft.definition,
                compiled_graph=compiled_graph,
                binding_snapshot=binding_snapshot,
                binding_model=binding_model,
                resolved_provider_bindings=resolved_provider_bindings,
                fingerprint=fingerprint,
                published_by=published_by,
            )
            connection.execute(
                """INSERT INTO pipeline_revisions(
                pipeline_id,revision,definition_json,compiled_graph_json,binding_snapshot_json,
                binding_model,resolved_provider_bindings_json,fingerprint,published_by,published_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    revision.pipeline_id,
                    revision.revision,
                    _json(revision.definition),
                    _json(revision.compiled_graph),
                    _json(revision.binding_snapshot),
                    revision.binding_model,
                    _json(revision.resolved_provider_bindings),
                    revision.fingerprint,
                    revision.published_by,
                    revision.published_at.isoformat(),
                ),
            )
            _append_event(
                connection,
                ProductEvent(
                    event_type="pipeline.revision-published",
                    aggregate_type="pipeline",
                    aggregate_id=revision.pipeline_id,
                    aggregate_version=revision.revision,
                    payload={
                        "revision": revision.revision,
                        "fingerprint": revision.fingerprint,
                    },
                ),
            )
        return revision

    def get_revision(self, pipeline_id: str, revision: int) -> PipelineRevision:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT pipeline_id,revision,definition_json,compiled_graph_json,
                binding_snapshot_json,binding_model,resolved_provider_bindings_json,
                fingerprint,published_by,published_at
                FROM pipeline_revisions WHERE pipeline_id=? AND revision=?""",
                (pipeline_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError(f"{pipeline_id}:{revision}")
        values = dict(zip(_REVISION_FIELDS, row, strict=True))
        for field in (
            "definition", "compiled_graph", "binding_snapshot", "resolved_provider_bindings"
        ):
            values[field] = json.loads(str(values[field]))
        return PipelineRevision.model_validate(values)

    def compare_and_swap_pipeline(
        self, expected_version: int, updated: Pipeline, *, activated_by: str
    ) -> bool:
        event = ProductEvent(
            event_type="pipeline.revision-activated",
            aggregate_type="pipeline",
            aggregate_id=updated.id,
            aggregate_version=updated.version,
            payload={
                "revision": updated.active_revision,
                "activated_by": activated_by,
            },
        )
        with sqlite3.connect(self.database, timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE pipelines SET active_revision=?,version=?,updated_at=?
                WHERE id=? AND version=?""",
                (
                    updated.active_revision,
                    updated.version,
                    updated.updated_at.isoformat(),
                    updated.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            _append_event(connection, event)
        return True


class SQLitePipelineRunRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(self, run: PipelineRunRecord, event: ProductEvent) -> None:
        pipeline_id, revision = _split_revision(run.pipeline_revision_id)
        with sqlite3.connect(self.database, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO pipeline_runs(
                id,delivery_id,pipeline_id,pipeline_revision,graph_fingerprint,status,
                version,snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    run.id,
                    run.delivery_id,
                    pipeline_id,
                    revision,
                    run.graph_fingerprint,
                    run.status,
                    run.version,
                    _json(run.snapshot),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            _append_pipeline_run_event(connection, event)

    def get(self, run_id: str) -> PipelineRunRecord:
        return self._find("id", run_id)

    def get_for_delivery(self, delivery_id: str) -> PipelineRunRecord:
        return self._find("delivery_id", delivery_id)

    def get_on(
        self, connection: sqlite3.Connection, run_id: str
    ) -> PipelineRunRecord:
        return self._find_on(connection, "id", run_id)

    def _find(self, field: str, value: str) -> PipelineRunRecord:
        if field not in {"id", "delivery_id"}:
            raise ValueError("Unsafe pipeline run lookup")
        with sqlite3.connect(self.database) as connection:
            return self._find_on(connection, field, value)

    @staticmethod
    def _find_on(
        connection: sqlite3.Connection, field: str, value: str
    ) -> PipelineRunRecord:
        if field not in {"id", "delivery_id"}:
            raise ValueError("Unsafe pipeline run lookup")
        row = connection.execute(
            f"""SELECT id,delivery_id,pipeline_id,pipeline_revision,
            graph_fingerprint,status,version,snapshot_json,created_at,updated_at
            FROM pipeline_runs WHERE {field}=?""",  # noqa: S608
            (value,),
        ).fetchone()
        if row is None:
            raise KeyError(value)
        values = dict(zip(_PIPELINE_RUN_FIELDS, row, strict=True))
        values["pipeline_revision_id"] = (
            f"{values.pop('pipeline_id')}:{values.pop('pipeline_revision')}"
        )
        values["snapshot"] = json.loads(str(values["snapshot"]))
        return PipelineRunRecord.model_validate(values)

    def compare_and_swap(
        self, expected_version: int, run: PipelineRunRecord, event: ProductEvent
    ) -> bool:
        with sqlite3.connect(self.database, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            return self.compare_and_swap_on(
                connection, expected_version, run, event
            )

    def compare_and_swap_on(
        self,
        connection: sqlite3.Connection,
        expected_version: int,
        run: PipelineRunRecord,
        event: ProductEvent,
    ) -> bool:
        cursor = connection.execute(
            """UPDATE pipeline_runs SET status=?,version=?,snapshot_json=?,updated_at=?
            WHERE id=? AND version=?""",
            (
                run.status,
                run.version,
                _json(run.snapshot),
                run.updated_at.isoformat(),
                run.id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            return False
        _append_pipeline_run_event(connection, event)
        return True

    def list_events(self, run_id: str) -> tuple[ProductEvent, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT id,event_type,aggregate_version,payload_json,occurred_at
                FROM pipeline_run_events WHERE pipeline_run_id=?
                ORDER BY occurred_at,id""",
                (run_id,),
            ).fetchall()
        return tuple(
            ProductEvent(
                id=str(row[0]),
                event_type=str(row[1]),
                aggregate_type="pipeline-run",
                aggregate_id=run_id,
                aggregate_version=int(row[2]),
                payload=json.loads(str(row[3])),
                occurred_at=datetime.fromisoformat(str(row[4])),
            )
            for row in rows
        )


_PIPELINE_FIELDS = (
    "id",
    "name",
    "description",
    "active_revision",
    "version",
    "created_by",
    "created_at",
    "updated_at",
)
_DRAFT_FIELDS = (
    "id",
    "pipeline_id",
    "name",
    "definition",
    "layout",
    "input_schema",
    "agent_assignments",
    "version",
    "validation_status",
    "validation_errors",
    "created_by",
    "created_at",
    "updated_at",
)
_REVISION_FIELDS = (
    "pipeline_id",
    "revision",
    "definition",
    "compiled_graph",
    "binding_snapshot",
    "binding_model",
    "resolved_provider_bindings",
    "fingerprint",
    "published_by",
    "published_at",
)
_PIPELINE_RUN_FIELDS = (
    "id",
    "delivery_id",
    "pipeline_id",
    "pipeline_revision",
    "graph_fingerprint",
    "status",
    "version",
    "snapshot",
    "created_at",
    "updated_at",
)


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


def _append_pipeline_run_event(
    connection: sqlite3.Connection, event: ProductEvent
) -> None:
    connection.execute(
        """INSERT INTO pipeline_run_events(
        id,pipeline_run_id,event_type,aggregate_version,payload_json,occurred_at)
        VALUES(?,?,?,?,?,?)""",
        (
            event.id,
            event.aggregate_id,
            event.event_type,
            event.aggregate_version,
            _json(event.payload),
            event.occurred_at.isoformat(),
        ),
    )


def _split_revision(reference: str) -> tuple[str, int]:
    pipeline_id, separator, revision = reference.rpartition(":")
    if not separator:
        raise ValueError("Invalid pipeline revision reference")
    return pipeline_id, int(revision)
