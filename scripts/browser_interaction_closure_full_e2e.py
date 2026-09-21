"""Complete v0.5.2 interaction closure up to Candidate, never Apply.

The server composition determines the evidence track. ``deterministic`` uses the
fixture Agent boundary; ``real-codex-local-pr`` invokes real Codex planning and
Workcell attempts while retaining the gate app's local deterministic PR surface.
Neither track is a formal Live Release Gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from playwright.sync_api import (
    APIRequestContext,
    Page,
    expect,
    sync_playwright,
)
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

WORKCELL_KEYS = ("design", "frontend", "backend", "qa")
EVALUATION_TARGET = """
在四个隔离 Workcell Repository 中实现可机器验证的 interaction-status-v1
健康状态合同，并严格保留以下八个 Acceptance ID：
IC-DESIGN-001: Design 定义版本化的 readiness、degraded 和 unavailable 状态语义。
IC-DESIGN-002: Design 用机器测试验证本仓规格包含完整状态集。
IC-FRONTEND-001: Frontend 按 interaction-status-v1 呈现状态文案与未知值的安全降级。
IC-FRONTEND-002: Frontend 用机器测试验证本仓的三种状态与降级行为。
IC-BACKEND-001: Backend 的 service_info 返回 interaction-status-v1 版本与 readiness 状态。
IC-BACKEND-002: Backend 用机器测试验证本仓返回值的精确合同。
IC-QA-001: QA 在本仓记录 interaction-status-v1 的独立可观察行为验收矩阵。
IC-QA-002: QA 用机器测试验证本仓矩阵覆盖三种状态且明确停在 Apply 前。
每个 Workcell 只能修改自己的 Repository，必须产生非空 Candidate 并通过本仓机器测试。
本次只验证 Plan/Design Gate、Workcell、Verification、Candidate 和 Evidence 回读，
不执行 Candidate/Release Gate 或 Apply。
""".strip()
# This is an aggregate browser observation window for a stage that can contain
# multiple sequential AgentAttempts.  It must not be confused with the product's
# per-Codex-call timeout, which remains 120 seconds and is asserted below.
STAGE_TIMEOUT_MS = int(
    os.environ.get("AGENT_TEAM_OS_BROWSER_STAGE_TIMEOUT_MS", "1800000")
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--track",
        choices=("deterministic", "real-codex-local-pr"),
        required=True,
    )
    parser.add_argument("--resume-project-id")
    parser.add_argument("--resume-delivery-id")
    args = parser.parse_args()
    if bool(args.resume_project_id) != bool(args.resume_delivery_id):
        parser.error("--resume-project-id and --resume-delivery-id must be provided together")
    password = os.environ.get("AGENT_TEAM_OS_TEST_PASSWORD")
    if not password:
        raise AssertionError("AGENT_TEAM_OS_TEST_PASSWORD must be session-injected")
    args.evidence_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        desktop = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = desktop.new_page()
        errors = _capture_errors(page)
        browser_external_writes: list[str] = []
        origin = urlsplit(args.url)
        page.on(
            "request",
            lambda request: (
                browser_external_writes.append(request.url)
                if request.method not in {"GET", "HEAD", "OPTIONS"}
                and urlsplit(request.url).netloc != origin.netloc
                else None
            ),
        )
        _authenticate(page, args.url, password)
        # The unauthenticated landing route probes the current session and a 401
        # is its expected control flow.  Product-page error evidence starts only
        # after login succeeds.
        errors.clear()
        settings = _configure_track(page.context.request, args.url, args.track)
        if args.resume_project_id:
            project_id = args.resume_project_id
            delivery_id = args.resume_delivery_id
            initial_main = _load_session_baseline(
                args.evidence_dir,
                track=args.track,
                project_id=project_id,
            )
            page.goto(f"{args.url}/projects/{project_id}/deliveries/{delivery_id}")
            page.locator(".run-hero").wait_for(timeout=30_000)
        else:
            project_id, initial_main = _create_ready_guided_workcell_project(
                page, args.url, args.data_dir
            )
            _write_session_baseline(
                args.evidence_dir,
                track=args.track,
                project_id=project_id,
                initial_main=initial_main,
            )
            _assert_setup_ready(page, args.url, project_id, args.evidence_dir)
            delivery_id = _run_to_candidate(
                page,
                args.url,
                args.data_dir,
                project_id,
                initial_main,
                args.evidence_dir,
                args.track,
            )
        report = _assert_candidate_evidence_and_complete(
            page,
            args.url,
            args.data_dir,
            project_id,
            delivery_id,
            initial_main,
            args.track,
        )
        page.screenshot(path=str(args.evidence_dir / "candidate-desktop.png"), full_page=True)

        mobile = browser.new_context(
            viewport={"width": 390, "height": 844}, storage_state=desktop.storage_state()
        )
        mobile_page = mobile.new_page()
        mobile_errors = _capture_errors(mobile_page)
        mobile_page.goto(f"{args.url}/projects/{project_id}/deliveries/{delivery_id}")
        mobile_page.locator(".run-hero").wait_for(timeout=30_000)
        mobile_page.get_by_role("button", name="打开导航与账户").click()
        mobile_page.get_by_role("dialog", name="导航与账户").wait_for()
        mobile_page.get_by_role("navigation", name="移动端系统目录").wait_for()
        mobile_page.wait_for_timeout(500)
        mobile_page.screenshot(path=str(args.evidence_dir / "candidate-mobile.png"), full_page=True)
        mobile_page.keyboard.press("Escape")
        expect(mobile_page.get_by_role("dialog", name="导航与账户")).not_to_be_visible()

        assert not browser_external_writes, browser_external_writes
        assert not errors, errors
        assert not mobile_errors, mobile_errors
        report.update(
            {
                "status": "passed",
                "git_head": _git_head(),
                "working_tree_dirty": bool(_git_status()),
                "browser_external_writes": 0,
                "console_page_errors": 0,
                "planning_timeout_seconds": settings["planning_timeout_seconds"],
                "browser_stage_wait_timeout_seconds": STAGE_TIMEOUT_MS // 1000,
                "session_baseline": "session-baseline.json",
                "screenshots": [
                    "setup-ready.png",
                    "candidate-desktop.png",
                    "candidate-mobile.png",
                ],
            }
        )
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


def _configure_track(
    request: APIRequestContext, url: str, track: str
) -> dict[str, object]:
    settings = cast(dict[str, object], _get_json(request, f"{url}/v1/settings"))
    if track == "real-codex-local-pr":
        assert settings["planning_timeout_seconds"] == 120, settings
    return settings


def _create_ready_guided_workcell_project(
    page: Page, url: str, data_dir: Path
) -> tuple[str, dict[str, str]]:
    request = page.context.request
    pipeline = next(
        item
        for item in _get_json(request, f"{url}/v1/pipelines")
        if item["id"] == "agent-workcell-delivery"
    )
    project_id = f"interaction-full-{int(time.time())}"
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_placeholder("例如：pj1").fill(project_id)
    page.get_by_placeholder("例如：客户门户后端").fill("v0.5.2 完整交互闭环")
    page.get_by_placeholder("说明项目边界和验收目标").fill(
        "在 Apply 前验证 Plan、Design、Workcell、Verification、Candidate 与 Evidence。"
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
    ) as created:
        page.get_by_role("button", name="创建项目并接入四个仓库").click()
    assert created.value.status == 201, created.value.text()
    assert created.value.json()["onboarding"]["status"] == "setup"

    initial_main: dict[str, str] = {}
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
                "verification_profile_id": "python-unittest-v1",
            },
            expected_status=201,
        )
        workspace = assignment["workspace_binding"]
        verified = _post_json(
            request,
            f"{url}/v1/workspace-bindings/{workspace['id']}/verify",
            {"expected_version": workspace["version"]},
        )
        qualified = _post_json(
            request,
            f"{url}/v1/workspace-bindings/{workspace['id']}/verification-profile/qualify",
            {"expected_version": verified["version"]},
        )
        assert qualified["status"] == "ready", qualified
        bare = (
            data_dir
            / "browser-workspaces"
            / "projects"
            / project_id
            / workcell_key
            / "backend-demo.git"
        )
        initial_main[workcell_key] = subprocess.run(
            ("git", f"--git-dir={bare}", "rev-parse", "refs/heads/main"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    topology = _get_json(request, f"{url}/v1/projects/{project_id}/workcells")
    _post_json(
        request,
        f"{url}/v1/projects/{project_id}/team-activate",
        {"expected_version": topology["team_binding"]["version"]},
    )
    return project_id, initial_main


def _assert_setup_ready(page: Page, url: str, project_id: str, evidence_dir: Path) -> None:
    page.goto(f"{url}/setup?project_id={project_id}")
    page.get_by_text(f"准备中心 · {project_id}", exact=True).wait_for()
    page.locator(".readiness-group").first.wait_for()
    report = _get_json(page.context.request, f"{url}/v1/setup-readiness?project_id={project_id}")
    assert report["status"] == "ready", report
    page.screenshot(path=str(evidence_dir / "setup-ready.png"), full_page=True)


def _run_to_candidate(
    page: Page,
    url: str,
    data_dir: Path,
    project_id: str,
    initial_main: dict[str, str],
    evidence_dir: Path,
    track: str,
) -> str:
    page.goto(f"{url}/projects/{project_id}/deliveries")
    page.get_by_label("交付目标").fill(EVALUATION_TARGET)
    page.reload()
    expect(page.get_by_label("交付目标")).to_have_value(EVALUATION_TARGET)
    page.get_by_role("button", name="生成交付计划").click()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/v1/deliveries")
    ) as created:
        page.get_by_role("button", name="启动评测交付").click()
    assert created.value.status == 202, created.value.text()
    delivery_id = str(created.value.json()["id"])

    try:
        _wait_delivery_status(
            page.context.request,
            f"{url}/v1/deliveries/{delivery_id}",
            "awaiting_plan_decision",
        )
        page.reload()
        plan = page.get_by_role("button", name="批准计划并开始设计")
        plan.wait_for(timeout=30_000)
        plan.click()
        dialog = page.get_by_role("alertdialog", name="批准计划并启动 UI 设计")
        dialog.wait_for()
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        plan.click()
        page.get_by_role("button", name="确认批准计划").click()

        _wait_delivery_status(
            page.context.request,
            f"{url}/v1/deliveries/{delivery_id}",
            "awaiting_design_decision",
        )
        design = page.get_by_role("button", name="批准设计并开始前后端实现")
        page.reload()
        design.wait_for(timeout=30_000)
        design.click()
        design_dialog = page.get_by_role("alertdialog", name="批准 UI 设计候选")
        design_dialog.wait_for()
        page.keyboard.press("Escape")
        expect(design_dialog).not_to_be_visible()
        design.click()
        page.get_by_role("button", name="确认批准设计").click()

        _wait_delivery_status(
            page.context.request,
            f"{url}/v1/deliveries/{delivery_id}",
            "awaiting_candidate_decision",
        )
        page.reload()
        page.get_by_text("评测已到达 Evidence 回读点", exact=True).wait_for(
            timeout=STAGE_TIMEOUT_MS
        )
        expect(page.get_by_role("button", name="批准四仓 Forward-only 发布")).to_have_count(0)
        page.screenshot(path=str(evidence_dir / "candidate-desktop.png"), full_page=True)
        return delivery_id
    except Exception:
        failure = _collect_failure_artifact(
            page.context.request,
            url=url,
            data_dir=data_dir,
            project_id=project_id,
            delivery_id=delivery_id,
            initial_main=initial_main,
            track=track,
        )
        (evidence_dir / "failure-result.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        page.screenshot(path=str(evidence_dir / "failure.png"), full_page=True)
        raise


def _wait_delivery_status(
    request: APIRequestContext, url: str, expected_status: str
) -> dict[str, object]:
    deadline = time.monotonic() + STAGE_TIMEOUT_MS / 1000
    latest: dict[str, object] = {}
    while time.monotonic() < deadline:
        try:
            latest = _get_json(request, url)
        except PlaywrightTimeoutError:
            time.sleep(0.5)
            continue
        if latest.get("status") == expected_status:
            return latest
        if latest.get("status") in {"failed", "cancelled", "rejected"}:
            raise AssertionError(
                "delivery terminalized before "
                f"{expected_status!r}: status={latest.get('status')!r}, "
                f"error_code={latest.get('error_code')!r}"
            )
        time.sleep(0.5)
    raise AssertionError(
        "delivery did not reach "
        f"{expected_status!r}; status={latest.get('status')!r}, "
        f"error_code={latest.get('error_code')!r}"
    )


def _assert_candidate_evidence_and_complete(
    page: Page,
    url: str,
    data_dir: Path,
    project_id: str,
    delivery_id: str,
    initial_main: dict[str, str],
    track: str,
) -> dict[str, object]:
    request = page.context.request
    delivery = _get_json(request, f"{url}/v1/deliveries/{delivery_id}")
    assert delivery["status"] == "awaiting_candidate_decision", delivery
    assert delivery["purpose"] == "onboarding_evaluation"
    assert delivery["apply_receipt"] is None
    assert delivery["release_manifest"] is None
    trees = _get_json(request, f"{url}/v1/deliveries/{delivery_id}/workcell-runs")
    final_trees = _select_final_workcell_trees(trees)
    assert len(final_trees) == 5, trees
    runtime_identities = sorted(
        {
            agent["runtime_identity"]
            for tree in final_trees
            for agent in tree["agent_runs"]
        }
    )
    expected_runtime = (
        ["codex-cli"] if track == "real-codex-local-pr" else ["deterministic-model-boundary"]
    )
    assert runtime_identities == expected_runtime, runtime_identities

    evidence = _get_json(request, f"{url}/v1/deliveries/{delivery_id}/evidence")
    verified = {item["kind"] for item in evidence if item["status"] == "verified"}
    assert {"candidate", "verification", "candidate-gate"} <= verified, verified
    page.get_by_role("link", name="在项目证据账本中查看").click()
    page.get_by_text("证据目录", exact=True).wait_for()
    page.go_back()
    page.get_by_text("评测已到达 Evidence 回读点", exact=True).wait_for()
    project = _get_json(request, f"{url}/v1/projects/{project_id}")
    if project["onboarding"]["status"] != "ready":
        page.get_by_role("button", name="完成评测并转为正式项目").click()
        page.get_by_text("就绪", exact=True).last.wait_for(timeout=30_000)
        project = _get_json(request, f"{url}/v1/projects/{project_id}")
    assert project["onboarding"]["status"] == "ready", project["onboarding"]
    delivery_after = _get_json(request, f"{url}/v1/deliveries/{delivery_id}")
    assert delivery_after["status"] == "awaiting_candidate_decision"
    assert delivery_after["apply_receipt"] is None
    release = _get_json(request, f"{url}/v1/releases/{delivery_id}")
    assert release["apply_attempt"] is None
    assert release["remote_apply_receipts"] == []
    assert release["manifest"] is None

    main_invariants = _repository_main_invariants(
        data_dir, project_id=project_id, initial_main=initial_main
    )
    assert all(
        item["unchanged"] is True for item in main_invariants.values()
    ), main_invariants

    return {
        "scope": "interaction-closure-to-candidate-no-apply",
        "track": track,
        "project_id": project_id,
        "delivery_id": delivery_id,
        "onboarding_status": "ready",
        "delivery_status": "awaiting_candidate_decision",
        "runtime_identities": runtime_identities,
        "failed_repair_workcell_count": len(trees) - len(final_trees),
        "verified_evidence_kinds": sorted(verified),
        "apply_receipt": None,
        "remote_apply_receipt_count": 0,
        "release_manifest": None,
        "repository_main_invariants": main_invariants,
        "remote_main_unchanged": True,
    }


def _select_final_workcell_trees(
    trees: list[dict[str, object]],
) -> tuple[dict[str, object], ...]:
    """Select one successful terminal Run per Stage while preserving bounded repair history."""
    successful: dict[str, dict[str, object]] = {}
    failed_iterations: dict[str, list[int]] = {}
    for tree in trees:
        raw_run = tree.get("workcell_run")
        if not isinstance(raw_run, dict):
            raise AssertionError("workcell tree is missing workcell_run")
        stage_path = raw_run.get("stage_path")
        status = raw_run.get("status")
        iteration = raw_run.get("loop_iteration")
        if not isinstance(stage_path, str) or not isinstance(iteration, int):
            raise AssertionError("workcell run identity is invalid")
        if status == "succeeded":
            if stage_path in successful:
                raise AssertionError("stage has multiple successful WorkcellRuns")
            successful[stage_path] = tree
        elif status == "failed":
            failed_iterations.setdefault(stage_path, []).append(iteration)
        else:
            raise AssertionError(f"workcell run is not terminal: {stage_path}={status}")
    for stage_path, iterations in failed_iterations.items():
        final = successful.get(stage_path)
        if final is None:
            raise AssertionError("failed stage has no successful bounded repair")
        final_run = final["workcell_run"]
        assert isinstance(final_run, dict)
        final_iteration = final_run["loop_iteration"]
        if not isinstance(final_iteration, int) or any(
            iteration >= final_iteration for iteration in iterations
        ):
            raise AssertionError("repair history is not older than the successful WorkcellRun")
    return tuple(successful[key] for key in sorted(successful))


def _write_session_baseline(
    evidence_dir: Path,
    *,
    track: str,
    project_id: str,
    initial_main: dict[str, str],
) -> dict[str, object]:
    baseline: dict[str, object] = {
        "schema_version": "interaction-closure-session-baseline-v1",
        "track": track,
        "project_id": project_id,
        "initial_main": dict(sorted(initial_main.items())),
    }
    (evidence_dir / "session-baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return baseline


def _load_session_baseline(
    evidence_dir: Path,
    *,
    track: str,
    project_id: str,
) -> dict[str, str]:
    baseline = json.loads(
        (evidence_dir / "session-baseline.json").read_text(encoding="utf-8")
    )
    assert baseline.get("schema_version") == "interaction-closure-session-baseline-v1"
    assert baseline.get("track") == track
    assert baseline.get("project_id") == project_id
    initial_main = baseline.get("initial_main")
    assert isinstance(initial_main, dict) and set(initial_main) == set(WORKCELL_KEYS)
    assert all(
        isinstance(key, str)
        and isinstance(value, str)
        and len(value) == 40
        for key, value in initial_main.items()
    )
    return cast(dict[str, str], initial_main)


def _repository_main_invariants(
    data_dir: Path,
    *,
    project_id: str,
    initial_main: dict[str, str],
) -> dict[str, dict[str, object]]:
    invariants: dict[str, dict[str, object]] = {}
    for workcell_key, before in sorted(initial_main.items()):
        bare = (
            data_dir
            / "browser-workspaces"
            / "projects"
            / project_id
            / workcell_key
            / "backend-demo.git"
        )
        after = subprocess.run(
            ("git", f"--git-dir={bare}", "rev-parse", "refs/heads/main"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        invariants[workcell_key] = {
            "initial_main": before,
            "current_main": after,
            "unchanged": after == before,
        }
    return invariants


def _collect_failure_artifact(
    request: APIRequestContext,
    *,
    url: str,
    data_dir: Path,
    project_id: str,
    delivery_id: str,
    initial_main: dict[str, str],
    track: str,
) -> dict[str, object]:
    delivery = cast(
        dict[str, object], _get_json(request, f"{url}/v1/deliveries/{delivery_id}")
    )
    project = cast(dict[str, object], _get_json(request, f"{url}/v1/projects/{project_id}"))
    release = cast(dict[str, object], _get_json(request, f"{url}/v1/releases/{delivery_id}"))
    evidence = cast(
        list[dict[str, object]],
        _get_json(request, f"{url}/v1/deliveries/{delivery_id}/evidence"),
    )
    settings = cast(dict[str, object], _get_json(request, f"{url}/v1/settings"))
    attempts = _read_attempt_summaries(
        data_dir / "agent-team-os.sqlite",
        delivery_id,
        artifacts_root=data_dir / "artifacts",
    )
    invariants = _repository_main_invariants(
        data_dir, project_id=project_id, initial_main=initial_main
    )
    return _build_failure_artifact(
        track=track,
        project_id=project_id,
        delivery_id=delivery_id,
        delivery=delivery,
        project=project,
        release=release,
        evidence=evidence,
        attempts=attempts,
        main_invariants=invariants,
        planning_timeout_seconds=settings.get("planning_timeout_seconds"),
    )


def _read_attempt_summaries(
    database: Path,
    delivery_id: str,
    *,
    artifacts_root: Path | None = None,
) -> list[dict[str, object]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT agent_runs.binding_site, agent_runs.run_role,
                      agent_attempts.phase, agent_attempts.ordinal,
                      agent_attempts.status, agent_attempts.error_code,
                      agent_attempts.runtime_identity, agent_attempts.started_at,
                      agent_attempts.finished_at,
                      agent_attempts.result_artifact_sha256
               FROM agent_attempts
               JOIN agent_runs ON agent_runs.id = agent_attempts.agent_run_id
               WHERE agent_runs.delivery_id = ?
               ORDER BY agent_attempts.started_at, agent_attempts.id""",
            (delivery_id,),
        ).fetchall()
    finally:
        connection.close()
    attempts: list[dict[str, object]] = []
    for row in rows:
        attempt = {
            "binding_site": str(row["binding_site"]),
            "run_role": str(row["run_role"]),
            "phase": str(row["phase"]),
            "ordinal": int(row["ordinal"]),
            "status": str(row["status"]),
            "error_code": row["error_code"],
            "runtime_identity": row["runtime_identity"],
            "duration_seconds": _attempt_duration_seconds(
                str(row["started_at"]),
                str(row["finished_at"]) if row["finished_at"] else None,
            ),
        }
        diagnostic = _safe_exit_diagnostic(
            artifacts_root,
            row["result_artifact_sha256"],
        )
        if diagnostic is not None:
            attempt["stderr_diagnostic"] = diagnostic
        attempts.append(attempt)
    return attempts


def _safe_exit_diagnostic(
    artifacts_root: Path | None,
    sha256: object,
) -> dict[str, object] | None:
    if (
        artifacts_root is None
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        return None
    path = artifacts_root / "sha256" / sha256[:2] / sha256
    try:
        payload_bytes = path.read_bytes()
        if hashlib.sha256(payload_bytes).hexdigest() != sha256:
            return None
        payload = json.loads(payload_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    version = payload.get("contract_version") if isinstance(payload, dict) else None
    expected = {
        "contract_version",
        "exit_code",
        "stderr_bytes",
        "stderr_sha256",
        "stable_category",
    }
    if version == "codex-cli-exit-diagnostic-v2":
        expected.add("signals")
    if not isinstance(payload, dict) or set(payload) != expected:
        return None
    if (
        version not in {
            "codex-cli-exit-diagnostic-v1",
            "codex-cli-exit-diagnostic-v2",
        }
        or not isinstance(payload.get("exit_code"), int)
        or not isinstance(payload.get("stderr_bytes"), int)
        or not isinstance(payload.get("stderr_sha256"), str)
        or len(str(payload.get("stderr_sha256"))) != 64
        or not isinstance(payload.get("stable_category"), str)
        or not str(payload.get("stable_category")).startswith("CODEX_WORKCELL_")
        or (
            version == "codex-cli-exit-diagnostic-v2"
            and (
                not isinstance(payload.get("signals"), list)
                or not all(
                    isinstance(item, str)
                    and item.replace("-", "").isalnum()
                    and item == item.casefold()
                    for item in payload["signals"]
                )
            )
        )
    ):
        return None
    return payload


def _attempt_duration_seconds(started_at: str, finished_at: str | None) -> float | None:
    if finished_at is None:
        return None
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return round((finished - started).total_seconds(), 3)


def _build_failure_artifact(
    *,
    track: str,
    project_id: str,
    delivery_id: str,
    delivery: dict[str, object],
    project: dict[str, object],
    release: dict[str, object],
    evidence: list[dict[str, object]],
    attempts: list[dict[str, object]],
    main_invariants: dict[str, dict[str, object]],
    planning_timeout_seconds: object,
) -> dict[str, object]:
    attempt_fields = (
        "binding_site",
        "run_role",
        "phase",
        "ordinal",
        "status",
        "error_code",
        "runtime_identity",
        "duration_seconds",
    )
    sanitized_attempts = [
        {field: item.get(field) for field in attempt_fields} for item in attempts
    ]
    onboarding = project.get("onboarding")
    onboarding_status = (
        onboarding.get("status") if isinstance(onboarding, dict) else None
    )
    verified = sorted(
        str(item["kind"])
        for item in evidence
        if item.get("status") == "verified" and isinstance(item.get("kind"), str)
    )
    remote_receipts = release.get("remote_apply_receipts")
    last_error = next(
        (
            item.get("error_code")
            for item in reversed(sanitized_attempts)
            if item.get("error_code") is not None
        ),
        None,
    )
    stderr_diagnostic = next(
        (
            item.get("stderr_diagnostic")
            for item in reversed(attempts)
            if isinstance(item.get("stderr_diagnostic"), dict)
        ),
        None,
    )
    return {
        "schema_version": "interaction-closure-failure-v1",
        "status": "failed",
        "track": track,
        "project_id": project_id,
        "delivery_id": delivery_id,
        "delivery_status": delivery.get("status"),
        "delivery_error_code": delivery.get("error_code"),
        "onboarding_status": onboarding_status,
        "planning_timeout_seconds": planning_timeout_seconds,
        "session_baseline": "session-baseline.json",
        "attempts": sanitized_attempts,
        "failure_classification": last_error or delivery.get("error_code"),
        "stderr_diagnostic_available": stderr_diagnostic is not None,
        "stderr_diagnostic": stderr_diagnostic,
        "verified_evidence_kinds": verified,
        "candidate_or_release_gate_evidence_present": bool(
            {"candidate-gate", "release-gate"}.intersection(verified)
        ),
        "apply_receipt": delivery.get("apply_receipt"),
        "release_manifest": release.get("manifest"),
        "apply_attempt_present": release.get("apply_attempt") is not None,
        "remote_apply_receipt_count": (
            len(remote_receipts) if isinstance(remote_receipts, list) else None
        ),
        "repository_main_invariants": main_invariants,
        "remote_main_unchanged": all(
            item.get("unchanged") is True for item in main_invariants.values()
        ),
    }


def _capture_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )
    return errors


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
    return _mutation_json(
        request,
        "POST",
        url,
        payload,
        expected_status=expected_status,
    )


def _mutation_json(
    request: APIRequestContext,
    method: str,
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
    response = request.fetch(
        url,
        method=method,
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
    dropdown = page.locator(".ant-select-dropdown:visible").last
    dropdown.wait_for()
    option = dropdown.locator(".ant-select-item-option:visible").filter(has_text=option_text)
    option.first.wait_for()
    option.first.evaluate("element => element.click()")
    control.wait_for()
    page.keyboard.press("Escape")


def _git_head() -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"), check=True, capture_output=True, text=True
    ).stdout.strip()


def _git_status() -> str:
    return subprocess.run(
        ("git", "status", "--porcelain"), check=True, capture_output=True, text=True
    ).stdout.strip()


if __name__ == "__main__":
    main()
