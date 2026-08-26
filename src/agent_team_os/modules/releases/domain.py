from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...delivery import RepositoryApplyReceipt
from ...shared.hashes import Sha256


class ReleaseApplyAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: str
    project_id: str
    bundle_sha256: Sha256
    status: Literal["applying", "completed", "compensating", "compensated", "needs_attention"]
    receipts: tuple[RepositoryApplyReceipt, ...] = ()
    error_code: str | None = None
    version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
