"""Read-only browser audit for record-picker layout and page overflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Page, ViewportSize, sync_playwright

VIEWPORTS: tuple[ViewportSize, ...] = (
    {"width": 1280, "height": 800},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 2560, "height": 1440},
)


def _row_metrics(page: Page, selector: str) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        page.locator(selector).evaluate_all(
            """elements => elements.map(element => {
          const rect = element.getBoundingClientRect();
          return {
            label: element.innerText.split(String.fromCharCode(10)).join(" ").slice(0, 90),
            height: Math.round(rect.height * 100) / 100,
            overflow_x: element.scrollWidth - element.clientWidth,
            overflow_y: element.scrollHeight - element.clientHeight,
            white_space: getComputedStyle(element).whiteSpace,
          };
        })""",
        ),
    )


def _overlapping_children(page: Page, selector: str) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        page.locator(selector).evaluate_all(
            """containers => containers.flatMap((container, containerIndex) => {
          const children = Array.from(container.children).map((child, childIndex) => ({
            childIndex,
            label: child.innerText.split(String.fromCharCode(10)).join(" ").slice(0, 90),
            rect: child.getBoundingClientRect(),
          }));
          const overlaps = [];
          for (let left = 0; left < children.length; left += 1) {
            for (let right = left + 1; right < children.length; right += 1) {
              const a = children[left];
              const b = children[right];
              const overlapX = Math.min(a.rect.right, b.rect.right)
                - Math.max(a.rect.left, b.rect.left);
              const overlapY = Math.min(a.rect.bottom, b.rect.bottom)
                - Math.max(a.rect.top, b.rect.top);
              if (overlapX > 1 && overlapY > 1) {
                overlaps.push({
                  container_index: containerIndex,
                  left_index: a.childIndex,
                  left_label: a.label,
                  right_index: b.childIndex,
                  right_label: b.label,
                  overlap_x: Math.round(overlapX * 100) / 100,
                  overlap_y: Math.round(overlapY * 100) / 100,
                });
              }
            }
          }
          return overlaps;
        })""",
        ),
    )


def _children_outside_bounds(page: Page, selector: str) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        page.locator(selector).evaluate_all(
            """containers => containers.flatMap((container, containerIndex) => {
          const bounds = container.getBoundingClientRect();
          return Array.from(container.children).flatMap((child, childIndex) => {
            const rect = child.getBoundingClientRect();
            const outside = {
              left: Math.max(0, bounds.left - rect.left),
              right: Math.max(0, rect.right - bounds.right),
              top: Math.max(0, bounds.top - rect.top),
              bottom: Math.max(0, rect.bottom - bounds.bottom),
            };
            if (Math.max(...Object.values(outside)) <= 1) return [];
            return [{
              container_index: containerIndex,
              child_index: childIndex,
              label: child.innerText.split(String.fromCharCode(10)).join(" ").slice(0, 90),
              outside_left: Math.round(outside.left * 100) / 100,
              outside_right: Math.round(outside.right * 100) / 100,
              outside_top: Math.round(outside.top * 100) / 100,
              outside_bottom: Math.round(outside.bottom * 100) / 100,
            }];
          });
        })""",
        ),
    )


def _authenticate(page: Page, url: str) -> None:
    page.goto(url, wait_until="networkidle")
    if not page.get_by_role("heading", name="登录 Agent-Team-OS").count():
        return
    username = os.environ.get("AGENT_TEAM_OS_TEST_USERNAME")
    password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD")
    if not username or not password:
        raise AssertionError(
            "登录页面需要 AGENT_TEAM_OS_TEST_USERNAME 与 "
            "AGENT_TEAM_OS_TEST_PASSWORD，布局审计不会内置评测凭据。"
        )
    page.get_by_label("用户名").fill(username)
    page.get_by_label("密码").fill(password)
    page.get_by_role("button", name="登录控制平面").click()
    page.get_by_role("link", name="项目", exact=True).wait_for()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--delivery-id")
    parser.add_argument("--screenshot-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    routes = {
        "deliveries": (
            f"/projects/{args.project_id}/deliveries",
            [".run-list .run-row", ".composer-config"],
        ),
        "evidence": (
            f"/projects/{args.project_id}/evidence",
            [".evidence-table > button"],
        ),
        "orchestration": ("/orchestration", [".pipeline-list > .ant-btn"]),
        "agents": ("/agents", [".profile-list > .ant-btn"]),
        "knowledge": (
            f"/projects/{args.project_id}/knowledge",
            [
                ".ant-btn.knowledge-space-item",
                ".ant-btn.knowledge-doc-item",
                ".knowledge-activity-item",
                ".external-history-list > div",
                ".rag-preview-results > article",
            ],
        ),
        "project": (
            f"/projects/{args.project_id}/overview",
            [".project-membership-list > div", ".project-source-approval-list > div"],
        ),
        "settings": (
            "/settings",
            [".knowledge-connection-list > article", ".knowledge-binding-list > article"],
        ),
        "board": (
            f"/projects/{args.project_id}/board",
            [".board-column", ".board-card"],
        ),
        "teams": ("/teams", [".team-catalog-list > button", ".topology-workcell"]),
    }
    if args.delivery_id:
        routes["delivery-detail"] = (
            f"/projects/{args.project_id}/deliveries/{args.delivery_id}",
            [
                ".pipeline-node-projections > article",
                ".workcell-candidate-proof",
                ".delivery-evidence-list button",
            ],
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context_options: dict[str, Any] = {"viewport": VIEWPORTS[0]}
        if args.state is not None:
            context_options["storage_state"] = str(args.state)
        context = browser.new_context(**context_options)
        page = context.new_page()
        console_errors: list[str] = []
        request_failures: list[str] = []
        http_failures: list[str] = []
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on(
            "requestfailed",
            lambda request: request_failures.append(
                f"{request.method} {request.url}: {request.failure}"
            ),
        )
        page.on(
            "response",
            lambda response: (
                http_failures.append(f"{response.status} {response.request.method} {response.url}")
                if response.status >= 400
                else None
            ),
        )
        _authenticate(page, args.url)
        # The anonymous session probe on the login screen is expected to be 401.
        console_errors.clear()
        request_failures.clear()
        http_failures.clear()

        report: dict[str, Any] = {}
        violations: list[dict[str, Any]] = []
        for viewport in VIEWPORTS:
            page.set_viewport_size(viewport)
            viewport_key = f"{viewport['width']}x{viewport['height']}"
            report[viewport_key] = {}
            for name, (route, selectors) in routes.items():
                page.goto(f"{args.url}{route}", wait_until="networkidle")
                page.wait_for_timeout(250)
                report[viewport_key][name] = {
                    "page_overflow_x": page.evaluate(
                        "document.documentElement.scrollWidth - "
                        "document.documentElement.clientWidth"
                    ),
                    "selectors": {},
                }
                for selector in selectors:
                    rows = _row_metrics(page, selector)
                    report[viewport_key][name]["selectors"][selector] = rows
                    for row in rows:
                        if (
                            row["overflow_x"] > 1
                            or row["overflow_y"] > 1
                            or row["white_space"] == "nowrap"
                        ):
                            violations.append(
                                {
                                    "viewport": viewport_key,
                                    "page": name,
                                    "selector": selector,
                                    **row,
                                }
                            )
                overlap_selectors = {
                    "deliveries": [".composer-config"],
                    "evidence": [".evidence-table > button"],
                }.get(name, [])
                for selector in overlap_selectors:
                    overlaps = _overlapping_children(page, selector)
                    report[viewport_key][name].setdefault("overlaps", {})[selector] = overlaps
                    violations.extend(
                        {
                            "viewport": viewport_key,
                            "page": name,
                            "selector": selector,
                            "reason": "direct children overlap",
                            **overlap,
                        }
                        for overlap in overlaps
                    )
                if name == "teams":
                    outside_nodes = _children_outside_bounds(page, ".team-topology-canvas")
                    report[viewport_key][name]["outside_topology_nodes"] = outside_nodes
                    violations.extend(
                        {
                            "viewport": viewport_key,
                            "page": name,
                            "selector": ".team-topology-canvas",
                            "reason": "topology node is clipped by canvas bounds",
                            **outside,
                        }
                        for outside in outside_nodes
                        if outside["label"]
                    )
                if name == "project":
                    status_badges = _row_metrics(page, ".project-hero > .ant-tag")
                    report[viewport_key][name]["project_status_badges"] = status_badges
                    for badge in status_badges:
                        if badge["height"] > 48:
                            violations.append(
                                {
                                    "viewport": viewport_key,
                                    "page": name,
                                    "selector": ".project-hero > .ant-tag",
                                    "reason": "project status badge is vertically stretched",
                                    **badge,
                                }
                            )
                if name == "knowledge":
                    activity_cards = _row_metrics(page, ".knowledge-activity-item")
                    report[viewport_key][name]["activity_cards"] = activity_cards
                    for card in activity_cards:
                        if card["height"] > 520:
                            violations.append(
                                {
                                    "viewport": viewport_key,
                                    "page": name,
                                    "selector": ".knowledge-activity-item",
                                    "reason": "collapsed activity card exceeds 520px",
                                    **card,
                                }
                            )
                if args.screenshot_dir:
                    args.screenshot_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(args.screenshot_dir / f"{viewport_key}-{name}.png"),
                        full_page=True,
                    )

        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(
                    {
                        "report": report,
                        "violations": violations,
                        "console_errors": console_errors,
                        "request_failures": request_failures,
                        "http_failures": http_failures,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        print(
            json.dumps(
                {
                    "viewports": len(report),
                    "routes": len(routes),
                    "violations": len(violations),
                    "console_errors": len(console_errors),
                    "request_failures": len(request_failures),
                    "http_failures": len(http_failures),
                    "report": str(args.report) if args.report else None,
                },
                ensure_ascii=False,
            )
        )
        assert all(
            page_report["page_overflow_x"] <= 1
            for viewport_report in report.values()
            for page_report in viewport_report.values()
        ), report
        assert not violations, violations
        assert not console_errors, console_errors
        assert not request_failures, request_failures
        assert not http_failures, http_failures
        browser.close()


if __name__ == "__main__":
    main()
