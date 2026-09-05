from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from review_scope_helpers import review_scope

from agent_team_os.delivery import DeliveryRun, SQLiteDeliveryRepository
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.agents import ArtifactEnvelope
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.workcells import (
    BlockingFinding,
    CandidateVerificationCreate,
    DelegationAssignment,
    FrozenSlotBinding,
    ReviewArtifactCreate,
    SQLiteWorkcellExecutionRepository,
    WorkcellExecutionModule,
    WorkcellExecutionSnapshot,
    WorkcellResultCreate,
    WorkcellResultValidationCreate,
    WorkcellRunCreate,
    WorkcellRunTree,
    WorkcellWorkspaceSnapshot,
    create_workcell_execution_router,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json


def _kernel(tmp_path: Path) -> tuple[WorkcellExecutionModule, ContentAddressedArtifactStorage]:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    SQLiteDeliveryRepository(database).save(
        DeliveryRun(
            id="delivery-workcell",
            workspace_id="project:workcell",
            user_request="实现四仓交付",
            status="executing",
            version=1,
            resolved_journey_sha256="1" * 64,
            evidence_identity="deterministic-test",
            planning_identity="deterministic-test",
        )
    )
    artifacts = ContentAddressedArtifactStorage(tmp_path / "artifacts")
    return (
        WorkcellExecutionModule(
            SQLiteWorkcellExecutionRepository(database),
            artifact_storage=artifacts,
        ),
        artifacts,
    )


def _snapshot() -> WorkcellExecutionSnapshot:
    return WorkcellExecutionSnapshot(
        team_template_revision_id="software-delivery-team:1",
        team_template_sha256="2" * 64,
        pipeline_revision_id="fullstack-product-delivery:2",
        pipeline_revision_sha256="3" * 64,
        stage_path="frontend.delivery",
        workcell_key="frontend",
        workspace=WorkcellWorkspaceSnapshot(
            workspace_binding_id="workspace-frontend",
            kind="git_repository_v1",
            adapter_type="managed-bare-git",
            repository_uri="projects/workcell/frontend",
            base_revision="4" * 40,
            verification_sha256="5" * 64,
        ),
        delegation_policy={
            "max_children": 3,
            "max_concurrency": 2,
            "max_writers": 1,
            "max_depth": 1,
            "wall_clock_budget_seconds": 900,
        },
        slot_bindings=tuple(
            FrozenSlotBinding(
                slot_key=slot,
                deployment_id=f"deployment-{slot}",
                resolved_provider_binding_hash=character * 64,
                deployment_snapshot={
                    "deployment_id": f"deployment-{slot}",
                    "runtime_identity": "deterministic-workcell",
                },
            )
            for slot, character in (
                ("main", "6"),
                ("delegate_1", "7"),
                ("delegate_2", "8"),
                ("delegate_3", "9"),
            )
        ),
        method_snapshot_sha256="a" * 64,
        review_scope=review_scope(),
    )


def test_new_workcell_without_frozen_review_scope_fails_closed(tmp_path: Path) -> None:
    kernel, _ = _kernel(tmp_path)
    snapshot = _snapshot().model_copy(update={"review_scope": None})
    with pytest.raises(ProductError) as missing:
        kernel.create(
            WorkcellRunCreate(
                delivery_id="delivery-workcell",
                pipeline_run_id="pipeline-no-scope",
                stage_attempt_id="attempt-no-scope",
                snapshot=snapshot,
            )
        )
    assert missing.value.code == "WORKCELL_REVIEW_SCOPE_REQUIRED"
    assert kernel.list_delivery("delivery-workcell") == ()


def test_main_writer_machine_verification_parallel_reviews_and_synthesis(
    tmp_path: Path,
) -> None:
    kernel, artifacts = _kernel(tmp_path)
    run = kernel.create(
        WorkcellRunCreate(
            delivery_id="delivery-workcell",
            pipeline_run_id="pipeline-run-1",
            stage_attempt_id="frontend-attempt-1",
            snapshot=_snapshot(),
        )
    )
    assert run.workcell_run.status == "planning"
    main_id = run.workcell_run.main_agent_run_id
    assert main_id is not None

    planned = kernel.submit_delegation_plan(
        run.workcell_run.id,
        (
            DelegationAssignment(
                slot_key="delegate_1",
                delegate_purpose="workspace_write",
                workspace_access="workspace_write",
            ),
            DelegationAssignment(
                slot_key="delegate_2",
                delegate_purpose="review",
                workspace_access="candidate_read",
            ),
            DelegationAssignment(
                slot_key="delegate_3",
                delegate_purpose="review",
                workspace_access="candidate_read",
            ),
        ),
    )
    children = {item.slot_key: item for item in planned.agent_runs if item.run_role == "child"}
    writer = children["delegate_1"]
    reviewers = (children["delegate_2"], children["delegate_3"])
    kernel.start_child(writer.id)
    with pytest.raises(ProductError) as review_too_early:
        kernel.start_child(reviewers[0].id)
    assert review_too_early.value.code == "REVIEW_CANDIDATE_NOT_VERIFIED"

    candidate_reference = artifacts.put_json({"candidate_sha": "b" * 40, "diff_sha256": "c" * 64})
    kernel.finish_child(
        writer.id,
        status="succeeded",
        artifacts=(
            ArtifactEnvelope(
                contract_id="workspace-candidate-v2",
                reference=candidate_reference,
                sha256=candidate_reference.sha256,
            ),
        ),
    )
    verification = kernel.record_candidate_verification(
        run.workcell_run.id,
        CandidateVerificationCreate(
            writer_agent_run_id=writer.id,
            candidate_sha="b" * 40,
            diff_sha256="c" * 64,
            status="passed",
            report={"commands": ["pnpm test"], "exit_code": 0},
        ),
    )

    for reviewer in reviewers:
        kernel.start_child(reviewer.id)
    assert (
        sum(item.status == "running" for item in kernel.tree(run.workcell_run.id).agent_runs) == 2
    )

    review_ids: list[str] = []
    for reviewer in reviewers:
        review_reference = artifacts.put_json(
            {
                "reviewed_candidate_sha": "b" * 40,
                "reviewed_diff_sha256": "c" * 64,
                "review_scope_sha256": review_scope().sha256,
                "blocking_findings": [],
            }
        )
        kernel.finish_child(
            reviewer.id,
            status="succeeded",
            artifacts=(
                ArtifactEnvelope(
                    contract_id="review-artifact-v1",
                    reference=review_reference,
                    sha256=review_reference.sha256,
                ),
            ),
        )
        reviewed = kernel.record_review(
            run.workcell_run.id,
            ReviewArtifactCreate(
                reviewer_agent_run_id=reviewer.id,
                candidate_sha="b" * 40,
                diff_sha256="c" * 64,
                blocking_findings=(),
                artifact_reference=review_reference,
            ),
        )
        review_ids.append(reviewed.reviews[-1].id)

    synthesizing = kernel.start_synthesis(run.workcell_run.id)
    main = next(item for item in synthesizing.agent_runs if item.id == main_id)
    assert main.status == "running"
    assert [
        attempt.phase for attempt in synthesizing.attempts if attempt.agent_run_id == main_id
    ] == ["planning", "synthesis"]

    output = artifacts.put_json({"summary": "frontend candidate accepted"})
    completed = kernel.complete(
        run.workcell_run.id,
        WorkcellResultCreate(
            candidate_sha="b" * 40,
            diff_sha256="c" * 64,
            verification_sha256=verification.sha256,
            review_artifact_ids=tuple(review_ids),
            output_artifact_references=(output,),
        ),
    )
    assert completed.workcell_run.status == "succeeded"
    assert completed.result is not None
    assert len(completed.agent_runs) == 4
    delivery_attempts = kernel.list_delivery_attempts("delivery-workcell")
    assert {item.id for item in delivery_attempts} == {item.id for item in completed.attempts}


def test_constraints_cancellation_and_blocking_evidence_fail_closed(tmp_path: Path) -> None:
    kernel, artifacts = _kernel(tmp_path)
    created = kernel.create(
        WorkcellRunCreate(
            delivery_id="delivery-workcell",
            pipeline_run_id="pipeline-run-2",
            stage_attempt_id="frontend-attempt-2",
            snapshot=_snapshot(),
        )
    )
    with pytest.raises(ProductError) as duplicate_writer:
        kernel.submit_delegation_plan(
            created.workcell_run.id,
            (
                DelegationAssignment(
                    slot_key="delegate_1",
                    delegate_purpose="workspace_write",
                    workspace_access="workspace_write",
                ),
                DelegationAssignment(
                    slot_key="delegate_2",
                    delegate_purpose="workspace_write",
                    workspace_access="workspace_write",
                ),
            ),
        )
    assert duplicate_writer.value.code == "WORKCELL_WRITER_LIMIT_EXCEEDED"

    planned = kernel.submit_delegation_plan(
        created.workcell_run.id,
        (
            DelegationAssignment(
                slot_key="delegate_1",
                delegate_purpose="artifact",
                workspace_access="artifact_only",
            ),
            DelegationAssignment(
                slot_key="delegate_2",
                delegate_purpose="artifact",
                workspace_access="artifact_only",
            ),
            DelegationAssignment(
                slot_key="delegate_3",
                delegate_purpose="artifact",
                workspace_access="artifact_only",
            ),
        ),
    )
    children = [item for item in planned.agent_runs if item.run_role == "child"]
    kernel.start_child(children[0].id)
    kernel.start_child(children[1].id)
    with pytest.raises(ProductError) as concurrency:
        kernel.start_child(children[2].id)
    assert concurrency.value.code == "WORKCELL_CHILD_CONCURRENCY_EXCEEDED"

    cancelled = kernel.cancel(
        created.workcell_run.id,
        expected_version=kernel.tree(created.workcell_run.id).workcell_run.version,
    )
    assert cancelled.workcell_run.status == "cancelled"
    assert all(item.status == "cancelled" for item in cancelled.agent_runs)
    assert sorted(item.status for item in cancelled.attempts) == [
        "cancelled",
        "cancelled",
        "succeeded",
    ]

    blocking_run = kernel.create(
        WorkcellRunCreate(
            delivery_id="delivery-workcell",
            pipeline_run_id="pipeline-run-3",
            stage_attempt_id="frontend-attempt-3",
            snapshot=_snapshot(),
        )
    )
    planned = kernel.submit_delegation_plan(
        blocking_run.workcell_run.id,
        (
            DelegationAssignment(
                slot_key="delegate_1",
                delegate_purpose="workspace_write",
                workspace_access="workspace_write",
            ),
            DelegationAssignment(
                slot_key="delegate_2",
                delegate_purpose="review",
                workspace_access="candidate_read",
            ),
        ),
    )
    children = {
        item.delegate_purpose: item for item in planned.agent_runs if item.run_role == "child"
    }
    kernel.start_child(children["workspace_write"].id)
    kernel.finish_child(children["workspace_write"].id, status="succeeded")
    kernel.record_candidate_verification(
        blocking_run.workcell_run.id,
        CandidateVerificationCreate(
            writer_agent_run_id=children["workspace_write"].id,
            candidate_sha="d" * 40,
            diff_sha256="e" * 64,
            status="passed",
            report={"exit_code": 0},
        ),
    )
    reviewer = children["review"]
    kernel.start_child(reviewer.id)
    finding = BlockingFinding(
        code="UX_EDGE",
        summary="键盘用户无法完成关键路径",
        evidence_sha256=sha256_json({"case": "keyboard"}),
        acceptance_id="AC-LOGIN",
    )
    review_reference = artifacts.put_json(
        {
            "reviewed_candidate_sha": "d" * 40,
            "reviewed_diff_sha256": "e" * 64,
            "review_scope_sha256": review_scope().sha256,
            "blocking_findings": [finding.model_dump(mode="json")],
        }
    )
    kernel.finish_child(
        reviewer.id,
        status="succeeded",
        artifacts=(
            ArtifactEnvelope(
                contract_id="review-artifact-v1",
                reference=review_reference,
                sha256=review_reference.sha256,
            ),
        ),
    )
    blocked = kernel.record_review(
        blocking_run.workcell_run.id,
        ReviewArtifactCreate(
            reviewer_agent_run_id=reviewer.id,
            candidate_sha="d" * 40,
            diff_sha256="e" * 64,
            blocking_findings=(finding,),
            artifact_reference=review_reference,
        ),
    )
    assert blocked.workcell_run.status == "failed"
    assert blocked.workcell_run.error_code == "WORKCELL_BLOCKING_REVIEW"
    with pytest.raises(ProductError) as cannot_override:
        kernel.start_synthesis(blocking_run.workcell_run.id)
    assert cannot_override.value.code == "WORKCELL_BLOCKING_REVIEW"


def test_unexpected_workcell_failure_terminalizes_main_attempt(tmp_path: Path) -> None:
    kernel, _artifacts = _kernel(tmp_path)
    created = kernel.create(
        WorkcellRunCreate(
            delivery_id="delivery-workcell",
            pipeline_run_id="pipeline-run-failure",
            stage_attempt_id="frontend-attempt-failure",
            snapshot=_snapshot(),
        )
    )

    failed = kernel.fail(
        created.workcell_run.id,
        error_code="WORKCELL_MAIN_DELEGATION_PLAN_INVALID",
    )

    assert failed.workcell_run.status == "failed"
    assert failed.workcell_run.error_code == "WORKCELL_MAIN_DELEGATION_PLAN_INVALID"
    assert [item.status for item in failed.agent_runs] == ["failed"]
    assert [item.status for item in failed.attempts] == ["failed"]
    assert [item.error_code for item in failed.attempts] == [
        "WORKCELL_MAIN_DELEGATION_PLAN_INVALID"
    ]


def test_workcell_cancel_endpoint_notifies_the_runtime_parent(tmp_path: Path) -> None:
    kernel, _artifacts = _kernel(tmp_path)
    created = kernel.create(
        WorkcellRunCreate(
            delivery_id="delivery-workcell",
            pipeline_run_id="pipeline-run-cancel-http",
            stage_attempt_id="frontend-attempt-cancel-http",
            snapshot=_snapshot(),
        )
    )
    cancelled_deliveries: list[str] = []

    async def before_cancel(tree: WorkcellRunTree) -> None:
        cancelled_deliveries.append(tree.workcell_run.delivery_id)

    app = FastAPI()
    app.include_router(create_workcell_execution_router(kernel, before_cancel=before_cancel))

    with TestClient(app) as client:
        response = client.post(
            f"/v1/workcell-runs/{created.workcell_run.id}/cancel",
            json={"expected_version": created.workcell_run.version},
        )

    assert response.status_code == 200
    assert response.json()["workcell_run"]["status"] == "cancelled"
    assert cancelled_deliveries == ["delivery-workcell"]


def test_artifact_only_workcell_requires_product_result_validation(tmp_path: Path) -> None:
    kernel, artifacts = _kernel(tmp_path)
    snapshot = _snapshot().model_copy(
        update={
            "stage_path": "qa-preparation-repair/qa-preparation",
            "workcell_key": "qa",
            "review_scope": review_scope("qa"),
            "slot_method_bindings": {
                "delegate_1": "bmad-testarch-test-design",
                "delegate_2": "bmad-testarch-atdd",
            },
            "slot_purpose_bindings": {
                "delegate_1": "artifact",
                "delegate_2": "artifact",
            },
        }
    )
    created = kernel.create(
        WorkcellRunCreate(
            delivery_id="delivery-workcell",
            pipeline_run_id="pipeline-run-4",
            stage_attempt_id="qa-preparation-attempt-1",
            snapshot=snapshot,
        )
    )
    planned = kernel.submit_delegation_plan(
        created.workcell_run.id,
        (
            DelegationAssignment(
                slot_key="delegate_1",
                delegate_purpose="artifact",
                workspace_access="artifact_only",
                method_id="bmad-testarch-test-design",
            ),
            DelegationAssignment(
                slot_key="delegate_2",
                delegate_purpose="artifact",
                workspace_access="artifact_only",
                method_id="bmad-testarch-atdd",
            ),
        ),
    )
    outputs = []
    for child in (item for item in planned.agent_runs if item.run_role == "child"):
        reference = artifacts.put_json(
            {"method_id": child.slot_key, "acceptance_coverage": ["AC-1"]}
        )
        outputs.append(reference)
        kernel.start_child(child.id)
        kernel.finish_child(
            child.id,
            status="succeeded",
            artifacts=(
                ArtifactEnvelope(
                    contract_id="qa-preparation-artifact-v1",
                    reference=reference,
                    sha256=reference.sha256,
                ),
            ),
        )
    kernel.start_synthesis(created.workcell_run.id)
    with pytest.raises(ProductError) as missing_validation:
        kernel.complete(
            created.workcell_run.id,
            WorkcellResultCreate(
                verification_sha256="f" * 64,
                output_artifact_references=tuple(outputs),
            ),
        )
    assert missing_validation.value.code == "WORKCELL_RESULT_VALIDATION_REQUIRED"

    validation = kernel.record_result_validation(
        created.workcell_run.id,
        WorkcellResultValidationCreate(
            status="passed",
            artifact_references=tuple(outputs),
            report={"contracts": ["test-design-v1", "atdd-v1"]},
        ),
    )
    completed = kernel.complete(
        created.workcell_run.id,
        WorkcellResultCreate(
            verification_sha256=validation.sha256,
            output_artifact_references=tuple(outputs),
        ),
    )
    assert completed.workcell_run.status == "succeeded"
    assert completed.result_validation == validation
