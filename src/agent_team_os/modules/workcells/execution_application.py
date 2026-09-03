from __future__ import annotations

from datetime import UTC, datetime

from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_json
from ..agents import ArtifactEnvelope
from ..artifacts import ContentAddressedArtifactStorage
from .execution_domain import (
    AgentAttempt,
    CandidateVerification,
    CandidateVerificationCreate,
    DelegationAssignment,
    ReviewArtifact,
    ReviewArtifactCreate,
    WorkcellResult,
    WorkcellResultCreate,
    WorkcellResultValidation,
    WorkcellResultValidationCreate,
    WorkcellRunCreate,
    WorkcellRunTree,
)
from .execution_repository import SQLiteWorkcellExecutionRepository


class WorkcellExecutionModule:
    """Product-owned observable scheduler for one-level Main/Child workcells."""

    def __init__(
        self,
        repository: SQLiteWorkcellExecutionRepository,
        *,
        artifact_storage: ContentAddressedArtifactStorage,
    ) -> None:
        self.repository = repository
        self.artifact_storage = artifact_storage

    def create(self, request: WorkcellRunCreate) -> WorkcellRunTree:
        try:
            run = self.repository.create(request)
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise _error(
                    "WORKCELL_STAGE_ATTEMPT_ALREADY_EXISTS",
                    "Stage Attempt 已经创建 WorkcellRun",
                    "读取现有 WorkcellRun；Repair 必须由新的 Loop Iteration 创建新 Run。",
                ) from error
            raise
        return self.tree(run.id)

    def tree(self, run_id: str) -> WorkcellRunTree:
        try:
            run = self.repository.get(run_id)
        except KeyError as error:
            raise _error(
                "WORKCELL_RUN_NOT_FOUND",
                "WorkcellRun 不存在",
                "刷新 Delivery Workcell Tree 后重试。",
                404,
            ) from error
        return WorkcellRunTree(
            workcell_run=run,
            delegation_plan=self.repository.get_plan(run_id),
            agent_runs=self.repository.list_agents(run_id),
            attempts=self.repository.list_attempts(run_id),
            verification=self.repository.get_verification(run_id),
            result_validation=self.repository.get_result_validation(run_id),
            reviews=self.repository.list_reviews(run_id),
            result=self.repository.get_result(run_id),
        )

    def list_delivery(self, delivery_id: str) -> tuple[WorkcellRunTree, ...]:
        return tuple(self.tree(item.id) for item in self.repository.list_delivery(delivery_id))

    def list_delivery_attempts(self, delivery_id: str) -> tuple[AgentAttempt, ...]:
        return self.repository.list_delivery_attempts(delivery_id)

    def submit_delegation_plan(
        self,
        run_id: str,
        assignments: tuple[DelegationAssignment, ...],
        *,
        planning_artifact_sha256: Sha256 | None = None,
    ) -> WorkcellRunTree:
        run = self.tree(run_id).workcell_run
        self._ensure_deadline(run_id)
        if run.status != "planning":
            raise _state_error(run.status)
        policy = run.workcell_snapshot.delegation_policy
        if len(assignments) > policy.max_children:
            raise _error(
                "WORKCELL_CHILD_LIMIT_EXCEEDED",
                "Child 数量超过冻结的 DelegationPolicy",
                f"最多允许 {policy.max_children} 个 Child。",
            )
        slots = tuple(item.slot_key for item in assignments)
        if len(set(slots)) != len(slots):
            raise _error(
                "WORKCELL_DELEGATE_SLOT_DUPLICATE",
                "DelegationPlan 重复使用 Delegate Slot",
                "每个冻结 Slot 最多创建一个 Child Run。",
            )
        frozen_slots = {item.slot_key for item in run.workcell_snapshot.slot_bindings}
        if any(slot not in frozen_slots for slot in slots):
            raise _error(
                "WORKCELL_DELEGATE_SLOT_NOT_FROZEN",
                "DelegationPlan 引用了未冻结 Slot",
                "只能使用 Published Pipeline 中已经解析的 Delegate Slot。",
            )
        writer_count = sum(item.delegate_purpose == "workspace_write" for item in assignments)
        if writer_count > policy.max_writers:
            raise _error(
                "WORKCELL_WRITER_LIMIT_EXCEEDED",
                "WorkcellRun 只能有一个 Workspace Writer",
                "保留一个 workspace_write Child，其余改为 Review 或 Artifact-only。",
            )
        for assignment in assignments:
            frozen_purpose = run.workcell_snapshot.slot_purpose_bindings.get(
                assignment.slot_key
            )
            frozen_method = run.workcell_snapshot.slot_method_bindings.get(
                assignment.slot_key
            )
            if frozen_purpose is not None and assignment.delegate_purpose != frozen_purpose:
                raise _error(
                    "WORKCELL_DELEGATE_PURPOSE_NOT_FROZEN",
                    "DelegationPlan 改变了 Published Pipeline 的 Delegate Purpose",
                    "使用 Workcell Snapshot 中冻结的 Slot Purpose。",
                )
            if frozen_method is not None and assignment.method_id != frozen_method:
                raise _error(
                    "WORKCELL_METHOD_ENTRY_NOT_FROZEN",
                    "DelegationPlan 改变了 Published Pipeline 的 Method Entry",
                    "使用 Workcell Snapshot 中冻结的 Method Entry。",
                )
        allowed_inputs = {
            reference.sha256 for reference in run.workcell_snapshot.input_artifacts
        }
        for assignment in assignments:
            for reference in assignment.input_artifacts:
                self.artifact_storage.get_bytes(reference)
                if reference.sha256 not in allowed_inputs:
                    raise _error(
                        "WORKCELL_INPUT_ARTIFACT_NOT_FROZEN",
                        "Child 输入不属于冻结的 ArtifactAttachment",
                        "只引用 DeliveryExecutionSnapshot 中内容寻址的输入工件。",
                    )
        try:
            self.repository.put_plan(
                run,
                assignments,
                planning_artifact_sha256=planning_artifact_sha256,
            )
        except RuntimeError as error:
            raise _repository_error(error) from error
        return self.tree(run_id)

    def start_child(self, agent_run_id: str) -> WorkcellRunTree:
        try:
            child = self.repository.get_agent(agent_run_id)
        except KeyError as error:
            raise _error(
                "AGENT_RUN_NOT_FOUND",
                "AgentRun 不存在",
                "刷新 WorkcellRun Tree 后重试。",
                404,
            ) from error
        if child.run_role != "child" or child.depth != 1 or child.parent_agent_run_id is None:
            raise _error(
                "WORKCELL_CHILD_DEPTH_INVALID",
                "只有 Main 的一级 Child 可以启动",
                "禁止 Child 再派生 AgentRun。",
            )
        if child.workcell_run_id is None:
            raise _error(
                "AGENT_RUN_NOT_IN_WORKCELL",
                "AgentRun 不属于 WorkcellRun",
                "使用 Workcell Execution Module 创建 Child。",
            )
        tree = self.tree(child.workcell_run_id)
        self._ensure_deadline(child.workcell_run_id)
        if tree.workcell_run.status in {
            "failed",
            "cancelled",
            "timed_out",
            "interrupted",
            "succeeded",
        }:
            raise _state_error(tree.workcell_run.status)
        try:
            self.repository.start_child(
                child,
                max_concurrency=tree.workcell_run.workcell_snapshot.delegation_policy.max_concurrency,
            )
        except RuntimeError as error:
            raise _repository_error(error) from error
        return self.tree(child.workcell_run_id)

    def finish_child(
        self,
        agent_run_id: str,
        *,
        status: str,
        artifacts: tuple[ArtifactEnvelope, ...] = (),
        error_code: str | None = None,
    ) -> WorkcellRunTree:
        if status not in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}:
            raise _error(
                "AGENT_RUN_TERMINAL_STATUS_INVALID",
                "Child 终态无效",
                "使用 succeeded、failed、cancelled、timed_out 或 interrupted。",
                422,
            )
        try:
            child = self.repository.get_agent(agent_run_id)
        except KeyError as error:
            raise _error(
                "AGENT_RUN_NOT_FOUND",
                "AgentRun 不存在",
                "刷新 WorkcellRun Tree 后重试。",
                404,
            ) from error
        if child.run_role != "child" or child.workcell_run_id is None:
            raise _error(
                "AGENT_RUN_NOT_WORKCELL_CHILD",
                "目标 AgentRun 不是 Workcell Child",
                "选择 Main 创建的一级 Child Run。",
            )
        persisted = tuple(self._persist(item) for item in artifacts)
        try:
            self.repository.finish_child(
                child,
                status=status,
                artifacts=persisted,
                error_code=error_code,
            )
        except RuntimeError as error:
            raise _repository_error(error) from error
        return self.tree(child.workcell_run_id)

    def record_candidate_verification(
        self,
        run_id: str,
        request: CandidateVerificationCreate,
    ) -> CandidateVerification:
        tree = self.tree(run_id)
        run = tree.workcell_run
        if run.status not in {"delegating", "verifying"}:
            raise _state_error(run.status)
        if tree.verification is not None:
            raise _error(
                "WORKCELL_VERIFICATION_ALREADY_RECORDED",
                "Candidate Verification 已经冻结",
                "Repair 由 ACWM 新建下一 Loop Iteration 的 WorkcellRun。",
            )
        writer = next(
            (item for item in tree.agent_runs if item.id == request.writer_agent_run_id),
            None,
        )
        if (
            writer is None
            or writer.delegate_purpose != "workspace_write"
            or writer.status != "succeeded"
        ):
            raise _error(
                "WORKCELL_WRITER_NOT_SUCCEEDED",
                "机器验证只能绑定已成功的唯一 Writer",
                "等待 Writer 完成 Candidate 后再执行产品机器验证。",
            )
        payload = {
            "workcell_run_id": run_id,
            **request.model_dump(mode="json"),
        }
        verification = CandidateVerification(
            workcell_run_id=run_id,
            **request.model_dump(),
            sha256=sha256_json(payload),
        )
        try:
            self.repository.put_verification(run, verification)
        except (RuntimeError, Exception) as error:
            if isinstance(error, RuntimeError):
                raise _repository_error(error) from error
            raise
        return verification

    def record_review(
        self,
        run_id: str,
        request: ReviewArtifactCreate,
    ) -> WorkcellRunTree:
        tree = self.tree(run_id)
        run = tree.workcell_run
        verification = tree.verification
        collecting_launched_batch = (
            run.status == "failed" and run.error_code == "WORKCELL_BLOCKING_REVIEW"
        )
        accepts_review_evidence = run.status == "reviewing" or collecting_launched_batch
        if (
            not accepts_review_evidence
            or verification is None
            or verification.status != "passed"
        ):
            raise _error(
                "REVIEW_CANDIDATE_NOT_VERIFIED",
                "Reviewer 只能审查已通过机器验证的不可变 Candidate",
                "先完成绑定 Candidate SHA 的 Product Machine Verification。",
            )
        reviewer = next(
            (item for item in tree.agent_runs if item.id == request.reviewer_agent_run_id),
            None,
        )
        if (
            reviewer is None
            or reviewer.delegate_purpose != "review"
            or reviewer.status != "succeeded"
        ):
            raise _error(
                "WORKCELL_REVIEWER_NOT_SUCCEEDED",
                "ReviewArtifact 必须绑定已成功的 Reviewer Child",
                "等待 Reviewer 完成后提交结构化 ReviewArtifact。",
            )
        if (
            request.candidate_sha != verification.candidate_sha
            or request.diff_sha256 != verification.diff_sha256
        ):
            raise _error(
                "REVIEW_CANDIDATE_EVIDENCE_MISMATCH",
                "ReviewArtifact 与机器验证 Candidate 不一致",
                "对同一 Candidate SHA 与 Diff SHA 重新执行审查。",
            )
        self.artifact_storage.get_bytes(request.artifact_reference)
        payload = {
            "workcell_run_id": run_id,
            "reviewer_binding_hash": reviewer.resolved_binding_hash,
            **request.model_dump(mode="json"),
        }
        review = ReviewArtifact(
            workcell_run_id=run_id,
            reviewer_binding_hash=reviewer.resolved_binding_hash,
            **request.model_dump(),
            sha256=sha256_json(payload),
        )
        try:
            self.repository.put_review(run, review)
        except RuntimeError as error:
            raise _repository_error(error) from error
        return self.tree(run_id)

    def start_synthesis(self, run_id: str) -> WorkcellRunTree:
        tree = self.tree(run_id)
        run = tree.workcell_run
        self._ensure_deadline(run_id)
        if run.error_code == "WORKCELL_BLOCKING_REVIEW":
            raise _error(
                "WORKCELL_BLOCKING_REVIEW",
                "Blocking Review 不可由 Main 覆盖",
                "由 ACWM bounded Loop 创建新的 WorkcellRun 修复。",
            )
        if run.status in {"failed", "cancelled", "timed_out", "interrupted", "succeeded"}:
            raise _state_error(run.status)
        children = tuple(item for item in tree.agent_runs if item.run_role == "child")
        if any(item.status != "succeeded" for item in children):
            raise _error(
                "WORKCELL_CHILDREN_NOT_COMPLETE",
                "Main Synthesis 等待所有 Child 成功完成",
                "完成或取消未终结 Child；失败后由 ACWM 创建 Repair Run。",
            )
        writers = tuple(item for item in children if item.delegate_purpose == "workspace_write")
        if writers and (tree.verification is None or tree.verification.status != "passed"):
            raise _error(
                "MACHINE_VERIFICATION_NOT_PASSED",
                "Writer Candidate 尚未通过机器验证",
                "完成 Product Machine Verification 后再合成结果。",
            )
        reviewers = tuple(item for item in children if item.delegate_purpose == "review")
        if {item.id for item in reviewers} != {
            item.reviewer_agent_run_id for item in tree.reviews
        }:
            raise _error(
                "WORKCELL_REVIEW_ARTIFACTS_INCOMPLETE",
                "Reviewer 尚未全部提交结构化 ReviewArtifact",
                "为每个 Reviewer 记录绑定同一 Candidate 的 ReviewArtifact。",
            )
        if any(item.blocking_findings for item in tree.reviews):
            raise _error(
                "WORKCELL_BLOCKING_REVIEW",
                "Blocking Review 不可由 Main 覆盖",
                "由 ACWM bounded Loop 创建新的 WorkcellRun 修复。",
            )
        if run.main_agent_run_id is None:
            raise _error(
                "WORKCELL_MAIN_RUN_MISSING",
                "WorkcellRun 缺少 Main AgentRun",
                "将该 Run 标记为失败并重新创建 Stage Attempt。",
            )
        main = next(item for item in tree.agent_runs if item.id == run.main_agent_run_id)
        try:
            self.repository.start_synthesis(run, main)
        except RuntimeError as error:
            raise _repository_error(error) from error
        return self.tree(run_id)

    def record_result_validation(
        self,
        run_id: str,
        request: WorkcellResultValidationCreate,
    ) -> WorkcellResultValidation:
        tree = self.tree(run_id)
        if tree.workcell_run.status != "synthesizing":
            raise _state_error(tree.workcell_run.status)
        if tree.verification is not None:
            raise _error(
                "WORKCELL_RESULT_VALIDATION_NOT_ARTIFACT_ONLY",
                "Candidate Workcell 使用机器验证而不是 Artifact-only Validation",
                "只为没有 Git Candidate 的 WorkcellRun 记录 Result Validation。",
            )
        for reference in request.artifact_references:
            self.artifact_storage.get_bytes(reference)
        payload = {"workcell_run_id": run_id, **request.model_dump(mode="json")}
        validation = WorkcellResultValidation(
            workcell_run_id=run_id,
            **request.model_dump(),
            sha256=sha256_json(payload),
        )
        try:
            return self.repository.put_result_validation(validation)
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise _error(
                    "WORKCELL_RESULT_VALIDATION_ALREADY_RECORDED",
                    "WorkcellResult Validation 已经冻结",
                    "Repair 必须由 ACWM 创建新的 WorkcellRun。",
                ) from error
            raise

    def complete(
        self,
        run_id: str,
        request: WorkcellResultCreate,
        *,
        synthesis_artifact_sha256: Sha256 | None = None,
    ) -> WorkcellRunTree:
        tree = self.tree(run_id)
        run = tree.workcell_run
        if run.status != "synthesizing":
            raise _state_error(run.status)
        if (request.candidate_sha is None) != (request.diff_sha256 is None):
            raise _error(
                "WORKCELL_RESULT_CANDIDATE_INCOMPLETE",
                "Candidate SHA 与 Diff SHA 必须同时出现",
                "Artifact-only Run 同时省略二者；Writer Run 同时提供二者。",
                422,
            )
        if tree.verification is not None and (
            request.verification_sha256 != tree.verification.sha256
            or request.candidate_sha != tree.verification.candidate_sha
            or request.diff_sha256 != tree.verification.diff_sha256
        ):
            raise _error(
                "WORKCELL_RESULT_VERIFICATION_MISMATCH",
                "WorkcellResult 未绑定冻结的机器验证结果",
                "使用同一 Candidate、Diff 和 Verification SHA 合成结果。",
            )
        if tree.verification is None:
            validation = tree.result_validation
            if validation is None or validation.status != "passed":
                raise _error(
                    "WORKCELL_RESULT_VALIDATION_REQUIRED",
                    "Artifact-only WorkcellResult 缺少通过的产品校验",
                    "先记录绑定输出 Artifact 的 WorkcellResultValidation。",
                )
            if (
                request.verification_sha256 != validation.sha256
                or request.output_artifact_references != validation.artifact_references
            ):
                raise _error(
                    "WORKCELL_RESULT_VALIDATION_MISMATCH",
                    "WorkcellResult 与冻结的 Artifact Validation 不一致",
                    "使用 Validation 绑定的相同 Artifact 与 SHA。",
                )
        expected_reviews = {item.id for item in tree.reviews}
        if set(request.review_artifact_ids) != expected_reviews:
            raise _error(
                "WORKCELL_RESULT_REVIEW_MISMATCH",
                "WorkcellResult 未引用完整 ReviewArtifact 集合",
                "引用当前 WorkcellRun 的全部结构化 ReviewArtifact。",
            )
        for reference in request.output_artifact_references:
            self.artifact_storage.get_bytes(reference)
        payload = {"workcell_run_id": run_id, **request.model_dump(mode="json")}
        result = WorkcellResult(
            workcell_run_id=run_id,
            **request.model_dump(),
            sha256=sha256_json(payload),
        )
        try:
            self.repository.put_result(
                run,
                result,
                synthesis_artifact_sha256=synthesis_artifact_sha256,
            )
        except RuntimeError as error:
            raise _repository_error(error) from error
        return self.tree(run_id)

    def cancel(self, run_id: str, *, expected_version: int) -> WorkcellRunTree:
        tree = self.tree(run_id)
        run = tree.workcell_run
        if run.version != expected_version:
            raise _error(
                "WORKCELL_RUN_VERSION_CONFLICT",
                "WorkcellRun 版本冲突",
                "刷新 Tree 后重新取消。",
            )
        if run.status in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}:
            if run.status == "cancelled":
                return tree
            raise _state_error(run.status)
        try:
            self.repository.cancel(run, expected_version=expected_version)
        except RuntimeError as error:
            raise _repository_error(error) from error
        return self.tree(run_id)

    def fail(self, run_id: str, *, error_code: str) -> WorkcellRunTree:
        """Persist an unexpected execution failure without leaving phantom running Attempts."""

        tree = self.tree(run_id)
        run = tree.workcell_run
        if run.status in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}:
            return tree
        try:
            self.repository.fail(
                run,
                expected_version=run.version,
                error_code=error_code,
            )
        except RuntimeError as error:
            raise _repository_error(error) from error
        return self.tree(run_id)

    def recover_interrupted_attempts(self) -> tuple[str, ...]:
        return self.repository.interrupt_running_codex_attempts()

    def _ensure_deadline(self, run_id: str) -> None:
        run = self.repository.get(run_id)
        if datetime.now(UTC) >= run.deadline_at:
            if run.status not in {
                "succeeded",
                "failed",
                "cancelled",
                "timed_out",
                "interrupted",
            }:
                self.repository.timeout(run, expected_version=run.version)
            raise _error(
                "WORKCELL_WALL_CLOCK_BUDGET_EXCEEDED",
                "WorkcellRun 已超过 Wall-clock Budget",
                "由 ACWM bounded Loop 决定是否创建新的 Repair Run。",
            )

    def _persist(self, envelope: ArtifactEnvelope) -> ArtifactEnvelope:
        if envelope.reference is not None:
            self.artifact_storage.get_bytes(envelope.reference)
            return envelope
        if envelope.content is None:
            raise _error(
                "ARTIFACT_ENVELOPE_CONTENT_MISSING",
                "ArtifactEnvelope 缺少内容地址",
                "提供内容或已存在的 ArtifactReference。",
                422,
            )
        reference = self.artifact_storage.put_json(envelope.content)
        if reference.sha256 != envelope.sha256:
            raise _error(
                "ARTIFACT_ENVELOPE_HASH_MISMATCH",
                "ArtifactEnvelope Hash 与内容不一致",
                "重新生成内容寻址 Envelope。",
                422,
            )
        return envelope.model_copy(update={"content": None, "reference": reference})


def _repository_error(error: RuntimeError) -> ProductError:
    code = str(error)
    messages = {
        "WORKCELL_CHILD_CONCURRENCY_EXCEEDED": (
            "Child 并发超过冻结上限",
            "等待一个运行中的 Child 进入终态后再启动。",
        ),
        "REVIEW_CANDIDATE_NOT_VERIFIED": (
            "Reviewer 不能读取未验证 Candidate",
            "先完成 Writer 和 Product Machine Verification。",
        ),
        "AGENT_RUN_NOT_PLANNED": (
            "AgentRun 当前不能启动",
            "刷新 WorkcellRun Tree 后重试。",
        ),
        "AGENT_RUN_NOT_RUNNING": (
            "AgentRun 当前不在运行",
            "刷新 WorkcellRun Tree 后重试。",
        ),
        "WORKCELL_RUN_VERSION_CONFLICT": (
            "WorkcellRun 版本冲突",
            "刷新 WorkcellRun Tree 后重新提交。",
        ),
    }
    title, repair = messages.get(
        code,
        ("Workcell Execution 状态冲突", "刷新 WorkcellRun Tree 后重试。"),
    )
    return _error(code, title, repair)


def _state_error(status: str) -> ProductError:
    return _error(
        "WORKCELL_RUN_STATE_CONFLICT",
        f"WorkcellRun 当前状态 {status} 不接受此操作",
        "刷新 WorkcellRun Tree，并按产品状态机执行下一动作。",
    )


def _error(
    code: str,
    title: str,
    repair: str,
    status_code: int = 409,
) -> ProductError:
    return ProductError(
        code=code,
        title=title,
        detail=title,
        repair=repair,
        status_code=status_code,
    )
