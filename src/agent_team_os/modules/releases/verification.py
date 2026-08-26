from __future__ import annotations

import hashlib

from ...delivery import (
    ProjectExecutionSnapshot,
    ReleaseBundle,
    RepositoryCandidate,
)
from ...shared.hashes import sha256_json
from ...shared.repositories import RepositoryRole

_REQUIRED_ROLES: tuple[RepositoryRole, ...] = (
    "backend",
    "design",
    "frontend",
    "qa",
)


class FullStackVerificationError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class FullStackVerifier:
    """Validate four independent candidates behind one Release Bundle interface."""

    def verify(
        self,
        *,
        delivery_id: str,
        snapshot: ProjectExecutionSnapshot,
        candidates: tuple[RepositoryCandidate, ...],
    ) -> ReleaseBundle:
        repositories = {item.role: item for item in snapshot.repositories}
        if tuple(sorted(repositories)) != tuple(sorted(_REQUIRED_ROLES)):
            raise FullStackVerificationError(
                "PROJECT_REPOSITORY_SET_INCOMPLETE",
                "full-stack Delivery requires backend, design, frontend and qa",
            )
        actual_set_hash = sha256_json(
            [
                item.model_dump(mode="json")
                for item in sorted(snapshot.repositories, key=lambda item: item.role)
            ]
        )
        if snapshot.repository_set_sha256 is None:
            raise FullStackVerificationError(
                "PROJECT_REPOSITORY_SET_HASH_MISSING",
                "full-stack Delivery has no frozen repository set hash",
            )
        if snapshot.repository_set_sha256 != actual_set_hash:
            raise FullStackVerificationError(
                "PROJECT_REPOSITORY_SET_HASH_MISMATCH",
                "frozen repository set content differs from its hash",
            )
        by_role = {item.role: item for item in candidates}
        if len(by_role) != len(candidates):
            raise FullStackVerificationError(
                "RELEASE_BUNDLE_ROLE_DUPLICATE",
                "a Repository Role produced more than one primary candidate",
            )
        missing = tuple(role for role in _REQUIRED_ROLES if role not in by_role)
        if missing:
            raise FullStackVerificationError("RELEASE_BUNDLE_ROLE_MISSING", ", ".join(missing))
        ordered = tuple(by_role[role] for role in _REQUIRED_ROLES)
        for item in ordered:
            repository = repositories[item.role]
            if (
                item.workspace_ref != repository.workspace_ref
                or item.repository_ref != repository.repository_ref
            ):
                raise FullStackVerificationError(
                    "REPOSITORY_SCOPE_MISMATCH",
                    f"{item.role} candidate belongs to another repository",
                )
            if item.candidate.base_revision != repository.seed_revision:
                raise FullStackVerificationError(
                    "REPOSITORY_BASE_REVISION_DRIFT",
                    f"{item.role} candidate was not created from the frozen Main",
                )
            if not item.candidate.changed_files:
                raise FullStackVerificationError(
                    "REPOSITORY_CANDIDATE_EMPTY", f"{item.role} candidate has no files"
                )
            actual_diff_hash = hashlib.sha256(
                item.candidate.unified_diff.encode("utf-8")
            ).hexdigest()
            if actual_diff_hash != item.candidate.diff_sha256:
                raise FullStackVerificationError(
                    "REPOSITORY_DIFF_HASH_MISMATCH",
                    f"{item.role} diff does not match its hash",
                )
            if item.verification.status != "passed" or item.verification.exit_code != 0:
                raise FullStackVerificationError(
                    "REPOSITORY_VERIFICATION_FAILED",
                    f"{item.role} machine verification did not pass",
                )
        content = {
            "delivery_id": delivery_id,
            "project_id": snapshot.project_id,
            "pipeline_revision_id": snapshot.pipeline_revision_id,
            "repository_set_sha256": snapshot.repository_set_sha256,
            "candidates": [item.model_dump(mode="json") for item in ordered],
        }
        return ReleaseBundle(
            delivery_id=delivery_id,
            project_id=snapshot.project_id,
            pipeline_revision_id=snapshot.pipeline_revision_id,
            repository_set_sha256=snapshot.repository_set_sha256,
            candidates=ordered,
            bundle_sha256=sha256_json(content),
            status="verified",
        )
