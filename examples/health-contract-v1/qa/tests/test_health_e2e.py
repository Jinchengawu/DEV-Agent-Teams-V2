"""仅通过产品提供的 HTTP 页面验证完整前后端交付。"""

from __future__ import annotations

import os
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


class HealthE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_url = os.environ["ATOS_QA_BASE_URL"].rstrip("/")
        parsed = urlsplit(cls.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("QA_ENDPOINT_MUST_BE_PRODUCT_LOOPBACK")
        executable = os.environ["ATOS_CHROMIUM_EXECUTABLE"]
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(executable_path=executable, headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self) -> None:
        self.page = self.browser.new_page()

    def tearDown(self) -> None:
        self.page.context.close()

    def check_status(self, status: str) -> None:
        self.page.goto(f"{self.base_url}/?status={status}")
        expect(self.page.get_by_test_id("health-status")).to_have_text(status)
        expect(self.page.get_by_test_id("health-error")).to_be_hidden()

    def test_ok(self) -> None:
        self.check_status("ok")

    def test_degraded(self) -> None:
        self.check_status("degraded")

    def test_unavailable(self) -> None:
        self.check_status("unavailable")

    def test_invalid_response(self) -> None:
        self.page.goto(f"{self.base_url}/?status=invalid")
        expect(self.page.get_by_test_id("health-status")).to_have_text("unavailable")
        expect(self.page.get_by_test_id("health-error")).to_be_visible()


if __name__ == "__main__":
    unittest.main()
