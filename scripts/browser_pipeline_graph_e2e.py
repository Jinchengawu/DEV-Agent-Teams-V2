"""Browser acceptance for multi-pipeline DAG and bounded LOOP editing."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--phase", choices=("execute", "recover"), default="execute")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1200},
            storage_state=(
                str(args.state)
                if args.phase == "recover" and args.state and args.state.exists()
                else None
            ),
        )
        page = context.new_page()
        console_errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        if args.phase == "execute":
            _authenticate(page, args.url)
            checkpoint = _create_and_publish_graph(page, args.url)
            if args.state is not None:
                args.state.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(args.state))
            if args.checkpoint is not None:
                args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
                args.checkpoint.write_text(
                    json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        else:
            if args.checkpoint is None:
                raise ValueError("recover phase requires --checkpoint")
            _verify_recovered_graph(page, args.url, args.checkpoint)
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


def _create_and_publish_graph(page: Page, url: str) -> dict[str, Any]:
    page.get_by_role("link", name="可视化编排", exact=True).click()
    page.get_by_label("流水线 ID").wait_for(timeout=30_000)
    workcell_pipeline = page.locator(".pipeline-list button").filter(
        has_text="agent-workcell-delivery"
    )
    workcell_pipeline.click()
    workcell_nodes = page.locator(".flow .react-flow__node")
    workcell_nodes.first.wait_for(timeout=30_000)
    assert workcell_nodes.count() == 10, workcell_nodes.all_inner_texts()
    workcell_node_text = "\n".join(workcell_nodes.all_inner_texts())
    for node_id in (
        "requirements",
        "tasking",
        "design-repair",
        "frontend-repair",
        "backend-repair",
        "qa-delivery-repair",
        "approve-release",
    ):
        assert node_id in workcell_node_text, workcell_node_text
    workcell_edges = page.locator(".flow .react-flow__edge-path")
    workcell_edges.first.wait_for(timeout=30_000)
    assert workcell_edges.count() == 10, workcell_edges.count()
    page.get_by_label("流水线 ID").fill("browser-dag-loop")
    page.get_by_label("流水线名称").fill("浏览器 DAG LOOP 验收")
    with page.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith("/v1/pipelines")
        )
    ) as created_response:
        page.get_by_role("button", name="创建流水线").click()
    assert created_response.value.status == 201, created_response.value.text()
    page.locator(".pipeline-list button.selected").get_by_text(
        "browser-dag-loop", exact=True
    ).wait_for(timeout=30_000)
    page.get_by_role("button", name="角色 Stage").click()
    page.get_by_role("button", name="角色 Stage").click()
    page.get_by_role("button", name="审批 Gate").click()
    page.get_by_role("button", name="审批 Gate").click()
    page.get_by_role("button", name="有限 LOOP").click()
    page.locator(".flow .react-flow__node").filter(
        has_text="stage-2"
    ).wait_for(timeout=30_000)

    _select_option(page, "选择主图节点", "stage-2")
    page.get_by_label("Capability").fill("hermes-project-admin")
    _select_option(page, "Agent Deployment stage-2.actor", "builtin-planning-deployment")
    _select_option(page, "选择主图节点", "stage-1")
    _select_option(page, "Agent Deployment stage-1.actor", "builtin-planning-deployment")
    _add_dependency(page, "主图", "stage-1", "stage-2")
    _add_dependency(page, "主图", "stage-2", "gate-1")
    _add_dependency(page, "主图", "gate-1", "loop-1", "approved")
    _add_dependency(page, "主图", "loop-1", "gate-2")

    _select_option(page, "选择主图节点", "loop-1")
    page.get_by_role("button", name="打开 LOOP 全屏工作区").click()
    page.get_by_label("退出条件策略").fill("machine-tests-passed")
    page.get_by_label("最大轮次").fill("4")
    page.locator(".loop-body-editor").get_by_role("button", name="角色 Stage").click()
    _select_option(page, "选择循环体节点", "loop-1-work")
    _select_option(
        page,
        "Agent Deployment loop-1/loop-1-work.developer",
        "builtin-backend-deployment",
    )
    _select_option(page, "选择循环体节点", "stage-1")
    _select_option(
        page,
        "Agent Deployment loop-1/stage-1.actor",
        "builtin-planning-deployment",
    )
    _add_dependency(page, "循环体", "loop-1-work", "stage-1")
    page.get_by_role("button", name="关闭 LOOP 工作区并保留草稿修改").click()

    with page.expect_response(
        lambda response: (
            response.request.method == "PATCH" and "/v1/pipeline-drafts/" in response.url
        )
    ) as saved_response:
        page.get_by_role("button", name="保存图与布局").click()
    assert saved_response.value.status == 200, saved_response.value.text()
    definition = saved_response.value.json()["definition"]
    assert len(definition["nodes"]) == 5, definition
    assert any(edge.get("condition") == "approved" for edge in definition["edges"]), definition
    rendered_edges = page.locator(".flow .react-flow__edge-path")
    rendered_edges.first.wait_for(timeout=10_000)
    assert rendered_edges.count() == len(definition["edges"]), {
        "semantic_edges": definition["edges"],
        "rendered_edge_count": rendered_edges.count(),
    }
    loop = next(node for node in definition["nodes"] if node["kind"] == "loop")
    assert loop["policy"]["max_iterations"] == 4, loop
    assert len(loop["nodes"]) == 2 and len(loop["edges"]) == 1, loop

    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url.endswith("/validate")
    ) as validated_response:
        page.get_by_role("button", name="ACWM 图校验").click()
    validated = validated_response.value.json()
    assert validated["validation_status"] == "valid", validated
    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url.endswith("/publish")
    ) as published_response:
        page.get_by_role("button", name="发布不可变版本").click()
        page.get_by_role("button", name="确认发布不可变版本").click()
    published = published_response.value.json()
    assert published["fingerprint"], published
    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url.endswith("/activate")
    ) as activated_response:
        page.get_by_role("button", name=f"激活 R{published['revision']}").click()
        page.get_by_role("button", name=f"确认激活 R{published['revision']}").click()
    assert activated_response.value.json()["active_revision"] == published["revision"]

    project_id = "browser-dag-project"
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_placeholder("例如：pj1").fill(project_id)
    page.get_by_placeholder("例如：客户门户后端").fill("浏览器 DAG LOOP 项目")
    _select_option(
        page,
        "默认流水线",
        f"浏览器 DAG LOOP 验收 · R{published['revision']}",
    )
    deployment_checks = page.locator(".project-create-form").get_by_role("checkbox")
    deployment_checks.first.wait_for()
    for index in range(deployment_checks.count()):
        deployment_checks.nth(index).check()
    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url.endswith("/v1/projects")
    ) as project_response:
        page.get_by_role("button", name="创建并初始化独立工作区").click()
    project = project_response.value.json()
    assert project["project"]["lifecycle_status"] == "active", project
    assert project["workspace"]["workspace_id"] == f"project:{project_id}", project
    page.goto(f"{url}/projects/{project_id}/deliveries")
    page.wait_for_load_state("networkidle")
    _select_option(
        page,
        "执行 Pipeline",
        f"浏览器 DAG LOOP 验收 · R{published['revision']}",
    )
    page.get_by_label("交付目标").fill("增加可审计的健康检查。")
    page.get_by_role("button", name="生成交付计划").click()
    with page.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith("/v1/deliveries")
        )
    ) as delivery_response:
        page.get_by_role("button", name="确认并启动").click()
    assert delivery_response.value.status == 202, delivery_response.value.text()
    delivery = delivery_response.value.json()
    assert delivery["project_id"] == project_id, delivery
    assert delivery["project_execution_snapshot"]["project_id"] == project_id, delivery
    assert delivery["pipeline_revision_id"] == (
        f"{published['pipeline_id']}:{published['revision']}"
    )
    assert delivery["pipeline_run_id"]
    page.get_by_text("ACWM DAG 运行账本", exact=True).wait_for()
    page.get_by_text("不可变图指纹", exact=True).wait_for()
    page.get_by_role("button", name="批准计划并开始设计").wait_for(timeout=30_000)
    page.get_by_role("button", name="批准计划并开始设计").click()
    page.get_by_role("button", name="确认批准计划").click()
    page.get_by_role("button", name="接受候选并原子应用").wait_for(timeout=30_000)
    page.get_by_role("button", name="接受候选并原子应用").click()
    page.get_by_role("button", name="确认应用 Candidate").click()
    page.get_by_text("单仓应用回执已核验", exact=True).wait_for(timeout=30_000)
    page.locator(".run-hero").get_by_text("已完成", exact=True).wait_for(timeout=30_000)
    completed = page.context.request.get(f"{url}/v1/deliveries/{delivery['id']}")
    assert completed.ok, completed.text()
    completed_payload = completed.json()
    candidate = completed_payload.get("candidate")
    receipt = completed_payload.get("apply_receipt")
    assert candidate and receipt, completed_payload
    candidate_matches_main = (
        receipt["candidate_revision"]
        == receipt["after_revision"]
        == candidate["candidate_revision"]
    )
    assert candidate_matches_main, completed_payload
    graph = page.context.request.get(f"{url}/v1/deliveries/{delivery['id']}/pipeline-run")
    assert graph.ok, graph.text()
    graph_payload = graph.json()
    assert graph_payload["status"] == "completed", graph_payload
    page.get_by_role("link", name="证据", exact=True).click()
    page.get_by_text("证据目录", exact=True).wait_for(timeout=30_000)
    page.get_by_label("按交付筛选").fill(delivery["id"])
    _select_option(page, "按完整性筛选", "已验证")
    page.wait_for_timeout(300)
    verified_evidence_count = page.locator(".evidence-table button").count()
    assert verified_evidence_count >= 7, verified_evidence_count
    return {
        "delivery_id": delivery["id"],
        "pipeline_run_id": delivery["pipeline_run_id"],
        "pipeline_revision_id": delivery["pipeline_revision_id"],
        "pipeline_fingerprint": published["fingerprint"],
        "project_id": project_id,
        "browser_evidence": {
            "multi_pipeline_e2e": True,
            "verified_evidence_count": verified_evidence_count,
            "candidate_matches_main": candidate_matches_main,
        },
    }


def _verify_recovered_graph(page: Page, url: str, checkpoint_path: Path) -> None:
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    delivery = page.context.request.get(f"{url}/v1/deliveries/{checkpoint['delivery_id']}")
    assert delivery.ok, delivery.text()
    delivery_payload = delivery.json()
    assert delivery_payload["status"] == "completed", delivery_payload
    assert delivery_payload["pipeline_run_id"] == checkpoint["pipeline_run_id"]
    assert delivery_payload["pipeline_revision_id"] == checkpoint["pipeline_revision_id"]
    graph = page.context.request.get(f"{url}/v1/pipeline-runs/{checkpoint['pipeline_run_id']}")
    assert graph.ok, graph.text()
    graph_payload = graph.json()
    assert graph_payload["status"] == "completed", graph_payload
    assert graph_payload["graph_fingerprint"] == checkpoint["pipeline_fingerprint"]
    page.goto(
        f"{url}/projects/{checkpoint['project_id']}/deliveries/"
        f"{checkpoint['delivery_id']}"
    )
    page.wait_for_load_state("networkidle")
    page.get_by_text("单仓应用回执已核验", exact=True).wait_for(timeout=30_000)
    page.goto(f"{url}/projects/{checkpoint['project_id']}/evidence")
    page.wait_for_load_state("networkidle")
    page.get_by_label("按交付筛选").fill(checkpoint["delivery_id"])
    _select_option(page, "按完整性筛选", "已验证")
    page.wait_for_timeout(300)
    assert (
        page.locator(".evidence-table button").count()
        == checkpoint["browser_evidence"]["verified_evidence_count"]
    )


def _add_dependency(page: Page, label: str, source: str, target: str, condition: str = "") -> None:
    editor = page.locator(".dependency-creator").filter(has_text=f"{label}依赖编辑器")
    _select_option(page, f"{label}上游节点", source, scope=editor)
    _select_option(page, f"{label}下游节点", target, scope=editor)
    if condition:
        editor.get_by_label(f"{label}分支条件").fill(condition)
    editor.get_by_role("button", name="添加依赖边").click()


def _select_option(
    page: Page,
    label: str,
    option_text: str,
    *,
    scope: Any | None = None,
) -> None:
    """Operate the Ant Design Select through its public accessibility surface."""

    root = scope or page
    page.wait_for_timeout(250)
    control = root.get_by_label(label)
    control.click()
    controlled_list_id = control.get_attribute("aria-controls")
    if not controlled_list_id:
        raise AssertionError(f"Select {label!r} does not expose aria-controls")
    dropdown = page.locator(".ant-select-dropdown").filter(
        has=page.locator(f'[id="{controlled_list_id}"]')
    )
    dropdown.wait_for()
    page.wait_for_timeout(150)
    visual_option = dropdown.locator(".ant-select-item-option").filter(
        has_text=option_text
    )
    if visual_option.count() > 0:
        visual_option.first.wait_for()
        visual_option.first.click()
        return
    accessible_option = dropdown.get_by_role("option").filter(
        has_text=option_text
    )
    accessible_option.first.wait_for(state="attached", timeout=5_000)
    accessible_label = accessible_option.first.get_attribute("aria-label")
    if not accessible_label:
        raise AssertionError(f"Select {label!r} option {option_text!r} has no label")
    labelled_option = dropdown.locator(
        ".ant-select-item-option-content"
    ).filter(has_text=accessible_label)
    labelled_option.first.wait_for()
    labelled_option.first.click()


if __name__ == "__main__":
    main()
