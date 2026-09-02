"""Browser acceptance for the v0.5 four-repository Agent Workcell journey.

This gate uses the deterministic Agent boundary. It proves product orchestration,
repository isolation, evidence binding, and forward-only release behavior; it is
not Live model evidence.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import APIRequestContext, Page, expect, sync_playwright

WORKCELL_KEYS = ("design", "frontend", "backend", "qa")
STAGE_TIMEOUT_MS = int(os.environ.get("AGENT_TEAM_OS_BROWSER_STAGE_TIMEOUT_MS", "120000"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path)
    arguments = parser.parse_args()

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
            _authenticate(page, arguments.url)
            # The anonymous bootstrap probe intentionally receives 401 before login.
            console_errors.clear()
            project_id, delivery_id = _execute_workcell_journey(
                page,
                arguments.url,
            )
            _assert_product_evidence(
                page.context.request,
                arguments.url,
                arguments.data_dir,
                project_id,
                delivery_id,
            )
            assert not console_errors, console_errors
        except Exception:
            if console_errors:
                print("Browser console errors:", *console_errors, sep="\n")
            raise
        finally:
            if arguments.screenshot is not None:
                arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=arguments.screenshot, full_page=True)
            browser.close()


def _authenticate(page: Page, url: str) -> None:
    password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD")
    if not password:
        raise AssertionError("AGENT_TEAM_OS_TEST_PASSWORD must be session-injected")
    page.goto(url)
    page.wait_for_load_state("networkidle")
    if page.get_by_role("heading", name="初始化管理员").count():
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="创建并登录").click()
    elif page.get_by_role("heading", name="登录 Agent-Team-OS").count():
        page.get_by_label("用户名").fill(
            os.environ.get("AGENT_TEAM_OS_TEST_USERNAME", "admin")
        )
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="登录控制平面").click()
    page.get_by_role("link", name="项目", exact=True).wait_for(timeout=30_000)


def _execute_workcell_journey(
    page: Page,
    url: str,
    *,
    knowledge_binding_id: str | None = None,
) -> tuple[str, str]:
    request = page.context.request
    pipeline = next(
        item
        for item in _get_json(request, f"{url}/v1/pipelines")
        if item["id"] == "agent-workcell-delivery"
    )
    assert pipeline["active_revision"], pipeline

    page.get_by_role("link", name="组织模板", exact=True).click()
    page.get_by_text("四仓软件交付团队", exact=True).first.wait_for()
    expect(page.locator(".topology-workcell")).to_have_count(4)
    expect(page.locator(".topology-workcell").get_by_text("git_repository_v1")).to_have_count(4)
    assert page.get_by_text("执行顺序由 Published Pipeline Revision 管理。").count() == 0

    project_id = (
        "browser-workcell-v051-knowledge"
        if knowledge_binding_id is not None
        else "browser-workcell-v050"
    )
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_placeholder("例如：pj1").fill(project_id)
    page.get_by_placeholder("例如：客户门户后端").fill("四仓 Workcell 浏览器验收")
    page.get_by_placeholder("说明项目边界和验收目标").fill(
        "验证四个独立 Repository Workcell、Main/Child/Attempt 与 Forward-only 发布。"
    )
    _select_option(
        page,
        "默认流水线",
        f"{pipeline['name']} · R{pipeline['active_revision']}",
    )
    page.get_by_label("组织模板 Revision").wait_for()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/projects")
    ) as created_response:
        page.get_by_role("button", name="创建项目并接入四个仓库").click()
    assert created_response.value.status == 201, created_response.value.text()
    project = created_response.value.json()
    assert project["project"]["lifecycle_status"] == "provisioning", project
    assert project["workspace"]["repository_ref"] == f"workspace-set/{project_id}"

    for workcell_key in WORKCELL_KEYS:
        assignment = _post_json(
            request,
            f"{url}/v1/projects/{project_id}/workspace-bindings",
            {
                "workcell_key": workcell_key,
                "kind": "git_repository_v1",
                "adapter_type": "managed-bare-git",
                "repository_uri": f"projects/{project_id}/{workcell_key}",
                "credential_reference": None,
            },
            expected_status=201,
        )
        workspace = assignment["workspace_binding"]
        verified = _post_json(
            request,
            f"{url}/v1/workspace-bindings/{workspace['id']}/verify",
            {"expected_version": workspace["version"]},
        )
        assert verified["status"] == "ready", verified
        assert verified["verification"]["direct_fast_forward_main"] is True

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".workspace-cassette")).to_have_count(4)
    expect(page.get_by_text("managed-bare-git", exact=True)).to_have_count(4)
    page.get_by_text("四个独立 Repository 均已验证", exact=False).wait_for()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith(f"/v1/projects/{project_id}/team-activate")
    ) as activated_response:
        page.get_by_role("button", name="激活四仓团队").click()
    activated = activated_response.value.json()
    assert activated["project_status"] == "active", activated

    if knowledge_binding_id is not None:
        approval = _put_json(
            request,
            f"{url}/v1/projects/{project_id}/knowledge-source-approvals/"
            f"{knowledge_binding_id}",
            {"enabled": True, "rag_enabled": True, "expected_version": None},
        )
        assert approval["enabled"] is True, approval
        assert approval["rag_enabled"] is True, approval

    page.get_by_role("link", name="交付工作台", exact=True).click()
    page.get_by_text("Repository Workcell Set", exact=True).wait_for()
    page.get_by_text("跨 Workcell 只传 Artifact", exact=False).wait_for()
    page.get_by_text("Pipeline 冻结 Slot", exact=True).wait_for()
    page.get_by_label("交付目标").fill(
        "交付状态页设计、前端、后端与 QA 自动化，所有跨仓输入仅使用内容寻址 Artifact。"
    )
    page.get_by_role("button", name="生成交付计划").click()
    page.get_by_text("四个隔离 Repository Workcell", exact=False).wait_for()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/deliveries")
    ) as delivery_response:
        page.get_by_role("button", name="确认并启动").click()
    assert delivery_response.value.status == 202, delivery_response.value.text()
    delivery_id = str(delivery_response.value.json()["id"])

    page.get_by_role("button", name="批准计划并开始设计").wait_for(
        timeout=STAGE_TIMEOUT_MS
    )
    page.get_by_role("button", name="批准计划并开始设计").click()
    page.get_by_role("button", name="确认批准计划").click()

    page.get_by_role("button", name="批准设计并开始前后端实现").wait_for(
        timeout=STAGE_TIMEOUT_MS
    )
    page.get_by_text("Candidate ", exact=False).first.wait_for()
    page.get_by_role("button", name="批准设计并开始前后端实现").click()
    page.get_by_role("button", name="确认批准设计").click()

    release_button = page.get_by_role("button", name="批准四仓 Forward-only 发布")
    release_button.wait_for(timeout=STAGE_TIMEOUT_MS)
    page.get_by_text("External ReleaseBundleV2 已通过系统校验", exact=True).wait_for()
    expect(page.locator(".workcell-run-card")).to_have_count(5)
    expect(page.locator(".method-freeze-strip")).to_have_count(5)
    page.get_by_text("Main / Child / Attempt Tree", exact=True).wait_for()
    page.get_by_text("external-forward-only-v1", exact=True).wait_for()
    release_button.click()
    page.get_by_text("已成功仓库不回滚", exact=False).wait_for()
    page.get_by_role("button", name="确认 Forward-only 发布").click()

    page.get_by_text("ReleaseManifestV2 已激活", exact=True).wait_for(
        timeout=STAGE_TIMEOUT_MS
    )
    page.locator(".run-hero").get_by_text("已完成", exact=True).wait_for()
    return project_id, delivery_id


def _assert_product_evidence(
    request: APIRequestContext,
    url: str,
    data_dir: Path,
    project_id: str,
    delivery_id: str,
) -> None:
    topology = _get_json(request, f"{url}/v1/projects/{project_id}/workcells")
    workspaces = topology["workspace_bindings"]
    assert topology["project_status"] == "active", topology
    assert {item["status"] for item in workspaces} == {"ready"}, workspaces
    repository_uris = [item["repository_uri"] for item in workspaces]
    assert len(repository_uris) == len(set(repository_uris)) == 4, repository_uris

    delivery = _get_json(request, f"{url}/v1/deliveries/{delivery_id}")
    assert delivery["status"] == "completed", delivery
    candidates = delivery["workcell_candidates"]
    assert set(candidates) == set(WORKCELL_KEYS), candidates
    assert delivery["release_bundle_v2_sha256"], delivery
    assert delivery["release_manifest_v2_sha256"], delivery

    trees = _get_json(request, f"{url}/v1/deliveries/{delivery_id}/workcell-runs")
    assert len(trees) == 5, trees
    for tree in trees:
        workcell_run = tree["workcell_run"]
        main = [item for item in tree["agent_runs"] if item["run_role"] == "main"]
        children = [item for item in tree["agent_runs"] if item["run_role"] == "child"]
        assert len(main) == 1, tree
        assert len(children) <= 3, tree
        assert all(
            item["depth"] == 1
            and item["parent_agent_run_id"] == main[0]["id"]
            for item in children
        )
        assert all(
            item["root_agent_run_id"] == main[0]["id"]
            for item in tree["agent_runs"]
        )
        assert all(
            item["slot_key"] in {"delegate_1", "delegate_2", "delegate_3"}
            for item in children
        )
        main_phases = {
            item["phase"]
            for item in tree["attempts"]
            if item["agent_run_id"] == main[0]["id"]
        }
        assert {"planning", "synthesis"} <= main_phases, tree
        assert workcell_run["status"] == "succeeded", tree
        if workcell_run["stage_path"] == "qa-preparation-repair/qa-preparation":
            assert tree["verification"] is None, tree
            assert tree["result_validation"]["status"] == "passed", tree
            assert tree["result"]["candidate_sha"] is None, tree
        else:
            assert tree["result_validation"] is None, tree
            assert tree["verification"]["status"] == "passed", tree
            assert tree["reviews"], tree
            assert all(
                review["candidate_sha"] == tree["result"]["candidate_sha"]
                for review in tree["reviews"]
            ), tree

    release = _get_json(request, f"{url}/v1/releases/{delivery_id}")
    assert len(release["candidates"]) == 4, release
    assert len(release["pull_requests"]) == 4, release
    assert len(release["remote_apply_receipts"]) == 4, release
    assert release["manifest"]["manifest_sha256"] == delivery["release_manifest_v2_sha256"]

    project_documents = _get_json(
        request,
        f"{url}/v1/wiki/documents?space_id=project-docs:{project_id}",
    )
    assert project_documents, "role document publication did not create project Wiki records"
    assert all(
        0 < len(item["title"]) <= 240
        and "\n" not in item["title"]
        and "external-collaborative-data" not in item["title"]
        for item in project_documents
    ), project_documents

    for workcell_key, candidate in candidates.items():
        bare_repository = (
            data_dir
            / "browser-workspaces"
            / "projects"
            / project_id
            / workcell_key
            / "backend-demo.git"
        )
        remote_main = _git(bare_repository, "rev-parse", "refs/heads/main")
        assert remote_main == candidate["candidate_revision"], (workcell_key, remote_main)
        changed_files = _git(
            bare_repository,
            "diff",
            "--name-only",
            candidate["base_revision"],
            candidate["candidate_revision"],
        ).splitlines()
        assert changed_files, (workcell_key, changed_files)
        assert all(
            not path.startswith(("_bmad/", ".agents/skills/"))
            for path in changed_files
        ), (workcell_key, changed_files)


def _get_json(request: APIRequestContext, url: str) -> Any:
    response = request.get(url)
    assert response.ok, response.text()
    return response.json()


def _post_json(
    request: APIRequestContext,
    url: str,
    payload: dict[str, object],
    *,
    expected_status: int = 200,
) -> Any:
    csrf_token = next(
        (
            str(cookie["value"])
            for cookie in request.storage_state()["cookies"]
            if cookie["name"] == "agent_team_os_csrf"
        ),
        None,
    )
    assert csrf_token, "authenticated browser context is missing the CSRF cookie"
    parsed = urlsplit(url)
    response = request.post(
        url,
        data=payload,
        headers={
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
            "X-CSRF-Token": csrf_token,
        },
    )
    assert response.status == expected_status, response.text()
    return response.json()


def _put_json(
    request: APIRequestContext,
    url: str,
    payload: dict[str, object],
    *,
    expected_status: int = 200,
) -> Any:
    csrf_token = next(
        (
            str(cookie["value"])
            for cookie in request.storage_state()["cookies"]
            if cookie["name"] == "agent_team_os_csrf"
        ),
        None,
    )
    assert csrf_token, "authenticated browser context is missing the CSRF cookie"
    parsed = urlsplit(url)
    response = request.put(
        url,
        data=payload,
        headers={
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
            "X-CSRF-Token": csrf_token,
        },
    )
    assert response.status == expected_status, response.text()
    return response.json()


def _select_option(page: Page, label: str, option_text: str) -> None:
    control = page.get_by_label(label)
    control.click()
    # Ant Design briefly keeps the leaving popup visible while the next popup
    # enters. The most recently mounted visible popup owns the active control.
    dropdown = page.locator(".ant-select-dropdown:visible").last
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
    # React Flow viewport animations can cause Ant Design to rebuild and
    # reposition its portal between Playwright's actionability check and the
    # native click. A DOM click is appropriate here: the option is already
    # proven visible and enabled, and it avoids a false "outside viewport"
    # failure without weakening the product assertion that follows.
    option.first.evaluate("element => element.click()")
    control.wait_for()
    page.keyboard.press("Escape")


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", f"--git-dir={repository}", *arguments),
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()


if __name__ == "__main__":
    main()
