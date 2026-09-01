from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, cast

from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from .v2_domain import (
    ExternalReleaseView,
    GitHubPRReceipt,
    GitHubPRReceiptCreate,
    ReleaseApplyAttemptV2,
    ReleaseBundleV2,
    ReleaseHealthV2,
    ReleaseManifestV2,
    RemoteApplyReceipt,
    WorkspaceCandidateV2,
    WorkspaceCandidateV2Create,
)
from .v2_repository import SQLiteExternalReleaseRepository


class ForwardOnlyRemote(Protocol):
    def revision(self, candidate: WorkspaceCandidateV2) -> str: ...
    def apply(self, candidate: WorkspaceCandidateV2, *, ordinal: int) -> RemoteApplyReceipt: ...


class ExternalReleaseError(ProductError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(
            code=code,
            title="External Forward-only Release 操作失败",
            detail=detail,
            repair=(
                "核对 ReleaseBundle、已 Apply Candidate 与未 Apply Base；"
                "Partial Apply 仅能使用同一 Bundle 执行 resume-forward。"
            ),
            status_code=409,
        )


class ExternalReleaseCatalog:
    """Freeze Candidate, PR and ReleaseBundle evidence before Apply authority is used."""

    def __init__(self, repository: SQLiteExternalReleaseRepository) -> None:
        self.repository = repository

    def record_candidate(self, request: WorkspaceCandidateV2Create) -> WorkspaceCandidateV2:
        candidate_branch = f"agent-team-os/{request.delivery_id}/{request.workcell_key}"
        payload = {
            **request.model_dump(mode="json"),
            "candidate_branch": candidate_branch,
            "status": "verified",
        }
        candidate = WorkspaceCandidateV2(
            **request.model_dump(),
            candidate_branch=candidate_branch,
            evidence_sha256=sha256_json(payload),
        )
        try:
            existing = self.repository.get_candidate_for_workcell(
                request.delivery_id,
                request.workcell_key,
            )
        except KeyError:
            return self.repository.put_candidate(candidate)
        if existing.evidence_sha256 != candidate.evidence_sha256:
            raise ExternalReleaseError(
                "WORKSPACE_CANDIDATE_LINEAGE_CONFLICT",
                "one Delivery/workcell cannot replace its frozen final candidate",
            )
        return existing

    def record_pr(
        self,
        candidate_id: str,
        request: GitHubPRReceiptCreate,
    ) -> GitHubPRReceipt:
        candidate = self.repository.get_candidate(candidate_id)
        if (
            request.head_branch != candidate.candidate_branch
            or request.head_candidate_sha != candidate.candidate_revision
        ):
            raise ExternalReleaseError(
                "GITHUB_PR_CANDIDATE_MISMATCH",
                "PR head does not equal the frozen candidate branch and SHA",
            )
        payload = {"candidate_id": candidate_id, **request.model_dump(mode="json")}
        receipt = GitHubPRReceipt(
            candidate_id=candidate_id,
            **request.model_dump(),
            receipt_sha256=sha256_json(payload),
        )
        return self.repository.put_pr(receipt)

    def build_bundle(
        self,
        *,
        delivery_id: str,
        project_id: str,
        pipeline_revision_id: str,
        release_contract_snapshot: tuple[str, ...],
    ) -> ReleaseBundleV2:
        candidates = self.repository.list_candidates(delivery_id)
        by_workcell = {item.workcell_key: item for item in candidates}
        required = set(release_contract_snapshot)
        if len(required) != len(release_contract_snapshot) or set(by_workcell) != required:
            raise ExternalReleaseError(
                "RELEASE_CONTRACT_CANDIDATES_INCOMPLETE",
                "candidate set does not exactly match the frozen release contract",
            )
        ordered = tuple(by_workcell[key] for key in release_contract_snapshot)
        if any(item.project_id != project_id for item in ordered):
            raise ExternalReleaseError(
                "RELEASE_CANDIDATE_PROJECT_MISMATCH",
                "candidate belongs to another project",
            )
        for candidate in ordered:
            receipt = self.repository.get_pr(candidate.id)
            if receipt is None:
                raise ExternalReleaseError(
                    "GITHUB_PR_RECEIPT_MISSING",
                    f"{candidate.workcell_key} candidate has no PR receipt",
                )
            if (
                receipt.state != "open"
                or receipt.base_branch != "main"
                or receipt.head_branch != candidate.candidate_branch
                or receipt.head_candidate_sha != candidate.candidate_revision
            ):
                raise ExternalReleaseError(
                    "GITHUB_PR_RECEIPT_NOT_RELEASE_READY",
                    f"{candidate.workcell_key} PR no longer represents the candidate",
                )
        payload = {
            "delivery_id": delivery_id,
            "project_id": project_id,
            "pipeline_revision_id": pipeline_revision_id,
            "release_contract_snapshot": release_contract_snapshot,
            "candidate_evidence_sha256": [item.evidence_sha256 for item in ordered],
            "pr_receipt_sha256": [
                cast(GitHubPRReceipt, self.repository.get_pr(item.id)).receipt_sha256
                for item in ordered
            ],
            "policy_version": "external-forward-only-v1",
        }
        bundle = ReleaseBundleV2(
            delivery_id=delivery_id,
            project_id=project_id,
            pipeline_revision_id=pipeline_revision_id,
            release_contract_snapshot=release_contract_snapshot,
            candidates=ordered,
            bundle_sha256=sha256_json(payload),
        )
        return self.repository.put_bundle(bundle)


class ExternalForwardReleaseCoordinator:
    """Apply exact candidates sequentially; partial success is never rolled back."""

    def __init__(
        self,
        repository: SQLiteExternalReleaseRepository,
        remote: ForwardOnlyRemote,
    ) -> None:
        self.repository = repository
        self.remote = remote

    def apply(self, bundle: ReleaseBundleV2) -> ReleaseManifestV2:
        manifest = self.repository.get_manifest(bundle.project_id)
        if (
            manifest is not None
            and manifest.delivery_id == bundle.delivery_id
            and manifest.bundle_sha256 == bundle.bundle_sha256
        ):
            return manifest
        attempt = self.repository.get_attempt(bundle.delivery_id)
        if attempt is None:
            self._preflight(bundle, receipts=())
            attempt = ReleaseApplyAttemptV2(
                delivery_id=bundle.delivery_id,
                project_id=bundle.project_id,
                bundle_sha256=bundle.bundle_sha256,
                status="applying",
                version=1,
            )
            self.repository.put_attempt(attempt, expected_version=None)
        elif attempt.bundle_sha256 != bundle.bundle_sha256:
            raise ExternalReleaseError(
                "RELEASE_BUNDLE_CHANGED",
                "existing attempt is bound to another ReleaseBundle hash",
            )
        elif attempt.status == "needs_attention":
            raise ExternalReleaseError(
                "RELEASE_RESUME_FORWARD_REQUIRED",
                "partial apply requires the explicit resume-forward operation",
            )
        elif attempt.status == "completed":
            if manifest is None:
                raise ExternalReleaseError(
                    "RELEASE_MANIFEST_MISSING",
                    "completed attempt has no active ReleaseManifestV2",
                )
            return manifest
        return self._continue(bundle, attempt)

    def resume_forward(self, delivery_id: str) -> ReleaseManifestV2:
        bundle = self.repository.get_bundle(delivery_id)
        attempt = self.repository.get_attempt(delivery_id)
        if attempt is None or attempt.status != "needs_attention":
            raise ExternalReleaseError(
                "RELEASE_ATTEMPT_NOT_RESUMABLE",
                "resume-forward requires a needs_attention attempt",
            )
        receipts = self.repository.list_remote_receipts(delivery_id)
        self._preflight(bundle, receipts=receipts)
        resumed = attempt.model_copy(
            update={
                "status": "applying",
                "error_code": None,
                "version": attempt.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self.repository.put_attempt(resumed, expected_version=attempt.version)
        return self._continue(bundle, resumed)

    def health(self, project_id: str) -> ReleaseHealthV2:
        return self.repository.get_health(project_id)

    def details(self, delivery_id: str) -> ExternalReleaseView:
        candidates = self.repository.list_candidates(delivery_id)
        pull_requests = tuple(
            receipt
            for candidate in candidates
            if (receipt := self.repository.get_pr(candidate.id)) is not None
        )
        try:
            bundle = self.repository.get_bundle(delivery_id)
        except KeyError:
            bundle = None
        attempt = self.repository.get_attempt(delivery_id)
        project_id = (
            bundle.project_id
            if bundle is not None
            else candidates[0].project_id
            if candidates
            else attempt.project_id
            if attempt is not None
            else None
        )
        manifest = None if project_id is None else self.repository.get_manifest(project_id)
        if manifest is not None and manifest.delivery_id != delivery_id:
            manifest = None
        return ExternalReleaseView(
            delivery_id=delivery_id,
            project_id=project_id,
            candidates=candidates,
            pull_requests=pull_requests,
            bundle=bundle,
            apply_attempt=attempt,
            remote_apply_receipts=self.repository.list_remote_receipts(delivery_id),
            manifest=manifest,
        )

    def _preflight(
        self,
        bundle: ReleaseBundleV2,
        *,
        receipts: tuple[RemoteApplyReceipt, ...],
    ) -> None:
        receipt_by_candidate = {item.candidate_id: item for item in receipts}
        for candidate in bundle.candidates:
            current = self.remote.revision(candidate)
            receipt = receipt_by_candidate.get(candidate.id)
            required = (
                candidate.candidate_revision
                if receipt is not None
                else candidate.base_revision
            )
            if current != required:
                code = (
                    "RELEASE_APPLIED_REPOSITORY_DRIFT"
                    if receipt is not None
                    else "RELEASE_UNAPPLIED_REPOSITORY_BASE_DRIFT"
                )
                raise ExternalReleaseError(
                    code,
                    f"{candidate.workcell_key} main is {current}, expected {required}",
                )

    def _continue(
        self,
        bundle: ReleaseBundleV2,
        attempt: ReleaseApplyAttemptV2,
    ) -> ReleaseManifestV2:
        receipts = {
            item.candidate_id: item
            for item in self.repository.list_remote_receipts(bundle.delivery_id)
        }
        try:
            for ordinal, candidate in enumerate(bundle.candidates):
                existing = receipts.get(candidate.id)
                if existing is not None:
                    if self.remote.revision(candidate) != candidate.candidate_revision:
                        raise ExternalReleaseError(
                            "RELEASE_APPLIED_REPOSITORY_DRIFT",
                            f"{candidate.workcell_key} changed after its Apply receipt",
                        )
                    continue
                current = self.remote.revision(candidate)
                if current == candidate.candidate_revision:
                    receipt = self._recovered_receipt(bundle, candidate, ordinal)
                elif current == candidate.base_revision:
                    receipt = self.remote.apply(candidate, ordinal=ordinal)
                else:
                    raise ExternalReleaseError(
                        "RELEASE_UNAPPLIED_REPOSITORY_BASE_DRIFT",
                        f"{candidate.workcell_key} main is neither Base nor Candidate",
                    )
                self._validate_receipt(bundle, candidate, ordinal, receipt)
                if self.remote.revision(candidate) != candidate.candidate_revision:
                    raise ExternalReleaseError(
                        "REMOTE_SHA_READBACK_MISMATCH",
                        f"{candidate.workcell_key} main readback differs from Candidate",
                    )
                receipts[candidate.id] = self.repository.put_remote_receipt(receipt)
        except Exception as error:
            code = getattr(error, "code", "EXTERNAL_FORWARD_APPLY_FAILED")
            self._needs_attention(bundle, attempt, str(code))
            if isinstance(error, ExternalReleaseError):
                raise
            raise ExternalReleaseError(str(code), str(error)) from error
        ordered = tuple(receipts[item.id] for item in bundle.candidates)
        content = {
            "project_id": bundle.project_id,
            "delivery_id": bundle.delivery_id,
            "pipeline_revision_id": bundle.pipeline_revision_id,
            "bundle_sha256": bundle.bundle_sha256,
            "repositories": [item.model_dump(mode="json") for item in ordered],
            "policy_version": "external-forward-only-v1",
        }
        manifest = ReleaseManifestV2(
            project_id=bundle.project_id,
            delivery_id=bundle.delivery_id,
            pipeline_revision_id=bundle.pipeline_revision_id,
            bundle_sha256=bundle.bundle_sha256,
            repositories=ordered,
            manifest_sha256=sha256_json(content),
        )
        self.repository.activate_manifest(manifest)
        completed = attempt.model_copy(
            update={
                "status": "completed",
                "error_code": None,
                "version": attempt.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self.repository.put_attempt(completed, expected_version=attempt.version)
        current_health = self.repository.get_health(bundle.project_id)
        self.repository.put_health(
            ReleaseHealthV2(
                project_id=bundle.project_id,
                status="healthy",
                delivery_id=bundle.delivery_id,
                bundle_sha256=bundle.bundle_sha256,
                version=current_health.version + 1,
            )
        )
        self.repository.mark_delivery_release_completed(
            bundle.delivery_id,
            manifest.manifest_sha256,
        )
        return manifest

    def _needs_attention(
        self,
        bundle: ReleaseBundleV2,
        attempt: ReleaseApplyAttemptV2,
        error_code: str,
    ) -> None:
        failed = attempt.model_copy(
            update={
                "status": "needs_attention",
                "error_code": error_code,
                "version": attempt.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self.repository.put_attempt(failed, expected_version=attempt.version)
        health = self.repository.get_health(bundle.project_id)
        self.repository.put_health(
            ReleaseHealthV2(
                project_id=bundle.project_id,
                status="release_drifted",
                delivery_id=bundle.delivery_id,
                bundle_sha256=bundle.bundle_sha256,
                error_code=error_code,
                version=health.version + 1,
            )
        )
        self.repository.mark_delivery_needs_attention(bundle.delivery_id, error_code)

    @staticmethod
    def _recovered_receipt(
        bundle: ReleaseBundleV2,
        candidate: WorkspaceCandidateV2,
        ordinal: int,
    ) -> RemoteApplyReceipt:
        payload = {
            "delivery_id": bundle.delivery_id,
            "ordinal": ordinal,
            "candidate_id": candidate.id,
            "workcell_key": candidate.workcell_key,
            "repository_uri": candidate.repository_uri,
            "before_revision": candidate.base_revision,
            "candidate_revision": candidate.candidate_revision,
            "after_revision": candidate.candidate_revision,
            "recovered": True,
        }
        return RemoteApplyReceipt.model_validate(
            {**payload, "receipt_sha256": sha256_json(payload)}
        )

    @staticmethod
    def _validate_receipt(
        bundle: ReleaseBundleV2,
        candidate: WorkspaceCandidateV2,
        ordinal: int,
        receipt: RemoteApplyReceipt,
    ) -> None:
        if (
            receipt.delivery_id != bundle.delivery_id
            or receipt.ordinal != ordinal
            or receipt.candidate_id != candidate.id
            or receipt.workcell_key != candidate.workcell_key
            or receipt.repository_uri != candidate.repository_uri
            or receipt.before_revision != candidate.base_revision
            or receipt.candidate_revision != candidate.candidate_revision
            or receipt.after_revision != candidate.candidate_revision
        ):
            raise ExternalReleaseError(
                "REMOTE_APPLY_RECEIPT_INVALID",
                f"{candidate.workcell_key} receipt differs from the approved Candidate",
            )
