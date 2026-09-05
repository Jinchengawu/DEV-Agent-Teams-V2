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
    if contract.get("contract_id") != "health-contract-v1":
        raise ValueError("设计合同 ID 不匹配")
    jsonschema.Draft202012Validator.check_schema(schema)
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or set(schema.get("required", [])) != {"status", "version"}
        or set(schema["properties"]["status"].get("enum", [])) != {"ok", "degraded", "unavailable"}
        or schema["properties"]["version"].get("const") != "health-contract-v1"
    ):
        raise ValueError("设计 Schema 偏离冻结的 health-contract-v1 合同")
    valid = vectors.get("valid", [])
    invalid = vectors.get("invalid", [])
    if not valid or not invalid:
        raise ValueError("设计合同必须含非空正反向量")
    if {item["payload"].get("status") for item in valid} != {"ok", "degraded", "unavailable"}:
        raise ValueError("设计正向量必须覆盖全部状态")
    validator = jsonschema.Draft202012Validator(schema)
    cases = []
    for category, values in (("valid", valid), ("invalid", invalid)):
        for item in values:
            accepted = validator.is_valid(item["payload"])
            cases.append(
                {
                    "id": f"{category}:{item['id']}",
                    "status": "passed" if accepted == (category == "valid") else "failed",
                }
            )
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("设计向量 ID 重复")
    return result(cases)


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
                    target = backend_url + "/health" + ("?" + path.query if path.query else "")
                    try:
                        with urllib.request.urlopen(target, timeout=3) as response:
                            status, body = response.status, response.read()
                    except urllib.error.HTTPError as error:
                        status, body = error.code, error.read()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()

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
