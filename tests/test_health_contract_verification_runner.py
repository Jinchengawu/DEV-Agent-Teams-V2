from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team_os.infrastructure.verification.runners.verify import design, qa
from agent_team_os.modules.workcells.verification_evidence import qa_product_observations_valid


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


def test_design_runner_reports_noncanonical_success_responses_shape(
    tmp_path: Path,
) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    contract_path = tmp_path / "contract.json"
    contract = json.loads(contract_path.read_text())
    contract.pop("contract_version")
    contract["success_responses"] = {
        "GET /health": {"body": {"schema": "schema.json"}},
        "HEAD /health": {"body": "empty"},
    }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"contract_version.*success_responses\.methods.*success_responses\.path",
    ):
        design(tmp_path)


def test_design_runner_reports_required_headers_object_shape(tmp_path: Path) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    contract_path = tmp_path / "contract.json"
    contract = json.loads(contract_path.read_text())
    contract["success_responses"]["required_headers"] = [
        "X-Health-Contract: health-contract-v2",
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
    ]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        design(tmp_path)

    message = str(raised.value)
    assert '"X-Health-Contract":{"value":"health-contract-v2"}' in message
    assert '"Cache-Control":{"value":"no-store"}' in message
    assert '"X-Content-Type-Options":{"value":"nosniff"}' in message


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


def test_design_runner_reports_missing_legacy_success_headers(tmp_path: Path) -> None:
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
                }
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

    with pytest.raises(
        ValueError,
        match=r"顶层 contract\.required_success_headers",
    ):
        design(tmp_path)


def test_design_runner_reports_missing_top_level_head_response(tmp_path: Path) -> None:
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
                }
            },
            "required_success_headers": {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            "head": {
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

    with pytest.raises(ValueError, match=r"顶层 contract\.head_response"):
        design(tmp_path)


def test_design_runner_reports_object_body_schema_without_type_error(tmp_path: Path) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    contract_path = tmp_path / "contract.json"
    contract = json.loads(contract_path.read_text())
    contract.pop("contract_version")
    contract.pop("success_responses")
    contract["success_response"] = {
        "method": "GET",
        "path": "/health",
        "status": 200,
        "body_schema": {"$ref": "schema.json"},
        "body": {
            "type": "object",
            "closed": True,
            "required_keys": ["status", "version", "service"],
            "version": "health-contract-v2",
            "service": "backend-demo",
        },
    }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ValueError, match=r"body_schema 必须指向"):
        design(tmp_path)


def test_design_runner_accepts_strict_embedded_schema_and_response_headers(
    tmp_path: Path,
) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    contract_path = tmp_path / "contract.json"
    schema = json.loads((tmp_path / "schema.json").read_text())
    contract = json.loads(contract_path.read_text())
    contract.pop("contract_version")
    contract.pop("success_responses")
    headers = {
        "X-Health-Contract": "health-contract-v2",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    contract.update(
        {
            "success_response": {
                "method": "GET",
                "path": "/health",
                "status_code": 200,
                "body_schema": "schema.json",
                "body": schema,
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


def test_design_runner_reports_all_independent_v2_contract_failures(tmp_path: Path) -> None:
    _write_design(tmp_path, contract_version="health-contract-v2")
    contract_path = tmp_path / "contract.json"
    contract = json.loads(contract_path.read_text())
    contract.pop("contract_version")
    contract.pop("success_responses")
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    vectors_path = tmp_path / "vectors.json"
    vectors = json.loads(vectors_path.read_text())
    vectors["valid"] = []
    vectors["invalid"] = []
    vectors.pop("header_valid")
    vectors.pop("header_invalid")
    vectors_path.write_text(json.dumps(vectors), encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        design(tmp_path)

    message = str(raised.value)
    assert "设计合同必须含非空正反向量" in message
    assert "health-contract-v2 缺少成功响应合同" in message
    assert "contract.success_responses" in message
    assert "顶层 contract.success_response" in message
    assert "顶层 contract.required_success_headers" in message
    assert "顶层 contract.head_response" in message
    assert "health-contract-v2 必须含非空 Header 正反向量" in message


def test_qa_runner_preserves_published_security_headers(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    tests = candidate / "tests"
    tests.mkdir(parents=True)
    (tests / "test_proxy.py").write_text(
        """
import os
import unittest
import urllib.request


class ProxyHeadersTest(unittest.TestCase):
    def test_success_response_preserves_security_header(self) -> None:
        with urllib.request.urlopen(
            os.environ["ATOS_QA_BASE_URL"] + "/api/health", timeout=3
        ) as response:
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
""",
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs"
    backend = inputs / "health-backend-runtime-v1" / "src"
    backend.mkdir(parents=True)
    (inputs / "health-frontend-dist-v1").mkdir(parents=True)
    (backend / "server.py").write_text(
        """
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"status":"ok","version":"health-contract-v2","service":"backend-demo"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Health-Contract", "health-contract-v2")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
""",
        encoding="utf-8",
    )

    report = qa(candidate, inputs)

    assert report["failed"] == 0
    assert report["skipped"] == 0


def test_qa_runner_provides_product_faults_and_records_actual_observations(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    tests = candidate / "tests"
    tests.mkdir(parents=True)
    (tests / "test_product_faults.py").write_text(
        """
import json
import os
import unittest
import urllib.request


class ProductFaultsTest(unittest.TestCase):
    def request(self, *, method="GET", fault=None):
        request = urllib.request.Request(
            os.environ["ATOS_QA_BASE_URL"] + "/api/health?status=ok",
            method=method,
            headers={"X-Agent-Team-OS-QA-Fault": fault} if fault else {},
        )
        return urllib.request.urlopen(request, timeout=3)

    def test_success_get_and_head(self) -> None:
        with self.request() as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("X-Health-Contract"), "health-contract-v2")
            self.assertEqual(
                json.loads(response.read()),
                {"status": "ok", "version": "health-contract-v2", "service": "backend-demo"},
            )
        with self.request(method="HEAD") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")

    def test_product_faults_are_real_http_responses(self) -> None:
        expected = {
            "missing_service": {"status": "ok", "version": "health-contract-v2"},
            "wrong_service": {
                "status": "ok", "version": "health-contract-v2", "service": "other-service"
            },
            "extra_field": {
                "status": "ok", "version": "health-contract-v2", "service": "backend-demo",
                "extra": True,
            },
            "wrong_version": {
                "status": "ok", "version": "health-contract-v1", "service": "backend-demo"
            },
        }
        for fault, body in expected.items():
            with self.subTest(fault=fault), self.request(fault=fault) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), body)
""",
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs"
    backend = inputs / "health-backend-runtime-v1" / "src"
    backend.mkdir(parents=True)
    (inputs / "health-frontend-dist-v1").mkdir(parents=True)
    (backend / "server.py").write_text(
        """
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def handle_request(self, *, include_body):
        body = b'{"status":"ok","version":"health-contract-v2","service":"backend-demo"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Health-Contract", "health-contract-v2")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self):
        self.handle_request(include_body=True)

    def do_HEAD(self):
        self.handle_request(include_body=False)

    def log_message(self, *_args):
        pass


parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
""",
        encoding="utf-8",
    )

    report = qa(candidate, inputs)

    assert report["failed"] == 0
    assert report["skipped"] == 0
    assert report["product_observations"] == {
        "contract_version": "qa-product-observations-v1",
        "success_get": {
            "body_keys": ["service", "status", "version"],
            "version": "health-contract-v2",
            "service": "backend-demo",
            "headers": {
                "cache-control": "no-store",
                "x-health-contract": "health-contract-v2",
                "x-content-type-options": "nosniff",
            },
        },
        "success_head": {
            "body_bytes": 0,
            "headers": {
                "cache-control": "no-store",
                "x-health-contract": "health-contract-v2",
                "x-content-type-options": "nosniff",
            },
        },
        "fault_modes_observed": [
            "extra_field",
            "missing_service",
            "wrong_service",
            "wrong_version",
        ],
    }
    assert qa_product_observations_valid(report)


def test_qa_product_observations_reject_missing_fault_or_runtime_value() -> None:
    complete = {
        "product_observations": {
            "contract_version": "qa-product-observations-v1",
            "success_get": {
                "body_keys": ["service", "status", "version"],
                "version": "health-contract-v2",
                "service": "backend-demo",
                "headers": {
                    "cache-control": "no-store",
                    "x-health-contract": "health-contract-v2",
                    "x-content-type-options": "nosniff",
                },
            },
            "success_head": {
                "body_bytes": 0,
                "headers": {
                    "cache-control": "no-store",
                    "x-health-contract": "health-contract-v2",
                    "x-content-type-options": "nosniff",
                },
            },
            "fault_modes_observed": [
                "extra_field",
                "missing_service",
                "wrong_service",
                "wrong_version",
            ],
        }
    }

    missing_fault = json.loads(json.dumps(complete))
    missing_fault["product_observations"]["fault_modes_observed"].pop()
    wrong_version = json.loads(json.dumps(complete))
    wrong_version["product_observations"]["success_get"]["version"] = "health-contract-v1"

    assert not qa_product_observations_valid(missing_fault)
    assert not qa_product_observations_valid(wrong_version)
