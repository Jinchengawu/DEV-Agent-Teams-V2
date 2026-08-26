from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

RepositoryRole = Literal["backend", "design", "frontend", "qa"]


class RepositorySnapshot(BaseModel):
    """Immutable repository identity frozen into a Delivery execution context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: RepositoryRole
    workspace_ref: str
    repository_ref: str
    seed_revision: str
