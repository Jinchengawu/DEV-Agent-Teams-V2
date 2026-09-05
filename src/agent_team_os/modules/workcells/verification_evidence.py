"""执行端与历史 Acceptance 共用结果合同，不重新运行历史工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...shared.errors import ProductError
from ...shared.verification import VerificationQualificationV2
from ..artifacts import ContentAddressedArtifactStorage
from .verification_application import VerificationProfileCatalog
from .verification_domain import (
    VerificationPackageManifestV1,
    VerificationPackagePublicationV1,
    VerificationReportV2,
)
from .verification_packages import validate_package_bytes

STEP_NAMES = {
    "design-contract-v1": ("design",),
    "frontend-ts-vite-vitest-v1": ("typecheck", "test", "build"),
    "backend-python-http-v1": ("unittest", "backend-http"),
    "qa-playwright-artifacts-v1": ("qa",),
}
QA_CASES = frozenset(
    "test_health_e2e.HealthE2E." + name
    for name in ("test_ok", "test_degraded", "test_unavailable", "test_invalid_response")
)


def command_values(snapshot: VerificationQualificationV2, root: Path, index: int) -> dict[str, str]:
    values: dict[str, str] = {item.name: item.executable for item in snapshot.tools}
    values.update({item.name: item.root for item in snapshot.dependencies})
    values.update(
        workspace=str(root / "workspace"),
        inputs=str(root / "inputs"),
        build=str(root / "build"),
        config=str(root / "config"),
        result=str(root / "results" / f"{index}.json"),
    )
    return values


def render_command(
    snapshot: VerificationQualificationV2, root: Path, index: int
) -> tuple[str, ...]:
    values = command_values(snapshot, root, index)
    template = snapshot.profile.commands[index]
    return (values[template[0]], *(value.format_map(values) for value in template[1:]))


def result_counts(
    profile_id: str, step: str, result: object
) -> tuple[int, int, int, int, tuple[str, ...]]:
    if not isinstance(result, dict):
        return (0, 0, 1, 0, ())
    payload: dict[str, Any] = result
    if profile_id == "frontend-ts-vite-vitest-v1" and step == "test":
        cases = [
            case
            for suite in payload.get("testResults", [])
            for case in suite.get("assertionResults", [])
        ]
        count = int(payload.get("numTotalTests", 0))
        passed = sum(case.get("status") == "passed" for case in cases)
        failed = sum(case.get("status") == "failed" for case in cases)
        skipped = sum(case.get("status") not in {"passed", "failed"} for case in cases)
        if (
            count != len(cases)
            or payload.get("numFailedTestSuites", 0)
            or not payload.get("success", False)
        ):
            failed += 1
        return (
            count,
            passed,
            failed,
            skipped,
            tuple(str(case.get("fullName", "")) for case in cases),
        )
    return (
        int(payload.get("discovered", 0)),
        int(payload.get("passed", 0)),
        int(payload.get("failed", 0)),
        int(payload.get("skipped", 0)),
        tuple(str(value) for value in payload.get("case_ids", ())),
    )


def passed_counts(step: str, counts: tuple[int, int, int, int, tuple[str, ...]]) -> bool:
    discovered, passed, failed, skipped, ids = counts
    return (
        discovered > 0
        and passed == discovered
        and failed == skipped == 0
        and len(ids) == discovered
        and len(set(ids)) == len(ids)
        and all(ids)
        and (step != "qa" or QA_CASES.issubset(ids))
        and (
            step != "backend-http"
            or set(ids) == {"http:ok", "http:degraded", "http:unavailable", "http:invalid"}
        )
        and (step not in {"typecheck", "build"} or ids == (step,))
    )


def validate_report_v2(
    report: VerificationReportV2,
    snapshot: VerificationQualificationV2,
    store: ContentAddressedArtifactStorage,
) -> None:
    VerificationProfileCatalog().validate_frozen(snapshot)
    expected_steps = STEP_NAMES[snapshot.profile.id]
    valid = (
        report.workcell_key == snapshot.profile.workcell_key
        and report.profile_sha256 == snapshot.profile_sha256
        and report.qualification_sha256 == snapshot.qualification_sha256
        and report.cleanup_completed
        and tuple(step.step for step in report.steps) == expected_steps
        and len(report.inputs) == len(snapshot.profile.input_contracts)
        and len({reference.sha256 for reference in report.inputs}) == len(report.inputs)
        and (report.output_manifest is not None) == (snapshot.profile.output_contract is not None)
    )
    for index, step in enumerate(report.steps):
        if index >= len(expected_steps):
            valid = False
            break
        valid = (
            valid
            and step.status == "passed"
            and step.exit_code == 0
            and step.result_contract_passed
            and step.command == render_command(snapshot, Path(report.execution_root), index)
        )
        store.get_bytes(step.log, max_bytes=2_000_000)
        if step.result is None:
            valid = False
            continue
        import json

        counts = result_counts(
            snapshot.profile.id,
            step.step,
            json.loads(store.get_bytes(step.result, max_bytes=2_000_000)),
        )
        valid = (
            valid
            and passed_counts(step.step, counts)
            and counts == (step.discovered, step.passed, step.failed, step.skipped, step.case_ids)
        )
    input_contracts = []
    for reference in report.inputs:
        publication = VerificationPackagePublicationV1.model_validate_json(
            store.get_bytes(reference, max_bytes=65_536)
        )
        manifest = VerificationPackageManifestV1.model_validate_json(
            store.get_bytes(publication.manifest, max_bytes=262_144)
        )
        valid = (
            valid
            and publication.delivery_id == report.delivery_id == manifest.delivery_id
            and publication.workcell_key == manifest.workcell_key
            and publication.candidate_sha == manifest.candidate_sha
        )
        validate_package_bytes(store, manifest)
        input_contracts.append(manifest.package_contract)
    valid = valid and sorted(input_contracts) == sorted(snapshot.profile.input_contracts)
    if report.output_manifest is not None:
        manifest = VerificationPackageManifestV1.model_validate_json(
            store.get_bytes(report.output_manifest, max_bytes=262_144)
        )
        validate_package_bytes(store, manifest)
        valid = (
            valid
            and manifest.package_contract == snapshot.profile.output_contract
            and manifest.delivery_id == report.delivery_id
            and manifest.workcell_key == report.workcell_key
            and manifest.candidate_sha == report.candidate_sha
            and manifest.profile_sha256 == report.profile_sha256
            and manifest.qualification_sha256 == report.qualification_sha256
        )
    if not valid:
        raise ProductError(
            code="WORKCELL_VERIFICATION_REPORT_INVALID",
            title="V2 验证证据无效",
            detail="实际步骤、非零测试结果或冻结工具/方案与报告不一致。",
            repair="重新运行产品机器验证。",
            status_code=409,
        )
