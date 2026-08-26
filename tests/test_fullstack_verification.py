import hashlib
from datetime import UTC, datetime

import pytest

from agent_team_os.delivery import (
    CandidateChange,
    ProjectExecutionSnapshot,
    RepositoryCandidate,
    VerificationRun,
)
from agent_team_os.modules.releases import (
    FullStackVerificationError,
    FullStackVerifier,
)
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.repositories import RepositorySnapshot


def _project_snapshot() -> ProjectExecutionSnapshot:
    repositories = tuple(
        RepositorySnapshot(
            role=role,
            workspace_ref=f"project:pj1:{role}",
            repository_ref=f"projects/pj1/{role}",
            seed_revision=(role[0] * 40),
        )
        for role in ("backend", "design", "frontend", "qa")
    )
    return ProjectExecutionSnapshot(
        project_id="pj1",
        project_version=2,
        workspace_id="project:pj1",
        repository_ref="projects/pj1",
        pipeline_revision_id="fullstack-delivery:1",
        repositories=repositories,
        repository_set_sha256=sha256_json([item.model_dump(mode="json") for item in repositories]),
    )


def _candidate(role: str) -> RepositoryCandidate:
    value = {
        "backend": "a",
        "design": "b",
        "frontend": "c",
        "qa": "d",
    }[role]
    base = role[0] * 40
    diff = f"+ {role}"
    return RepositoryCandidate(
        role=role,  # type: ignore[arg-type]
        workspace_ref=f"project:pj1:{role}",
        repository_ref=f"projects/pj1/{role}",
        candidate=CandidateChange(
            base_revision=base,
            candidate_revision=(value * 40),
            diff_sha256=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            changed_files=("src/change.txt",),
            candidate_ref=f"refs/candidates/delivery-1/{role}",
            unified_diff=diff,
        ),
        verification=VerificationRun(
            status="passed",
            commands=("python -m unittest discover -s tests -v",),
            exit_code=0,
            log_sha256=(value * 64),
        ),
        producer_identity="codex-cli",
        created_at=datetime.now(UTC),
    )


def test_fullstack_verifier_builds_deterministic_four_repository_bundle() -> None:
    candidates = tuple(_candidate(role) for role in ("backend", "design", "frontend", "qa"))

    bundle = FullStackVerifier().verify(
        delivery_id="delivery-1",
        snapshot=_project_snapshot(),
        candidates=candidates,
    )

    assert bundle.status == "verified"
    assert tuple(item.role for item in bundle.candidates) == (
        "backend",
        "design",
        "frontend",
        "qa",
    )
    assert len(bundle.bundle_sha256) == 64
    assert (
        FullStackVerifier()
        .verify(
            delivery_id="delivery-1",
            snapshot=_project_snapshot(),
            candidates=tuple(reversed(candidates)),
        )
        .bundle_sha256
        == bundle.bundle_sha256
    )


def test_fullstack_verifier_fails_closed_on_missing_role_or_base_drift() -> None:
    snapshot = _project_snapshot()
    candidates = tuple(_candidate(role) for role in ("backend", "design", "frontend", "qa"))
    with pytest.raises(FullStackVerificationError, match="RELEASE_BUNDLE_ROLE_MISSING"):
        FullStackVerifier().verify(
            delivery_id="delivery-1",
            snapshot=snapshot,
            candidates=candidates[:-1],
        )

    drifted = candidates[0].model_copy(
        update={"candidate": candidates[0].candidate.model_copy(update={"base_revision": "9" * 40})}
    )
    with pytest.raises(FullStackVerificationError, match="REPOSITORY_BASE_REVISION_DRIFT"):
        FullStackVerifier().verify(
            delivery_id="delivery-1",
            snapshot=snapshot,
            candidates=(drifted, *candidates[1:]),
        )
