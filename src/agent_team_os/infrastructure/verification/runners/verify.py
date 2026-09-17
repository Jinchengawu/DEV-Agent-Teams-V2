"""产品固定子进程入口。加载产品测试框架后，才允许导入 Candidate 模块。"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

ErrorInfo = tuple[type[BaseException], BaseException, TracebackType] | tuple[None, None, None]


def result(cases: Sequence[Mapping[str, object]]) -> dict[str, Any]:
    return {
        "discovered": len(cases),
        "passed": sum(case["status"] == "passed" for case in cases),
        "failed": sum(case["status"] == "failed" for case in cases),
        "skipped": sum(case["status"] == "skipped" for case in cases),
        "case_ids": [case["id"] for case in cases],
        "cases": cases,
    }


def design(root: Path) -> dict[str, object]:
    import jsonschema  # type: ignore[import-untyped]

    contract = json.loads((root / "contract.json").read_text())
    schema = json.loads((root / "schema.json").read_text())
    vectors = json.loads((root / "vectors.json").read_text())
    contract_id = contract.get("contract_id")
    if contract_id not in {"health-contract-v1", "health-contract-v2"}:
        raise ValueError("设计合同 ID 不匹配")
    is_v2 = contract_id == "health-contract-v2"
    required_fields = {"status", "version", *( {"service"} if is_v2 else set())}
    jsonschema.Draft202012Validator.check_schema(schema)
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or set(schema.get("required", [])) != required_fields
        or set(schema["properties"]["status"].get("enum", [])) != {"ok", "degraded", "unavailable"}
        or schema["properties"]["version"].get("const") != contract_id
        or (is_v2 and schema["properties"].get("service", {}).get("const") != "backend-demo")
    ):
        raise ValueError(f"设计 Schema 偏离冻结的 {contract_id} 合同")
    valid = vectors.get("valid", [])
    invalid = vectors.get("invalid", [])
    validation_errors: list[str] = []
    if not valid or not invalid:
        validation_errors.append("设计合同必须含非空正反向量")
    if {item["payload"].get("status") for item in valid} != {"ok", "degraded", "unavailable"}:
        validation_errors.append("设计正向量必须覆盖全部状态")
    validator = jsonschema.Draft202012Validator(schema)
    cases: list[dict[str, object]] = []
    for category, values in (("valid", valid), ("invalid", invalid)):
        for item in values:
            accepted = validator.is_valid(item["payload"])
            cases.append(
                {
                    "id": f"{category}:{item['id']}",
                    "status": "passed" if accepted == (category == "valid") else "failed",
                }
            )
    if is_v2:
        try:
            _validate_health_contract_v2_metadata(contract)
        except ValueError as error:
            validation_errors.append(str(error))
        try:
            cases.extend(_health_contract_v2_header_cases(vectors))
        except ValueError as error:
            validation_errors.append(str(error))
    if len({case["id"] for case in cases}) != len(cases):
        validation_errors.append("设计向量 ID 重复")
    if validation_errors:
        raise ValueError("; ".join(validation_errors))
    return result(cases)


def _validate_health_contract_v2_metadata(contract: Mapping[str, object]) -> None:
    success = contract.get("success_responses")
    expected_headers = {
        "X-Health-Contract": "health-contract-v2",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if isinstance(success, Mapping):
        headers = success.get("required_headers")
        actual_headers = (
            {
                name: value.get("value")
                for name, value in headers.items()
                if isinstance(value, Mapping)
            }
            if isinstance(headers, Mapping)
            else {}
        )
        mismatches: list[str] = []
        if contract.get("contract_version") != "health-contract-v2":
            mismatches.append('contract_version 必须为 "health-contract-v2"')
        if success.get("methods") != ["GET", "HEAD"]:
            mismatches.append('success_responses.methods 必须为 ["GET","HEAD"]')
        if success.get("path") != "/health":
            mismatches.append('success_responses.path 必须为 "/health"')
        if success.get("success_body_fields") != ["status", "version", "service"]:
            mismatches.append(
                "success_responses.success_body_fields 必须为 "
                '["status","version","service"]'
            )
        if success.get("body_schema") != "schema.json":
            mismatches.append('success_responses.body_schema 必须为 "schema.json"')
        if success.get("head_body_bytes") != 0:
            mismatches.append("success_responses.head_body_bytes 必须为 0")
        if actual_headers != expected_headers:
            mismatches.append(
                "success_responses.required_headers 必须精确声明 "
                "X-Health-Contract=health-contract-v2、Cache-Control=no-store 与 "
                "X-Content-Type-Options=nosniff"
            )
        if mismatches:
            raise ValueError(
                "health-contract-v2 成功响应元数据不匹配：" + "; ".join(mismatches)
            )
        return

    success_response = contract.get("success_response")
    if isinstance(success_response, Mapping):
        body = success_response.get("body")
        version_header = contract.get("success_response_headers")
        required_headers = contract.get("required_success_headers")
        head_response = contract.get("head_response")
        version_header_contract = (
            version_header.get("X-Health-Contract")
            if isinstance(version_header, Mapping)
            else None
        )
        compact_body = (
            isinstance(body, Mapping)
            and body.get("type") == "object"
            and body.get("closed") is True
            and body.get("required_keys") == ["status", "version", "service"]
            and body.get("version") == "health-contract-v2"
            and body.get("service") == "backend-demo"
        )
        properties = body.get("properties") if isinstance(body, Mapping) else None
        embedded_schema_body = (
            isinstance(body, Mapping)
            and body.get("type") == "object"
            and body.get("additionalProperties") is False
            and body.get("required") == ["status", "version", "service"]
            and isinstance(properties, Mapping)
            and set(properties) == {"status", "version", "service"}
            and properties.get("status")
            == {"type": "string", "enum": ["ok", "degraded", "unavailable"]}
            and properties.get("version")
            == {"type": "string", "const": "health-contract-v2"}
            and properties.get("service")
            == {"type": "string", "const": "backend-demo"}
        )
        declared_headers = (
            {
                name: value.get("required_value")
                for name, value in version_header.items()
                if isinstance(value, Mapping)
            }
            if isinstance(version_header, Mapping)
            else {}
        )
        mismatches = []
        if success_response.get("method") != "GET":
            mismatches.append('success_response.method 必须为 "GET"')
        if success_response.get("path") != "/health":
            mismatches.append('success_response.path 必须为 "/health"')
        if success_response.get("body_schema") not in {"schema.json", "./schema.json"}:
            mismatches.append('success_response.body_schema 必须指向 "schema.json"')
        if compact_body:
            if success_response.get("status") != 200:
                mismatches.append("success_response.status 必须为 200")
            if not (
                isinstance(version_header_contract, Mapping)
                and version_header_contract.get("required_value") == "health-contract-v2"
                and version_header_contract.get("applies_to")
                == ["GET /health", "HEAD /health"]
            ):
                mismatches.append(
                    "success_response_headers.X-Health-Contract 必须声明 "
                    'required_value="health-contract-v2" 且同时适用 GET/HEAD'
                )
            if required_headers != {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            }:
                mismatches.append(
                    "顶层 contract.required_success_headers 必须精确为 "
                    '{"Cache-Control":"no-store",'
                    '"X-Content-Type-Options":"nosniff"}'
                )
            if not (
                isinstance(head_response, Mapping)
                and head_response.get("method") == "HEAD"
                and head_response.get("path") == "/health"
                and head_response.get("body_length") == 0
                and set(head_response.get("headers_equal_to_get", []))
                == set(expected_headers)
            ):
                mismatches.append(
                    "顶层 contract.head_response 必须声明 method=HEAD、"
                    "path=/health、body_length=0，且 headers_equal_to_get 覆盖三个固定 Header"
                )
        elif embedded_schema_body:
            if success_response.get("status_code") != 200:
                mismatches.append("success_response.status_code 必须为 200")
            if success_response.get("headers") != expected_headers:
                mismatches.append("success_response.headers 必须精确包含三个固定 Header")
            if declared_headers != expected_headers:
                mismatches.append(
                    "success_response_headers 必须为三个固定 Header 声明 required_value"
                )
            if not (
                isinstance(head_response, Mapping)
                and head_response.get("method") == "HEAD"
                and head_response.get("path") == "/health"
                and head_response.get("status_code") == 200
                and head_response.get("body_bytes") == 0
                and head_response.get("headers") == expected_headers
            ):
                mismatches.append(
                    "顶层 contract.head_response 必须声明 HEAD /health、"
                    "status_code=200、body_bytes=0 与三个固定 Header"
                )
        else:
            mismatches.append(
                "success_response.body 必须使用支持的 compact contract 或完整内嵌 Schema"
            )
        if mismatches:
            raise ValueError(
                "health-contract-v2 成功响应元数据不匹配：" + "; ".join(mismatches)
            )
        return

    success_contract = contract.get("success_contract")
    if not isinstance(success_contract, Mapping):
        raise ValueError("health-contract-v2 缺少成功响应合同")
    body = success_contract.get("body")
    responses = success_contract.get("responses")
    alternate_headers = success_contract.get("headers")
    response_contracts = (
        {
            (response.get("method"), response.get("path"), response.get("body"))
            for response in responses
            if isinstance(response, Mapping)
        }
        if isinstance(responses, list)
        else set()
    )
    expected_responses = {("GET", "/health", "json"), ("HEAD", "/health", "empty")}
    if (
        success_contract.get("status_code") != 200
        or not isinstance(body, Mapping)
        or body.get("schema") not in {"schema.json", "./schema.json"}
        or body.get("allowed_fields") != ["status", "version", "service"]
        or body.get("required_fields") != ["status", "version", "service"]
        or response_contracts != expected_responses
        or alternate_headers != expected_headers
    ):
        raise ValueError("health-contract-v2 成功响应元数据不匹配")


def _health_contract_v2_header_cases(vectors: Mapping[str, object]) -> list[dict[str, object]]:
    expected_headers = {
        "X-Health-Contract": "health-contract-v2",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    valid = vectors.get("header_valid")
    invalid = vectors.get("header_invalid")
    if not isinstance(valid, list) or not valid or not isinstance(invalid, list) or not invalid:
        raise ValueError("health-contract-v2 必须含非空 Header 正反向量")
    cases: list[dict[str, object]] = []
    full_response_methods: set[str] = set()
    full_responses = vectors.get("response_valid")
    if isinstance(full_responses, list):
        for item in full_responses:
            response = item.get("response", {}) if isinstance(item, Mapping) else {}
            headers = response.get("headers", {}) if isinstance(response, Mapping) else {}
            method = response.get("method") if isinstance(response, Mapping) else None
            accepted = (
                method in {"GET", "HEAD"}
                and response.get("path") == "/health"
                and response.get("status") == 200
                and isinstance(headers, Mapping)
                and all(headers.get(name) == value for name, value in expected_headers.items())
                and (method != "HEAD" or response.get("body") == "")
            )
            if accepted and isinstance(method, str):
                full_response_methods.add(method)
            cases.append(
                {
                    "id": f"response-valid:{item['id']}",
                    "status": "passed" if accepted else "failed",
                }
            )
    split_header_vectors = full_response_methods == {"GET", "HEAD"}
    header_vector_expectation = (
        {"X-Health-Contract": "health-contract-v2"}
        if split_header_vectors
        else expected_headers
    )
    valid_methods: set[str] = set()
    for category, values in (("header-valid", valid), ("header-invalid", invalid)):
        for item in values:
            response = item.get("response", {}) if isinstance(item, Mapping) else {}
            headers = response.get("headers", {}) if isinstance(response, Mapping) else {}
            method = response.get("method") if isinstance(response, Mapping) else None
            accepted = (
                method in {"GET", "HEAD"}
                and response.get("path") == "/health"
                and response.get("status") == 200
                and isinstance(headers, Mapping)
                and all(
                    headers.get(name) == value
                    for name, value in header_vector_expectation.items()
                )
                and (method != "HEAD" or response.get("body") == "")
            )
            if category == "header-valid" and accepted and isinstance(method, str):
                valid_methods.add(method)
            cases.append(
                {
                    "id": f"{category}:{item['id']}",
                    "status": "passed" if accepted == (category == "header-valid") else "failed",
                }
            )
    if valid_methods | full_response_methods != {"GET", "HEAD"}:
        raise ValueError("health-contract-v2 Header 正向量必须覆盖 GET 与 HEAD")
    return cases


class Results(unittest.TestResult):
    def __init__(self) -> None:
        super().__init__()
        self.cases: list[dict[str, object]] = []

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.cases.append({"id": test.id(), "status": "passed"})

    def addFailure(self, test: unittest.TestCase, err: ErrorInfo) -> None:
        super().addFailure(test, err)
        print(self.failures[-1][1], file=sys.stderr)
        self.cases.append({"id": test.id(), "status": "failed"})

    def addError(self, test: unittest.TestCase, err: ErrorInfo) -> None:
        super().addError(test, err)
        print(self.errors[-1][1], file=sys.stderr)
        self.cases.append({"id": test.id(), "status": "failed"})

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self.cases.append({"id": test.id(), "status": "skipped"})

    def addExpectedFailure(self, test: unittest.TestCase, err: ErrorInfo) -> None:
        self.cases.append({"id": test.id(), "status": "skipped"})

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        self.cases.append({"id": test.id(), "status": "failed"})

    def addSubTest(
        self, test: unittest.TestCase, subtest: unittest.TestCase, err: ErrorInfo | None
    ) -> None:
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.cases.append({"id": subtest.id(), "status": "failed"})


def python_tests(root: Path) -> dict[str, object]:
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    discovered = suite.countTestCases()
    output = Results()
    suite.run(output)
    data = result(output.cases)
    data["discovered"] = discovered
    # 类级 setup/teardown 错误也必须由结果合同看见。
    if not discovered or output.testsRun != discovered:
        data["failed"] = max(1, int(data["failed"]))
    return data


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def backend(root: Path) -> Iterator[str]:
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(root / "src/server.py"), "--port", str(port)],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Backend 服务提前退出")
            try:
                with urllib.request.urlopen(url + "/health", timeout=0.2) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.02)
        else:
            raise RuntimeError("Backend 服务未就绪")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def backend_http(root: Path, inputs: Path) -> dict[str, object]:
    import jsonschema

    design_root = inputs / "health-design-v1"
    design(design_root)
    schema = json.loads((design_root / "schema.json").read_text())
    cases = []
    with backend(root) as url:
        for status in ("ok", "degraded", "unavailable"):
            with urllib.request.urlopen(url + "/health?status=" + status, timeout=3) as response:
                data = json.loads(response.read())
            passed = (
                jsonschema.Draft202012Validator(schema).is_valid(data) and data["status"] == status
            )
            cases.append({"id": "http:" + status, "status": "passed" if passed else "failed"})
        try:
            urllib.request.urlopen(url + "/health?status=invalid", timeout=3).close()
            rejected = False
        except urllib.error.HTTPError as error:
            rejected = error.code == 400
        cases.append({"id": "http:invalid", "status": "passed" if rejected else "failed"})
    return result(cases)


def qa(root: Path, inputs: Path) -> dict[str, object]:
    frontend = inputs / "health-frontend-dist-v1"
    with backend(inputs / "health-backend-runtime-v1") as backend_url:

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=str(frontend), **kwargs)

            def do_GET(self) -> None:
                path = urllib.parse.urlsplit(self.path)
                if path.path == "/api/health":
                    self._proxy_health(path.query, method="GET")
                else:
                    super().do_GET()

            def do_HEAD(self) -> None:
                path = urllib.parse.urlsplit(self.path)
                if path.path == "/api/health":
                    self._proxy_health(path.query, method="HEAD")
                else:
                    super().do_HEAD()

            def _proxy_health(self, query: str, *, method: str) -> None:
                target = backend_url + "/health" + ("?" + query if query else "")
                request = urllib.request.Request(target, method=method)
                try:
                    with urllib.request.urlopen(request, timeout=3) as response:
                        status = response.status
                        headers = response.headers
                        body = response.read() if method == "GET" else b""
                except urllib.error.HTTPError as error:
                    status = error.code
                    headers = error.headers
                    body = error.read() if method == "GET" else b""
                self.send_response(status)
                for name in (
                    "Content-Type",
                    "Cache-Control",
                    "X-Health-Contract",
                    "X-Content-Type-Options",
                ):
                    if value := headers.get(name):
                        self.send_header(name, value)
                self.send_header("Content-Length", headers.get("Content-Length", str(len(body))))
                self.end_headers()
                if method == "GET":
                    self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        os.environ["ATOS_QA_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
        try:
            return python_tests(root)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def main() -> int:
    mode, directory, result_file, input_directory = sys.argv[1:]
    root, inputs = Path(directory), Path(input_directory)
    # Candidate 测试不继承控制器的参数路径。
    sys.argv = ["product-verification"]
    if os.environ.get("ATOS_VERIFICATION_PYTHON_SITE"):
        sys.path.insert(0, os.environ["ATOS_VERIFICATION_PYTHON_SITE"])
    try:
        if mode == "design":
            data = design(root / "design")
        elif mode == "backend-http":
            data = backend_http(root, inputs)
        elif mode == "qa":
            data = qa(root, inputs)
        else:
            data = python_tests(root)
    except Exception as error:
        data = result([{"id": "runner", "status": "failed"}])
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
    Path(result_file).write_text(json.dumps(data, sort_keys=True))
    return 0 if data["discovered"] and not data["failed"] and not data["skipped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
