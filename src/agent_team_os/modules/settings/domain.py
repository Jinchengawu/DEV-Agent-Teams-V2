from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AppSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(default=1, ge=1)
    planning_timeout_seconds: int = Field(default=120, ge=30, le=300)
    execution_timeout_seconds: int = Field(default=180, ge=60, le=600)
    verification_timeout_seconds: int = Field(default=60, ge=10, le=300)
    evidence_retention_days: int = Field(default=7, ge=1, le=30)
    language: Literal["zh-CN"] = "zh-CN"
    allowed_paths: tuple[str, ...] = ("src/**", "tests/**")
    verification_commands: tuple[str, ...] = (
        "python -m unittest discover -s tests -v",
    )
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AppSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    planning_timeout_seconds: int | None = Field(default=None, ge=30, le=300)
    execution_timeout_seconds: int | None = Field(default=None, ge=60, le=600)
    verification_timeout_seconds: int | None = Field(default=None, ge=10, le=300)
    evidence_retention_days: int | None = Field(default=None, ge=1, le=30)

