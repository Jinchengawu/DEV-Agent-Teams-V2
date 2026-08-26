"""Browser acceptance for the five-role, four-repository product delivery loop."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    APIRequestContext,
    Page,
    expect,
    sync_playwright,
)

REPOSITORY_ROLES = {"backend", "design", "frontend", "qa"}
STAGE_TIMEOUT_MS = int(os.environ.get("AGENT_TEAM_OS_BROWSER_STAGE_TIMEOUT_MS", "90000"))
REQUIRED_EVIDENCE = {
    "journey",
    "requirement",
    "task",
    "plan-gate",
    "design-gate",
    "candidate-gate",
    "release-bundle",
    "release-manifest",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1200})
        page = context.new_page()
        console_errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        try:
            _authenticate(page, args.url)
            # The anonymous bootstrap probe intentionally receives 401 before login.
            console_errors.clear()
            result = _execute_fullstack_delivery(page, args.url)
            _verify_knowledge_activity(page, args.url, result)
            assert not console_errors, console_errors
        except Exception:
            if console_errors:
                print("Browser console errors:", *console_errors, sep="\n")
            raise
        finally:
            if args.screenshot is not None:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=args.screenshot, full_page=True)
            browser.close()


def _authenticate(page: Page, url: str) -> None:
    page.goto(url)
    page.wait_for_load_state("networkidle")
    if page.get_by_role("heading", name="初始化管理员").count():
        password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD") or (
            f"Fullstack-{secrets.token_urlsafe(18)}-2026"
        )
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="创建并登录").click()
    elif page.get_by_role("heading", name="登录 Agent-Team-OS").count():
        existing_password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD")
        if not existing_password:
            raise AssertionError(
                "existing browser-test database requires AGENT_TEAM_OS_TEST_PASSWORD"
            )
        page.get_by_label("用户名").fill(
            os.environ.get("AGENT_TEAM_OS_TEST_USERNAME", "admin")
        )
        page.get_by_label("密码").fill(existing_password)
        page.get_by_role("button", name="登录控制平面").click()
    page.get_by_role("link", name="项目", exact=True).wait_for(timeout=30_000)
    assert not page.locator(".vite-error-overlay").count(), "Vite error overlay found"


def _execute_fullstack_delivery(page: Page, url: str) -> dict[str, str]:
    project_id = f"browser-five-role-{secrets.token_hex(3)}"
    pipelines = _json(page.context.request, f"{url}/v1/pipelines")
    fullstack_pipeline = next(
        item for item in pipelines if item["id"] == "fullstack-product-delivery"
    )
    assert fullstack_pipeline["active_revision"] is not None, fullstack_pipeline
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_role("heading", name="项目", exact=True).wait_for()
    page.get_by_placeholder("例如：pj1").fill(project_id)
    page.get_by_placeholder("例如：客户门户后端").fill("五角色四仓浏览器验收")
    page.get_by_placeholder("说明项目边界和验收目标").fill(
        "验证产品规划、UI 设计、前后端实现、测试审查与四仓发布。"
    )
    _select_option(
        page,
        "默认流水线",
        f"{fullstack_pipeline['name']} · R{fullstack_pipeline['active_revision']}",
    )

    selected_deployments = page.locator(".project-deployment-list input:checked")
    expect(selected_deployments).to_have_count(5, timeout=10_000)
    assert selected_deployments.count() == 5, selected_deployments.count()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/projects")
    ) as project_response:
        page.get_by_role("button", name="创建并初始化独立工作区").click()
    assert project_response.value.status == 201, project_response.value.text()
    project = project_response.value.json()
    assert project["project"]["lifecycle_status"] == "active", project
    repositories = project["repositories"]
    assert {item["role"] for item in repositories} == REPOSITORY_ROLES, repositories
    assert all(item["status"] == "ready" for item in repositories), repositories

    page.goto(f"{url}/projects/{project_id}/deliveries")
    page.wait_for_load_state("networkidle")
    _select_option(
        page,
        "执行流水线",
        f"{fullstack_pipeline['name']} · R{fullstack_pipeline['active_revision']}",
    )
    request_text = (
        "为产品状态页交付一套中文 UI 设计规范，后端提供 status_summary，"
        "前端渲染 ready 状态，并由测试角色验证四仓证据。"
    )
    page.get_by_label("交付需求").fill(request_text)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/deliveries")
    ) as delivery_response:
        page.get_by_role("button", name="按所选流水线启动闭环").click()
    assert delivery_response.value.status == 202, delivery_response.value.text()
    delivery = delivery_response.value.json()
    delivery_id = str(delivery["id"])
    assert delivery["project_id"] == project_id, delivery
    assert delivery["pipeline_revision_id"].startswith("fullstack-product-delivery:"), delivery
    assert {
        item["role"]
        for item in delivery["project_execution_snapshot"]["repositories"]
    } == REPOSITORY_ROLES

    page.get_by_role("button", name="批准计划并开始设计").wait_for(
        timeout=STAGE_TIMEOUT_MS
    )
    page.get_by_role("button", name="批准计划并开始设计").click()
    page.get_by_role("button", name="确认批准计划").click()

    page.get_by_role("button", name="批准设计并开始前后端实现").wait_for(
        timeout=STAGE_TIMEOUT_MS
    )
    page.get_by_text("UI 设计仓库", exact=True).first.wait_for()
    page.get_by_role("button", name="批准设计并开始前后端实现").click()
    page.get_by_role("button", name="确认批准设计").click()

    release_button = page.get_by_role("button", name="批准四仓发布并执行 CAS")
    release_button.wait_for(timeout=STAGE_TIMEOUT_MS)
    page.get_by_text("四仓 Release Bundle 已通过系统校验", exact=True).wait_for()
    for role_label in ("后端", "UI 设计", "前端", "测试审查"):
        page.get_by_role("tab", name=role_label).wait_for()
    release_button.click()
    page.get_by_role("button", name="确认发布四个仓库").click()

    page.get_by_text("Release Manifest 已激活", exact=True).wait_for(
        timeout=STAGE_TIMEOUT_MS
    )
    page.locator(".detail-hero").get_by_text("已完成", exact=True).wait_for()

    completed = _json(page.context.request, f"{url}/v1/deliveries/{delivery_id}")
    assert completed["status"] == "completed", completed
    candidates = completed["repository_candidates"]
    assert {item["role"] for item in candidates} == REPOSITORY_ROLES, candidates
    assert completed["release_bundle"]["bundle_sha256"], completed
    manifest = completed["release_manifest"]
    assert manifest["manifest_sha256"], completed
    receipts = {item["role"]: item["receipt"] for item in manifest["repositories"]}
    for candidate in candidates:
        receipt = receipts[candidate["role"]]
        assert (
            receipt["candidate_revision"]
            == receipt["after_revision"]
            == candidate["candidate"]["candidate_revision"]
        ), {"candidate": candidate, "receipt": receipt}

    evidence = _json(
        page.context.request, f"{url}/v1/deliveries/{delivery_id}/evidence"
    )
    kinds = {item["kind"] for item in evidence}
    assert kinds >= REQUIRED_EVIDENCE, kinds
    assert sum(item["kind"] == "candidate" for item in evidence) == 4, evidence
    assert sum(item["kind"] == "diff" for item in evidence) == 4, evidence
    assert sum(item["kind"] == "verification" for item in evidence) == 4, evidence
    assert all(item["status"] == "verified" for item in evidence), evidence

    agent_runs = _json(
        page.context.request, f"{url}/v1/deliveries/{delivery_id}/agent-runs"
    )
    binding_sites = {item["binding_site"] for item in agent_runs}
    assert {
        "requirements.actor",
        "design.developer",
        "implementation-repair/backend.developer",
        "implementation-repair/frontend.developer",
        "implementation-repair/qa.developer",
    } <= binding_sites, binding_sites
    candidate_runs = [
        item
        for item in agent_runs
        if item["binding_site"]
        in {
            "design.developer",
            "implementation-repair/backend.developer",
            "implementation-repair/frontend.developer",
            "implementation-repair/qa.developer",
        }
    ]
    assert all(
        item["artifact_envelopes"][0]["contract_id"] == "candidate-change-v1"
        for item in candidate_runs
    ), candidate_runs
    return {"project_id": project_id, "delivery_id": delivery_id}


def _verify_knowledge_activity(
    page: Page, url: str, result: dict[str, str]
) -> None:
    project_id = result["project_id"]
    delivery_id = result["delivery_id"]
    activity = _json(
        page.context.request,
        f"{url}/v1/knowledge/activity?project_id={project_id}&delivery_id={delivery_id}",
    )
    assert activity, activity
    assert all(item["source_kind"] == "evidence" for item in activity), activity
    assert {item["delivery_id"] for item in activity} == {delivery_id}, activity

    page.goto(f"{url}/projects/{project_id}/knowledge")
    page.wait_for_load_state("networkidle")
    page.get_by_text("项目知识动态", exact=True).wait_for(timeout=30_000)
    page.locator(".knowledge-activity-list").wait_for()
    assert page.locator(".knowledge-activity-item").count() == len(activity)
    page.get_by_text("发布清单", exact=False).first.wait_for()


def _json(request: APIRequestContext, url: str) -> Any:
    response = request.get(url)
    assert response.ok, response.text()
    return response.json()


def _select_option(page: Page, label: str, option_text: str) -> None:
    page.get_by_label(label).click()
    dropdown = page.locator(".ant-select-dropdown:visible")
    dropdown.wait_for()
    option = dropdown.locator(".ant-select-item-option:visible").filter(
        has_text=option_text
    )
    try:
        option.first.wait_for()
    except Exception as error:
        available = dropdown.locator(".ant-select-item-option:visible").all_inner_texts()
        raise AssertionError(
            f"{label} 缺少选项 {option_text!r}；当前选项：{available!r}"
        ) from error
    option.first.click()
    page.keyboard.press("Escape")


if __name__ == "__main__":
    main()
