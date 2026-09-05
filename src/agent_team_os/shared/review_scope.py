"""产品校验已批准的验收归属；Scope 是规划事实的冻结派生输入。"""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
)

from .errors import ProductError
from .hashes import Sha256, sha256_json


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WorkcellAcceptanceResponsibility(_FrozenModel):
    acceptance_id: str = Field(min_length=1, max_length=120)
    responsibility: str = Field(min_length=1, max_length=2_000)


class WorkcellAcceptanceAssignment(_FrozenModel):
    workcell_key: str = Field(min_length=1, max_length=120)
    acceptance: tuple[WorkcellAcceptanceResponsibility, ...] = Field(min_length=1)


class ReviewSystemPolicy(_FrozenModel):
    id: str
    revision: int = Field(ge=1)
    statement: str
    allowed_paths: tuple[str, ...] = ()


class ReviewPolicySnapshot(_FrozenModel):
    workcells: dict[str, tuple[ReviewSystemPolicy, ...]]
    sha256: Sha256


class FrozenAcceptanceResponsibility(WorkcellAcceptanceResponsibility):
    statement: str = Field(min_length=1)


class WorkcellReviewScope(_FrozenModel):
    workcell_key: str
    source_plan_sha256: Sha256
    requirements_sha256: Sha256
    task_sha256: Sha256
    acceptance: tuple[FrozenAcceptanceResponsibility, ...] = Field(min_length=1)
    system_policies: tuple[ReviewSystemPolicy, ...] = Field(min_length=1)
    sha256: Sha256


def product_workcell_allowed_paths(workcell_key: str) -> tuple[str, ...]:
    paths = {
        "design": ("design/**", "tests/**"),
        "frontend": ("src/**", "tests/**"),
        "backend": ("src/**", "tests/**"),
        "qa": ("tests/**", "reports/**"),
    }
    if workcell_key not in paths:
        raise review_error("WORKCELL_REVIEW_POLICY_UNKNOWN", "产品没有发布该 Workcell 的路径政策。")
    return paths[workcell_key]


def product_review_policies(workcell_keys: tuple[str, ...]) -> ReviewPolicySnapshot:
    workcells: dict[str, tuple[ReviewSystemPolicy, ...]] = {
        key: (
            ReviewSystemPolicy(
                id="workspace.allowed-paths.v1",
                revision=1,
                statement="Candidate 只能新增或修改本 Workcell 产品允许路径内的文件。",
                allowed_paths=product_workcell_allowed_paths(key),
            ),
            ReviewSystemPolicy(
                id="candidate.source-files-only.v1",
                revision=1,
                statement="Candidate 不得包含 __pycache__、*.pyc 或 *.pyo 等 Python 运行时生成物。",
            ),
            ReviewSystemPolicy(
                id="candidate.non-empty.v1",
                revision=1,
                statement="Git Writer 必须产生非空 Candidate；Artifact-only Stage 不适用。",
            ),
        )
        for key in sorted(workcell_keys)
    }
    payload = {
        key: [item.model_dump(mode="json") for item in items] for key, items in workcells.items()
    }
    return ReviewPolicySnapshot(workcells=workcells, sha256=sha256_json({"workcells": payload}))


def validate_workcell_acceptance(
    requirements: dict[str, Any], task: dict[str, Any], required_workcells: tuple[str, ...]
) -> None:
    criteria = requirements.get("acceptance_criteria")
    task_ids = task.get("acceptance_ids")
    raw_assignments = task.get("workcell_acceptance")
    invalid = "WORKCELL_ACCEPTANCE_ASSIGNMENT_INVALID"
    if not isinstance(criteria, list | tuple) or not criteria:
        raise review_error(invalid, "需求缺少可引用的 Acceptance。")
    criteria_ids: list[str] = []
    for criterion in criteria:
        if (
            not isinstance(criterion, dict)
            or not isinstance(criterion.get("id"), str)
            or not criterion["id"].strip()
            or not isinstance(criterion.get("statement"), str)
            or not criterion["statement"].strip()
        ):
            raise review_error(invalid, "Acceptance ID 或原始正文无效。")
        criteria_ids.append(criterion["id"])
    if len(set(criteria_ids)) != len(criteria_ids):
        raise review_error(invalid, "需求中的 Acceptance ID 重复。")
    if (
        not isinstance(task_ids, list | tuple)
        or not task_ids
        or any(not isinstance(item, str) for item in task_ids)
        or len(set(task_ids)) != len(task_ids)
        or not set(task_ids).issubset(criteria_ids)
    ):
        raise review_error(invalid, "Task 的 Acceptance ID 必须唯一且来自原始需求。")
    if not isinstance(raw_assignments, list | tuple):
        raise review_error(invalid, "Task 缺少明确的 Workcell Acceptance 责任映射。")
    try:
        assignments = tuple(
            WorkcellAcceptanceAssignment.model_validate(item) for item in raw_assignments
        )
    except ValidationError as error:
        raise review_error(invalid, "Workcell Acceptance 责任映射不符合产品合同。") from error
    keys = tuple(item.workcell_key for item in assignments)
    if (
        not required_workcells
        or len(set(required_workcells)) != len(required_workcells)
        or len(set(keys)) != len(keys)
        or set(keys) != set(required_workcells)
    ):
        raise review_error(invalid, "责任映射必须且只能覆盖冻结 Pipeline 所选 Workcell。")
    assigned: set[str] = set()
    for assignment in assignments:
        identifiers = tuple(item.acceptance_id for item in assignment.acceptance)
        if (
            len(set(identifiers)) != len(identifiers)
            or not set(identifiers).issubset(task_ids)
            or any(not item.responsibility.strip() for item in assignment.acceptance)
        ):
            raise review_error(invalid, "本仓责任存在重复、越界引用或空责任说明。")
        assigned.update(identifiers)
    if assigned != set(task_ids):
        raise review_error(invalid, "Task 中的 Acceptance 未被本次 Workcell 责任映射完整覆盖。")


def compile_review_scope(
    *,
    requirements: dict[str, Any],
    task: dict[str, Any],
    plan_subject_sha256: str,
    plan_approved: bool,
    workcell_key: str,
    required_workcells: tuple[str, ...],
    policies: ReviewPolicySnapshot | None,
) -> WorkcellReviewScope:
    validate_workcell_acceptance(requirements, task, required_workcells)
    source_hash = sha256_json({"requirements": requirements, "task": task})
    if not plan_approved or source_hash != plan_subject_sha256:
        raise review_error(
            "WORKCELL_REVIEW_PLAN_MISMATCH", "Review Scope 的规划内容不匹配已批准 Plan Gate。"
        )
    if policies is None:
        raise review_error(
            "WORKCELL_REVIEW_POLICY_REQUIRED", "Delivery 缺少冻结的产品 Review Policy。"
        )
    if policies != product_review_policies(required_workcells):
        raise review_error(
            "WORKCELL_REVIEW_POLICY_INVALID", "Review Policy 与产品已发布规则不一致。"
        )
    assignment = next(
        (item for item in task["workcell_acceptance"] if item["workcell_key"] == workcell_key), None
    )
    if assignment is None:
        raise review_error("WORKCELL_REVIEW_SCOPE_INVALID", "当前 Workcell 不属于批准的责任映射。")
    criteria = {item["id"]: item["statement"] for item in requirements["acceptance_criteria"]}
    acceptance = tuple(
        FrozenAcceptanceResponsibility(**item, statement=criteria[item["acceptance_id"]])
        for item in assignment["acceptance"]
    )
    payload = {
        "workcell_key": workcell_key,
        "source_plan_sha256": source_hash,
        "requirements_sha256": sha256_json(requirements),
        "task_sha256": sha256_json(task),
        "acceptance": [item.model_dump(mode="json") for item in acceptance],
        "system_policies": [
            item.model_dump(mode="json") for item in policies.workcells[workcell_key]
        ],
    }
    return WorkcellReviewScope.model_validate({**payload, "sha256": sha256_json(payload)})


def validate_review_scope(scope: WorkcellReviewScope | None, *, workcell_key: str) -> None:
    if scope is None:
        raise review_error("WORKCELL_REVIEW_SCOPE_REQUIRED", "新 Workcell 缺少冻结 Review Scope。")
    payload = scope.model_dump(mode="json", exclude={"sha256"})
    if scope.workcell_key != workcell_key or sha256_json(payload) != scope.sha256:
        raise review_error("WORKCELL_REVIEW_SCOPE_INVALID", "Review Scope 的工作区或内容哈希无效。")
    acceptance_ids = [item.acceptance_id for item in scope.acceptance]
    if (
        len(set(acceptance_ids)) != len(acceptance_ids)
        or scope.system_policies != product_review_policies((workcell_key,)).workcells[workcell_key]
    ):
        raise review_error("WORKCELL_REVIEW_SCOPE_INVALID", "冻结 Acceptance 或产品 Policy 无效。")


class BlockingFinding(_FrozenModel):
    code: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1_000)
    evidence_sha256: Sha256
    acceptance_id: str | None = Field(default=None, min_length=1, max_length=120)
    system_policy_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_serializer(mode="wrap")
    def serialize_legacy_references(  # type: ignore[no-untyped-def]
        self, handler: SerializerFunctionWrapHandler
    ):
        payload: dict[str, Any] = handler(self)
        for field in ("acceptance_id", "system_policy_id"):
            if getattr(self, field) is None:
                payload.pop(field, None)
        return payload


INVALID_REVIEW_CODES = frozenset(
    {
        "WORKCELL_REVIEW_ARTIFACT_INVALID",
        "WORKCELL_REVIEW_EVIDENCE_MISMATCH",
        "WORKCELL_REVIEW_SCOPE_MISMATCH",
        "WORKCELL_REVIEW_FINDING_REFERENCE_INVALID",
        "WORKCELL_REVIEW_FINDING_OUT_OF_SCOPE",
    }
)


def parse_review_findings(content: dict[str, Any]) -> tuple[BlockingFinding, ...]:
    raw = content.get("blocking_findings")
    if not isinstance(raw, list):
        raise review_error(
            "WORKCELL_REVIEW_ARTIFACT_INVALID", "Reviewer 必须显式返回 blocking_findings 数组。"
        )
    try:
        findings = tuple(BlockingFinding.model_validate(item) for item in raw)
    except ValidationError as error:
        raise review_error(
            "WORKCELL_REVIEW_ARTIFACT_INVALID", "Blocking Finding 不符合产品 Schema。"
        ) from error
    verdict = content.get("verdict", content.get("decision"))
    if (
        isinstance(verdict, str)
        and verdict.lower()
        in {"blocked", "changes_required", "fail", "failed", "reject", "rejected"}
        and not findings
    ):
        raise review_error(
            "WORKCELL_REVIEW_ARTIFACT_INVALID", "Reviewer 给出阻断结论但没有结构化 Finding。"
        )
    return findings


def validate_review_output(
    content: dict[str, Any],
    *,
    scope: WorkcellReviewScope | None,
    candidate_sha: str,
    diff_sha256: str,
) -> tuple[BlockingFinding, ...]:
    if (
        content.get("reviewed_candidate_sha") != candidate_sha
        or content.get("reviewed_diff_sha256") != diff_sha256
    ):
        raise review_error(
            "WORKCELL_REVIEW_EVIDENCE_MISMATCH", "Review 没有绑定机器验证的 Candidate 与 Diff。"
        )
    validate_review_scope(scope, workcell_key="" if scope is None else scope.workcell_key)
    assert scope is not None
    if content.get("review_scope_sha256") != scope.sha256:
        raise review_error(
            "WORKCELL_REVIEW_SCOPE_MISMATCH", "Review 没有绑定当前 Workcell 冻结 Scope。"
        )
    findings = parse_review_findings(content)
    acceptance_ids = {item.acceptance_id for item in scope.acceptance}
    policy_ids = {item.id for item in scope.system_policies}
    for finding in findings:
        if (finding.acceptance_id is None) == (finding.system_policy_id is None):
            raise review_error(
                "WORKCELL_REVIEW_FINDING_REFERENCE_INVALID",
                "Finding 必须且只能引用 acceptance_id 或 system_policy_id；code 不是归属凭据。",
            )
        if (finding.acceptance_id is not None and finding.acceptance_id not in acceptance_ids) or (
            finding.system_policy_id is not None and finding.system_policy_id not in policy_ids
        ):
            raise review_error(
                "WORKCELL_REVIEW_FINDING_OUT_OF_SCOPE",
                "Finding 引用了本 Workcell 冻结 Scope 之外的条目。",
            )
    return findings


def review_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Review 归属合同无效",
        detail=detail,
        repair="保留原始证据，修复规划或由既有 bounded Loop 创建新的 Review Run。",
        status_code=409,
    )
