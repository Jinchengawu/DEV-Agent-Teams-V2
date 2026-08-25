"""Read-only browser audit for record-picker layout and page overflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Page, sync_playwright


def _row_metrics(page: Page, selector: str) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        page.locator(selector).evaluate_all(
            """elements => elements.map(element => {
          const rect = element.getBoundingClientRect();
          return {
            label: element.innerText.split(String.fromCharCode(10)).join(" ").slice(0, 90),
            height: Math.round(rect.height * 100) / 100,
            overflow_x: element.scrollWidth - element.clientWidth,
            overflow_y: element.scrollHeight - element.clientHeight,
            white_space: getComputedStyle(element).whiteSpace,
          };
        })""",
        ),
    )


def _authenticate(page: Page, url: str) -> None:
    page.goto(url, wait_until="networkidle")
    if not page.get_by_role("heading", name="登录 Agent-Team-OS").count():
        return
    username = os.environ.get("AGENT_TEAM_OS_TEST_USERNAME")
    password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD")
    if not username or not password:
        raise AssertionError(
            "登录页面需要 AGENT_TEAM_OS_TEST_USERNAME 与 "
            "AGENT_TEAM_OS_TEST_PASSWORD，布局审计不会内置评测凭据。"
        )
    page.get_by_label("用户名").fill(username)
    page.get_by_label("密码").fill(password)
    page.get_by_role("button", name="登录控制平面").click()
    page.get_by_role("link", name="项目", exact=True).wait_for()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--screenshot-dir", type=Path)
    args = parser.parse_args()
    routes = {
        "deliveries": (
            f"/projects/{args.project_id}/deliveries",
            [".delivery-list > .ant-btn"],
        ),
        "evidence": (
            f"/projects/{args.project_id}/evidence",
            [".ledger-table > .ant-btn"],
        ),
        "orchestration": ("/orchestration", [".pipeline-list > .ant-btn"]),
        "agents": ("/agents", [".profile-list > .ant-btn"]),
        "knowledge": (
            f"/projects/{args.project_id}/knowledge",
            [".ant-btn.knowledge-space-item", ".ant-btn.knowledge-doc-item"],
        ),
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        console_errors: list[str] = []
        request_failures: list[str] = []
        http_failures: list[str] = []
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on(
            "requestfailed",
            lambda request: request_failures.append(
                f"{request.method} {request.url}: {request.failure}"
            ),
        )
        page.on(
            "response",
            lambda response: (
                http_failures.append(f"{response.status} {response.request.method} {response.url}")
                if response.status >= 400
                else None
            ),
        )
        _authenticate(page, args.url)
        # The anonymous session probe on the login screen is expected to be 401.
        console_errors.clear()
        request_failures.clear()
        http_failures.clear()

        report: dict[str, Any] = {}
        violations: list[dict[str, Any]] = []
        for name, (route, selectors) in routes.items():
            page.goto(f"{args.url}{route}", wait_until="networkidle")
            page.wait_for_timeout(250)
            report[name] = {
                "page_overflow_x": page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                ),
                "selectors": {},
            }
            for selector in selectors:
                rows = _row_metrics(page, selector)
                report[name]["selectors"][selector] = rows
                for row in rows:
                    if (
                        row["overflow_x"] > 1
                        or row["overflow_y"] > 1
                        or row["white_space"] == "nowrap"
                    ):
                        violations.append({"page": name, "selector": selector, **row})
            if args.screenshot_dir:
                args.screenshot_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshot_dir / f"{name}.png"), full_page=True)

        print(json.dumps(report, ensure_ascii=False, indent=2))
        assert all(item["page_overflow_x"] <= 1 for item in report.values()), report
        assert not violations, violations
        assert not console_errors, console_errors
        assert not request_failures, request_failures
        assert not http_failures, http_failures
        browser.close()


if __name__ == "__main__":
    main()
