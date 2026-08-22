from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from ...shared.hashes import Sha256, is_valid_sha256, sha256_json
from .domain import EvidenceKind, EvidenceRecord, EvidenceStatus
from .ports import EvidenceRepository


class EvidenceLedger:
    def __init__(self, repository: EvidenceRepository) -> None:
        self.repository = repository

    def sync_delivery(self, snapshot: Mapping[str, object]) -> tuple[EvidenceRecord, ...]:
        delivery_id = str(snapshot["id"])
        created_at = _date(snapshot.get("created_at"))
        planning_identity = str(snapshot.get("planning_identity") or "unknown")
        execution_identity = str(snapshot.get("execution_identity") or planning_identity)
        candidates: list[tuple[EvidenceKind, str, str, str | None, object]] = []

        pipeline_revision_id = _text(snapshot.get("pipeline_revision_id"))
        journey_revision_id = _text(snapshot.get("journey_revision_id"))
        journey_hash = _text(
            snapshot.get("resolved_pipeline_sha256")
            or snapshot.get("resolved_journey_sha256")
        )
        candidates.append(
            (
                EvidenceKind.JOURNEY,
                "pipeline-revision" if pipeline_revision_id else "journey-revision",
                str(
                    pipeline_revision_id
                    or journey_revision_id
                    or f"{delivery_id}:legacy-journey"
                ),
                journey_hash,
                {
                    "pipeline_revision_id": pipeline_revision_id,
                    "journey_revision_id": journey_revision_id,
                    "sha256": journey_hash,
                },
            )
        )
        for field, kind in (
            ("plan_gate", EvidenceKind.PLAN_GATE),
            ("candidate_gate", EvidenceKind.CANDIDATE_GATE),
        ):
            gate = snapshot.get(field)
            if isinstance(gate, Mapping):
                candidates.append(
                    (
                        kind,
                        "gate-subject",
                        str(gate.get("artifact_id") or f"{delivery_id}:{field}"),
                        _text(gate.get("subject_sha256")),
                        dict(gate),
                    )
                )
        candidate = snapshot.get("candidate")
        if isinstance(candidate, Mapping):
            candidate_payload = dict(candidate)
            candidates.extend(
                (
                    (
                        EvidenceKind.CANDIDATE,
                        "git-candidate",
                        str(candidate.get("candidate_revision") or f"{delivery_id}:candidate"),
                        str(sha256_json(candidate_payload)),
                        candidate_payload,
                    ),
                    (
                        EvidenceKind.DIFF,
                        "unified-diff",
                        f"{delivery_id}:diff",
                        _text(candidate.get("diff_sha256")),
                        {"unified_diff": candidate.get("unified_diff", "")},
                    ),
                )
            )
        verification = snapshot.get("verification")
        if isinstance(verification, Mapping):
            candidates.append(
                (
                    EvidenceKind.VERIFICATION,
                    "verification-log",
                    f"{delivery_id}:verification",
                    _text(verification.get("log_sha256")),
                    dict(verification),
                )
            )
        receipt = snapshot.get("apply_receipt")
        if isinstance(receipt, Mapping):
            receipt_payload = dict(receipt)
            candidates.append(
                (
                    EvidenceKind.APPLY_RECEIPT,
                    "apply-receipt",
                    f"{delivery_id}:receipt",
                    str(sha256_json(receipt_payload)),
                    receipt_payload,
                )
            )

        records: list[EvidenceRecord] = []
        for kind, source_kind, source_id, source_hash, payload in candidates:
            valid = is_valid_sha256(source_hash)
            record = EvidenceRecord(
                id=str(uuid5(NAMESPACE_URL, f"agent-team-os:{delivery_id}:{kind}:{source_id}")),
                delivery_id=delivery_id,
                kind=kind,
                source_kind=source_kind,
                source_id=source_id,
                producer_identity=(
                    planning_identity
                    if kind in {EvidenceKind.JOURNEY, EvidenceKind.PLAN_GATE}
                    else execution_identity
                ),
                content_sha256=(Sha256.validate(source_hash) if valid and source_hash else None),
                status=(EvidenceStatus.VERIFIED if valid else EvidenceStatus.INVALID),
                payload=(dict(payload) if isinstance(payload, Mapping) else {"value": payload}),
                created_at=created_at,
                verification_error=(None if valid else "MISSING_OR_ZERO_SHA256"),
            )
            records.append(self.repository.append(record))
        return tuple(records)

    def list(self, delivery_id: str | None = None) -> tuple[EvidenceRecord, ...]:
        return self.repository.list(delivery_id)

    def verify(self, evidence_id: str) -> EvidenceRecord:
        record = self.repository.get(evidence_id)
        if record is None:
            raise KeyError(evidence_id)
        if record.content_sha256 is None:
            return self.repository.append_verification(
                evidence_id, EvidenceStatus.INVALID, "MISSING_OR_ZERO_SHA256"
            )
        return self.repository.append_verification(evidence_id, EvidenceStatus.VERIFIED, None)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _date(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(UTC)
