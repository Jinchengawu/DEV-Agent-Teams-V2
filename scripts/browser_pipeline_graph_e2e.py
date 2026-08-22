"""Browser acceptance for multi-pipeline DAG and bounded LOOP editing."""

from __future__ import annotations

import argparse
import os
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
        page = browser.new_page(viewport={"width": 1920, "height": 1200})
        console_errors: list[str] = []
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        _authenticate(page, args.url)
        _create_and_publish_graph(page)
        if args.screenshot is not None:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=args.screenshot, full_page=True)
        assert not console_errors, console_errors
        browser.close()


def _authenticate(page: Page, url: str) -> None:
    page.goto(url)
    page.wait_for_load_state("networkidle")
    if page.get_by_role("heading", name="初始化管理员").count():
        password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD") or (
            f"Pipeline-{secrets.token_urlsafe(18)}-2026"
        )
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="创建并登录").click()
    page.get_by_role("link", name="可视化编排", exact=True).wait_for()


def _create_and_publish_graph(page: Page) -> None:
    page.get_by_role("link", name="可视化编排", exact=True).click()
    page.get_by_label("流水线 ID").fill("browser-dag-loop")
    page.get_by_label("流水线名称").fill("浏览器 DAG LOOP 验收")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/pipelines")
    ) as created_response:
        page.get_by_role("button", name="创建流水线").click()
    assert created_response.value.status == 201, created_response.value.text()
    page.get_by_role("button", name="角色 Stage").click()
    page.get_by_role("button", name="角色 Stage").click()
    page.get_by_role("button", name="审批 Gate").click()
    page.get_by_role("button", name="审批 Gate").click()
    page.get_by_role("button", name="有限 LOOP").click()

    page.locator(".flow > .react-flow .react-flow__node").filter(
        has_text="stage-2"
    ).click()
    page.get_by_label("Capability").fill("hermes-project-admin")
    _add_dependency(page, "主图", "stage-1", "stage-2")
    _add_dependency(page, "主图", "stage-2", "gate-1")
    _add_dependency(page, "主图", "gate-1", "loop-1", "approved")
    _add_dependency(page, "主图", "loop-1", "gate-2")

    page.locator(".flow > .react-flow .react-flow__node").filter(
        has_text="loop-1"
    ).click()
    page.get_by_label("退出条件策略").fill("machine-tests-passed")
    page.get_by_label("最大轮次").fill("4")
    page.locator(".loop-body-editor").get_by_role(
        "button", name="角色 Stage"
    ).click()
    _add_dependency(page, "循环体", "loop-1-work", "stage-1")

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and "/v1/pipeline-drafts/" in response.url
    ) as saved_response:
        page.get_by_role("button", name="保存图与布局").click()
    assert saved_response.value.status == 200, saved_response.value.text()
    definition = saved_response.value.json()["definition"]
    assert len(definition["nodes"]) == 5, definition
    assert any(edge.get("condition") == "approved" for edge in definition["edges"]), definition
    loop = next(node for node in definition["nodes"] if node["kind"] == "loop")
    assert loop["policy"]["max_iterations"] == 4, loop
    assert len(loop["nodes"]) == 2 and len(loop["edges"]) == 1, loop

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/validate")
    ) as validated_response:
        page.get_by_role("button", name="ACWM 图校验").click()
    validated = validated_response.value.json()
    assert validated["validation_status"] == "valid", validated
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/publish")
    ) as published_response:
        page.get_by_role("button", name="发布不可变版本").click()
    published = published_response.value.json()
    assert published["fingerprint"], published
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/activate")
    ) as activated_response:
        page.get_by_role("button", name=f"激活 R{published['revision']}").click()
    assert activated_response.value.json()["active_revision"] == published["revision"]

    page.get_by_role("link", name="交付", exact=True).click()
    page.get_by_label("执行流水线").select_option(
        f"{published['pipeline_id']}:{published['revision']}"
    )
    page.get_by_label("交付需求").fill("增加可审计的健康检查。")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/deliveries")
    ) as delivery_response:
        page.get_by_role("button", name="按所选流水线启动闭环").click()
    delivery = delivery_response.value.json()
    assert delivery["pipeline_revision_id"] == (
        f"{published['pipeline_id']}:{published['revision']}"
    )
    assert delivery["pipeline_run_id"]
    page.get_by_text("ACWM DAG 运行账本", exact=True).wait_for()
    page.get_by_text("不可变图指纹", exact=True).wait_for()
    page.get_by_role("button", name="批准计划并开始执行").wait_for(timeout=30_000)
    page.get_by_role("button", name="批准计划并开始执行").click()
    page.get_by_role("button", name="接受候选并原子应用").wait_for(timeout=30_000)
    page.get_by_role("button", name="接受候选并原子应用").click()
    page.get_by_text("应用回执已核验", exact=True).wait_for(timeout=30_000)
    page.locator(".detail-hero").get_by_text("已完成", exact=True).wait_for(
        timeout=30_000
    )


def _add_dependency(
    page: Page, label: str, source: str, target: str, condition: str = ""
) -> None:
    editor = page.locator(".dependency-creator").filter(has_text=f"{label}依赖编辑器")
    editor.get_by_label(f"{label}上游节点").select_option(source)
    editor.get_by_label(f"{label}下游节点").select_option(target)
    if condition:
        editor.get_by_label(f"{label}分支条件").fill(condition)
    editor.get_by_role("button", name="添加依赖边").click()


if __name__ == "__main__":
    main()
