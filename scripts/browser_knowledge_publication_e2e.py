"""Browser acceptance for role-document publication and Knowledge/Evidence separation."""

from __future__ import annotations

import argparse
from pathlib import Path

from browser_e2e import (
    _authenticate,
    _capture_console_errors,
)
from playwright.sync_api import BrowserContext, Page, sync_playwright


def _create_planning_delivery(page: Page) -> str:
    page.get_by_role("link", name="交付工作台", exact=True).click()
    pipeline_selector = page.get_by_label("执行 Pipeline")
    pipeline_selector.wait_for(timeout=30_000)
    page.wait_for_function("document.querySelector('#delivery-pipeline')?.value.length > 0")
    page.get_by_label("交付目标").fill(
        "增加 health_status 函数，返回 status=ok 和 version=0.1，并补充 unittest。"
    )
    page.get_by_role("button", name="生成交付计划").click()
    page.get_by_role("region", name="确认交付边界").wait_for()
    with page.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith("/v1/deliveries")
        )
    ) as delivery_response:
        page.get_by_role("button", name="确认并启动").click()
    assert delivery_response.value.status == 202, delivery_response.value.text()
    delivery_id = str(delivery_response.value.json()["id"])
    page.locator(".run-hero").wait_for(timeout=30_000)
    page.get_by_role("button", name="审查计划").wait_for(timeout=90_000)
    return delivery_id


def _get_json(context: BrowserContext, url: str) -> object:
    response = context.request.get(url)
    assert response.ok, response.text()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshot", type=Path)
    arguments = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1200})
        page = context.new_page()
        console_errors, authentication = _capture_console_errors(page)
        try:
            _authenticate(page, arguments.url)
            authentication["in_progress"] = False
            delivery_id = _create_planning_delivery(page)

            publications = _get_json(
                context,
                f"{arguments.url}/v1/deliveries/{delivery_id}/knowledge-publications"
            )
            assert isinstance(publications, list)
            assert len(publications) == 2, publications
            assert {item["contract_id"] for item in publications} == {
                "requirement-artifact-v1",
                "task-contract-v1",
            }
            assert all(item["status"] == "published" for item in publications), publications

            spaces = _get_json(
                context,
                f"{arguments.url}/v1/wiki/spaces?project_id=legacy-default&include_global=true"
            )
            assert isinstance(spaces, list)
            project_space = next(
                item
                for item in spaces
                if item["space_kind"] == "project-documents"
            )
            documents = _get_json(
                context,
                f"{arguments.url}/v1/wiki/documents?space_id={project_space['id']}"
            )
            assert isinstance(documents, list)
            assert len(documents) == 2, documents
            assert {(item["document_kind"], item["role_key"]) for item in documents} == {
                ("product-requirement", "product-manager"),
                ("delivery-plan", "project-admin"),
            }
            assert all(item["source_kind"] == "agent-publication" for item in documents)
            evidence = _get_json(
                context, f"{arguments.url}/v1/deliveries/{delivery_id}/evidence"
            )
            assert isinstance(evidence, list) and evidence

            page.goto(f"{arguments.url}/projects/legacy-default/knowledge")
            page.wait_for_load_state("networkidle")
            selected_space = page.locator(".knowledge-space-item.selected")
            selected_space.wait_for()
            assert "标准项目文档" in selected_space.inner_text()

            requirement_document = next(
                item for item in documents if item["document_kind"] == "product-requirement"
            )
            page.get_by_role(
                "button", name=f"文档 {requirement_document['title']}"
            ).click()
            publication = next(
                item
                for item in publications
                if item["contract_id"] == "requirement-artifact-v1"
            )
            page.get_by_text(
                f"AgentRun {publication['agent_run_id']}", exact=False
            ).wait_for()
            page.get_by_text("原始 Artifact SHA-256", exact=False).wait_for()
            rendered_markdown = page.locator(".document-content").inner_text()
            assert "health_status" in rendered_markdown
            editor_markdown = page.get_by_placeholder("Markdown 正文", exact=True).input_value()
            assert "health_status" in editor_markdown
            assert "project-document-v1" not in editor_markdown

            search = page.get_by_label("全文搜索")
            search.fill("health_status")
            page.get_by_label("项目文档搜索结果").wait_for()
            search.fill(delivery_id)
            evidence_group = page.get_by_label("Evidence搜索结果")
            evidence_group.wait_for()
            evidence_links = evidence_group.locator("a")
            assert evidence_links.count() > 0
            assert all(
                "/evidence?evidence_id=" in (evidence_links.nth(index).get_attribute("href") or "")
                for index in range(evidence_links.count())
            )

            if arguments.screenshot is not None:
                arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=arguments.screenshot, full_page=True)
            assert not console_errors, console_errors
            print(
                "knowledge-publication-browser-e2e: "
                f"delivery={delivery_id} publications=2 documents=2 grouped-search=passed"
            )
        except Exception:
            if arguments.screenshot is not None:
                arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=arguments.screenshot, full_page=True)
            if console_errors:
                print("Browser console errors:", *console_errors, sep="\n")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
