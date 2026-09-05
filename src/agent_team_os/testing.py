"""Deterministic boundary adapters. They are never valid live evidence."""

from __future__ import annotations

import json

from .delivery import (
    AcceptanceCriterion,
    ApplyReceipt,
    CandidateChange,
    RequirementArtifact,
    TaskContract,
    VerificationRun,
)
from .modules.releases import GitHubPRReceiptCreate, WorkspaceCandidateV2
from .modules.workcells import WorkcellAgentInvocation, WorkcellAgentOutput
from .shared.review_scope import WorkcellAcceptanceAssignment, WorkcellAcceptanceResponsibility


class DeterministicPlanningService:
    evidence_identity = "deterministic-test"

    async def analyze(self, user_request: str) -> RequirementArtifact:
        return RequirementArtifact(
            summary=_deterministic_stage_objective(user_request),
            acceptance_criteria=(
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "The requested Backend behavior is implemented and machine-verifiable."
                    ),
                ),
            ),
        )

    async def plan(
        self, requirements: RequirementArtifact, *, required_workcells: tuple[str, ...] = ()
    ) -> TaskContract:
        approved_marker = "已批准需求摘要："
        instructions = (
            requirements.summary.rsplit(approved_marker, 1)[1].strip()
            if approved_marker in requirements.summary
            else _deterministic_stage_objective(requirements.summary)
        )
        return TaskContract(
            title="Implement the approved Backend request",
            instructions=instructions,
            acceptance_ids=tuple(item.id for item in requirements.acceptance_criteria),
            workcell_acceptance=(
                tuple(
                    WorkcellAcceptanceAssignment(
                        workcell_key=key,
                        acceptance=tuple(
                            WorkcellAcceptanceResponsibility(
                                acceptance_id=item.id,
                                responsibility={
                                    "design": "定义验收项的接口合同和状态展示规范。",
                                    "frontend": "实现验收项的前端交互与页面测试。",
                                    "backend": "实现验收项的服务端接口与单元测试。",
                                    "qa": "验证验收项的跨仓端到端闭环。",
                                }[key],
                            )
                            for item in requirements.acceptance_criteria
                        ),
                    )
                    for key in required_workcells
                )
                if required_workcells else None
            ),
        )


def _deterministic_stage_objective(instruction: str) -> str:
    """Keep the fixture output shaped like provider output without echoing its prompt."""
    prompt = instruction.split("<external-collaborative-data", 1)[0].strip()
    marker = "本次 Stage 目标："
    if marker in prompt:
        return prompt.rsplit(marker, 1)[1].strip()
    return prompt


class DeterministicCodeExecutor:
    evidence_identity = "deterministic-test"

    async def execute(
        self, task: TaskContract, workspace_id: str, delivery_id: str
    ) -> CandidateChange:
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


class DeterministicWorkcellAgent:
    """Fixture-only Agent boundary; never valid Live evidence."""

    async def run(self, invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
        if invocation.phase == "planning":
            content: dict[str, object] = {
                "assignments": json.loads(
                    invocation.instruction.rsplit("冻结 assignments 数组：", 1)[1]
                )
            }
        elif invocation.phase == "synthesis":
            content = {"status": "passed", "workcell": invocation.workcell_key}
        elif invocation.workspace_access == "workspace_write":
            source, test = {
                "design": ("design/candidate.md", "tests/test_design_candidate.py"),
                "frontend": ("src/candidate.txt", "tests/test_frontend_candidate.py"),
                "backend": ("src/candidate.txt", "tests/test_backend_candidate.py"),
                "qa": ("reports/candidate.md", "tests/test_qa_candidate.py"),
            }[invocation.workcell_key]
            source_path = invocation.workspace / source
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                f"{invocation.workcell_key} deterministic candidate\n",
                encoding="utf-8",
            )
            test_path = invocation.workspace / test
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(
                "import unittest\n\n"
                "class CandidateTest(unittest.TestCase):\n"
                "    def test_candidate(self) -> None:\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            content = {"changed_files": [source, test]}
        elif invocation.workspace_access == "candidate_read":
            try:
                evidence_text = invocation.instruction.split("Candidate Review Evidence：", 1)[1]
                review_evidence = json.loads(evidence_text.splitlines()[0])
                content = {
                    "reviewed_candidate_sha": review_evidence["candidate_revision"],
                    "reviewed_diff_sha256": review_evidence["diff_sha256"],
                    "review_scope_sha256": review_evidence["review_scope_sha256"],
                    "blocking_findings": [],
                    "method_id": invocation.method_id,
                }
            except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
                raise ValueError("DETERMINISTIC_REVIEW_EVIDENCE_MISSING") from error
        else:
            content = {
                "artifact": invocation.method_id,
                "acceptance_coverage": ["AC-1"],
            }
        return WorkcellAgentOutput(
            runtime_identity="deterministic-model-boundary",
            content=content,
            knowledge_citation_ids=invocation.allowed_knowledge_citation_ids,
        )


class DeterministicPullRequestSurface:
    """Fixture-only PR review surface with GitHub-shaped immutable receipts."""

    def ensure(
        self,
        candidate: WorkspaceCandidateV2,
        _binding: object,
    ) -> GitHubPRReceiptCreate:
        ordinal = {"design": 1, "frontend": 2, "backend": 3, "qa": 4}.get(
            candidate.workcell_key,
            99,
        )
        return GitHubPRReceiptCreate(
            pull_request_id=ordinal,
            url=(f"https://github.com/deterministic/{candidate.workcell_key}/pull/{ordinal}"),
            head_branch=candidate.candidate_branch,
            head_candidate_sha=candidate.candidate_revision,
            state="open",
        )
