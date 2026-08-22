from __future__ import annotations

from typing import Protocol

from .domain import EvidenceRecord, EvidenceStatus


class EvidenceRepository(Protocol):
    def append(self, record: EvidenceRecord) -> EvidenceRecord: ...

    def get(self, evidence_id: str) -> EvidenceRecord | None: ...

    def list(self, delivery_id: str | None = None) -> tuple[EvidenceRecord, ...]: ...

    def append_verification(
        self, evidence_id: str, status: EvidenceStatus, error: str | None
    ) -> EvidenceRecord: ...

