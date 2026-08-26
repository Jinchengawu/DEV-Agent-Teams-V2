from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Never, Protocol, cast

from ...delivery import (
    ApplyReceipt,
    CandidateChange,
    ReleaseBundle,
    ReleaseManifest,
    RepositoryApplyReceipt,
)
from ...shared.hashes import sha256_json
from ...shared.repositories import RepositoryRole
from .domain import ReleaseApplyAttempt
from .repository import SQLiteReleaseRepository


class ReleaseWorkspaceApplier(Protocol):
    async def apply(self, candidate: CandidateChange, workspace_id: str) -> ApplyReceipt: ...

    async def rollback(self, receipt: ApplyReceipt, workspace_id: str) -> str: ...

    def revision(self, workspace_id: str) -> str: ...


class ReleaseApplyError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ReleaseCoordinator:
    """Coordinate recoverable multi-repository release behind one interface.

    Git refs cannot be atomically updated across repositories. The active
    ReleaseManifest is therefore the product authority; partial Git updates are
    compensated with compare-and-swap rollback before a manifest may activate.
    """

    def __init__(
        self, repository: SQLiteReleaseRepository, applier: ReleaseWorkspaceApplier
    ) -> None:
        self.repository = repository
        self.applier = applier

    async def apply(self, bundle: ReleaseBundle) -> ReleaseManifest:
        existing_manifest = self.repository.get_manifest(bundle.project_id)
        if (
            existing_manifest is not None
            and existing_manifest.delivery_id == bundle.delivery_id
            and existing_manifest.bundle_sha256 == bundle.bundle_sha256
        ):
            return existing_manifest
        attempt = self.repository.get_attempt(bundle.delivery_id)
        if attempt is None:
            self._preflight(bundle)
            attempt = ReleaseApplyAttempt(
                delivery_id=bundle.delivery_id,
                project_id=bundle.project_id,
                bundle_sha256=bundle.bundle_sha256,
                status="applying",
                version=1,
            )
            self.repository.put_attempt(attempt, expected_version=None)
        elif attempt.bundle_sha256 != bundle.bundle_sha256:
            raise ReleaseApplyError(
                "RELEASE_BUNDLE_CHANGED",
                "existing release attempt references another bundle",
            )
        if attempt.status == "completed":
            manifest = self.repository.get_manifest(bundle.project_id)
            if manifest is None:
                raise ReleaseApplyError(
                    "RELEASE_MANIFEST_MISSING", "completed attempt has no active manifest"
                )
            return manifest
        if attempt.status in {"compensated", "needs_attention"}:
            raise ReleaseApplyError("RELEASE_ATTEMPT_NOT_RESUMABLE", f"release is {attempt.status}")
        return await self._continue(bundle, attempt)

    def _preflight(self, bundle: ReleaseBundle) -> None:
        for item in bundle.candidates:
            actual = self.applier.revision(item.workspace_ref)
            if actual != item.candidate.base_revision:
                raise ReleaseApplyError(
                    "REPOSITORY_BASE_REVISION_CHANGED",
                    f"{item.role} Main no longer equals the reviewed base",
                )

    async def _continue(
        self, bundle: ReleaseBundle, attempt: ReleaseApplyAttempt
    ) -> ReleaseManifest:
        receipts = {item.role: item for item in attempt.receipts}
        try:
            for item in bundle.candidates:
                if item.role in receipts:
                    current = self.applier.revision(item.workspace_ref)
                    if current != item.candidate.candidate_revision:
                        raise ReleaseApplyError(
                            "RELEASE_RECOVERY_REVISION_CONFLICT",
                            f"{item.role} no longer equals the recorded candidate",
                        )
                    continue
                current = self.applier.revision(item.workspace_ref)
                if current == item.candidate.candidate_revision:
                    receipt = ApplyReceipt(
                        before_revision=item.candidate.base_revision,
                        candidate_revision=item.candidate.candidate_revision,
                        after_revision=item.candidate.candidate_revision,
                        result="applied",
                        recovered=True,
                    )
                elif current == item.candidate.base_revision:
                    receipt = await self.applier.apply(item.candidate, item.workspace_ref)
                else:
                    raise ReleaseApplyError(
                        "RELEASE_RECOVERY_REVISION_CONFLICT",
                        f"{item.role} Main is neither base nor candidate",
                    )
                _validate_receipt(item.candidate, receipt)
                receipts[item.role] = RepositoryApplyReceipt(
                    role=item.role,
                    workspace_ref=item.workspace_ref,
                    repository_ref=item.repository_ref,
                    receipt=receipt,
                )
                attempt = self._update_attempt(
                    attempt,
                    status="applying",
                    receipts=tuple(receipts.values()),
                )
        except Exception as error:
            await self._compensate(bundle, attempt, receipts, error)
        ordered = tuple(receipts[item.role] for item in bundle.candidates)
        content = {
            "delivery_id": bundle.delivery_id,
            "project_id": bundle.project_id,
            "pipeline_revision_id": bundle.pipeline_revision_id,
            "bundle_sha256": bundle.bundle_sha256,
            "repositories": [item.model_dump(mode="json") for item in ordered],
        }
        manifest = ReleaseManifest(
            delivery_id=bundle.delivery_id,
            project_id=bundle.project_id,
            pipeline_revision_id=bundle.pipeline_revision_id,
            bundle_sha256=bundle.bundle_sha256,
            repositories=ordered,
            manifest_sha256=sha256_json(content),
        )
        self.repository.activate_manifest(manifest)
        self._update_attempt(attempt, status="completed", receipts=ordered)
        return manifest

    async def _compensate(
        self,
        bundle: ReleaseBundle,
        attempt: ReleaseApplyAttempt,
        receipts: dict[RepositoryRole, RepositoryApplyReceipt],
        cause: Exception,
    ) -> Never:
        attempt = self._update_attempt(
            attempt,
            status="compensating",
            receipts=tuple(receipts.values()),
            error_code=getattr(cause, "code", "RELEASE_APPLY_FAILED"),
        )
        try:
            by_role = {item.role: item for item in bundle.candidates}
            for role in reversed(tuple(receipts)):
                record = receipts[role]
                current = self.applier.revision(record.workspace_ref)
                candidate_revision = by_role[role].candidate.candidate_revision
                if current == record.receipt.before_revision:
                    continue
                if current != candidate_revision:
                    raise RuntimeError(f"{role} changed during compensation")
                rolled_back = await self.applier.rollback(record.receipt, record.workspace_ref)
                if rolled_back != record.receipt.before_revision:
                    raise RuntimeError(f"{role} rollback did not restore base")
        except Exception as rollback_error:
            self._update_attempt(
                attempt,
                status="needs_attention",
                receipts=tuple(receipts.values()),
                error_code="RELEASE_COMPENSATION_FAILED",
            )
            raise ReleaseApplyError("RELEASE_COMPENSATION_FAILED", str(rollback_error)) from cause
        self._update_attempt(
            attempt,
            status="compensated",
            receipts=tuple(receipts.values()),
            error_code=getattr(cause, "code", "RELEASE_APPLY_FAILED"),
        )
        raise ReleaseApplyError("RELEASE_APPLY_COMPENSATED", str(cause)) from cause

    def _update_attempt(
        self,
        attempt: ReleaseApplyAttempt,
        *,
        status: str,
        receipts: tuple[RepositoryApplyReceipt, ...],
        error_code: str | None = None,
    ) -> ReleaseApplyAttempt:
        updated = attempt.model_copy(
            update={
                "status": cast(
                    Literal[
                        "applying",
                        "completed",
                        "compensating",
                        "compensated",
                        "needs_attention",
                    ],
                    status,
                ),
                "receipts": receipts,
                "error_code": error_code,
                "version": attempt.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self.repository.put_attempt(updated, expected_version=attempt.version)
        return updated


def _validate_receipt(candidate: CandidateChange, receipt: ApplyReceipt) -> None:
    if (
        receipt.before_revision != candidate.base_revision
        or receipt.candidate_revision != candidate.candidate_revision
        or receipt.after_revision != candidate.candidate_revision
    ):
        raise ReleaseApplyError(
            "REPOSITORY_APPLY_RECEIPT_INVALID", "receipt differs from reviewed candidate"
        )
