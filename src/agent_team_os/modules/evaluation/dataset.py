from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_bytes, sha256_json
from .domain import EvaluationDimension, EvaluationSuite


class EvaluationDatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_id: str
    version: str
    source: str
    license: str
    official: bool = False
    scorer_version: str
    required_runtime_features: tuple[str, ...] = ()
    cases_file: str = "cases.jsonl"
    schema_file: str = "schema.json"
    cases_sha256: Sha256
    schema_sha256: Sha256
    case_counts: dict[EvaluationDimension, int]


class EvaluationDatasetCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    dimension: EvaluationDimension
    category: str
    difficulty: int | None = Field(default=None, ge=1, le=3)
    input: dict[str, object]
    expected: dict[str, object]
    scoring: dict[str, object]
    fixture_output: dict[str, object]

    @model_validator(mode="after")
    def validate_dimension_contract(self) -> EvaluationDatasetCase:
        if self.dimension == EvaluationDimension.TOOL_CALL:
            _require(self.expected, "calls", list, self.id)
            _require(self.fixture_output, "calls", list, self.id)
            parallel = self.scoring.get("parallel")
            if not isinstance(parallel, bool):
                raise ValueError(f"{self.id}: scoring.parallel must be boolean")
        elif self.dimension == EvaluationDimension.GENERAL_AGENT:
            _require(self.expected, "value", object, self.id)
            _require(self.fixture_output, "value", object, self.id)
            answer_type = self.scoring.get("answer_type")
            if answer_type not in {"text", "number", "date", "list"}:
                raise ValueError(f"{self.id}: scoring.answer_type is unsupported")
            if self.difficulty is None:
                raise ValueError(f"{self.id}: general_agent requires difficulty")
        elif self.dimension == EvaluationDimension.DATA_GENERATION:
            if self.scoring.get("rubric_version") != "full-chain-v1":
                raise ValueError(f"{self.id}: unsupported generation rubric")
        elif self.scoring.get("probe") != "graph-sqlite-recovery":
            raise ValueError(f"{self.id}: unsupported control-plane probe")
        return self

    def execution_payload(self, mode: Literal["offline", "live"]) -> dict[str, object]:
        payload: dict[str, object] = {
            "dataset_case_id": self.id,
            "dimension": self.dimension.value,
            "category": self.category,
        }
        if self.difficulty is not None:
            payload["difficulty"] = self.difficulty
        if self.dimension == EvaluationDimension.TOOL_CALL:
            payload["expected_calls"] = self.expected["calls"]
            payload["parallel"] = self.scoring["parallel"]
            if mode == "offline":
                payload["actual_calls"] = self.fixture_output["calls"]
        elif self.dimension == EvaluationDimension.GENERAL_AGENT:
            payload["expected"] = self.expected["value"]
            payload["expected_type"] = self.scoring["answer_type"]
            if mode == "offline":
                payload["actual"] = self.fixture_output["value"]
        return payload


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: EvaluationDatasetManifest
    cases: tuple[EvaluationDatasetCase, ...]
    source_sha256: Sha256
    root: Path

    def suite(self) -> EvaluationSuite:
        return EvaluationSuite(
            id=self.manifest.suite_id,
            version=self.manifest.version,
            source=self.manifest.source,
            source_sha256=self.source_sha256,
            scorer_version=self.manifest.scorer_version,
            dimensions=tuple(EvaluationDimension),
            required_runtime_features=self.manifest.required_runtime_features,
            official=self.manifest.official,
        )

    def execution_cases(self, mode: Literal["offline", "live"]) -> tuple[dict[str, object], ...]:
        return tuple(case.execution_payload(mode) for case in self.cases)


def default_dataset_dir(project_root: Path) -> Path:
    return project_root / "evaluation" / "datasets" / "agent-team-os-mvp" / "1.3.0"


def load_evaluation_dataset(dataset_dir: Path) -> EvaluationDataset:
    manifest_path = dataset_dir / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = EvaluationDatasetManifest.model_validate_json(manifest_bytes)
        cases_path = _safe_child(dataset_dir, manifest.cases_file)
        schema_path = _safe_child(dataset_dir, manifest.schema_file)
        cases_bytes = cases_path.read_bytes()
        schema_bytes = schema_path.read_bytes()
    except (OSError, ValidationError, ValueError) as error:
        raise _dataset_error("EVALUATION_DATASET_INVALID", str(error)) from error
    if sha256_bytes(cases_bytes) != manifest.cases_sha256:
        raise _dataset_error("EVALUATION_DATASET_HASH_INVALID", "cases.jsonl SHA-256 mismatch")
    if sha256_bytes(schema_bytes) != manifest.schema_sha256:
        raise _dataset_error("EVALUATION_DATASET_HASH_INVALID", "schema.json SHA-256 mismatch")
    try:
        schema = json.loads(schema_bytes)
        if schema.get("$id") != "agent-team-os-evaluation-case-v1":
            raise ValueError("schema.json has an unexpected $id")
        cases = tuple(
            EvaluationDatasetCase.model_validate_json(line)
            for line in cases_bytes.splitlines()
            if line.strip()
        )
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        raise _dataset_error("EVALUATION_DATASET_INVALID", str(error)) from error
    identifiers = [case.id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise _dataset_error("EVALUATION_DATASET_INVALID", "case ids must be unique")
    actual_counts = Counter(case.dimension for case in cases)
    expected_counts = Counter(manifest.case_counts)
    if actual_counts != expected_counts:
        raise _dataset_error(
            "EVALUATION_DATASET_DISTRIBUTION_INVALID",
            f"manifest={dict(expected_counts)}, actual={dict(actual_counts)}",
        )
    if set(actual_counts) != set(EvaluationDimension):
        raise _dataset_error(
            "EVALUATION_DATASET_DISTRIBUTION_INVALID", "all evaluation dimensions are required"
        )
    source_sha256 = sha256_json(
        {
            "manifest": json.loads(manifest_bytes),
            "cases_sha256": str(manifest.cases_sha256),
            "schema_sha256": str(manifest.schema_sha256),
        }
    )
    return EvaluationDataset(
        manifest=manifest,
        cases=cases,
        source_sha256=source_sha256,
        root=dataset_dir,
    )


def _safe_child(root: Path, name: str) -> Path:
    candidate = (root / name).resolve()
    if candidate.parent != root.resolve():
        raise ValueError("dataset files must be direct children of the dataset directory")
    return candidate


def _require(values: dict[str, object], key: str, expected: type[object], case_id: str) -> None:
    if key not in values or (expected is not object and not isinstance(values[key], expected)):
        raise ValueError(f"{case_id}: {key} has the wrong type")


def _dataset_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="评测数据集校验失败",
        detail=detail,
        repair="恢复版本化数据集，或升级 manifest 中的版本与 SHA-256。",
    )
