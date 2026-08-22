from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...shared.permissions import Role


class User(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    username: str
    display_name: str
    role: Role
    enabled: bool
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    role: Role
    password: str = Field(min_length=12, max_length=256)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, value: str) -> str:
        if not any(character.isalpha() for character in value) or not any(
            character.isdigit() for character in value
        ):
            raise ValueError("密码必须同时包含字母和数字")
        return value


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(default="admin", min_length=3, max_length=64)
    display_name: str = Field(default="系统管理员", min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: Role | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user: User
    bearer: str
    csrf_token: str
    expires_at: datetime
