from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_team_os.delivery import (
    ApplyReceipt,
    CandidateChange,
    ReleaseBundle,
    RepositoryCandidate,
    VerificationRun,
)
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.releases import (
    ReleaseApplyAttempt,
    ReleaseApplyError,
    ReleaseCoordinator,
    SQLiteReleaseRepository,
)
from agent_team_os.shared.hashes import sha256_json


class MemoryReleaseApplier:
    def __init__(self, *, fail_role: str | None = None) -> None:
        self.revisions = {role: f"{role}-base" for role in _roles()}
        self.fail_role = fail_role

    async def apply(self, candidate: CandidateChange, workspace_id: str) -> ApplyReceipt:
        role = workspace_id.rsplit(":", 1)[-1]
        if role == self.fail_role:
            raise RuntimeError("injected apply failure")
        if self.revisions[role] != candidate.base_revision:
            raise RuntimeError("base drift")
        self.revisions[role] = candidate.candidate_revision
        return ApplyReceipt(
            before_revision=candidate.base_revision,
            candidate_revision=candidate.candidate_revision,
            after_revision=candidate.candidate_revision,
            result="applied",
        )

    async def rollback(self, receipt: ApplyReceipt, workspace_id: str) -> str:
        role = workspace_id.rsplit(":", 1)[-1]
        if self.revisions[role] != receipt.after_revision:
            raise RuntimeError("rollback drift")
        self.revisions[role] = receipt.before_revision
        return receipt.before_revision

    def revision(self, workspace_id: str) -> str:
        return self.revisions[workspace_id.rsplit(":", 1)[-1]]


def test_release_coordinator_applies_four_repositories_and_persists_manifest(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = _repository(tmp_path)
        applier = MemoryReleaseApplier()
        bundle = _bundle()

        manifest = await ReleaseCoordinator(repository, applier).apply(bundle)

        assert manifest.status == "active"
        assert manifest.bundle_sha256 == bundle.bundle_sha256
        assert tuple(item.role for item in manifest.repositories) == _roles()
        assert all(applier.revisions[role] == f"{role}-candidate" for role in _roles())
        assert repository.get_manifest("pj1") == manifest

    asyncio.run(scenario())


def test_release_coordinator_compensates_partial_apply_and_fails_closed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = _repository(tmp_path)
        applier = MemoryReleaseApplier(fail_role="frontend")

        with pytest.raises(ReleaseApplyError, match="RELEASE_APPLY_COMPENSATED"):
            await ReleaseCoordinator(repository, applier).apply(_bundle())

        assert applier.revisions == {role: f"{role}-base" for role in _roles()}
        attempt = repository.get_attempt("delivery-1")
        assert attempt is not None
        assert attempt.status == "compensated"
        assert repository.get_manifest("pj1") is None

    asyncio.run(scenario())


def test_release_coordinator_recovers_git_update_written_before_receipt(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = _repository(tmp_path)
        applier = MemoryReleaseApplier()
        bundle = _bundle()
        applier.revisions["backend"] = "backend-candidate"
        repository.put_attempt(
            ReleaseApplyAttempt(
                delivery_id=bundle.delivery_id,
                project_id=bundle.project_id,
                bundle_sha256=bundle.bundle_sha256,
                status="applying",
                version=1,
            ),
            expected_version=None,
        )

        manifest = await ReleaseCoordinator(repository, applier).apply(bundle)

        backend = next(item for item in manifest.repositories if item.role == "backend")
        assert backend.receipt.recovered is True
        assert all(applier.revisions[role] == f"{role}-candidate" for role in _roles())
        assert repository.get_attempt(bundle.delivery_id).status == "completed"  # type: ignore[union-attr]

    asyncio.run(scenario())


def _repository(tmp_path: Path) -> SQLiteReleaseRepository:
    root = Path(__file__).parents[1]
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, root / "migrations").migrate()
    return SQLiteReleaseRepository(database)


def _roles() -> tuple[str, ...]:
    return ("backend", "design", "frontend", "qa")


def _bundle() -> ReleaseBundle:
    candidates = tuple(_candidate(role) for role in _roles())
    content = {
        "delivery_id": "delivery-1",
        "project_id": "pj1",
        "pipeline_revision_id": "fullstack-delivery:1",
        "repository_set_sha256": "e" * 64,
        "candidates": [item.model_dump(mode="json") for item in candidates],
    }
    return ReleaseBundle(
        delivery_id="delivery-1",
        project_id="pj1",
        pipeline_revision_id="fullstack-delivery:1",
        repository_set_sha256="e" * 64,
        candidates=candidates,
        bundle_sha256=sha256_json(content),
    )


def _candidate(role: str) -> RepositoryCandidate:
    diff = f"+ {role}"
    return RepositoryCandidate(
        role=role,  # type: ignore[arg-type]
        workspace_ref=f"project:pj1:{role}",
        repository_ref=f"projects/pj1/{role}",
        candidate=CandidateChange(
            base_revision=f"{role}-base",
            candidate_revision=f"{role}-candidate",
            diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
            changed_files=("tests/test_release.py",),
            unified_diff=diff,
        ),
        verification=VerificationRun(
            status="passed",
            commands=("python -m unittest discover -s tests -v",),
            exit_code=0,
            log_sha256="f" * 64,
        ),
        producer_identity="codex-cli",
        created_at=datetime.now(UTC),
    )
