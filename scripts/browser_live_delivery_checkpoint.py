"""观察真实 Delivery 的人工 Gate；检查点不是正式 Live Release Report。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

SCHEMA = "live-delivery-browser-checkpoint-v1"
GATES = {
    "awaiting_plan_decision": ("plan", "plan_gate", "批准计划并开始设计"),
    "awaiting_design_decision": ("design", "design_gate", "批准设计并开始前后端实现"),
    "awaiting_candidate_decision": ("release", "candidate_gate", "批准四仓 Forward-only 发布"),
}
STOP_STATUSES = {"completed", "failed", "rejected", "cancelled", "needs_attention"}


class CheckpointError(RuntimeError):
    """错误码不包含响应正文、认证数据或 Playwright 的填值日志。"""


def _pick(value: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    source = value or {}
    return {key: source[key] for key in keys if key in source}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise CheckpointError("CHECKPOINT_BASE_URL_INVALID")
    return value.rstrip("/")


def project_checkpoint(
    *,
    delivery: dict[str, Any],
    release: dict[str, Any],
    evidence: list[dict[str, Any]],
    workcells: list[dict[str, Any]],
    base_url: str,
    project_id: str,
    delivery_id: str,
    expected_product_sha: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """仅投影允许保存的身份与证据引用；不复制 Artifact 正文或 Provider 配置。"""
    base_url = validate_base_url(base_url)
    if delivery.get("id") != delivery_id or delivery.get("project_id") != project_id:
        raise CheckpointError("CHECKPOINT_SUBJECT_MISMATCH")
    snapshot = delivery.get("delivery_execution_snapshot") or {}
    build = snapshot.get("build_identity") or {}
    if (
        not re.fullmatch(r"[0-9a-f]{40}", expected_product_sha)
        or build.get("product_revision") != expected_product_sha
        or build.get("product_worktree_clean") is not True
        or build.get("framework_dependency_status") != "ready"
        or snapshot.get("project_id") != project_id
        or not re.fullmatch(r"[0-9a-f]{64}", snapshot.get("snapshot_sha256", ""))
    ):
        raise CheckpointError("CHECKPOINT_FROZEN_BUILD_INVALID")
    bindings = snapshot.get("resolved_provider_bindings") or {}
    if not bindings or any(
        item.get("deployment", {}).get("adapter_id") not in {"codex.cli", "hermes.acp"}
        or any(
            marker in str(item.get("runtime_identity", "")).lower()
            for marker in ("deterministic", "simulated", "fake")
        )
        for item in bindings.values()
    ):
        raise CheckpointError("CHECKPOINT_LIVE_BINDING_REQUIRED")
    workspaces = snapshot.get("workspaces") or []
    if (
        {item.get("workcell_key") for item in workspaces} != {"design", "frontend", "backend", "qa"}
        or len(workspaces) != 4
        or any(item.get("adapter_type") != "external-git" for item in workspaces)
    ):
        raise CheckpointError("CHECKPOINT_FOUR_EXTERNAL_WORKSPACES_REQUIRED")
    identity = {
        "base_url": base_url,
        "project_id": project_id,
        "delivery_id": delivery_id,
        "execution_snapshot_sha256": snapshot["snapshot_sha256"],
        "build": _pick(
            build,
            "product_revision",
            "product_worktree_clean",
            "acwm_revision",
            "acwm_version",
            "framework_lock_sha256",
            "framework_dependency_status",
            "snapshot_sha256",
        ),
        **_pick(
            snapshot,
            "pipeline_revision_id",
            "pipeline_revision_sha256",
            "team_template_revision_id",
            "team_template_sha256",
        ),
        "providers": [
            {
                "slot": slot,
                "runtime_identity": item.get("runtime_identity"),
                "binding_fingerprint": item.get("binding", {}).get("binding_fingerprint"),
                **_pick(
                    item.get("deployment"),
                    "id",
                    "version",
                    "instance_id",
                    "instance_version",
                    "adapter_id",
                    "adapter_version",
                    "provider_id",
                    "provider_revision",
                    "provider_fingerprint",
                    "profile_id",
                    "profile_revision",
                    "profile_sha256",
                ),
            }
            for slot, item in sorted(bindings.items())
        ],
        "method": {
            **_pick(snapshot.get("method_snapshot"), "snapshot_id", "qualification_sha256"),
            "method_ids": sorted((snapshot.get("method_snapshot") or {}).get("method_entries", {})),
        },
        "workspaces": [
            {
                **_pick(
                    item,
                    "workcell_key",
                    "workspace_binding_id",
                    "adapter_type",
                    "base_revision",
                    "verification_sha256",
                ),
                "verification_profile": _pick(
                    item.get("verification_profile"), "profile_sha256", "qualification_sha256"
                ),
            }
            for item in workspaces
        ],
        "knowledge": [
            {
                "stage_path": stage,
                **_pick(item, "authorization_epoch_hash", "trust_class", "citation_ids"),
                "artifact_reference": _pick(item.get("artifact_reference"), "uri", "sha256"),
            }
            for stage, item in sorted(snapshot.get("knowledge_contexts", {}).items())
        ],
        "knowledge_policies": [
            {
                "stage_path": stage,
                **_pick(item, "retrieval_policy_revision_id", "acwm_artifact_contract_sha256"),
            }
            for stage, item in sorted(snapshot.get("knowledge_context_bindings", {}).items())
        ],
    }
    if previous is not None:
        if previous.get("schema_version") != SCHEMA or previous.get("identity") != identity:
            raise CheckpointError("CHECKPOINT_FROZEN_IDENTITY_CHANGED")
        if delivery["version"] < previous.get("delivery_version", 0):
            raise CheckpointError("CHECKPOINT_VERSION_REGRESSED")
    did, pid = quote(delivery_id, safe=""), quote(project_id, safe="")
    links = {
        "delivery_ui": f"{base_url}/projects/{pid}/deliveries/{did}",
        "delivery_api": f"{base_url}/v1/deliveries/{did}",
        "evidence_ui": f"{base_url}/projects/{pid}/evidence?delivery_id={did}",
        "evidence_api": f"{base_url}/v1/deliveries/{did}/evidence",
        "workcells_api": f"{base_url}/v1/deliveries/{did}/workcell-runs",
        "release_api": f"{base_url}/v1/releases/{did}",
        "release_health_api": f"{base_url}/v1/projects/{pid}/release-health",
        "knowledge_api": f"{base_url}/v1/deliveries/{did}/knowledge-context",
    }
    status = delivery["status"]
    gate = None
    if status in GATES:
        kind, field, action = GATES[status]
        record = delivery.get(field)
        if not record or record.get("decision") is not None:
            raise CheckpointError("CHECKPOINT_PENDING_GATE_MISSING")
        artifacts = []
        pointers = {
            "plan": ("/requirements", "/task"),
            "design": ("/workcell_candidates/design",),
            "release": ("/bundle",),
        }[kind]
        for pointer in pointers:
            source = release if kind == "release" else delivery
            value = source
            for part in pointer.strip("/").split("/"):
                value = value.get(part) if isinstance(value, dict) else None
            if value is None:
                raise CheckpointError("CHECKPOINT_GATE_ARTIFACT_MISSING")
            artifacts.append(
                {
                    "api_url": links["release_api" if kind == "release" else "delivery_api"],
                    "json_pointer": pointer,
                }
            )
        gate = {
            "kind": kind,
            **_pick(record, "gate_id", "subject_kind", "artifact_id", "subject_sha256", "revision"),
            "artifacts": artifacts,
            "human_ui_action": action,
        }
        if kind == "release" and record.get("subject_sha256") != release["bundle"].get(
            "bundle_sha256"
        ):
            raise CheckpointError("CHECKPOINT_RELEASE_SUBJECT_MISMATCH")
    if release.get("delivery_id") != delivery_id:
        raise CheckpointError("CHECKPOINT_RELEASE_SUBJECT_MISMATCH")
    manifest = release.get("manifest")
    if status == "completed" and (
        not manifest
        or manifest.get("delivery_id") != delivery_id
        or manifest.get("project_id") != project_id
        or manifest.get("status") != "active"
        or manifest.get("manifest_sha256") != delivery.get("release_manifest_v2_sha256")
    ):
        raise CheckpointError("CHECKPOINT_COMPLETION_EVIDENCE_MISSING")
    return {
        "schema_version": SCHEMA,
        "formal_release_acceptance": "not_evaluated",
        "observed_at": datetime.now(UTC).isoformat(),
        "identity": identity,
        "delivery_version": delivery["version"],
        "delivery_status": status,
        "observation": "awaiting_human_decision"
        if gate
        else (
            "completed_observed"
            if status == "completed"
            else "attention_required"
            if status in STOP_STATUSES
            else "running_observed"
        ),
        "error_code": delivery.get("error_code"),
        "gate": gate,
        "links": links,
        "evidence": [
            _pick(
                item,
                "id",
                "kind",
                "source_kind",
                "source_id",
                "producer_identity",
                "content_sha256",
                "status",
            )
            for item in evidence
        ],
        "workcells": [
            {
                **_pick(tree.get("workcell_run"), "id", "workcell_key", "stage_path", "status"),
                "attempts": [
                    _pick(item, "id", "agent_run_id", "phase", "status", "error_code")
                    for item in tree.get("attempts", [])
                ],
            }
            for tree in workcells
        ],
        "release": {
            "candidates": [
                _pick(
                    item,
                    "id",
                    "workcell_key",
                    "base_revision",
                    "candidate_revision",
                    "diff_sha256",
                    "verification_sha256",
                    "review_artifact_ids",
                    "evidence_sha256",
                )
                for item in release.get("candidates", [])
            ],
            "pull_requests": [
                _pick(
                    item,
                    "candidate_id",
                    "pull_request_id",
                    "url",
                    "head_candidate_sha",
                    "receipt_sha256",
                    "state",
                )
                for item in release.get("pull_requests", [])
            ],
            "apply_attempt": _pick(
                release.get("apply_attempt"), "status", "bundle_sha256", "error_code", "version"
            ),
            "remote_apply_receipts": [
                _pick(
                    item,
                    "workcell_key",
                    "candidate_id",
                    "before_revision",
                    "candidate_revision",
                    "after_revision",
                    "receipt_sha256",
                )
                for item in release.get("remote_apply_receipts", [])
            ],
            "manifest": _pick(manifest, "manifest_sha256", "bundle_sha256", "status"),
        },
    }


def write_checkpoint(path: Path, report: dict[str, Any], *, password: str) -> None:
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    escaped_password = json.dumps(password, ensure_ascii=False)[1:-1]
    if not password or password in payload or escaped_password in payload:
        raise CheckpointError("CHECKPOINT_SECRET_PERSISTENCE_BLOCKED")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def browser_request_allowed(method: str, url: str, base_url: str) -> bool:
    parsed, base = urlsplit(url), urlsplit(base_url)
    if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
        return False
    return method in {"GET", "HEAD"} or (method == "POST" and parsed.path == "/v1/auth/login")


def authenticate(page: Any, base_url: str, *, username: str, password: str) -> None:
    if not username or not password:
        raise CheckpointError("CHECKPOINT_SESSION_CREDENTIALS_REQUIRED")
    page.goto(base_url)
    heading = page.get_by_role("heading", name="登录 Agent-Team-OS")
    heading.wait_for(timeout=30_000)
    page.get_by_label("用户名").fill(username)
    page.get_by_label("密码").fill(password)
    page.get_by_role("button", name="登录控制平面").click()
    page.get_by_role("link", name="项目", exact=True).wait_for(timeout=30_000)
    if page.context.request.get(f"{base_url}/v1/auth/session").status != 200:
        raise CheckpointError("CHECKPOINT_AUTHENTICATION_FAILED")


def safe_screenshot(page: Any, path: Path, *, password: str, authenticated: bool) -> bool:
    if (
        not authenticated
        or not password
        or page.locator('input[type="password"]').count()
        or password in page.content()
        or password in page.inner_text("body")
    ):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=path, full_page=True)
    return True


def _get_json(page: Any, base_url: str, path: str) -> Any:
    response = page.context.request.get(f"{base_url}{path}")
    if response.status != 200:
        raise CheckpointError(f"CHECKPOINT_READ_FAILED_HTTP_{response.status}")
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--expected-product-sha", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=0, help="有限只读观察；默认采样后退出")
    args = parser.parse_args()
    username, password = (
        os.environ.get(key, "")
        for key in ("AGENT_TEAM_OS_TEST_USERNAME", "AGENT_TEAM_OS_TEST_PASSWORD")
    )
    try:
        if not username or not password:
            raise CheckpointError("CHECKPOINT_SESSION_CREDENTIALS_REQUIRED")
        base_url = validate_base_url(args.url)
        if args.wait_seconds < 0 or args.wait_seconds > 3600:
            raise CheckpointError("CHECKPOINT_WAIT_OUT_OF_RANGE")
        previous = json.loads(args.checkpoint.read_text()) if args.checkpoint.exists() else None
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            context = browser.new_context(
                viewport={"width": 1600, "height": 1000}, service_workers="block"
            )
            context.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if browser_request_allowed(route.request.method, route.request.url, base_url)
                    else route.abort()
                ),
            )
            page = context.new_page()
            try:
                authenticate(page, base_url, username=username, password=password)
                did, pid = quote(args.delivery_id, safe=""), quote(args.project_id, safe="")
                page.goto(f"{base_url}/projects/{pid}/deliveries/{did}")
                page.locator(".run-hero").wait_for(timeout=30_000)
                deadline = time.monotonic() + args.wait_seconds
                last_content = None
                while True:
                    delivery_path = f"/v1/deliveries/{did}"
                    report = project_checkpoint(
                        delivery=_get_json(page, base_url, delivery_path),
                        release=_get_json(page, base_url, f"/v1/releases/{did}"),
                        evidence=_get_json(page, base_url, f"{delivery_path}/evidence"),
                        workcells=_get_json(page, base_url, f"{delivery_path}/workcell-runs"),
                        base_url=base_url,
                        project_id=args.project_id,
                        delivery_id=args.delivery_id,
                        expected_product_sha=args.expected_product_sha,
                        previous=previous,
                    )
                    content = _hash(
                        {key: value for key, value in report.items() if key != "observed_at"}
                    )
                    if content != last_content:
                        write_checkpoint(args.checkpoint, report, password=password)
                        previous, last_content = report, content
                        print(
                            json.dumps(
                                {
                                    "observation": report["observation"],
                                    "gate": report["gate"],
                                    "links": report["links"],
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                    if args.screenshot is not None:
                        safe_screenshot(
                            page, args.screenshot, password=password, authenticated=True
                        )
                    if report["delivery_status"] in STOP_STATUSES or time.monotonic() >= deadline:
                        return (
                            0
                            if report["delivery_status"] == "completed"
                            else (20 if report["gate"] else 21)
                        )
                    page.wait_for_timeout(min(5000, max(1, (deadline - time.monotonic()) * 1000)))
            finally:
                browser.close()
    except CheckpointError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        # Playwright 异常可能携带 fill 参数；禁止输出原始异常/响应/Console 日志。
        print("CHECKPOINT_OBSERVATION_FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
