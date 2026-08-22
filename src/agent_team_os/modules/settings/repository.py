from __future__ import annotations

import sqlite3
from pathlib import Path

from .domain import AppSettings


class SQLiteSettingsRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def get(self) -> AppSettings:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM app_settings WHERE singleton=1"
            ).fetchone()
            if row is None:
                settings = AppSettings()
                connection.execute(
                    "INSERT INTO app_settings(singleton,snapshot_json) VALUES(1,?)",
                    (settings.model_dump_json(),),
                )
                return settings
        return AppSettings.model_validate_json(row[0])

    def compare_and_swap(self, expected_version: int, updated: AppSettings) -> bool:
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                """UPDATE app_settings SET snapshot_json=?
                WHERE singleton=1 AND json_extract(snapshot_json,'$.version')=?""",
                (updated.model_dump_json(), expected_version),
            )
        return cursor.rowcount == 1

