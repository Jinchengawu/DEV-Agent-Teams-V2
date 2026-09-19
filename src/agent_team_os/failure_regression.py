"""真实交付失败的版本化离线合同回放。"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import jsonschema  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .infrastructure.verification.runners.verify import design
from .shared.errors import ProductError
from .shared.hashes import Sha256, sha256_bytes, sha256_file, sha256_json
from .shared.review_scope import validate_workcell_acceptance


class FailureRegressionManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_id: Literal["delivery-failure-regression"]
    version: str
    proof_scope: Literal["offline_contract_regression"]
    cases_file: str = "cases.jsonl"
    schema_file: str = "schema.json"
    fixtures_file: str = "fixtures.json"
    cases_sha256: Sha256
    schema_sha256: Sha256
    fixtures_sha256: Sha256
    case_counts: dict[str, int]


class FailureRegressionCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    family: Literal["workcell_acceptance", "design_contract"]
    fixture: str
    variant: str
    source_kind: Literal["sanitized_capture", "regression_fixture", "synthetic_negative"]
    fix_revision: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    expected_outcome: Literal["accepted", "rejected"]
    expected_error_code: str | None = None
    attribution: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_rejection_code(self) -> FailureRegressionCase:
        if self.expected_outcome == "rejected" and not self.expected_error_code:
            raise ValueError("rejected case requires expected_error_code")
        if self.expected_outcome == "accepted" and self.expected_error_code is not None:
            raise ValueError("accepted case cannot declare expected_error_code")
        return self


class FailureRegressionDataset(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    manifest: FailureRegressionManifest
    cases: tuple[FailureRegressionCase, ...]
    fixtures: dict[str, object]
    dataset_sha256: Sha256
    root: Path


class FailureRegressionCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    family: str
    source_kind: str
    fix_revision: str
    input_sha256: Sha256
    expected_outcome: str
    actual_outcome: Literal["accepted", "rejected", "execution_error"]
    expected_error_code: str | None = None
    actual_error_code: str | None = None
    matched: bool
    duration_ms: float


class FailureRegressionReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_id: str
    suite_version: str
    proof_scope: Literal["offline_contract_regression"]
    dataset_sha256: Sha256
    git_revision: str
    git_dirty: bool
    validator_source_sha256: dict[str, Sha256]
    environment: dict[str, str]
    totals: dict[str, int]
    cases: tuple[FailureRegressionCaseResult, ...]
    created_at: datetime


def load_failure_regression_dataset(dataset_dir: Path) -> FailureRegressionDataset:
    root = dataset_dir.resolve()
    if dataset_dir.is_symlink():
        raise ValueError("dataset directory cannot be a symbolic link")
    manifest_path = _safe_file(root, "manifest.json")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = FailureRegressionManifest.model_validate_json(manifest_bytes)
        cases_path = _safe_file(root, manifest.cases_file)
        schema_path = _safe_file(root, manifest.schema_file)
        fixtures_path = _safe_file(root, manifest.fixtures_file)
        expected_files = {
            "manifest.json",
            manifest.cases_file,
            manifest.schema_file,
            manifest.fixtures_file,
        }
        actual_files = {item.name for item in root.iterdir()}
        if actual_files != expected_files:
            raise ValueError(
                f"dataset files do not match manifest: expected={sorted(expected_files)}, "
                f"actual={sorted(actual_files)}"
            )
        cases_bytes = cases_path.read_bytes()
        schema_bytes = schema_path.read_bytes()
        fixtures_bytes = fixtures_path.read_bytes()
    except (OSError, ValidationError, ValueError) as error:
        raise ValueError(f"invalid failure regression dataset: {error}") from error
    for name, content, expected in (
        (manifest.cases_file, cases_bytes, manifest.cases_sha256),
        (manifest.schema_file, schema_bytes, manifest.schema_sha256),
        (manifest.fixtures_file, fixtures_bytes, manifest.fixtures_sha256),
    ):
        if sha256_bytes(content) != expected:
            raise ValueError(f"{name} SHA-256 mismatch")
    try:
        schema = json.loads(schema_bytes)
        fixtures = json.loads(fixtures_bytes)
        raw_cases = [json.loads(line) for line in cases_bytes.splitlines() if line.strip()]
        validator = jsonschema.Draft202012Validator(schema)
        for index, raw_case in enumerate(raw_cases, start=1):
            errors = sorted(validator.iter_errors(raw_case), key=lambda item: list(item.path))
            if errors:
                raise ValueError(f"cases.jsonl line {index}: {errors[0].message}")
        cases = tuple(FailureRegressionCase.model_validate(item) for item in raw_cases)
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        raise ValueError(f"invalid failure regression dataset: {error}") from error
    identifiers = [case.id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("case ids must be unique")
    actual_counts = Counter(case.family for case in cases)
    if actual_counts != Counter(manifest.case_counts):
        raise ValueError(
            "case distribution mismatch: "
            f"manifest={manifest.case_counts}, actual={dict(actual_counts)}"
        )
    for case in cases:
        if case.fixture not in fixtures:
            raise ValueError(f"{case.id}: fixture {case.fixture!r} does not exist")
        _materialize_case(case, fixtures)
    return FailureRegressionDataset(
        manifest=manifest,
        cases=cases,
        fixtures=fixtures,
        dataset_sha256=sha256_json(
            {
                "manifest": json.loads(manifest_bytes),
                "cases_sha256": str(manifest.cases_sha256),
                "schema_sha256": str(manifest.schema_sha256),
                "fixtures_sha256": str(manifest.fixtures_sha256),
            }
        ),
        root=root,
    )


def replay_failure_regressions(
    dataset: FailureRegressionDataset, *, project_root: Path
) -> FailureRegressionReport:
    import time

    results: list[FailureRegressionCaseResult] = []
    for case in dataset.cases:
        payload = _materialize_case(case, dataset.fixtures)
        started = time.perf_counter()
        actual_outcome: Literal["accepted", "rejected", "execution_error"] = "accepted"
        actual_error_code = None
        try:
            _dispatch(case, payload)
        except ProductError as error:
            actual_outcome = "rejected"
            actual_error_code = error.code
        except ValueError:
            if case.family == "design_contract":
                actual_outcome = "rejected"
                actual_error_code = "DESIGN_CONTRACT_REJECTED"
            else:
                actual_outcome = "execution_error"
                actual_error_code = "UNEXPECTED_VALUE_ERROR"
        except Exception as error:  # noqa: BLE001 - report unexpected validator failures verbatim by type
            actual_outcome = "execution_error"
            actual_error_code = type(error).__name__
        expected_code_matches = (
            case.expected_error_code is None or case.expected_error_code == actual_error_code
        )
        results.append(
            FailureRegressionCaseResult(
                case_id=case.id,
                family=case.family,
                source_kind=case.source_kind,
                fix_revision=case.fix_revision,
                input_sha256=sha256_json(payload),
                expected_outcome=case.expected_outcome,
                actual_outcome=actual_outcome,
                expected_error_code=case.expected_error_code,
                actual_error_code=actual_error_code,
                matched=actual_outcome == case.expected_outcome and expected_code_matches,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        )
    mismatched = sum(not item.matched for item in results)
    execution_errors = sum(item.actual_outcome == "execution_error" for item in results)
    false_rejections = sum(
        item.expected_outcome == "accepted" and item.actual_outcome == "rejected"
        for item in results
    )
    false_acceptances = sum(
        item.expected_outcome == "rejected" and item.actual_outcome == "accepted"
        for item in results
    )
    return FailureRegressionReport(
        suite_id=dataset.manifest.suite_id,
        suite_version=dataset.manifest.version,
        proof_scope=dataset.manifest.proof_scope,
        dataset_sha256=dataset.dataset_sha256,
        git_revision=_git(project_root, "rev-parse", "HEAD"),
        git_dirty=bool(_git(project_root, "status", "--porcelain")),
        validator_source_sha256={
            "workcell_acceptance": sha256_file(
                project_root / "src/agent_team_os/shared/review_scope.py"
            ),
            "design_contract": sha256_file(
                project_root / "src/agent_team_os/infrastructure/verification/runners/verify.py"
            ),
        },
        environment={
            "python": platform.python_version(),
            "pydantic": importlib.metadata.version("pydantic"),
            "jsonschema": importlib.metadata.version("jsonschema"),
        },
        totals={
            "total": len(results),
            "matched": len(results) - mismatched,
            "mismatched": mismatched,
            "false_rejections": false_rejections,
            "false_acceptances": false_acceptances,
            "execution_errors": execution_errors,
            "skipped": 0,
        },
        cases=tuple(results),
        created_at=datetime.now(UTC),
    )


def write_failure_regression_report(report: FailureRegressionReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rows = "\n".join(
        "| " + " | ".join(
            (
                item.case_id,
                item.expected_outcome,
                item.actual_outcome,
                item.actual_error_code or "-",
                "通过" if item.matched else "失败",
            )
        ) + " |"
        for item in report.cases
    )
    summary = report.totals
    scope_note = (
        "> 证据范围：`offline_contract_regression`。本报告不证明 Live Agent、浏览器闭环、"
        "Release Gate 或 Apply 成功。"
    )
    totals_line = (
        f"- 误拒绝：{summary['false_rejections']}；误接受：{summary['false_acceptances']}；"
        f"执行错误：{summary['execution_errors']}；跳过：{summary['skipped']}"
    )
    markdown = f"""# 交付失败离线合同回放报告

{scope_note}

- Suite：`{report.suite_id}@{report.suite_version}`
- Git Revision：`{report.git_revision}`（dirty：`{str(report.git_dirty).lower()}`）
- 匹配：{summary['matched']}/{summary['total']}
{totals_line}

| Case | 预期 | 实际 | 错误码 | 结果 |
|---|---|---|---|---|
{rows}
"""
    (output_dir / "report.md").write_text(markdown, encoding="utf-8")


def run_failure_regression_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="回放版本化的交付失败案例，输出离线合同回归报告。"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    try:
        dataset = load_failure_regression_dataset(arguments.dataset)
        report = replay_failure_regressions(dataset, project_root=project_root)
        write_failure_regression_report(report, arguments.output_dir)
    except Exception as error:  # noqa: BLE001 - CLI maps data/environment failures to exit 2
        print(f"failure regression execution error: {error}", file=sys.stderr)
        return 2
    print(
        f"failure regression: {report.totals['matched']}/{report.totals['total']} matched; "
        f"report={arguments.output_dir.resolve()}"
    )
    return 0 if report.totals["mismatched"] == 0 else 1


def _dispatch(case: FailureRegressionCase, payload: dict[str, Any]) -> None:
    if case.family == "workcell_acceptance":
        validate_workcell_acceptance(
            payload["requirements"],
            payload["task"],
            tuple(payload["required_workcells"]),
        )
        return
    with tempfile.TemporaryDirectory(prefix="agent-team-os-failure-regression-") as directory:
        root = Path(directory)
        for name in ("contract.json", "schema.json", "vectors.json"):
            (root / name).write_text(
                json.dumps(payload[name], ensure_ascii=False), encoding="utf-8"
            )
        report = design(root)
        if report.get("failed") != 0 or report.get("skipped") != 0:
            raise ValueError("design contract report contains failed or skipped cases")


def _materialize_case(
    case: FailureRegressionCase, fixtures: dict[str, object]
) -> dict[str, Any]:
    raw = fixtures[case.fixture]
    if not isinstance(raw, dict):
        raise ValueError(f"{case.id}: fixture must be an object")
    payload = cast(dict[str, Any], deepcopy(raw))
    variants = {
        "workcell_acceptance": _ownership_variant,
        "design_contract": _design_variant,
    }
    return variants[case.family](payload, case.variant)


def _ownership_variant(payload: dict[str, Any], variant: str) -> dict[str, Any]:
    requirements = payload["requirements"]
    task = payload["task"]
    assert isinstance(requirements, dict) and isinstance(task, dict)
    assignments = task["workcell_acceptance"]
    assert isinstance(assignments, list)
    if variant == "frozen-literal":
        requirements["acceptance_criteria"][0]["statement"] = (
            "执行机器检查时，严格验证 service === 'backend-demo'，并运行本仓真实测试。"
        )
    elif variant == "artifact-consumption":
        assignments[3]["acceptance"][0]["responsibility"] = (
            "消费 Frontend Candidate ArtifactAttachment 并验证跨层行为"
        )
    elif variant == "cross-repository-execution":
        assignments[3]["acceptance"][0]["responsibility"] = (
            "执行 Design、Frontend、Backend 的真实 Candidate 测试并汇总结果"
        )
    elif variant == "missing-workcell":
        assignments.pop()
    else:
        raise ValueError(f"unknown ownership variant: {variant}")
    return payload


def _design_variant(payload: dict[str, Any], variant: str) -> dict[str, Any]:
    contract = payload["contract.json"]
    schema = payload["schema.json"]
    vectors = payload["vectors.json"]
    assert isinstance(contract, dict) and isinstance(schema, dict) and isinstance(vectors, dict)
    if variant == "v1-standard":
        _downgrade_v1(contract, schema, vectors)
    elif variant == "v2-standard":
        pass
    elif variant == "v2-success-contract":
        contract.pop("contract_version")
        contract.pop("success_responses")
        contract["success_contract"] = {
            "status_code": 200,
            "body": {
                "schema": "./schema.json",
                "allowed_fields": ["status", "version", "service"],
                "required_fields": ["status", "version", "service"],
            },
            "responses": [
                {"method": "GET", "path": "/health", "body": "json"},
                {"method": "HEAD", "path": "/health", "body": "empty"},
            ],
            "headers": _headers(),
        }
    elif variant == "v2-singular-response":
        _singular_response(contract, embedded_schema=None)
    elif variant == "v2-embedded-schema":
        _singular_response(contract, embedded_schema=schema)
    elif variant == "v2-split-vectors":
        vectors["response_valid"] = deepcopy(vectors["header_valid"])
        vectors["header_valid"] = [
            {
                **item,
                "response": {
                    **item["response"],
                    "headers": {
                        "X-Health-Contract": item["response"]["headers"]["X-Health-Contract"]
                    },
                },
            }
            for item in vectors["header_valid"]
        ]
    elif variant == "missing-service":
        schema["required"].remove("service")
    elif variant == "wrong-service":
        schema["properties"]["service"]["const"] = "frontend-demo"
    elif variant == "extra-fields":
        schema["additionalProperties"] = True
    elif variant == "missing-header":
        contract["success_responses"]["required_headers"].pop("Cache-Control")
    elif variant == "head-nonempty":
        contract["success_responses"]["head_body_bytes"] = 1
    else:
        raise ValueError(f"unknown design variant: {variant}")
    return payload


def _downgrade_v1(
    contract: dict[str, Any], schema: dict[str, Any], vectors: dict[str, Any]
) -> None:
    contract.clear()
    contract["contract_id"] = "health-contract-v1"
    schema["required"] = ["status", "version"]
    schema["properties"].pop("service")
    schema["properties"]["version"]["const"] = "health-contract-v1"
    for item in vectors["valid"]:
        item["payload"].pop("service", None)
        item["payload"]["version"] = "health-contract-v1"
    wrong_status = vectors["invalid"][0]
    wrong_status["payload"].pop("service", None)
    wrong_status["payload"]["version"] = "health-contract-v1"
    vectors["invalid"] = [wrong_status]
    vectors.pop("header_valid", None)
    vectors.pop("header_invalid", None)


def _singular_response(
    contract: dict[str, Any], *, embedded_schema: dict[str, Any] | None
) -> None:
    contract.pop("contract_version")
    contract.pop("success_responses")
    headers = _headers()
    body: object = (
        embedded_schema
        if embedded_schema is not None
        else {
            "type": "object",
            "closed": True,
            "required_keys": ["status", "version", "service"],
            "version": "health-contract-v2",
            "service": "backend-demo",
        }
    )
    if embedded_schema is None:
        contract.update(
            {
                "success_response": {
                    "method": "GET",
                    "path": "/health",
                    "status": 200,
                    "body_schema": "schema.json",
                    "body": body,
                },
                "success_response_headers": {
                    "X-Health-Contract": {
                        "required_value": "health-contract-v2",
                        "applies_to": ["GET /health", "HEAD /health"],
                        "scope": "successful responses only",
                    }
                },
                "required_success_headers": {
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
                "head_response": {
                    "method": "HEAD",
                    "path": "/health",
                    "body_length": 0,
                    "headers_equal_to_get": list(headers),
                },
            }
        )
        return
    contract.update(
        {
            "success_response": {
                "method": "GET",
                "path": "/health",
                "status_code": 200,
                "body_schema": "schema.json",
                "body": body,
                "headers": headers,
            },
            "success_response_headers": {
                name: {
                    "required_value": value,
                    "applies_to": ["GET /health", "HEAD /health"],
                    "scope": "successful responses only",
                }
                for name, value in headers.items()
            },
            "head_response": {
                "method": "HEAD",
                "path": "/health",
                "status_code": 200,
                "body_bytes": 0,
                "headers": headers,
            },
        }
    )


def _headers() -> dict[str, str]:
    return {
        "X-Health-Contract": "health-contract-v2",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


def _safe_file(root: Path, name: str) -> Path:
    if Path(name).name != name:
        raise ValueError("dataset files must be direct children")
    candidate = root / name
    if candidate.is_symlink() or candidate.resolve().parent != root:
        raise ValueError("dataset files cannot escape the dataset directory or be symbolic links")
    return candidate


def _git(project_root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
