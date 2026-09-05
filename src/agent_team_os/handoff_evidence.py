"""关联原生验收证据；本模块不产生新的 Release 或业务 Gate 通过权威。"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from .knowledge_context_contract import KNOWLEDGE_CONTEXT_STAGE_PATHS
from .modules.releases.acceptance_domain import ReleaseAcceptanceReportV2
from .release import GateReport, _report_evidence_is_valid
from .shared.hashes import Sha256, sha256_bytes, sha256_json

BROWSER_BASE_CHECKS = (
    "PLAN_ACCEPTANCE_VISIBLE",
    "DESIGN_DIFF_DIALOG_VERIFIED",
    "RAW_REVIEW_DIALOG_VERIFIED",
    "FROZEN_METHOD_PROFILE_VISIBLE",
    "WORKCELL_TREE_VERIFIED",
    "REVIEW_SCOPE_PLAN_BOUND",
    "FOUR_REMOTE_FORWARD_APPLY_VERIFIED",
    "RELEASE_MANIFEST_VERIFIED",
    "WIKI_PUBLICATION_VERIFIED",
    "BROWSER_CONSOLE_CLEAN",
    "BUILD_IDENTITY_STABLE",
    "CURRENT_CONSOLE_BUNDLE_VERIFIED",
)
BROWSER_R2_CHECKS = (
    "KNOWLEDGE_R2_CONTEXTS_VERIFIED",
    "KNOWLEDGE_R2_CITATIONS_VERIFIED",
    "QA_PREPARATION_VERIFIED",
)
LIVE_R2_REQUIRED_CHECKS = (
    "ACCEPTANCE_SUBJECT_VERIFIED",
    "BUILD_IDENTITY_VERIFIED",
    "DELIVERY_TERMINAL_VERIFIED",
    "PIPELINE_TERMINAL_VERIFIED",
    "WORKCELL_TERMINALS_VERIFIED",
    "CODEX_WORKCELL_ATTEMPTS_VERIFIED",
    "WORKCELL_RESULTS_VERIFIED",
    "KNOWLEDGE_CONTEXTS_VERIFIED",
    "CANDIDATE_EVIDENCE_VERIFIED",
    "RELEASE_BUNDLE_VERIFIED",
    "REMOTE_MAIN_VERIFIED",
    "RELEASE_MANIFEST_VERIFIED",
    "RELEASE_HEALTH_VERIFIED",
)
_PLANNING_CHECKS = {
    "CODEX_PLANNING_ATTEMPTS_VERIFIED": "codex.cli",
    "HERMES_PLANNING_ATTEMPTS_VERIFIED": "hermes.acp",
}
_ROLES = ("core_browser", "deterministic_gate", "live_release")
_QA_PREPARATION = "qa-preparation-repair/qa-preparation"
_MAX_REPORT_BYTES = 4 * 1024 * 1024


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BrowserKnowledgeContext(_EvidenceModel):
    stage_path: str = Field(min_length=1)
    artifact_sha256: Sha256
    citation_ids: tuple[str, ...] = Field(min_length=1)
    authorization_epoch_hash: Sha256


class BrowserKnowledgeScope(_EvidenceModel):
    required_stage_paths: tuple[str, ...]
    contexts: tuple[BrowserKnowledgeContext, ...]
    context_count: int
    workcell_run_ids: dict[str, str]
    qa_preparation_run_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def exact_r2_scope(self) -> Self:
        expected = set(KNOWLEDGE_CONTEXT_STAGE_PATHS)
        if set(self.required_stage_paths) != expected or len(self.required_stage_paths) != 7:
            raise ValueError("R2 必须包含七个产品定义的 Stage。")
        if len(self.contexts) != 7 or self.context_count != 7:
            raise ValueError("R2 Context 数量不完整。")
        if {context.stage_path for context in self.contexts} != expected:
            raise ValueError("R2 Context 必须精确覆盖必需 Stage。")
        if any(not item.strip() for context in self.contexts for item in context.citation_ids):
            raise ValueError("R2 Citation ID 不得为空。")
        if len({context.authorization_epoch_hash for context in self.contexts}) != 1:
            raise ValueError("R2 Context 授权 Epoch 不一致。")
        if set(self.workcell_run_ids) != expected - {"requirements", "tasking"}:
            raise ValueError("R2 必须保留五个最终 Workcell Run。")
        if len(set(self.workcell_run_ids.values())) != 5 or any(
            not value.strip() for value in self.workcell_run_ids.values()
        ):
            raise ValueError("R2 Workcell Run ID 缺失或重复。")
        if self.workcell_run_ids[_QA_PREPARATION] != self.qa_preparation_run_id:
            raise ValueError("QA Preparation 必须指向对应实际 Workcell Run。")
        return self


class CoreBrowserRunReceipt(_EvidenceModel):
    """仅由完整浏览器断言成功后的 runner 生成，保留实际 Runtime 身份。"""

    schema_version: Literal["core-browser-run-receipt-v1"] = "core-browser-run-receipt-v1"
    kind: Literal["browser"] = "browser"
    scenario: Literal["agent-workcell-delivery-v1", "agent-workcell-knowledge-delivery-v1"]
    status: Literal["passed"] = "passed"
    fail: Literal[0] = 0
    warn: Literal[0] = 0
    skipped: Literal[0] = 0
    product_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    product_worktree_clean: Literal[True]
    acwm_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    project_id: str = Field(min_length=1)
    delivery_id: str = Field(min_length=1)
    pipeline_revision_id: str = Field(min_length=1)
    pipeline_revision_sha256: Sha256
    build_identity_sha256: Sha256
    execution_snapshot_sha256: Sha256
    console_bundle_sha256: Sha256
    planning_identity: Literal["deterministic-test"]
    execution_identity: Literal["deterministic-model-boundary"] | None
    evidence_identity: Literal["deterministic-test"]
    runtime_identity: Literal["deterministic-model-boundary"]
    started_at: datetime
    completed_at: datetime
    checks_passed: tuple[str, ...]
    knowledge_scope: BrowserKnowledgeScope | None
    receipt_sha256: Sha256

    @classmethod
    def create(cls, **payload: Any) -> Self:
        # 先规范化默认值与日期，Hash 始终对应原生模型的完整 JSON 表达。
        draft = cls.model_validate(
            {**payload, "receipt_sha256": "1" * 64}, context={"building_receipt": True}
        )
        content = draft.model_dump(mode="json", exclude={"receipt_sha256"})
        return cls.model_validate({**content, "receipt_sha256": sha256_json(content)})

    @model_validator(mode="after")
    def native_assertion_contract(self, info: ValidationInfo) -> Self:
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ValueError("浏览器收据时间必须带时区。")
        if self.completed_at < self.started_at:
            raise ValueError("浏览器收据时间顺序错误。")
        r2 = self.scenario == "agent-workcell-knowledge-delivery-v1"
        if r2 != (self.knowledge_scope is not None):
            raise ValueError("基础与 R2 收据的 Knowledge 范围不匹配。")
        checks = BROWSER_BASE_CHECKS + (BROWSER_R2_CHECKS if r2 else ())
        if len(self.checks_passed) != len(checks) or set(self.checks_passed) != set(checks):
            raise ValueError("原生浏览器收据缺少必需断言，或包含未知/重复断言。")
        if not (info.context or {}).get("building_receipt"):
            content = self.model_dump(mode="json", exclude={"receipt_sha256"})
            if sha256_json(content) != self.receipt_sha256:
                raise ValueError("浏览器收据内容 Hash 不一致。")
        return self


class HandoffEvidenceSource(_EvidenceModel):
    role: Literal["core_browser", "deterministic_gate", "live_release"]
    path: str
    file_sha256: Sha256
    native_schema: str
    native_hash_field: str
    native_hash: Sha256
    scope: str
    identity: dict[str, str | None]


class HandoffReferenceIssue(_EvidenceModel):
    code: str
    role: str | None = None
    detail: str
    severity: Literal["incomplete", "invalid"]


class HandoffEvidenceIndex(_EvidenceModel):
    schema_version: Literal["delivery-handoff-evidence-index-v1"] = (
        "delivery-handoff-evidence-index-v1"
    )
    target: Literal["four-repo-r2-alpha"] = "four-repo-r2-alpha"
    target_product_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    target_acwm_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    reference_check: Literal["consistent", "incomplete", "invalid"]
    sources: tuple[HandoffEvidenceSource, ...]
    issues: tuple[HandoffReferenceIssue, ...]
    created_at: datetime
    index_sha256: Sha256

    @model_validator(mode="after")
    def index_integrity(self) -> Self:
        if self.index_sha256 != sha256_json(self.model_dump(mode="json", exclude={"index_sha256"})):
            raise ValueError("交接索引内容 Hash 不一致。")
        return self


def write_core_browser_receipt(path: Path, receipt: CoreBrowserRunReceipt) -> None:
    """原子写出已验证的原生收据；runner 仍负责断言和旧输出失效。"""
    validated = CoreBrowserRunReceipt.model_validate_json(receipt.model_dump_json())
    _atomic_json(path, validated.model_dump(mode="json"))


def write_handoff_evidence_index(path: Path, index: HandoffEvidenceIndex) -> None:
    validated = HandoffEvidenceIndex.model_validate_json(index.model_dump_json())
    _atomic_json(path, validated.model_dump(mode="json"))


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = stream.name
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def build_handoff_evidence_index(
    *,
    product_revision: str,
    acwm_revision: str,
    sources: dict[str, Path],
) -> HandoffEvidenceIndex:
    """只读显式文件；consistent 仅指引用相容，不能替代原生验收结果。"""
    issues: list[HandoffReferenceIssue] = []
    references: list[HandoffEvidenceSource] = []
    for unknown in set(sources) - set(_ROLES):
        issues.append(_issue("SOURCE_ROLE_UNKNOWN", unknown, "存在未知证据类别。"))
    for role in _ROLES:
        path = sources.get(role)
        if path is None or not path.is_file():
            issues.append(_issue("SOURCE_MISSING", role, "缺少此轨原生报告。", "incomplete"))
            continue
        try:
            with path.open("rb") as stream:
                data = stream.read(_MAX_REPORT_BYTES + 1)
            if len(data) > _MAX_REPORT_BYTES:
                raise ValueError("报告超出读取限制。")
            reference = _read_source(role, path, data)
        except (OSError, ValueError, TypeError):
            issues.append(_issue("SOURCE_INVALID", role, "原生 Schema、Hash、身份或断言不合格。"))
            continue
        references.append(reference)
        if (
            reference.identity["product_revision"] != product_revision
            or reference.identity["acwm_revision"] != acwm_revision
        ):
            issues.append(
                _issue("SOURCE_REVISION_MISMATCH", role, "证据与目标 Product/ACWM 不一致。")
            )
        if role == "core_browser" and reference.scope != "core-four-repository-r2":
            issues.append(
                _issue(
                    "BROWSER_R2_SCOPE_MISSING",
                    role,
                    "基础四仓浏览器收据不能替代 R2 浏览器。",
                    "incomplete",
                )
            )
    builds = {
        reference.identity.get("build_identity_sha256")
        for reference in references
        if reference.role in {"core_browser", "live_release"}
    }
    if len(builds) > 1:
        issues.append(
            _issue("BUILD_IDENTITY_MISMATCH", None, "Browser/Live 的 Build Identity 不一致。")
        )
    status = (
        "invalid"
        if any(issue.severity == "invalid" for issue in issues)
        else "incomplete"
        if issues
        else "consistent"
    )
    payload = {
        "schema_version": "delivery-handoff-evidence-index-v1",
        "target": "four-repo-r2-alpha",
        "target_product_revision": product_revision,
        "target_acwm_revision": acwm_revision,
        "reference_check": status,
        "sources": [item.model_dump(mode="json") for item in references],
        "issues": [item.model_dump(mode="json") for item in issues],
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    return HandoffEvidenceIndex.model_validate({**payload, "index_sha256": sha256_json(payload)})


def _issue(
    code: str, role: str | None, detail: str, severity: Literal["incomplete", "invalid"] = "invalid"
) -> HandoffReferenceIssue:
    return HandoffReferenceIssue(code=code, role=role, detail=detail, severity=severity)


def _read_source(role: str, path: Path, data: bytes) -> HandoffEvidenceSource:
    identity: dict[str, str | None]
    schema: str
    native_hash: str
    if role == "core_browser":
        browser = CoreBrowserRunReceipt.model_validate_json(data)
        schema, native_field, native_hash = (
            browser.schema_version,
            "receipt_sha256",
            browser.receipt_sha256,
        )
        scope = (
            "core-four-repository-r2"
            if browser.knowledge_scope is not None
            else "core-four-repository"
        )
        identity = {
            key: getattr(browser, key)
            for key in (
                "product_revision",
                "acwm_revision",
                "project_id",
                "delivery_id",
                "pipeline_revision_id",
                "pipeline_revision_sha256",
                "build_identity_sha256",
                "execution_snapshot_sha256",
                "console_bundle_sha256",
                "planning_identity",
                "execution_identity",
                "evidence_identity",
                "runtime_identity",
            )
        }
    elif role == "deterministic_gate":
        deterministic = GateReport.model_validate_json(data)
        _validate_deterministic(deterministic)
        schema, native_field, native_hash = (
            "GateReport",
            "evidence_sha256",
            deterministic.evidence_sha256,
        )
        scope = "deterministic-release-baseline"
        identity = {
            key: getattr(deterministic, key)
            for key in (
                "acwm_revision",
                "pipeline_revision_id",
                "pipeline_fingerprint",
                "pipeline_run_id",
                "candidate_revision",
                "diff_sha256",
                "planning_identity",
                "execution_identity",
            )
        }
        identity["product_revision"] = deterministic.dev_revision
    else:
        live = ReleaseAcceptanceReportV2.model_validate_json(data)
        _validate_live(live)
        schema, native_field, native_hash = live.schema_version, "report_sha256", live.report_sha256
        scope = "live-four-repository-r2"
        identity = {
            key: getattr(live, key)
            for key in (
                "product_revision",
                "acwm_revision",
                "project_id",
                "delivery_id",
                "pipeline_revision_id",
                "build_identity_sha256",
                "knowledge_context_set_sha256",
                "workcell_evidence_sha256",
                "release_bundle_sha256",
                "release_manifest_sha256",
            )
        }
        # V2 原生 Report 没有 Runtime identity 字段，仅引用其 Binding 断言含义。
        identity["planning_adapter_verified"] = next(
            _PLANNING_CHECKS[check.code] for check in live.checks if check.code in _PLANNING_CHECKS
        )
        identity["execution_adapter_verified"] = "codex.cli"
    return HandoffEvidenceSource.model_validate(
        {
            "role": role,
            "path": str(path.resolve()),
            "file_sha256": sha256_bytes(data),
            "native_schema": schema,
            "native_hash_field": native_field,
            "native_hash": native_hash,
            "scope": scope,
            "identity": identity,
        }
    )


def _validate_deterministic(report: GateReport) -> None:
    if (
        report.kind != "deterministic"
        or not _report_evidence_is_valid(report)
        or report.status != "passed"
        or (report.fail, report.warn, report.skipped) != (0, 0, 0)
        or report.error is not None
        or report.planning_identity != "deterministic-test"
        or report.execution_identity != "deterministic-model-boundary"
        or not report.browser_e2e
        or not report.browser_restart_recovery
        or not report.browser_multi_pipeline_e2e
        or report.browser_verified_evidence_count < 7
        or not report.browser_candidate_matches_main
        or not report.candidate_revision
        or not report.diff_sha256
        or not report.pipeline_revision_id
        or not report.pipeline_fingerprint
        or not report.pipeline_run_id
        or report.pipeline_run_status != "completed"
        or report.verification_exit_code != 0
    ):
        raise ValueError("Deterministic GateReport 原生证据不完整。")


def _validate_live(report: ReleaseAcceptanceReportV2) -> None:
    codes = {check.code for check in report.checks}
    planning = codes & set(_PLANNING_CHECKS)
    if (
        report.status != "passed"
        or (report.fail, report.warn, report.skipped) != (0, 0, 0)
        or len(planning) != 1
        or codes - planning != set(LIVE_R2_REQUIRED_CHECKS)
        or any(check.status != "passed" for check in report.checks)
        or not all(
            (
                report.product_revision,
                report.acwm_revision,
                report.pipeline_revision_id,
                report.build_identity_sha256,
                report.knowledge_context_set_sha256,
                report.workcell_evidence_sha256,
                report.release_bundle_sha256,
                report.release_manifest_sha256,
            )
        )
    ):
        raise ValueError("Live R2 原生检查集或身份不完整。")
