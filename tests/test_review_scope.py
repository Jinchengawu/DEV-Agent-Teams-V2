from __future__ import annotations

import copy
from pathlib import Path

import pytest
from review_scope_helpers import WORKCELL_KEYS, planning_payloads
from test_workcell_execution_kernel import _kernel, _snapshot

from agent_team_os.modules.agents import ArtifactEnvelope
from agent_team_os.modules.workcells import (
    BlockingFinding,
    CandidateVerificationCreate,
    DelegationAssignment,
    ReviewArtifactCreate,
    WorkcellRunCreate,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.review_scope import (
    compile_review_scope,
    product_review_policies,
    validate_workcell_acceptance,
)


def test_review_scope_uses_approved_responsibility_and_rejects_changed_gate_source() -> None:
    requirements, task = planning_payloads()
    approved_hash = sha256_json({"requirements": requirements, "task": task})
    arguments = dict(
        requirements=requirements,
        task=task,
        plan_subject_sha256=approved_hash,
        plan_approved=True,
        workcell_key="frontend",
        required_workcells=WORKCELL_KEYS,
        policies=product_review_policies(WORKCELL_KEYS),
    )
    scope = compile_review_scope(**arguments)
    assert scope.acceptance[0].acceptance_id == "AC-LOGIN"
    assert scope.acceptance[0].statement == "用户可以完成登录"
    assert scope.acceptance[0].responsibility == "实现登录页面交互和错误状态"
    assert scope.source_plan_sha256 == approved_hash
    changed = copy.deepcopy(task)
    changed["workcell_acceptance"][1]["acceptance"][0]["responsibility"] = "可以不实现登录"
    with pytest.raises(ProductError) as altered:
        compile_review_scope(**{**arguments, "task": changed})
    assert altered.value.code == "WORKCELL_REVIEW_PLAN_MISMATCH"


def _reviewing_run(tmp_path: Path):
    kernel, artifacts = _kernel(tmp_path)
    tree = kernel.create(
        WorkcellRunCreate(
            delivery_id="delivery-workcell",
            pipeline_run_id="review-pipeline",
            stage_attempt_id="review-stage",
            snapshot=_snapshot(),
        )
    )
    tree = kernel.submit_delegation_plan(
        tree.workcell_run.id,
        (
            DelegationAssignment(
                slot_key="delegate_1",
                delegate_purpose="workspace_write",
                workspace_access="workspace_write",
            ),
            DelegationAssignment(
                slot_key="delegate_2", delegate_purpose="review", workspace_access="candidate_read"
            ),
            DelegationAssignment(
                slot_key="delegate_3", delegate_purpose="review", workspace_access="candidate_read"
            ),
        ),
    )
    children = {item.slot_key: item for item in tree.agent_runs if item.run_role == "child"}
    writer = children["delegate_1"]
    kernel.start_child(writer.id)
    kernel.finish_child(writer.id, status="succeeded")
    kernel.record_candidate_verification(
        tree.workcell_run.id,
        CandidateVerificationCreate(
            writer_agent_run_id=writer.id,
            candidate_sha="b" * 40,
            diff_sha256="c" * 64,
            status="passed",
            report={"exit_code": 0},
        ),
    )
    for slot in ("delegate_2", "delegate_3"):
        kernel.start_child(children[slot].id)
    return kernel, artifacts, tree.workcell_run.id, children


def test_kernel_rejects_unknown_ownership_even_when_code_is_known_acceptance(
    tmp_path: Path,
) -> None:
    kernel, artifacts, run_id, children = _reviewing_run(tmp_path)
    finding = BlockingFinding(
        code="AC-LOGIN",
        summary="伪装成本仓问题",
        evidence_sha256="d" * 64,
        acceptance_id="AC-OTHER-WORKCELL",
    )
    scope = kernel.tree(run_id).workcell_run.workcell_snapshot.review_scope
    assert scope is not None
    raw = {
        "reviewed_candidate_sha": "b" * 40,
        "reviewed_diff_sha256": "c" * 64,
        "review_scope_sha256": scope.sha256,
        "blocking_findings": [finding.model_dump(mode="json")],
    }
    reference = artifacts.put_json(raw)
    reviewer = children["delegate_2"]
    kernel.finish_child(
        reviewer.id,
        status="succeeded",
        artifacts=(
            ArtifactEnvelope(
                contract_id="review-artifact-v1",
                reference=reference,
                sha256=reference.sha256,
            ),
        ),
    )
    with pytest.raises(ProductError) as invalid:
        kernel.record_review(
            run_id,
            ReviewArtifactCreate(
                reviewer_agent_run_id=reviewer.id,
                candidate_sha="b" * 40,
                diff_sha256="c" * 64,
                blocking_findings=(finding,),
                artifact_reference=reference,
            ),
        )
    assert invalid.value.code == "WORKCELL_REVIEW_FINDING_OUT_OF_SCOPE"
    assert not kernel.tree(run_id).reviews
    assert artifacts.get_bytes(reference)


def test_valid_blocking_review_is_retained_after_invalid_sibling_fails(tmp_path: Path) -> None:
    kernel, artifacts, run_id, children = _reviewing_run(tmp_path)
    invalid_output = {"blocking_findings": [{"summary": "原始问题必须保留"}]}
    invalid_reference = artifacts.put_json(invalid_output)
    kernel.finish_child(
        children["delegate_2"].id,
        status="failed",
        error_code="WORKCELL_REVIEW_ARTIFACT_INVALID",
        artifacts=(
            ArtifactEnvelope(
                contract_id="review-artifact-v1",
                reference=invalid_reference,
                sha256=invalid_reference.sha256,
            ),
        ),
    )
    finding = BlockingFinding(
        code="LOGIN_BROKEN",
        summary="登录按钮未发起请求",
        acceptance_id="AC-LOGIN",
        evidence_sha256="e" * 64,
    )
    scope = kernel.tree(run_id).workcell_run.workcell_snapshot.review_scope
    assert scope is not None
    reference = artifacts.put_json(
        {
            "reviewed_candidate_sha": "b" * 40,
            "reviewed_diff_sha256": "c" * 64,
            "review_scope_sha256": scope.sha256,
            "blocking_findings": [finding.model_dump(mode="json")],
        }
    )
    reviewer = children["delegate_3"]
    kernel.finish_child(
        reviewer.id,
        status="succeeded",
        artifacts=(
            ArtifactEnvelope(
                contract_id="review-artifact-v1",
                reference=reference,
                sha256=reference.sha256,
            ),
        ),
    )
    tree = kernel.record_review(
        run_id,
        ReviewArtifactCreate(
            reviewer_agent_run_id=reviewer.id,
            candidate_sha="b" * 40,
            diff_sha256="c" * 64,
            blocking_findings=(finding,),
            artifact_reference=reference,
        ),
    )
    assert tree.workcell_run.status == "failed"
    assert tree.reviews[0].blocking_findings == (finding,)
    assert {item.result_artifact_sha256 for item in tree.attempts} >= {
        invalid_reference.sha256,
        reference.sha256,
    }
    with pytest.raises(ProductError):
        kernel.start_synthesis(run_id)


@pytest.mark.parametrize(
    ("references", "error_code"),
    [
        ({}, "WORKCELL_REVIEW_FINDING_REFERENCE_INVALID"),
        (
            {"acceptance_id": "AC-LOGIN", "system_policy_id": "workspace.allowed-paths.v1"},
            "WORKCELL_REVIEW_FINDING_REFERENCE_INVALID",
        ),
        ({"system_policy_id": "SYSTEM-POLICY-LOOKS-VALID"}, "WORKCELL_REVIEW_FINDING_OUT_OF_SCOPE"),
        ({"acceptance_id": "AC-BACKEND"}, "WORKCELL_REVIEW_FINDING_OUT_OF_SCOPE"),
        ({"acceptance_id": "AC-LOGIN"}, None),
        ({"system_policy_id": "workspace.allowed-paths.v1"}, None),
    ],
)
def test_kernel_checks_explicit_membership_and_keeps_real_blocks(
    tmp_path: Path, references: dict, error_code: str | None
) -> None:
    kernel, artifacts, run_id, children = _reviewing_run(tmp_path)
    scope = kernel.tree(run_id).workcell_run.workcell_snapshot.review_scope
    assert scope is not None
    finding = BlockingFinding(
        code="ARBITRARY_ISSUE_CODE", summary="实际缺陷证据", evidence_sha256="d" * 64, **references
    )
    reference = artifacts.put_json(
        {
            "reviewed_candidate_sha": "b" * 40,
            "reviewed_diff_sha256": "c" * 64,
            "review_scope_sha256": scope.sha256,
            "blocking_findings": [finding.model_dump(mode="json")],
        }
    )
    reviewer = children["delegate_2"]
    kernel.finish_child(
        reviewer.id,
        status="succeeded",
        artifacts=(
            ArtifactEnvelope(
                contract_id="review-artifact-v1",
                reference=reference,
                sha256=reference.sha256,
            ),
        ),
    )
    request = ReviewArtifactCreate(
        reviewer_agent_run_id=reviewer.id,
        candidate_sha="b" * 40,
        diff_sha256="c" * 64,
        blocking_findings=(finding,),
        artifact_reference=reference,
    )
    if error_code:
        with pytest.raises(ProductError) as error:
            kernel.record_review(run_id, request)
        assert error.value.code == error_code
    else:
        tree = kernel.record_review(run_id, request)
        assert tree.workcell_run.error_code == "WORKCELL_BLOCKING_REVIEW"
        assert tree.reviews[0].blocking_findings == (finding,)
    with pytest.raises(ProductError):
        kernel.start_synthesis(run_id)


@pytest.mark.parametrize("tamper", ["scope", "candidate", "diff", "findings"])
def test_kernel_binds_raw_review_before_accepting_empty_record(tmp_path: Path, tamper: str) -> None:
    kernel, artifacts, run_id, children = _reviewing_run(tmp_path)
    scope = kernel.tree(run_id).workcell_run.workcell_snapshot.review_scope
    assert scope is not None
    raw = {
        "reviewed_candidate_sha": "b" * 40,
        "reviewed_diff_sha256": "c" * 64,
        "review_scope_sha256": scope.sha256,
        "blocking_findings": [],
    }
    key, value = {
        "scope": ("review_scope_sha256", "e" * 64),
        "candidate": ("reviewed_candidate_sha", "e" * 40),
        "diff": ("reviewed_diff_sha256", "e" * 64),
        "findings": (
            "blocking_findings",
            [
                {
                    "code": "LOGIN_BROKEN",
                    "summary": "不得删除实际缺陷",
                    "evidence_sha256": "e" * 64,
                    "acceptance_id": "AC-LOGIN",
                }
            ],
        ),
    }[tamper]
    raw[key] = value
    reference = artifacts.put_json(raw)
    reviewer = children["delegate_2"]
    kernel.finish_child(
        reviewer.id,
        status="succeeded",
        artifacts=(
            ArtifactEnvelope(
                contract_id="review-artifact-v1",
                reference=reference,
                sha256=reference.sha256,
            ),
        ),
    )
    with pytest.raises(ProductError):
        kernel.record_review(
            run_id,
            ReviewArtifactCreate(
                reviewer_agent_run_id=reviewer.id,
                candidate_sha="b" * 40,
                diff_sha256="c" * 64,
                blocking_findings=(),
                artifact_reference=reference,
            ),
        )
    assert kernel.tree(run_id).reviews == ()


@pytest.mark.parametrize(
    "tamper",
    ["unknown_id", "unknown_workcell", "duplicate", "missing_workcell", "missing_coverage"],
)
def test_task_ownership_requires_actual_requirement_membership_and_complete_coverage(
    tamper: str,
) -> None:
    requirements, task = planning_payloads()
    if tamper == "unknown_id":
        task["workcell_acceptance"][0]["acceptance"][0]["acceptance_id"] = "AC-FAKE"
    elif tamper == "unknown_workcell":
        task["workcell_acceptance"][0]["workcell_key"] = "another-project"
    elif tamper == "duplicate":
        task["workcell_acceptance"].append(task["workcell_acceptance"][0])
    elif tamper == "missing_workcell":
        task["workcell_acceptance"].pop()
    else:
        requirements["acceptance_criteria"].append(
            {"id": "AC-OTHER", "statement": "另一项真实需求"}
        )
        task["acceptance_ids"].append("AC-OTHER")
    with pytest.raises(ProductError) as invalid:
        validate_workcell_acceptance(requirements, task, WORKCELL_KEYS)
    assert invalid.value.code == "WORKCELL_ACCEPTANCE_ASSIGNMENT_INVALID"


def test_legacy_scope_and_finding_fields_preserve_original_hash_inputs() -> None:
    legacy_snapshot = _snapshot().model_dump(mode="json")
    legacy_snapshot.pop("review_scope")
    assert (
        type(_snapshot()).model_validate(legacy_snapshot).model_dump(mode="json") == legacy_snapshot
    )
    original = {"code": "OLD-CODE", "summary": "历史原文", "evidence_sha256": "e" * 64}
    assert BlockingFinding.model_validate(original).model_dump(mode="json") == original


def test_kernel_cannot_replace_reviewer_registered_output_with_another_empty_artifact(
    tmp_path: Path,
) -> None:
    kernel, artifacts, run_id, children = _reviewing_run(tmp_path)
    scope = kernel.tree(run_id).workcell_run.workcell_snapshot.review_scope
    assert scope is not None
    reviewed = {
        "reviewed_candidate_sha": "b" * 40,
        "reviewed_diff_sha256": "c" * 64,
        "review_scope_sha256": scope.sha256,
        "blocking_findings": [
            {
                "code": "LOGIN_BROKEN",
                "summary": "真实 Reviewer 指出缺陷",
                "evidence_sha256": "d" * 64,
                "acceptance_id": "AC-LOGIN",
            }
        ],
    }
    original = artifacts.put_json(reviewed)
    reviewer = children["delegate_2"]
    kernel.finish_child(
        reviewer.id,
        status="succeeded",
        artifacts=(
            ArtifactEnvelope(
                contract_id="review-artifact-v1",
                reference=original,
                sha256=original.sha256,
            ),
        ),
    )
    replacement = artifacts.put_json({**reviewed, "blocking_findings": []})
    with pytest.raises(ProductError) as replaced:
        kernel.record_review(
            run_id,
            ReviewArtifactCreate(
                reviewer_agent_run_id=reviewer.id,
                candidate_sha="b" * 40,
                diff_sha256="c" * 64,
                blocking_findings=(),
                artifact_reference=replacement,
            ),
        )
    assert replaced.value.code == "WORKCELL_REVIEW_OUTPUT_NOT_REGISTERED"
    assert kernel.tree(run_id).reviews == ()
