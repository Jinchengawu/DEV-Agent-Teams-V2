from __future__ import annotations

import sqlite3
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ...shared.hashes import Sha256


class RoleDocumentPublicationRequest(BaseModel):
    """Delivery-owned DTO for publishing a validated role Artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(min_length=1)
    delivery_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    binding_site: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_key: str = Field(min_length=1, max_length=120)
    contract_id: str = Field(min_length=1, max_length=160)
    artifact_sha256: Sha256
    runtime_identity: str | None = None
    required: bool = True

    @property
    def publication_key(self) -> str:
        return ":".join(
            (
                self.delivery_id,
                self.node_id,
                self.binding_site,
                self.contract_id,
                self.artifact_key,
            )
        )


class RoleDocumentPublicationPort(Protocol):
    """Pipeline seam; implementations may persist and publish through Knowledge."""

    def register_on(
        self,
        connection: sqlite3.Connection,
        request: RoleDocumentPublicationRequest,
    ) -> object: ...


class RoleDocumentPublisher(Protocol):
    def publish_required(self, delivery_id: str) -> bool: ...


class PublicationBarrier(Protocol):
    """Answers whether the next Gate/completion transition may be opened."""

    def is_satisfied(self, delivery_id: str) -> bool: ...

    def has_recoverable(self, delivery_id: str) -> bool: ...

    def has_publications(self, delivery_id: str) -> bool: ...
