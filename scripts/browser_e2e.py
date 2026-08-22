"""Browser acceptance for a running Agent-Team-OS demo (Playwright is test-only)."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Only verify rendering, navigation and console integrity; do not mutate data.",
    )
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

        bootstrap_heading = page.get_by_role("heading", name="初始化管理员")
        if bootstrap_heading.count():
            password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD") or (
                f"Browser-{secrets.token_urlsafe(18)}-2026"
            )
            page.get_by_label("密码").fill(password)
            page.get_by_role("button", name="创建并登录").click()
            page.get_by_role("link", name="交付", exact=True).wait_for()
        elif page.get_by_role("heading", name="登录 Agent-Team-OS").count():
            password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD")
            if not password:
                raise AssertionError(
                    "existing browser-test database requires AGENT_TEAM_OS_TEST_PASSWORD"
                )
            page.get_by_label("用户名").fill(
                os.environ.get("AGENT_TEAM_OS_TEST_USERNAME", "admin")
            )
            page.get_by_label("密码").fill(password)
            page.get_by_role("button", name="登录控制平面").click()
            page.get_by_role("link", name="交付", exact=True).wait_for()

        assert page.locator("body").inner_text().strip(), "rendered page is blank"
        assert not page.locator(".vite-error-overlay").count(), "Vite error overlay found"
        for navigation in (
            "交付",
            "看板",
            "可视化编排",
            "智能体实例",
            "知识中心",
            "证据",
            "设置",
        ):
            page.get_by_role("link", name=navigation, exact=True).click()
            page.get_by_role("heading", name=navigation, exact=True).wait_for()
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot), full_page=True)
        if args.smoke_only:
            assert not console_errors, console_errors
            browser.close()
            return

        page.get_by_role("link", name="智能体实例").click()
        page.get_by_label("实例名称").fill("Browser simulated Codex")
        page.get_by_role("button", name="注册实例").click()
        page.get_by_text("Browser simulated Codex").wait_for()

        page.get_by_role("link", name="可视化编排").click()
        page.get_by_role("button", name="克隆为可编辑草稿", exact=True).click()
        page.get_by_role("button", name="ACWM 校验").click()
        page.get_by_text("草稿", exact=False).first.wait_for()
        page.get_by_role("button", name="发布不可变版本").click()

        page.get_by_role("link", name="交付").click()
        page.get_by_label("交付需求").fill(
            "增加 health_status 函数，返回 status=ok 和 version=0.1，并补充 unittest。"
        )
        page.get_by_role("button", name="启动真实闭环").click()
        page.locator(".map-label b").filter(has_text="等待计划审批").wait_for(timeout=300_000)
        page.get_by_role("button", name="批准计划并开始执行").click()
        page.get_by_role("button", name="接受候选并原子应用").wait_for(
            timeout=360_000
        )
        page.get_by_role("button", name="接受候选并原子应用").click()
        page.locator(".map-label b").filter(has_text="已完成").wait_for(timeout=60_000)

        page.get_by_role("link", name="证据").click()
        page.get_by_text("应用回执", exact=True).first.wait_for()
        page.get_by_role("link", name="知识中心").click()
        page.get_by_label("全文搜索").fill("health")
        page.get_by_text("需求", exact=False).first.wait_for(timeout=15_000)
        page.reload()
        page.wait_for_load_state("networkidle")
        try:
            page.get_by_role("link", name="交付", exact=True).wait_for(timeout=15_000)
        except Exception as error:
            body = page.locator("body").inner_text()
            raise AssertionError(f"刷新后会话未恢复：{body}") from error
        page.get_by_role("link", name="交付", exact=True).click()
        page.locator(".status-completed").first.wait_for(timeout=15_000)
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot), full_page=True)
        assert not console_errors, console_errors
        browser.close()


if __name__ == "__main__":
    main()
