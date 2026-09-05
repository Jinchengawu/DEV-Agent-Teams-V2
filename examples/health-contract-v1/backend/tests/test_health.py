"""通过真实 loopback HTTP 验证后端公开合同。"""

from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


class HealthHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        module_path = Path(__file__).resolve().parents[1] / "src" / "server.py"
        spec = importlib.util.spec_from_file_location("health_server", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), module.HealthHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)

    def read_health(self, suffix: str = "") -> dict[str, str]:
        with urlopen(f"{self.url}/health{suffix}", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/json")
            return json.load(response)

    def test_default_ok(self) -> None:
        self.assertEqual(self.read_health(), {"status": "ok", "version": "health-contract-v1"})

    def test_supported_statuses(self) -> None:
        for status in ("ok", "degraded", "unavailable"):
            with self.subTest(status=status):
                self.assertEqual(
                    self.read_health(f"?status={status}"),
                    {
                        "status": status,
                        "version": "health-contract-v1",
                    },
                )

    def test_invalid_status_is_bad_request(self) -> None:
        for query in ("?status=invalid", "?status=", "?status=ok&status=degraded"):
            with self.subTest(query=query):
                with self.assertRaises(HTTPError) as error:
                    self.read_health(query)
                self.assertEqual(error.exception.code, 400)

    def test_unknown_route_is_not_health(self) -> None:
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.url}/unknown", timeout=5)
        self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
