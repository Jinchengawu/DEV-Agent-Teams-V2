"""v0.5.2 interaction-closure browser harness without Gate approval or Apply.

This deterministic UI harness proves onboarding/setup/workbench/agent-editor
interaction only. It deliberately stops before creating a Delivery whenever
readiness is blocked and never approves a Gate or invokes Apply.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD")
    if not password:
        raise AssertionError("AGENT_TEAM_OS_TEST_PASSWORD must be session-injected")
    args.evidence_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        desktop = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = desktop.new_page()
        errors = _capture_errors(page)
        _authenticate(page, args.url, password)
        project_id = _create_guided_project(page)
        _verify_setup_and_blocked_workbench(
            page, args.url, project_id, args.evidence_dir
        )
        _verify_agent_drawers(page, args.evidence_dir)
        _verify_roles(browser, desktop, args.url, project_id)
        page.screenshot(path=str(args.evidence_dir / "desktop.png"), full_page=True)
        mobile = browser.new_context(
            viewport={"width": 390, "height": 844}, storage_state=desktop.storage_state()
        )
        mobile_page = mobile.new_page()
        mobile_errors = _capture_errors(mobile_page)
        mobile_page.goto(f"{args.url}/setup?project_id={project_id}")
        mobile_page.get_by_text(f"准备中心 · {project_id}", exact=True).wait_for()
        mobile_page.locator(".readiness-group").first.wait_for()
        mobile_page.get_by_role("button", name="打开导航与账户").click()
        mobile_page.get_by_role("dialog", name="导航与账户").wait_for()
        mobile_navigation = mobile_page.get_by_role(
            "navigation", name="移动端系统目录"
        )
        mobile_navigation.wait_for()
        expect(mobile_navigation.get_by_role("link", name="准备中心")).to_be_visible()
        _verify_tab_boundary(mobile_page, "导航与账户")
        mobile_page.wait_for_timeout(500)
        mobile_page.screenshot(path=str(args.evidence_dir / "mobile.png"), full_page=True)

        assert not errors, errors
        assert not mobile_errors, mobile_errors
        report = {
            "status": "passed",
            "scope": "deterministic-ui-no-gate-no-apply",
            "project_id": project_id,
            "git_head": _git_head(),
            "working_tree_dirty": bool(_git_status()),
            "roles": ["administrator", "editor", "viewer"],
            "viewer_api_write_status": 403,
            "keyboard": ["Tab", "Shift+Tab", "Escape", "focus-return"],
            "credential_storage": "memory-only",
            "screenshots": [
                "setup-desktop.png",
                "agents-drawer.png",
                "desktop.png",
                "mobile.png",
            ],
        }
        (args.evidence_dir / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
        browser.close()


def _authenticate(page: Page, url: str, password: str) -> None:
    page.goto(url)
    page.wait_for_load_state("networkidle")
    if page.get_by_role("heading", name="初始化管理员").count():
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="创建并登录").click()
    else:
        page.get_by_label("用户名").fill(os.environ.get("AGENT_TEAM_OS_TEST_USERNAME", "admin"))
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="登录控制平面").click()
    page.get_by_role("link", name="项目", exact=True).wait_for(timeout=30_000)


def _create_guided_project(page: Page) -> str:
    project_id = f"guided-browser-{int(time.time())}"
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_placeholder("例如：pj1").fill(project_id)
    page.get_by_placeholder("例如：客户门户后端").fill("v0.5.2 交互闭环评测")
    page.get_by_placeholder("说明项目边界和验收目标").fill(
        "只验证准备度、草稿恢复与失败关闭，不批准 Gate，不执行 Apply。"
    )
    pipeline = page.get_by_label("默认流水线")
    pipeline.click()
    pipeline.press("ArrowDown")
    pipeline.press("Enter")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/projects")
    ) as created:
        page.locator(".project-create-form button[type=submit]").click()
    assert created.value.status == 201, created.value.text()
    payload = created.value.json()
    assert payload["onboarding"]["mode"] == "guided_evaluation", payload
    assert payload["onboarding"]["status"] == "setup", payload
    return project_id


def _verify_setup_and_blocked_workbench(
    page: Page, url: str, project_id: str, evidence_dir: Path
) -> None:
    page.goto(f"{url}/setup?project_id={project_id}")
    setup_identity = page.get_by_text(f"准备中心 · {project_id}", exact=True)
    setup_identity.wait_for()
    expect(setup_identity).to_be_visible()
    page.locator(".readiness-group").first.wait_for()
    page.screenshot(path=str(evidence_dir / "setup-desktop.png"), full_page=True)
    page.goto(f"{url}/projects/{project_id}/deliveries")
    page.get_by_role("heading", name="发起一次交付").wait_for()
    page.get_by_label("交付目标").fill("验证 blocked 时草稿保留且服务端不会收到 Delivery POST。")
    posts: list[str] = []
    page.on(
        "request",
        lambda request: (
            posts.append(request.url)
            if request.method == "POST" and request.url.endswith("/v1/deliveries")
            else None
        ),
    )
    page.get_by_role("button", name="生成交付计划").click()
    confirm = page.get_by_role("button", name="启动评测交付")
    expect(confirm).to_be_disabled()
    confirm.click(force=True)
    page.wait_for_timeout(200)
    assert posts == [], posts
    page.reload()
    expect(page.get_by_label("交付目标")).to_have_value(
        "验证 blocked 时草稿保留且服务端不会收到 Delivery POST。"
    )


def _verify_agent_drawers(page: Page, evidence_dir: Path) -> None:
    page.get_by_role("link", name="智能体实例", exact=True).click()
    page.get_by_role("button", name="创建 Deployment").click()
    page.get_by_role("dialog", name="创建 Deployment").wait_for()
    role_select = page.get_by_role("combobox", name="已发布 Agent 角色", exact=True)
    role_select.click()
    role_select.press("Escape")
    expect(page.get_by_role("dialog", name="创建 Deployment")).to_be_visible()
    _verify_tab_boundary(page, "创建 Deployment")
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="创建 Deployment")).to_be_focused()
    page.get_by_role("tab", name="Agent 角色").click()
    page.get_by_role("button", name="创建角色").click()
    page.get_by_role("dialog", name="创建智能体角色").wait_for()
    page.wait_for_timeout(500)
    page.screenshot(path=str(evidence_dir / "agents-drawer.png"), full_page=True)
    page.keyboard.press("Escape")


def _verify_tab_boundary(page: Page, name: str) -> None:
    dialog = page.get_by_role("dialog", name=name, exact=True)
    # 等待 Drawer 打开动画与 React focus-lock effect 完成后再测试按键循环。
    page.wait_for_timeout(500)
    for key in ("Tab", "Shift+Tab"):
        for _ in range(24):
            page.keyboard.press(key)
            # Ant Drawer 的焦点锁容器包含 dialog，并可自身接收初始焦点。
            assert dialog.evaluate(
                "element => (element.closest('.ant-drawer') ?? element)"
                ".contains(document.activeElement)"
            ), (
                "焦点逃出对话框", name, key,
                page.evaluate("({tag: document.activeElement?.tagName, "
                              "class: document.activeElement?.className})"),
            )


def _verify_roles(browser, administrator, url: str, project_id: str) -> None:
    cookies = {item["name"]: item["value"] for item in administrator.cookies(url)}
    headers = {"Origin": url, "X-CSRF-Token": cookies["agent_team_os_csrf"]}
    for role in ("editor", "viewer"):
        password = secrets.token_urlsafe(24)
        username = f"nr08-{role}-{int(time.time())}"
        created = administrator.request.post(
            f"{url}/v1/users", headers=headers,
            data={"username": username, "display_name": role, "role": role, "password": password},
        )
        assert created.status == 201
        member = administrator.request.put(
            f"{url}/v1/projects/{project_id}/memberships/{created.json()['id']}",
            headers=headers, data={"role": role},
        )
        assert member.ok
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        login = context.request.post(
            f"{url}/v1/auth/login", headers={"Origin": url},
            data={"username": username, "password": password},
        )
        assert login.status == 200
        page = context.new_page()
        page.goto(f"{url}/projects/{project_id}/deliveries")
        if role == "viewer":
            expect(page.get_by_role("region", name="只读交付权限")).to_be_visible()
            expect(page.get_by_role("button", name="生成交付计划")).to_have_count(0)
            viewer_cookies = {item["name"]: item["value"] for item in context.cookies(url)}
            response = context.request.post(
                f"{url}/v1/deliveries",
                headers={"Origin": url, "X-CSRF-Token": viewer_cookies["agent_team_os_csrf"]},
                data={
                    "project_id": project_id, "user_request": "拒绝越权",
                    "purpose": "onboarding_evaluation",
                },
            )
            assert response.status == 403
        else:
            expect(page.get_by_role("button", name="生成交付计划")).to_be_visible()
        context.close()


def _capture_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )
    return errors


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True
    ).stdout.strip()


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], check=True, text=True, capture_output=True
    ).stdout.strip()


if __name__ == "__main__":
    main()
