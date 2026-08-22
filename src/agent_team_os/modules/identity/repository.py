from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ...shared.events import ProductEvent
from ...shared.permissions import Role
from .domain import User
from .ports import UserUpdateResult


class SQLiteIdentityRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def count_users(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, user: User, password_hash: str) -> User:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO users(
                id,username,display_name,role,password_hash,enabled,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    user.id,
                    user.username,
                    user.display_name,
                    user.role.value,
                    password_hash,
                    int(user.enabled),
                    user.version,
                    user.created_at.isoformat(),
                    user.updated_at.isoformat(),
                ),
            )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="identity.user-created",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    aggregate_version=user.version,
                    payload={"username": user.username, "role": user.role.value},
                    occurred_at=user.updated_at,
                ),
            )
            connection.commit()
        return user

    def get_user(self, user_id: str) -> User | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)
            ).fetchone()
        return None if row is None else self._user(row)

    def get_user_by_username(self, username: str) -> tuple[User, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        if row is None:
            return None
        return self._user(row), str(row["password_hash"])

    def list_users(self) -> tuple[User, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY created_at,id"
            ).fetchall()
        return tuple(self._user(row) for row in rows)

    def compare_and_swap_user(
        self, expected_version: int, user: User, password_hash: str | None
    ) -> UserUpdateResult:
        assignments = [
            "display_name=?",
            "role=?",
            "enabled=?",
            "version=?",
            "updated_at=?",
        ]
        values: list[object] = [
            user.display_name,
            user.role.value,
            int(user.enabled),
            user.version,
            user.updated_at.isoformat(),
        ]
        if password_hash is not None:
            assignments.append("password_hash=?")
            values.append(password_hash)
        values.extend((user.id, expected_version))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT role,enabled,version FROM users WHERE id=?", (user.id,)
            ).fetchone()
            if current is None or int(current["version"]) != expected_version:
                connection.rollback()
                return UserUpdateResult.VERSION_CONFLICT
            removes_enabled_admin = (
                str(current["role"]) == Role.ADMINISTRATOR.value
                and bool(current["enabled"])
                and (user.role != Role.ADMINISTRATOR or not user.enabled)
            )
            if removes_enabled_admin:
                administrator_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM users "
                        "WHERE role='administrator' AND enabled=1"
                    ).fetchone()[0]
                )
                if administrator_count == 1:
                    connection.rollback()
                    return UserUpdateResult.LAST_ADMIN_REQUIRED
            result = connection.execute(
                f"UPDATE users SET {','.join(assignments)} WHERE id=? AND version=?",  # noqa: S608
                values,
            )
            if result.rowcount != 1:
                connection.rollback()
                return UserUpdateResult.VERSION_CONFLICT
            self._append_event(
                connection,
                ProductEvent(
                    event_type="identity.user-updated",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    aggregate_version=user.version,
                    payload={"role": user.role.value, "enabled": user.enabled},
                    occurred_at=user.updated_at,
                ),
            )
            connection.commit()
        return UserUpdateResult.UPDATED

    def create_session(
        self,
        session_id: str,
        user_id: str,
        bearer_hash: str,
        csrf_hash: str,
        expires_at: datetime,
        created_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO sessions(
                id,user_id,bearer_hash,expires_at,created_at,csrf_hash)
                VALUES(?,?,?,?,?,?)""",
                (
                    session_id,
                    user_id,
                    bearer_hash,
                    expires_at.isoformat(),
                    created_at.isoformat(),
                    csrf_hash,
                ),
            )
            self._append_event(
                connection,
                ProductEvent(
                    event_type="identity.session-created",
                    aggregate_type="user",
                    aggregate_id=user_id,
                    aggregate_version=1,
                    payload={"session_id": session_id, "expires_at": expires_at.isoformat()},
                    occurred_at=created_at,
                ),
            )
            connection.commit()

    def resolve_session(self, bearer_hash: str, now: datetime) -> tuple[User, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT users.*,sessions.csrf_hash AS session_csrf_hash
                FROM sessions JOIN users ON users.id=sessions.user_id
                WHERE sessions.bearer_hash=? AND sessions.expires_at>? AND users.enabled=1""",
                (bearer_hash, now.isoformat()),
            ).fetchone()
        if row is None:
            return None
        return self._user(row), str(row["session_csrf_hash"])

    def revoke_session(self, bearer_hash: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE bearer_hash=?", (bearer_hash,))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _user(row: sqlite3.Row) -> User:
        return User(
            id=str(row["id"]),
            username=str(row["username"]),
            display_name=str(row["display_name"]),
            role=Role(str(row["role"])),
            enabled=bool(row["enabled"]),
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
