from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .domain import User


class UserUpdateResult(StrEnum):
    UPDATED = "updated"
    VERSION_CONFLICT = "version-conflict"
    LAST_ADMIN_REQUIRED = "last-admin-required"


class IdentityRepository(Protocol):
    def count_users(self) -> int: ...

    def create_user(self, user: User, password_hash: str) -> User: ...

    def get_user(self, user_id: str) -> User | None: ...

    def get_user_by_username(self, username: str) -> tuple[User, str] | None: ...

    def list_users(self) -> tuple[User, ...]: ...

    def compare_and_swap_user(
        self, expected_version: int, user: User, password_hash: str | None
    ) -> UserUpdateResult: ...

    def create_session(
        self,
        session_id: str,
        user_id: str,
        bearer_hash: str,
        csrf_hash: str,
        expires_at: datetime,
        created_at: datetime,
    ) -> None: ...

    def resolve_session(self, bearer_hash: str, now: datetime) -> tuple[User, str] | None: ...

    def revoke_session(self, bearer_hash: str) -> None: ...
