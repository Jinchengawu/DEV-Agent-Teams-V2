"""Browser acceptance for Agent-Team-OS control-plane delivery (Playwright is test-only)."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--phase", choices=("execute", "recover", "full"), default="full")
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Only verify rendering, navigation and console integrity; do not mutate data.",
    )
    args = parser.parse_args()
    if args.phase == "recover" and (args.state is None or args.checkpoint is None):
        parser.error("recover phase requires --state and --checkpoint")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context_options: dict[str, Any] = {"viewport": {"width": 1440, "height": 1100}}
        if args.phase == "recover" and args.state is not None:
            context_options["storage_state"] = str(args.state)
        context = browser.new_context(**context_options)
        page = context.new_page()
        console_errors = _capture_console_errors(page)
        try:
            if args.phase == "recover":
                _verify_recovery(page, context, args.url, _read_checkpoint(args.checkpoint))
            else:
                _authenticate(page, args.url)
                _verify_navigation(page)
                if not args.smoke_only:
                    checkpoint = _execute_control_plane_loop(page, context, args.url)
                    if args.checkpoint is not None:
                        args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
                        args.checkpoint.write_text(
                            json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                    if args.state is not None:
                        args.state.parent.mkdir(parents=True, exist_ok=True)
                        context.storage_state(path=str(args.state))
        except Exception:
            if console_errors:
                print("Browser console errors:", *console_errors, sep="\n")
            raise
        _save_screenshot(page, args.screenshot)
        assert not console_errors, console_errors
        browser.close()


def _authenticate(page: Page, url: str) -> None:
    page.goto(url)
    page.wait_for_load_state("networkidle")
    bootstrap_heading = page.get_by_role("heading", name="初始化管理员")
    if bootstrap_heading.count():
        password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD") or (
            f"Browser-{secrets.token_urlsafe(18)}-2026"
        )
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="创建并登录").click()
        page.get_by_role("link", name="交付工作台", exact=True).wait_for()
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
        page.get_by_role("link", name="交付工作台", exact=True).wait_for()
    assert page.locator("body").inner_text().strip(), "rendered page is blank"
    assert not page.locator(".vite-error-overlay").count(), "Vite error overlay found"


def _verify_navigation(page: Page) -> None:
    for navigation in (
        "交付工作台",
        "交付看板",
        "可视化编排",
        "智能体实例",
        "知识中心",
        "证据",
        "设置",
    ):
        page.get_by_role("link", name=navigation, exact=True).click()
        page.get_by_role("heading", name=navigation, exact=True).wait_for()


def _execute_control_plane_loop(
    page: Page, context: BrowserContext, url: str
) -> dict[str, str]:
    page.get_by_role("link", name="智能体实例").click()
    page.get_by_role("tab", name="运行实例", exact=True).click()
    page.get_by_label("实例名称").fill("Browser 确定性执行器")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/agent-instances")
    ) as created_response:
        page.get_by_role("button", name="注册实例").click()
    created = created_response.value.json()
    instance_id = str(created["id"])
    instance_card = page.locator(".instance-grid article").filter(
        has_text="Browser 确定性执行器"
    )
    instance_card.wait_for()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith(f"/v1/agent-instances/{instance_id}/health-check")
    ) as health_response:
        instance_card.get_by_role("button", name="健康检查").click()
    health = health_response.value.json()
    assert health["health"]["status"] == "ready", health
    instance_card.get_by_text("就绪", exact=True).wait_for()

    binding_selector = page.get_by_label("为 后端代码交付 选择实例")
    _select_option(page, "为 后端代码交付 选择实例", instance_id)
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url.endswith("/v1/capability-bindings/codex-backend")
    ) as binding_response:
        binding_selector.locator("xpath=ancestor::article").get_by_role(
            "button", name="保存能力绑定"
        ).click()
    binding = binding_response.value.json()
    assert binding["instance_id"] == instance_id, binding

    page.get_by_role("link", name="可视化编排").click()
    page.get_by_role("button", name="克隆为可编辑草稿", exact=True).click()
    page.get_by_role("button", name="添加角色阶段").wait_for()
    page.get_by_role("button", name="添加角色阶段").click()
    _select_option(page, "Capability", "hermes-project-admin")
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and "/v1/journey-drafts/" in response.url
    ) as save_response:
        page.get_by_role("button", name="保存语义与顺序").click()
    saved = save_response.value.json()
    saved_steps = saved["definition"]["steps"]
    assert any(step["id"] == "role-1" for step in saved_steps), saved_steps

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/validate")
    ) as validate_response:
        page.get_by_role("button", name="ACWM 校验").click()
    validated = validate_response.value.json()
    assert validated["validation_status"] == "valid", validated
    publish_button = page.get_by_role("button", name="发布不可变版本")
    publish_button.wait_for()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/publish")
    ) as publish_response:
        publish_button.click()
    revision = publish_response.value.json()
    assert revision["revision"] >= 2, revision
    assert revision["binding_snapshot"]["codex-backend"]["instance_id"] == instance_id
    journey_revision_id = f"{revision['journey_id']}:{revision['revision']}"
    page.get_by_text(f"发布版本 {revision['revision']}", exact=True).wait_for()

    page.get_by_role("link", name="交付工作台", exact=True).click()
    page.get_by_label("交付需求").fill(
        "增加 health_status 函数，返回 status=ok 和 version=0.1，并补充 unittest。"
    )
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/deliveries")
    ) as delivery_response:
        page.get_by_role("button", name="启动真实闭环").click()
    created_delivery = delivery_response.value.json()
    delivery_id = str(created_delivery["id"])
    assert created_delivery["journey_revision_id"] == journey_revision_id
    assert (
        created_delivery["journey_binding_snapshot"]["codex-backend"]["instance_id"]
        == instance_id
    )
    page.locator(".map-label b").filter(has_text="等待计划审批").wait_for(
        timeout=60_000
    )

    page.get_by_role("link", name="交付看板", exact=True).click()
    _drag_delivery(page, delivery_id, target_column=2)
    _confirm_board_command(page, delivery_id, "批准计划")
    _wait_for_board_column(page, delivery_id, column=3, timeout=120_000)

    page.get_by_role("link", name="交付工作台", exact=True).click()
    page.locator(".unified-diff").wait_for(timeout=30_000)
    assert page.locator(".unified-diff").inner_text().strip(), "candidate diff is empty"
    page.get_by_text("固定机器测试：通过", exact=True).wait_for()
    detail = _delivery_json(context, url, delivery_id)
    assert detail["candidate"]["candidate_revision"]
    assert detail["candidate"]["diff_sha256"]
    assert detail["verification"]["exit_code"] == 0

    page.get_by_role("link", name="交付看板", exact=True).click()
    _wait_for_board_column(page, delivery_id, column=3, timeout=30_000)
    _drag_delivery(page, delivery_id, target_column=4)
    _confirm_board_command(page, delivery_id, "接受候选")
    _wait_for_board_column(page, delivery_id, column=4, timeout=60_000)

    page.get_by_role("link", name="交付工作台", exact=True).click()
    page.get_by_text("应用回执已核验", exact=True).wait_for(timeout=30_000)
    completed = _delivery_json(context, url, delivery_id)
    receipt = completed["apply_receipt"]
    candidate = completed["candidate"]
    assert completed["status"] == "completed", completed
    assert receipt["after_revision"] == candidate["candidate_revision"]

    page.get_by_role("link", name="证据").click()
    page.get_by_text("应用回执", exact=True).first.wait_for()
    page.get_by_role("link", name="知识中心").click()
    page.get_by_label("全文搜索").fill("health")
    page.get_by_text("需求", exact=False).first.wait_for(timeout=15_000)
    return {
        "delivery_id": delivery_id,
        "journey_revision_id": journey_revision_id,
        "instance_id": instance_id,
        "candidate_revision": str(candidate["candidate_revision"]),
        "diff_sha256": str(candidate["diff_sha256"]),
        "apply_after_revision": str(receipt["after_revision"]),
    }


def _verify_recovery(
    page: Page, context: BrowserContext, url: str, checkpoint: dict[str, str]
) -> None:
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name="交付工作台", exact=True).wait_for(timeout=15_000)
    delivery_id = checkpoint["delivery_id"]
    recovered = _delivery_json(context, url, delivery_id)
    assert recovered["status"] == "completed", recovered
    assert recovered["journey_revision_id"] == checkpoint["journey_revision_id"]
    assert recovered["candidate"]["candidate_revision"] == checkpoint["candidate_revision"]
    assert recovered["candidate"]["diff_sha256"] == checkpoint["diff_sha256"]
    assert recovered["apply_receipt"]["after_revision"] == checkpoint[
        "apply_after_revision"
    ]

    evidence_response = context.request.get(f"{url}/v1/deliveries/{delivery_id}/evidence")
    assert evidence_response.ok, evidence_response.text()
    evidence = evidence_response.json()
    assert any(
        item["kind"] == "apply-receipt" and item["status"] == "verified"
        for item in evidence
    ), evidence
    events_response = context.request.get(f"{url}/v1/deliveries/{delivery_id}/events")
    assert events_response.ok, events_response.text()
    assert any(
        item["event_type"] == "delivery.completed" for item in events_response.json()
    )

    page.get_by_role("link", name="交付工作台", exact=True).click()
    page.locator(".delivery-list button").filter(has_text=delivery_id[:8]).click()
    page.locator(".status-completed").first.wait_for(timeout=15_000)
    page.get_by_text("应用回执已核验", exact=True).wait_for()
    page.get_by_role("link", name="证据").click()
    page.get_by_text("应用回执", exact=True).first.wait_for()


def _drag_delivery(page: Page, delivery_id: str, target_column: int) -> None:
    handle = page.get_by_role("button", name=f"拖动交付 {delivery_id}")
    handle.wait_for()
    target = page.locator(".board-column").nth(target_column)
    source_box = handle.bounding_box()
    target_box = target.bounding_box()
    assert source_box is not None and target_box is not None
    page.mouse.move(
        source_box["x"] + source_box["width"] / 2,
        source_box["y"] + source_box["height"] / 2,
    )
    page.mouse.down()
    page.mouse.move(source_box["x"] + 12, source_box["y"] + 12, steps=3)
    page.mouse.move(
        target_box["x"] + target_box["width"] / 2,
        target_box["y"] + min(target_box["height"] / 2, 180),
        steps=15,
    )
    page.mouse.up()
    page.get_by_role("dialog").wait_for()


def _confirm_board_command(page: Page, delivery_id: str, command_text: str) -> None:
    page.get_by_role("heading", name=command_text, exact=False).wait_for()
    button = page.get_by_role("button", name="确认发出命令")
    assert button.is_enabled(), button.evaluate("element => element.outerHTML")
    # dnd-kit suppresses the click immediately following a completed pointer drag.
    page.wait_for_timeout(750)
    with page.expect_request(
        lambda request: request.method == "POST"
        and request.url.endswith(f"/v1/work-items/{delivery_id}/command")
    ) as command_request:
        button.click()
    response = command_request.value.response()
    assert response is not None and response.status == 202, (
        None if response is None else response.text()
    )


def _wait_for_board_column(
    page: Page, delivery_id: str, *, column: int, timeout: int
) -> None:
    page.locator(".board-column").nth(column).locator(".work-card").filter(
        has_text=delivery_id[:8]
    ).wait_for(timeout=timeout)


def _delivery_json(context: BrowserContext, url: str, delivery_id: str) -> dict[str, Any]:
    response = context.request.get(f"{url}/v1/deliveries/{delivery_id}")
    assert response.ok, response.text()
    result: dict[str, Any] = response.json()
    return result


def _select_option(page: Page, label: str, option_text: str) -> None:
    """Operate the Ant Design Select through its public accessibility surface."""

    page.get_by_label(label).click()
    dropdown = page.locator(".ant-select-dropdown:visible")
    option = dropdown.get_by_role("option").filter(has_text=option_text)
    option.first.wait_for()
    option.first.click()


def _capture_console_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    return errors


def _read_checkpoint(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise AssertionError("browser checkpoint is invalid")
    return value


def _save_screenshot(page: Page, path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=True)


if __name__ == "__main__":
    main()
