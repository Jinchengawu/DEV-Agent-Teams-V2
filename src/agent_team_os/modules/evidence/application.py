from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from ...shared.hashes import Sha256, is_valid_sha256, sha256_json
from .domain import EvidenceKind, EvidenceRecord, EvidenceStatus, EvidenceVerificationRecord
from .ports import EvidenceRepository


class EvidenceLedger:
    def __init__(self, repository: EvidenceRepository) -> None:
        self.repository = repository

    def sync_delivery(self, snapshot: Mapping[str, object]) -> tuple[EvidenceRecord, ...]:
        delivery_id = str(snapshot["id"])
        project_id = str(snapshot.get("project_id") or "legacy-default")
        created_at = _date(snapshot.get("created_at"))
        planning_identity = str(snapshot.get("planning_identity") or "unknown")
        execution_identity = str(snapshot.get("execution_identity") or planning_identity)
        candidates: list[tuple[EvidenceKind, str, str, str | None, object]] = []

        pipeline_revision_id = _text(snapshot.get("pipeline_revision_id"))
        journey_revision_id = _text(snapshot.get("journey_revision_id"))
        journey_hash = _text(
            snapshot.get("resolved_pipeline_sha256") or snapshot.get("resolved_journey_sha256")
        )
        candidates.append(
            (
                EvidenceKind.JOURNEY,
                "pipeline-revision" if pipeline_revision_id else "journey-revision",
                str(pipeline_revision_id or journey_revision_id or f"{delivery_id}:legacy-journey"),
                journey_hash,
                {
                    "pipeline_revision_id": pipeline_revision_id,
                    "journey_revision_id": journey_revision_id,
                    "sha256": journey_hash,
                },
            )
        )
        for field, kind, source_kind in (
            ("requirements", EvidenceKind.REQUIREMENT, "requirement-artifact"),
            ("task", EvidenceKind.TASK, "task-contract"),
        ):
            artifact = snapshot.get(field)
            if isinstance(artifact, Mapping):
                artifact_payload = dict(artifact)
                candidates.append(
                    (
                        kind,
                        source_kind,
                        f"{delivery_id}:{field}",
                        str(sha256_json(artifact_payload)),
                        artifact_payload,
                    )
                )
        for field, kind in (
            ("plan_gate", EvidenceKind.PLAN_GATE),
            ("design_gate", EvidenceKind.DESIGN_GATE),
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
        repository_candidates = snapshot.get("repository_candidates")
        if isinstance(repository_candidates, (list, tuple)) and repository_candidates:
            for repository_candidate in repository_candidates:
                if not isinstance(repository_candidate, Mapping):
                    continue
                role = str(repository_candidate.get("role") or "unknown")
                candidate = repository_candidate.get("candidate")
                verification = repository_candidate.get("verification")
                if isinstance(candidate, Mapping):
                    _append_candidate_evidence(
                        candidates,
                        delivery_id,
                        candidate,
                        source_suffix=role,
                    )
                if isinstance(verification, Mapping):
                    candidates.append(
                        (
                            EvidenceKind.VERIFICATION,
                            f"verification-log:{role}",
                            f"{delivery_id}:{role}:verification",
                            _text(verification.get("log_sha256")),
                            dict(verification),
                        )
                    )
        else:
            candidate = snapshot.get("candidate")
            if isinstance(candidate, Mapping):
                _append_candidate_evidence(candidates, delivery_id, candidate)
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
        for field, kind, source_kind, hash_field in (
            (
                "release_bundle",
                EvidenceKind.RELEASE_BUNDLE,
                "release-bundle",
                "bundle_sha256",
            ),
            (
                "release_manifest",
                EvidenceKind.RELEASE_MANIFEST,
                "release-manifest",
                "manifest_sha256",
            ),
        ):
            value = snapshot.get(field)
            if isinstance(value, Mapping):
                candidates.append(
                    (
                        kind,
                        source_kind,
                        str(value.get(hash_field) or f"{delivery_id}:{field}"),
                        _text(value.get(hash_field)),
                        dict(value),
                    )
                )

        records: list[EvidenceRecord] = []
        for kind, source_kind, source_id, source_hash, candidate_payload in candidates:
            valid = is_valid_sha256(source_hash)
            record = EvidenceRecord(
                id=str(uuid5(NAMESPACE_URL, f"agent-team-os:{delivery_id}:{kind}:{source_id}")),
                project_id=project_id,
                delivery_id=delivery_id,
                kind=kind,
                source_kind=source_kind,
                source_id=source_id,
                producer_identity=(
                    planning_identity
                    if kind
                    in {
                        EvidenceKind.JOURNEY,
                        EvidenceKind.REQUIREMENT,
                        EvidenceKind.TASK,
                        EvidenceKind.PLAN_GATE,
                    }
                    else execution_identity
                ),
                content_sha256=(Sha256.validate(source_hash) if valid and source_hash else None),
                status=(EvidenceStatus.VERIFIED if valid else EvidenceStatus.INVALID),
                payload=(
                    dict(candidate_payload)
                    if isinstance(candidate_payload, Mapping)
                    else {"value": candidate_payload}
                ),
                created_at=created_at,
                verification_error=(None if valid else "MISSING_OR_ZERO_SHA256"),
            )
            records.append(self.repository.append(record))
        return tuple(records)

    def list(
        self, delivery_id: str | None = None, project_id: str | None = None
    ) -> tuple[EvidenceRecord, ...]:
        return self.repository.list(delivery_id, project_id)

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        return self.repository.get(evidence_id)

    def verify(self, evidence_id: str) -> EvidenceRecord:
        record = self.repository.get(evidence_id)
        if record is None:
            raise KeyError(evidence_id)
        if record.content_sha256 is None:
            return self.repository.append_verification(
                evidence_id, EvidenceStatus.INVALID, "MISSING_OR_ZERO_SHA256"
            )
        return self.repository.append_verification(evidence_id, EvidenceStatus.VERIFIED, None)

    def verification_history(self, evidence_id: str) -> tuple[EvidenceVerificationRecord, ...]:
        if self.repository.get(evidence_id) is None:
            raise KeyError(evidence_id)
        return self.repository.list_verifications(evidence_id)

    def record_evaluation_report(self, payload: Mapping[str, object]) -> EvidenceRecord:
        """Archive one immutable evaluation report without pretending it is Delivery evidence."""
        run_id = str(payload["run_id"])
        content_sha256 = _text(payload.get("evidence_sha256"))
        valid = is_valid_sha256(content_sha256)
        record = EvidenceRecord(
            id=str(uuid5(NAMESPACE_URL, f"agent-team-os:evaluation:{run_id}")),
            delivery_id=f"evaluation:{run_id}",
            kind=EvidenceKind.EVALUATION_REPORT,
            source_kind="evaluation-report",
            source_id=run_id,
            producer_identity=str(payload.get("proof_scope") or "unknown-evaluation-producer"),
            content_sha256=(Sha256.validate(content_sha256) if valid and content_sha256 else None),
            status=EvidenceStatus.VERIFIED if valid else EvidenceStatus.INVALID,
            payload=dict(payload),
            verification_error=None if valid else "MISSING_OR_ZERO_SHA256",
        )
        return self.repository.append(record)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _append_candidate_evidence(
    candidates: list[tuple[EvidenceKind, str, str, str | None, object]],
    delivery_id: str,
    candidate: Mapping[object, object],
    *,
    source_suffix: str | None = None,
) -> None:
    payload = {str(key): value for key, value in candidate.items()}
    suffix = "" if source_suffix is None else f":{source_suffix}"
    candidates.extend(
        (
            (
                EvidenceKind.CANDIDATE,
                f"git-candidate{suffix}",
                str(candidate.get("candidate_revision") or f"{delivery_id}{suffix}:candidate"),
                str(sha256_json(payload)),
                payload,
            ),
            (
                EvidenceKind.DIFF,
                f"unified-diff{suffix}",
                f"{delivery_id}{suffix}:diff",
                _text(candidate.get("diff_sha256")),
                {"unified_diff": candidate.get("unified_diff", "")},
            ),
        )
    )


def _date(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(UTC)
