"""Browser acceptance for project creation, switching, scoping and persistence."""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        console_errors: list[str] = []
        authenticating = {"value": True}

        def capture_console(message: object) -> None:
            message_type = getattr(message, "type", "")
            text = str(getattr(message, "text", ""))
            if message_type != "error":
                return
            if authenticating["value"] and "401 (Unauthorized)" in text:
                return
            console_errors.append(text)

        page.on("console", capture_console)
        page.on("pageerror", lambda error: console_errors.append(str(error)))
        scoped_delivery_requests: list[str] = []
        page.on(
            "request",
            lambda request: scoped_delivery_requests.append(request.url)
            if "/v1/deliveries?project_id=" in request.url
            else None,
        )

        try:
            _authenticate(page, args.url)
            authenticating["value"] = False
            suffix = secrets.token_hex(4)
            project_one = f"switch-one-{suffix}"
            project_two = f"switch-two-{suffix}"
            _create_project(page, args.url, project_one, "项目切换验收一")
            _create_project(page, args.url, project_two, "项目切换验收二")

            selector = page.get_by_label("当前项目")
            selector.select_option(project_one)
            page.wait_for_url(re.compile(rf"/projects/{project_one}/overview$"))
            _wait_for_project_selector(page, project_one)
            page.wait_for_load_state("networkidle")
            page.get_by_role("link", name="交付工作台", exact=True).click()
            page.wait_for_url(re.compile(rf"/projects/{project_one}/deliveries$"))
            page.get_by_text("当前还没有交付运行", exact=True).wait_for()

            selector.select_option(project_two)
            page.wait_for_url(re.compile(rf"/projects/{project_two}/deliveries$"))
            _wait_for_project_selector(page, project_two)
            page.get_by_text("当前还没有交付运行", exact=True).wait_for()

            page.get_by_role("link", name="设置", exact=True).click()
            page.wait_for_url(re.compile(r"/settings$"))
            page.reload()
            page.wait_for_load_state("networkidle")
            page.get_by_label("当前项目").wait_for()
            assert page.get_by_label("当前项目").input_value() == project_two
            page.get_by_role("link", name="交付工作台", exact=True).click()
            page.wait_for_url(re.compile(rf"/projects/{project_two}/deliveries$"))
            page.get_by_text("当前还没有交付运行", exact=True).wait_for()

            assert any(f"project_id={project_one}" in url for url in scoped_delivery_requests)
            assert any(f"project_id={project_two}" in url for url in scoped_delivery_requests)
            assert not console_errors, console_errors
            if args.screenshot is not None:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=args.screenshot, full_page=True)
        finally:
            browser.close()


def _authenticate(page: Page, url: str) -> None:
    page.goto(url)
    page.wait_for_load_state("networkidle")
    if page.get_by_role("heading", name="初始化管理员").count():
        password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD") or (
            f"Project-{secrets.token_urlsafe(18)}-2026"
        )
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="创建并登录").click()
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
    page.get_by_role("link", name="项目", exact=True).wait_for()


def _create_project(page: Page, url: str, project_id: str, name: str) -> None:
    page.goto(f"{url}/projects")
    page.wait_for_load_state("networkidle")
    page.get_by_placeholder("例如：pj1").fill(project_id)
    page.get_by_placeholder("例如：客户门户后端").fill(name)
    pipeline = page.get_by_label("默认流水线")
    pipeline.select_option(index=1)
    deployment_checks = page.locator(".project-create input[type=checkbox]")
    for index in range(deployment_checks.count()):
        deployment_checks.nth(index).check()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/projects")
    ) as created_response:
        page.get_by_role("button", name="创建并初始化独立工作区").click()
    assert created_response.value.status == 201, created_response.value.text()
    detail = created_response.value.json()
    assert detail["project"]["id"] == project_id, detail
    assert detail["workspace"]["workspace_id"] == f"project:{project_id}", detail
    page.wait_for_url(re.compile(rf"/projects/{project_id}/overview$"))
    _wait_for_project_selector(page, project_id)


def _wait_for_project_selector(page: Page, project_id: str) -> None:
    page.wait_for_function(
        "projectId => document.querySelector('[aria-label=\"当前项目\"]')?.value === projectId",
        arg=project_id,
    )


if __name__ == "__main__":
    main()
