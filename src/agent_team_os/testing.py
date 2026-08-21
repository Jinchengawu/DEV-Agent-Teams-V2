"""Deterministic boundary adapters. They are never valid live evidence."""

from __future__ import annotations

from .delivery import (
    AcceptanceCriterion,
    ApplyReceipt,
    CandidateChange,
    RequirementArtifact,
    TaskContract,
    VerificationRun,
)


class DeterministicPlanningService:
    evidence_identity = "deterministic-test"

    async def analyze(self, user_request: str) -> RequirementArtifact:
        return RequirementArtifact(
            summary=user_request,
            acceptance_criteria=(
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "The requested Backend behavior is implemented and machine-verifiable."
                    ),
                ),
            ),
        )

    async def plan(self, requirements: RequirementArtifact) -> TaskContract:
        return TaskContract(
            title="Implement the approved Backend request",
            instructions=requirements.summary,
            acceptance_ids=tuple(item.id for item in requirements.acceptance_criteria),
        )


class DeterministicCodeExecutor:
    evidence_identity = "deterministic-test"

    async def execute(self, task: TaskContract, workspace_id: str) -> CandidateChange:
        return CandidateChange(
            base_revision="base-revision",
            candidate_revision="candidate-revision",
            diff_sha256="a" * 64,
            changed_files=("src/health.py", "tests/test_health.py"),
        )


class DeterministicCandidateVerifier:
    async def verify(
        self, candidate: CandidateChange, task: TaskContract, workspace_id: str
    ) -> VerificationRun:
        return VerificationRun(
            status="passed",
            commands=task.system_policy.verification_commands,
            exit_code=0,
            log_sha256="b" * 64,
        )


class DeterministicCandidateApplier:
    async def apply(self, candidate: CandidateChange, workspace_id: str) -> ApplyReceipt:
        return ApplyReceipt(
            before_revision=candidate.base_revision,
            candidate_revision=candidate.candidate_revision,
            after_revision=candidate.candidate_revision,
            result="applied",
        )
