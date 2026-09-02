"""Deterministic browser acceptance for Feishu Gate A/B knowledge ingestion and RAG."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from browser_e2e import _authenticate, _capture_console_errors
from browser_workcell_e2e import (
    _assert_product_evidence,
    _execute_workcell_journey,
    _select_option,
)
from playwright.sync_api import BrowserContext, Page, sync_playwright

from agent_team_os.journey import load_agent_workcell_knowledge_delivery_definition
from agent_team_os.knowledge_live_readiness import KnowledgeLiveFactsCollector
from agent_team_os.modules.workcells import builtin_knowledge_context_bindings
from agent_team_os.shared.hashes import sha256_json


def _csrf_headers(context: BrowserContext, base_url: str) -> dict[str, str]:
    cookies = {item["name"]: item["value"] for item in context.cookies(base_url)}
    return {
        "Origin": base_url,
        "X-CSRF-Token": cookies["agent_team_os_csrf"],
    }


def _request_json(
    context: BrowserContext,
    base_url: str,
    method: str,
    path: str,
    payload: object | None = None,
) -> Any:
    response = context.request.fetch(
        f"{base_url}{path}",
        method=method,
        headers=_csrf_headers(context, base_url) if method != "GET" else None,
        data=payload,
    )
    assert response.ok, f"{method} {path}: {response.status} {response.text()}"
    return response.json()


def _create_tenant_binding(page: Page, base_url: str) -> dict[str, Any]:
    page.goto(f"{base_url}/settings", wait_until="networkidle")
    page.get_by_text("Gate A · Tenant 同步", exact=True).wait_for()
    page.get_by_text("飞书知识接入", exact=True).wait_for()
    page.get_by_label("连接名称").fill("Gate 研发飞书")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/knowledge/connections")
    ) as created_connection:
        page.get_by_role("button", name="创建连接", exact=True).click()
    assert created_connection.value.status == 201, created_connection.value.text()

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/diagnose")
        and "/v1/knowledge/connections/" in response.url
    ) as diagnosed_connection:
        page.get_by_role("button", name="诊断连接", exact=True).click()
    assert diagnosed_connection.value.status == 200, diagnosed_connection.value.text()
    page.get_by_role("button", name="Gate 研发知识库", exact=False).wait_for()
    page.get_by_role("button", name="Gate 研发知识库", exact=False).click()
    page.get_by_label("Binding 名称").fill("Gate 研发 Wiki")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/knowledge/provider-bindings-v2")
    ) as created_binding:
        page.get_by_role("button", name="冻结 Binding", exact=True).click()
    assert created_binding.value.status == 201, created_binding.value.text()
    binding: dict[str, Any] = created_binding.value.json()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith(f"/provider-bindings-v2/{binding['id']}/diagnose")
    ) as diagnosed_binding:
        page.get_by_role("button", name="刷新权限", exact=True).click()
    assert diagnosed_binding.value.status == 200, diagnosed_binding.value.text()
    return binding


def _approve_and_sync(
    page: Page,
    base_url: str,
    binding: dict[str, Any],
) -> None:
    page.goto(f"{base_url}/projects/legacy-default/overview", wait_until="networkidle")
    page.get_by_text("成员与知识来源授权", exact=True).wait_for()
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url.endswith(
            f"/knowledge-source-approvals/{binding['id']}"
        )
    ) as approval:
        page.get_by_role("button", name="批准来源", exact=True).click()
    assert approval.value.status == 200, approval.value.text()

    page.goto(f"{base_url}/projects/legacy-default/knowledge", wait_until="networkidle")
    page.get_by_text("外部知识快照与 RAG", exact=True).wait_for()
    page.get_by_label("飞书文档").wait_for()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/knowledge-sync-jobs")
    ) as sync:
        page.get_by_role("button", name="同步当前来源", exact=True).click()
    assert sync.value.status == 202, sync.value.text()
    page.locator(".external-snapshot-card").wait_for()


def _qualify_and_activate_index(
    context: BrowserContext,
    base_url: str,
    binding: dict[str, Any],
) -> str:
    profile = _request_json(
        context,
        base_url,
        "POST",
        "/v1/knowledge/index-profiles",
        {
            "id": "gate-cjk-hybrid-v1",
            "display_name": "Gate CJK Hybrid v1",
            "embedding_model_name": "gate-bge-m3",
        },
    )
    qualification = _request_json(
        context,
        base_url,
        "POST",
        "/v1/knowledge/embedding-qualifications",
        {"model_name": "gate-bge-m3"},
    )
    policy = _request_json(
        context,
        base_url,
        "POST",
        "/v1/knowledge/retrieval-policies",
        {
            "id": "gate-retrieval-v1",
            "display_name": "Gate Retrieval v1",
            "index_profile_revision_id": profile["id"],
            "top_k": 4,
        },
    )
    cases = [
        {
            "id": "workspace-isolation",
            "query": "四个工作仓为什么不能共享 workspace？",
            "expected_source_ids": ["docx:gate-architecture"],
        }
    ]
    evaluation_policy = _request_json(
        context,
        base_url,
        "POST",
        "/v1/knowledge/retrieval-evaluation-policies",
        {
            "id": "gate-retrieval-eval-v1",
            "retrieval_policy_revision_id": policy["id"],
            "index_profile_revision_id": profile["id"],
            "dataset_manifest_sha256": sha256_json(cases),
            "recall_at_k_min": 0.5,
            "zero_hit_rate_max": 0.5,
            "error_rate_max": 0.0,
            "p95_latency_ms_max": 10_000,
            "peak_rss_bytes_max": 10_000_000_000,
            "target_hardware": "deterministic-browser-gate",
        },
    )
    index_revision = _request_json(
        context,
        base_url,
        "POST",
        "/v1/knowledge/index-revisions",
        {
            "provider_binding_id": binding["id"],
            "index_profile_revision_id": profile["id"],
            "embedding_qualification_id": qualification["id"],
        },
    )
    evaluation = _request_json(
        context,
        base_url,
        "POST",
        "/v1/knowledge/retrieval-evaluation-runs",
        {
            "evaluation_policy_revision_id": evaluation_policy["id"],
            "index_revision_id": index_revision["id"],
            "cases": cases,
            "target_hardware": "deterministic-browser-gate",
        },
    )
    assert evaluation["status"] == "passed", evaluation
    active = _request_json(
        context,
        base_url,
        "POST",
        f"/v1/knowledge/index-revisions/{index_revision['id']}/activate",
        {"expected_pointer_version": None},
    )
    assert active["status"] == "active", active
    return str(policy["id"])


def _verify_rag_ui(page: Page, base_url: str, screenshot: Path | None) -> None:
    page.goto(f"{base_url}/projects/legacy-default/knowledge", wait_until="networkidle")
    page.get_by_label("Retrieval Policy").wait_for()
    page.get_by_label("检索查询").fill("四仓为什么必须隔离 workspace？")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/knowledge-retrieval-preview")
    ) as preview:
        page.get_by_role("button", name="运行 RAG 预览", exact=True).click()
    assert preview.value.status == 200, preview.value.text()
    payload = preview.value.json()
    assert payload["hits"], payload
    assert payload["hits"][0]["source_id"] == "docx:gate-architecture", payload
    results = page.get_by_label("RAG 检索结果")
    results.wait_for()
    results.get_by_text("四仓隔离架构规范", exact=True).first.wait_for()
    if screenshot is not None:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)


def _publish_gate_c_pipeline(
    context: BrowserContext,
    base_url: str,
    retrieval_policy_revision_id: str,
) -> dict[str, Any]:
    pipeline = next(
        item
        for item in _request_json(context, base_url, "GET", "/v1/pipelines")
        if item["id"] == "agent-workcell-delivery"
    )
    drafts = _request_json(
        context,
        base_url,
        "GET",
        "/v1/pipelines/agent-workcell-delivery/drafts",
    )
    assert len(drafts) == 1, drafts
    draft = drafts[0]
    root = Path(__file__).parents[1]
    bindings = builtin_knowledge_context_bindings(retrieval_policy_revision_id)
    patched = _request_json(
        context,
        base_url,
        "PATCH",
        f"/v1/pipeline-drafts/{draft['id']}",
        {
            "expected_version": draft["version"],
            "definition": load_agent_workcell_knowledge_delivery_definition(
                root / "config"
            ),
            "knowledge_context_bindings": {
                stage_path: binding.model_dump(mode="json")
                for stage_path, binding in bindings.items()
            },
        },
    )
    validated = _request_json(
        context,
        base_url,
        "POST",
        f"/v1/pipeline-drafts/{draft['id']}/validate",
        {"expected_version": patched["version"]},
    )
    assert validated["validation_status"] == "valid", validated
    revision = _request_json(
        context,
        base_url,
        "POST",
        f"/v1/pipeline-drafts/{draft['id']}/publish",
        {"expected_version": validated["version"]},
    )
    compiled_contracts = revision["compiled_graph"]["stage_input_artifact_contracts"]
    assert set(compiled_contracts) == set(bindings), compiled_contracts
    activated = _request_json(
        context,
        base_url,
        "POST",
        "/v1/pipelines/agent-workcell-delivery/activate",
        {
            "revision": revision["revision"],
            "expected_version": pipeline["version"],
        },
    )
    assert activated["active_revision"] == revision["revision"], activated
    return revision


def _verify_gate_c_evidence(
    context: BrowserContext,
    base_url: str,
    delivery_id: str,
) -> None:
    delivery = _request_json(
        context,
        base_url,
        "GET",
        f"/v1/deliveries/{delivery_id}",
    )
    overview = _request_json(
        context,
        base_url,
        "GET",
        f"/v1/deliveries/{delivery_id}/knowledge-context",
    )
    assert delivery["status"] == "completed", delivery
    assert overview["preparation_run"]["status"] == "succeeded", overview
    assert overview["unavailable"] == [], overview
    contexts = {item["stage_path"]: item for item in overview["contexts"]}
    expected_paths = set(
        builtin_knowledge_context_bindings("gate-retrieval-v1")
    )
    assert set(contexts) == expected_paths, contexts
    assert all(item["citation_ids"] for item in contexts.values()), contexts

    assert set(delivery["requirements"]["knowledge_citation_ids"]) <= set(
        contexts["requirements"]["citation_ids"]
    )
    assert delivery["requirements"]["knowledge_citation_ids"], delivery["requirements"]
    assert set(delivery["task"]["knowledge_citation_ids"]) <= set(
        contexts["tasking"]["citation_ids"]
    )
    assert delivery["task"]["knowledge_citation_ids"], delivery["task"]

    trees = _request_json(
        context,
        base_url,
        "GET",
        f"/v1/deliveries/{delivery_id}/workcell-runs",
    )
    assert len(trees) == 5, trees
    for tree in trees:
        stage_path = tree["workcell_run"]["stage_path"]
        result = tree["result"]
        assert result is not None, tree
        citations = set(result["knowledge_citation_ids"])
        assert citations, tree
        assert citations <= set(contexts[stage_path]["citation_ids"]), tree


def _verify_live_readiness_projection(data_dir: Path, project_id: str) -> None:
    """Prove Gate C facts are readable without upgrading them to Live evidence."""

    facts = KnowledgeLiveFactsCollector(
        data_dir / "agent-team-os.sqlite"
    ).collect(project_id)
    assert facts.database_ready is True, facts
    assert facts.project_status == "active", facts
    assert facts.team_status == "active", facts
    assert set(facts.workcell_keys) == {"design", "frontend", "backend", "qa"}, facts
    assert facts.workspace_count == 4, facts
    assert facts.ready_workspace_count == 4, facts
    assert facts.unique_repository_count == 4, facts
    assert facts.external_workspace_count == 0, facts
    assert facts.pipeline_binding_model == "provider-v1", facts
    assert facts.release_contract == ("backend", "design", "frontend", "qa"), facts
    assert facts.required_knowledge_context_count == 7, facts
    assert len(facts.knowledge_context_stage_paths) == 7, facts


def _verify_orchestration_knowledge_contract(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/orchestration", wait_until="networkidle")
    pipeline = page.locator(".pipeline-list button").filter(
        has_text="agent-workcell-delivery"
    )
    pipeline.wait_for()
    pipeline.click()
    page.get_by_label("选择主图节点").wait_for()

    _select_option(page, "选择主图节点", "frontend-repair")
    page.get_by_role("button", name="打开 LOOP 全屏工作区").click()
    _select_option(page, "选择循环体节点", "frontend")
    assert page.get_by_label(
        "Retrieval Policy frontend-repair/frontend"
    ).input_value() == "gate-retrieval-v1"
    assert page.get_by_label(
        "最大 Context Bytes frontend-repair/frontend"
    ).input_value() == "65536"
    page.get_by_role(
        "button", name="关闭 LOOP 工作区并保留草稿修改"
    ).click()

    _select_option(page, "选择主图节点", "requirements")
    policy = page.get_by_label("Retrieval Policy requirements")
    maximum = page.get_by_label("最大 Context Bytes requirements")
    assert policy.input_value() == "gate-retrieval-v1"
    assert maximum.input_value() == "65536"
    maximum.fill("65535")
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and "/v1/pipeline-drafts/" in response.url
    ) as saved:
        page.get_by_role("button", name="保存图与布局").click()
    assert saved.value.status == 200, saved.value.text()
    payload = saved.value.json()
    assert payload["definition"]["version"] == "2.0.0", payload
    assert (
        payload["knowledge_context_bindings"]["requirements"][
            "max_context_bytes"
        ]
        == 65535
    ), payload
    contract = payload["definition"]["nodes"][0]["input_artifact_contracts"][0]
    assert contract["id"] == "knowledge-context-v1", contract
    page.wait_for_timeout(300)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/validate")
    ) as validated:
        page.get_by_role("button", name="ACWM 图校验").click()
    assert validated.value.status == 200, validated.value.text()
    assert validated.value.json()["validation_status"] == "valid"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--gate-c", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    arguments = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1200})
        page = context.new_page()
        console_errors = _capture_console_errors(page)
        try:
            _authenticate(page, arguments.url)
            binding = _create_tenant_binding(page, arguments.url)
            _approve_and_sync(page, arguments.url, binding)
            policy_id = _qualify_and_activate_index(context, arguments.url, binding)
            _verify_rag_ui(
                page,
                arguments.url,
                None if arguments.gate_c else arguments.screenshot,
            )
            if arguments.gate_c:
                if arguments.data_dir is None:
                    raise AssertionError("--gate-c requires --data-dir")
                revision = _publish_gate_c_pipeline(context, arguments.url, policy_id)
                assert revision["definition"]["version"] == "2.0.0", revision
                _verify_orchestration_knowledge_contract(page, arguments.url)
                project_id, delivery_id = _execute_workcell_journey(
                    page,
                    arguments.url,
                    knowledge_binding_id=str(binding["id"]),
                )
                _verify_gate_c_evidence(
                    context,
                    arguments.url,
                    delivery_id,
                )
                _assert_product_evidence(
                    context.request,
                    arguments.url,
                    arguments.data_dir,
                    project_id,
                    delivery_id,
                )
                _verify_live_readiness_projection(arguments.data_dir, project_id)
                if arguments.screenshot is not None:
                    arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(arguments.screenshot), full_page=True)
            if arguments.state is not None:
                arguments.state.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(arguments.state))
        except Exception:
            if console_errors:
                print("Browser console errors:", *console_errors, sep="\n")
            raise
        assert not console_errors, console_errors
        browser.close()


if __name__ == "__main__":
    main()
