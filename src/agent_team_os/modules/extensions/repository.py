from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .domain import RuntimeExtension


class SQLiteRuntimeExtensionRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create(self, extension: RuntimeExtension) -> RuntimeExtension:
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO runtime_extensions({_COLUMNS}) "  # noqa: S608
                f"VALUES({','.join('?' * 14)})",
                _values(extension),
            )
        return extension

    def get(self, extension_id: str) -> RuntimeExtension:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM runtime_extensions WHERE id=?",  # noqa: S608
                (extension_id,),
            ).fetchone()
        if row is None:
            raise KeyError(extension_id)
        return _extension(row)

    def list(self) -> tuple[RuntimeExtension, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM runtime_extensions ORDER BY kind,id"  # noqa: S608
            ).fetchall()
        return tuple(_extension(row) for row in rows)

    def compare_and_swap(self, expected_version: int, updated: RuntimeExtension) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE runtime_extensions SET
                status=?,qualification_sha256=?,qualification_errors_json=?,
                version=?,updated_at=? WHERE id=? AND version=?""",
                (
                    updated.status,
                    updated.qualification_sha256,
                    _json(updated.qualification_errors),
                    updated.version,
                    updated.updated_at.isoformat(),
                    updated.id,
                    expected_version,
                ),
            )
        return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


_COLUMNS = """id,name,kind,extension_version,source_uri,revision_sha256,
requested_permissions_json,status,qualification_sha256,qualification_errors_json,
version,created_by,created_at,updated_at"""


def _values(extension: RuntimeExtension) -> tuple[object, ...]:
    return (
        extension.id,
        extension.name,
        extension.kind,
        extension.version_label,
        extension.source_uri,
        extension.revision_sha256,
        _json(extension.requested_permissions),
        extension.status,
        extension.qualification_sha256,
        _json(extension.qualification_errors),
        extension.version,
        extension.created_by,
        extension.created_at.isoformat(),
        extension.updated_at.isoformat(),
    )


def _extension(row: tuple[object, ...]) -> RuntimeExtension:
    keys = tuple(item.strip() for item in _COLUMNS.replace("\n", "").split(","))
    values = dict(zip(keys, row, strict=True))
    values["version_label"] = values.pop("extension_version")
    values["requested_permissions"] = json.loads(str(values.pop("requested_permissions_json")))
    values["qualification_errors"] = json.loads(str(values.pop("qualification_errors_json")))
    return RuntimeExtension.model_validate(values)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
