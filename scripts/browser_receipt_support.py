"""浏览器原生收据的干净 Revision 与实际静态资源边界。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from agent_team_os.handoff_evidence import BROWSER_BASE_CHECKS, BROWSER_R2_CHECKS
from agent_team_os.readiness import snapshot_delivery_build_identity
from agent_team_os.shared.hashes import sha256_json


class BrowserReceiptRun:
    def __init__(
        self,
        root: Path,
        url: str,
        output: Path,
        build: Any,
        bundle_sha256: str,
        started_at: datetime,
    ) -> None:
        self.root = root
        self.url = url
        self.output = output
        self.build = build
        self.bundle_sha256 = bundle_sha256
        self.started_at = started_at


def discard_browser_receipt(output: Path | None) -> None:
    """只清除本次显式指定的输出，不清理历史证据目录。"""
    if output is not None:
        output.unlink(missing_ok=True)


def verify_http_console_bundle(root: Path, url: str) -> str:
    dist = (root / "console" / "dist").resolve()
    assert (dist / "index.html").is_file(), "BROWSER_RECEIPT_CONSOLE_BUILD_MISSING"
    fingerprints = {}
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        for asset in sorted(dist.rglob("*")):
            if not asset.is_file():
                continue
            assert asset.resolve().is_relative_to(dist), "BROWSER_RECEIPT_ASSET_OUTSIDE_DIST"
            relative = asset.relative_to(dist).as_posix()
            route = "/" if relative == "index.html" else "/" + quote(relative, safe="/")
            response = client.get(url.rstrip("/") + route)
            expected = asset.read_bytes()
            assert response.status_code == 200 and response.content == expected, (
                "BROWSER_RECEIPT_HTTP_BUNDLE_MISMATCH",
                relative,
                response.status_code,
            )
            fingerprints[relative] = hashlib.sha256(expected).hexdigest()
    assert any(path.endswith(".js") for path in fingerprints), "BROWSER_RECEIPT_ASSETS_MISSING"
    return str(sha256_json(fingerprints))


def begin_browser_receipt(root: Path, url: str, output: Path | None) -> BrowserReceiptRun | None:
    discard_browser_receipt(output)
    if output is None:
        return None
    started_at = datetime.now(UTC)
    build = snapshot_delivery_build_identity(root)
    assert build.product_worktree_clean and build.framework_dependency_status == "ready", (
        "BROWSER_RECEIPT_CLEAN_BUILD_REQUIRED"
    )
    bundle = verify_http_console_bundle(root, url)
    return BrowserReceiptRun(root, url, output, build, bundle, started_at)


def complete_browser_receipt(
    run: BrowserReceiptRun | None,
    delivery: dict[str, Any],
    *,
    knowledge_scope: dict[str, Any] | None,
) -> None:
    """只能在驱动全部用户闭环、API、Console 与资源断言通过后调用。"""
    if run is None:
        return
    from agent_team_os.handoff_evidence import CoreBrowserRunReceipt, write_core_browser_receipt

    current = snapshot_delivery_build_identity(run.root)
    snapshot = delivery["delivery_execution_snapshot"]
    initial = run.build.model_dump(mode="json")
    assert (
        current.product_worktree_clean
        and current.framework_dependency_status == "ready"
        and current.model_dump(mode="json") == initial == snapshot["build_identity"]
        and verify_http_console_bundle(run.root, run.url) == run.bundle_sha256
    ), "BROWSER_RECEIPT_IDENTITY_CHANGED"
    assert delivery["status"] == "completed", "BROWSER_RECEIPT_DELIVERY_INCOMPLETE"
    assert delivery["evidence_identity"] == "deterministic-test", (
        "BROWSER_RECEIPT_DETERMINISTIC_IDENTITY_REQUIRED"
    )
    r2 = knowledge_scope is not None
    receipt = CoreBrowserRunReceipt.create(
        schema_version="core-browser-run-receipt-v1",
        kind="browser",
        scenario="agent-workcell-knowledge-delivery-v1" if r2 else "agent-workcell-delivery-v1",
        status="passed",
        fail=0,
        warn=0,
        skipped=0,
        product_revision=current.product_revision,
        product_worktree_clean=True,
        acwm_revision=current.acwm_revision,
        project_id=delivery["project_id"],
        delivery_id=delivery["id"],
        pipeline_revision_id=snapshot["pipeline_revision_id"],
        pipeline_revision_sha256=snapshot["pipeline_revision_sha256"],
        build_identity_sha256=current.snapshot_sha256,
        execution_snapshot_sha256=snapshot["snapshot_sha256"],
        console_bundle_sha256=run.bundle_sha256,
        planning_identity=delivery["planning_identity"],
        execution_identity=delivery["execution_identity"],
        evidence_identity=delivery["evidence_identity"],
        # 两个驱动先逐一断言实际 Workcell Agent 的 Runtime 身份。
        runtime_identity="deterministic-model-boundary",
        started_at=run.started_at,
        completed_at=datetime.now(UTC),
        checks_passed=BROWSER_BASE_CHECKS + (BROWSER_R2_CHECKS if r2 else ()),
        knowledge_scope=knowledge_scope,
    )
    write_core_browser_receipt(run.output, receipt)
