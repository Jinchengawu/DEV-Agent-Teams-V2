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
        page.get_by_label("你希望交付什么？").fill(
            "增加 health_status 函数，返回 status=ok 和 version=0.1，并补充 unittest。"
        )
        page.get_by_role("button", name="创建 Delivery").click()
        page.locator("#status").wait_for(state="visible")
        page.wait_for_function(
            "document.querySelector('#status').textContent === 'awaiting_plan_decision'",
            timeout=300_000,
        )
        page.get_by_role("button", name="批准计划并执行").click()
        page.wait_for_function(
            "document.querySelector('#status').textContent === 'awaiting_candidate_decision'",
            timeout=360_000,
        )
        candidate = page.locator("#candidate").inner_text()
        verification = page.locator("#verification").inner_text()
        assert '"unified_diff": "diff --git' in candidate
        assert '"candidate_revision"' in candidate
        assert '"diff_sha256"' in candidate
        assert '"exit_code": 0' in verification
        page.get_by_role("button", name="接受并应用候选").click()
        page.wait_for_function(
            "document.querySelector('#status').textContent === 'completed'",
            timeout=60_000,
        )
        assert '"after_revision"' in page.locator("#receipt").inner_text()
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "document.querySelector('#history').textContent.includes('completed')",
            timeout=15_000,
        )
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot), full_page=True)
        assert not console_errors, console_errors
        browser.close()


if __name__ == "__main__":
    main()
