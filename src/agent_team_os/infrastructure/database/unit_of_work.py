from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import TracebackType

from ...shared.events import ProductEvent


class SQLiteUnitOfWork:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> SQLiteUnitOfWork:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE")
        self.connection = connection
        return self

    def append(self, event: ProductEvent) -> None:
        if self.connection is None:
            raise RuntimeError("UnitOfWork is not active")
        self.connection.execute(
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

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self.connection is None:
            return
        if exception_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()
        self.connection = None

