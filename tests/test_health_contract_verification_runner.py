from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team_os.infrastructure.verification.runners.verify import design


def _write_design(root: Path, *, contract_version: str) -> None:
    is_v2 = contract_version == "health-contract-v2"
    required = ["status", "version", *(["service"] if is_v2 else [])]
    properties: dict[str, object] = {
        "status": {"type": "string", "enum": ["ok", "degraded", "unavailable"]},
        "version": {"type": "string", "const": contract_version},
    }
    if is_v2:
        properties["service"] = {"type": "string", "const": "backend-demo"}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }
    valid = []
    for status in ("ok", "degraded", "unavailable"):
        payload = {"status": status, "version": contract_version}
        if is_v2:
            payload["service"] = "backend-demo"
        valid.append({"id": status, "payload": payload})
    invalid = [{"id": "wrong-status", "payload": {**valid[0]["payload"], "status": "bad"}}]
    if is_v2:
        invalid.extend(
            [
                {"id": "missing-service", "payload": {"status": "ok", "version": contract_version}},
                {
                    "id": "wrong-service",
                    "payload": {
                        "status": "ok",
                        "version": contract_version,
                        "service": "frontend-demo",
                    },
                },
                {
                    "id": "extra-field",
                    "payload": {
                        "status": "ok",
                        "version": contract_version,
                        "service": "backend-demo",
                        "extra": True,
                    },
                },
            ]
        )
    contract: dict[str, object] = {"contract_id": contract_version}
    vectors: dict[str, object] = {"valid": valid, "invalid": invalid}
    if is_v2:
        contract.update(
            {
                "contract_version": contract_version,
                "success_responses": {
                    "methods": ["GET", "HEAD"],
                    "path": "/health",
                    "required_headers": {
                        "X-Health-Contract": {"value": contract_version, "match": "exact"},
                        "Cache-Control": {"value": "no-store", "match": "exact"},
                        "X-Content-Type-Options": {"value": "nosniff", "match": "exact"},
                    },
                    "success_body_fields": required,
                    "body_schema": "schema.json",
                    "head_body_bytes": 0,
                },
            }
        )
        vectors.update(
            {
                "header_valid": [
                    {
                        "id": "get",
                        "response": {
                            "method": "GET",
                            "path": "/health",
                            "status": 200,
                            "headers": {
                                "X-Health-Contract": contract_version,
                                "Cache-Control": "no-store",
                                "X-Content-Type-Options": "nosniff",
                            },
                        },
                    },
                    {
                        "id": "head",
                        "response": {
                            "method": "HEAD",
                            "path": "/health",
                            "status": 200,
                            "headers": {
                                "X-Health-Contract": contract_version,
                                "Cache-Control": "no-store",
                                "X-Content-Type-Options": "nosniff",
                            },
                            "body": "",
                        },
                    },
                ],
                "header_invalid": [
                    {"id": "missing", "response": {"method": "GET", "headers": {}}},
                    {
                        "id": "wrong",
                        "response": {
                            "method": "HEAD",
                            "headers": {"X-Health-Contract": "health-contract-v1"},
                            "body": "",
                        },
                    },
                ],
            }
        )
    files = (("contract.json", contract), ("schema.json", schema), ("vectors.json", vectors))
    for name, value in files:
        (root / name).write_text(json.dumps(value), encoding="utf-8")


def test_design_runner_keeps_v1_compatibility(tmp_path: Path) -> None:
    _write_design(tmp_path, contract_version="health-contract-v1")

    report = design(tmp_path)

    assert report["failed"] == 0


def test_design_runner_accepts_strict_health_contract_v2(tmp_path: Path) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")

    report = design(tmp_path)

    assert report["failed"] == 0
    assert report["skipped"] == 0
    assert report["passed"] >= 9


def test_design_runner_accepts_equivalent_v2_success_contract_shape(tmp_path: Path) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    contract_path = tmp_path / "contract.json"
    contract = json.loads(contract_path.read_text())
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
        "headers": {
            "X-Health-Contract": "health-contract-v2",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    report = design(tmp_path)

    assert report["failed"] == 0


def test_design_runner_accepts_equivalent_v2_singular_success_response_shape(
    tmp_path: Path,
) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    contract_path = tmp_path / "contract.json"
    contract = json.loads(contract_path.read_text())
    contract.pop("contract_version")
    contract.pop("success_responses")
    contract.update(
        {
            "success_response": {
                "method": "GET",
                "path": "/health",
                "status": 200,
                "body_schema": "schema.json",
                "body": {
                    "type": "object",
                    "closed": True,
                    "required_keys": ["status", "version", "service"],
                    "version": "health-contract-v2",
                    "service": "backend-demo",
                },
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
                "headers_equal_to_get": [
                    "X-Health-Contract",
                    "Cache-Control",
                    "X-Content-Type-Options",
                ],
            },
        }
    )
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    report = design(tmp_path)

    assert report["failed"] == 0


def test_design_runner_accepts_split_contract_header_and_full_response_vectors(
    tmp_path: Path,
) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    vectors_path = tmp_path / "vectors.json"
    vectors = json.loads(vectors_path.read_text())
    vectors["response_valid"] = vectors["header_valid"]
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
    vectors_path.write_text(json.dumps(vectors), encoding="utf-8")

    report = design(tmp_path)

    assert report["failed"] == 0


def test_design_runner_rejects_v2_without_service_constraint(tmp_path: Path) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    schema_path = tmp_path / "schema.json"
    schema = json.loads(schema_path.read_text())
    schema["required"].remove("service")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(ValueError, match="health-contract-v2"):
        design(tmp_path)
