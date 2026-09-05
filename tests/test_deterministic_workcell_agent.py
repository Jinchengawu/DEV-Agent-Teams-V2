from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_team_os.modules.workcells import WorkcellAgentInvocation
from agent_team_os.testing import DeterministicWorkcellAgent


def _review_invocation(workspace: Path, instruction: str) -> WorkcellAgentInvocation:
    return WorkcellAgentInvocation(
        delivery_id="deterministic-delivery",
        workcell_run_id="design-workcell",
        agent_run_id="design-reviewer",
        phase="delegate",
        workcell_key="design",
        stage_path="design-repair/design",
        instruction=instruction,
        workspace=workspace,
        workspace_access="candidate_read",
        method_id="design-review",
        allowed_knowledge_citation_ids=("frozen-citation",),
    )


def test_public_deterministic_reviewer_returns_frozen_candidate_evidence(tmp_path: Path) -> None:
    candidate_sha = "0123456789abcdef0123456789abcdef01234567"
    diff_sha = "abcdef0123456789" * 4
    evidence = {
        "candidate_revision": candidate_sha, "diff_sha256": diff_sha,
        "review_scope_sha256": "a" * 64,
    }
    invocation = _review_invocation(
        tmp_path,
        "执行只读 Review。\nCandidate Review Evidence："
        + json.dumps(evidence)
        + "\nFrozen Acceptance Contract：[]\nReview Output Contract：保留冻结身份。",
    )

    result = asyncio.run(DeterministicWorkcellAgent().run(invocation))

    assert result.content == {
        "reviewed_candidate_sha": candidate_sha,
        "reviewed_diff_sha256": diff_sha,
        "review_scope_sha256": "a" * 64,
        "blocking_findings": [],
        "method_id": "design-review",
    }
    assert result.runtime_identity == "deterministic-model-boundary"
    assert result.knowledge_citation_ids == ("frozen-citation",)
    assert list(tmp_path.iterdir()) == []


def test_public_deterministic_main_reads_assignment_marker_after_review_scope(tmp_path: Path):
    invocation = _review_invocation(tmp_path, "").model_copy(update={
        "phase": "planning", "workspace_access": "none",
        "instruction": 'Frozen Review Scope：{"sha256":"scope"}\n冻结 assignments 数组：[]',
    })
    result = asyncio.run(DeterministicWorkcellAgent().run(invocation))
    assert result.content == {"assignments": []}


@pytest.mark.parametrize(
    "instruction",
    [
        "只读 Review，但没有 Product Evidence。",
        "Candidate Review Evidence：{}",
        'Candidate Review Evidence：{"candidate_revision":"abc"}',
    ],
)
def test_public_deterministic_reviewer_rejects_missing_frozen_evidence(
    tmp_path: Path, instruction: str
) -> None:
    with pytest.raises(ValueError, match="DETERMINISTIC_REVIEW_EVIDENCE_MISSING"):
        asyncio.run(DeterministicWorkcellAgent().run(_review_invocation(tmp_path, instruction)))
