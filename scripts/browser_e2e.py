"""Browser acceptance for a running Agent-Team-OS demo (Playwright is test-only)."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        console_errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto(args.url)
        page.wait_for_load_state("networkidle")

        page.get_by_role("button", name="Agents").click()
        page.get_by_label("实例名称").fill("Browser simulated Codex")
        page.get_by_role("button", name="注册实例").click()
        page.get_by_text("Browser simulated Codex").wait_for()

        page.get_by_role("button", name="Orchestration").click()
        page.get_by_role("button", name="克隆为 Draft").click()
        page.get_by_role("button", name="ACWM 校验").click()
        page.get_by_text("DRAFT", exact=False).wait_for()
        page.get_by_role("button", name="发布不可变 Revision").click()

        page.get_by_role("button", name="Deliveries").click()
        page.locator("textarea").fill(
            "增加 health_status 函数，返回 status=ok 和 version=0.1，并补充 unittest。"
        )
        page.get_by_role("button", name="启动交付闭环 →").click()
        page.locator(".map-label b").filter(has_text="awaiting_plan_decision").wait_for(
            timeout=300_000
        )
        page.get_by_role("button", name="Board").click()
        page.get_by_role("button", name="approve-plan").click()
        page.get_by_role("button", name="accept-candidate").wait_for(timeout=360_000)
        page.get_by_role("button", name="accept-candidate").click()
        page.locator(".map-label b").filter(has_text="completed").wait_for(timeout=60_000)

        page.get_by_role("button", name="Evidence").click()
        assert "diff" in page.locator(".evidence-grid").inner_text().lower()
        assert "APPLY RECEIPT" in page.locator(".evidence-grid").inner_text()
        page.get_by_role("button", name="Knowledge").click()
        page.get_by_placeholder("搜索 Delivery、Acceptance ID、Artifact…").fill("health")
        page.get_by_text("requirement", exact=False).first.wait_for(timeout=15_000)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator(".status-completed").wait_for(timeout=15_000)
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot), full_page=True)
        assert not console_errors, console_errors
        browser.close()


if __name__ == "__main__":
    main()
