from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent_team_os.handoff_evidence import CoreBrowserRunReceipt
from agent_team_os.knowledge_context_contract import KNOWLEDGE_CONTEXT_STAGE_PATHS

_spec = importlib.util.spec_from_file_location(
    "browser_receipt_support", Path(__file__).parents[1] / "scripts/browser_receipt_support.py"
)
assert _spec is not None and _spec.loader is not None
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)

HASH = "a" * 64
PRODUCT_SHA = "b" * 40


@pytest.mark.parametrize("mismatch", [False, True])
def test_receipt_reads_actual_http_bytes_for_every_current_bundle_file(
    tmp_path: Path, monkeypatch, mismatch: bool
) -> None:
    dist = tmp_path / "console" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<script src='/assets/current.js'></script>")
    (dist / "assets" / "current.js").write_text("console.log('current')")
    requested = []

    def respond(request):
        requested.append(request.url.path)
        relative = "index.html" if request.url.path == "/" else request.url.path.lstrip("/")
        content = (dist / relative).read_bytes()
        if mismatch and relative.endswith(".js"):
            content = b"old bundle"
        return httpx.Response(200, content=content)

    original = httpx.Client
    monkeypatch.setattr(
        support.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    if mismatch:
        with pytest.raises(AssertionError, match="BROWSER_RECEIPT_HTTP_BUNDLE_MISMATCH"):
            support.verify_http_console_bundle(tmp_path, "http://127.0.0.1:8765")
    else:
        assert len(support.verify_http_console_bundle(tmp_path, "http://127.0.0.1:8765")) == 64
        assert set(requested) == {"/", "/assets/current.js"}


def _build(*, clean: bool = True, revision: str = PRODUCT_SHA) -> SimpleNamespace:
    payload = {
        "product_revision": revision,
        "product_worktree_clean": clean,
        "acwm_revision": "c" * 40,
        "acwm_version": "0.5.1",
        "framework_lock_sha256": HASH,
        "framework_dependency_status": "ready",
        "snapshot_sha256": HASH,
    }
    return SimpleNamespace(**payload, model_dump=lambda **kwargs: payload)


def _delivery() -> dict:
    return {
        "id": "delivery-1",
        "project_id": "project-1",
        "status": "completed",
        "planning_identity": "deterministic-test",
        "execution_identity": None,
        "evidence_identity": "deterministic-test",
        "delivery_execution_snapshot": {
            "build_identity": _build().model_dump(),
            "snapshot_sha256": HASH,
            "pipeline_revision_id": "agent-workcell-delivery:1",
            "pipeline_revision_sha256": HASH,
        },
    }


def test_dirty_browser_run_removes_only_requested_previous_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "current.json"
    history = tmp_path / "history.json"
    target.write_text("previous success")
    history.write_text("older evidence")
    monkeypatch.setattr(support, "snapshot_delivery_build_identity", lambda _: _build(clean=False))
    monkeypatch.setattr(support, "verify_http_console_bundle", lambda *args: pytest.fail("dirty"))

    with pytest.raises(AssertionError, match="BROWSER_RECEIPT_CLEAN_BUILD_REQUIRED"):
        support.begin_browser_receipt(tmp_path, "http://127.0.0.1:8765", target)

    assert not target.exists()
    assert history.read_text() == "older evidence"


@pytest.mark.parametrize("mismatch", ["frozen_build", "changed_build", "changed_bundle"])
def test_browser_receipt_rejects_changed_or_unrelated_evidence(
    tmp_path: Path,
    monkeypatch,
    mismatch: str,
) -> None:
    builds = [_build(), _build(revision="d" * 40) if mismatch == "changed_build" else _build()]
    bundles = [HASH, "e" * 64 if mismatch == "changed_bundle" else HASH]
    monkeypatch.setattr(support, "snapshot_delivery_build_identity", lambda _: builds.pop(0))
    monkeypatch.setattr(support, "verify_http_console_bundle", lambda *args: bundles.pop(0))
    target = tmp_path / "current.json"
    run = support.begin_browser_receipt(tmp_path, "http://127.0.0.1:8765", target)
    delivery = _delivery()
    if mismatch == "frozen_build":
        delivery["delivery_execution_snapshot"]["build_identity"]["product_revision"] = "f" * 40

    with pytest.raises(AssertionError, match="BROWSER_RECEIPT_IDENTITY_CHANGED"):
        support.complete_browser_receipt(run, delivery, knowledge_scope=None)
    assert not target.exists()


def test_base_browser_receipt_preserves_actual_identity_and_has_no_r2_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(support, "snapshot_delivery_build_identity", lambda _: _build())
    monkeypatch.setattr(support, "verify_http_console_bundle", lambda *args: HASH)
    target = tmp_path / "current.json"
    run = support.begin_browser_receipt(tmp_path, "http://127.0.0.1:8765", target)
    support.complete_browser_receipt(run, _delivery(), knowledge_scope=None)

    payload = json.loads(target.read_text())
    assert payload["schema_version"] == "core-browser-run-receipt-v1"
    assert payload["scenario"] == "agent-workcell-delivery-v1"
    assert payload["knowledge_scope"] is None
    assert payload["evidence_identity"] == "deterministic-test"
    assert payload["planning_identity"] == "deterministic-test"
    assert payload["execution_identity"] is None
    assert payload["runtime_identity"] == "deterministic-model-boundary"
    assert payload["console_bundle_sha256"] == HASH
    assert payload["status"] == "passed"
    assert (payload["fail"], payload["warn"], payload["skipped"]) == (0, 0, 0)
    assert "KNOWLEDGE_R2_CONTEXTS_VERIFIED" not in payload["checks_passed"]
    CoreBrowserRunReceipt.model_validate(payload)


def test_r2_browser_receipt_keeps_exact_observed_context_and_qa_identity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(support, "snapshot_delivery_build_identity", lambda _: _build())
    monkeypatch.setattr(support, "verify_http_console_bundle", lambda *args: HASH)
    target = tmp_path / "r2.json"
    run = support.begin_browser_receipt(tmp_path, "http://127.0.0.1:8765", target)
    paths = sorted(KNOWLEDGE_CONTEXT_STAGE_PATHS)
    runs = {path: f"run-{index}" for index, path in enumerate(paths) if "/" in path}
    scope = {
        "required_stage_paths": paths,
        "contexts": [
            {
                "stage_path": path,
                "artifact_sha256": f"{index + 1:064x}",
                "citation_ids": [f"citation-{index}"],
                "authorization_epoch_hash": HASH,
            }
            for index, path in enumerate(paths)
        ],
        "context_count": 7,
        "workcell_run_ids": runs,
        "qa_preparation_run_id": runs["qa-preparation-repair/qa-preparation"],
    }
    support.complete_browser_receipt(run, _delivery(), knowledge_scope=scope)

    receipt = CoreBrowserRunReceipt.model_validate_json(target.read_text())
    assert receipt.scenario == "agent-workcell-knowledge-delivery-v1"
    assert receipt.knowledge_scope.model_dump(mode="json") == scope
    assert "QA_PREPARATION_VERIFIED" in receipt.checks_passed


@pytest.mark.parametrize("script", ["browser_workcell_e2e", "browser_feishu_knowledge_e2e"])
def test_driver_failure_never_keeps_requested_passed_receipt(
    tmp_path: Path, monkeypatch, script: str
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    module = __import__(script)
    target = tmp_path / "receipt.json"
    target.write_text("previous success")
    arguments = [script, "--receipt", str(target), "--data-dir", str(tmp_path)]
    if script == "browser_feishu_knowledge_e2e":
        arguments.append("--gate-c")
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(module, "begin_browser_receipt", lambda *args: object())

    def failed(_):
        raise AssertionError("actual assertion failed")

    monkeypatch.setattr(module, "_run_browser", failed)
    monkeypatch.setattr(module, "complete_browser_receipt", lambda *args, **kwargs: pytest.fail())
    with pytest.raises(AssertionError, match="actual assertion failed"):
        module.main()
    assert not target.exists()


def test_r2_receipt_rejects_auth_state_export_before_browser(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    import browser_feishu_knowledge_e2e as script

    target = tmp_path / "receipt.json"
    target.write_text("previous success")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "browser_feishu_knowledge_e2e",
            "--receipt",
            str(target),
            "--gate-c",
            "--data-dir",
            str(tmp_path),
            "--state",
            str(tmp_path / "cookies.json"),
        ],
    )
    monkeypatch.setattr(script, "_run_browser", lambda *args: pytest.fail("must not log in"))
    with pytest.raises(AssertionError, match="R2_BROWSER_RECEIPT_FORBIDS_AUTH_STATE_EXPORT"):
        script.main()
    assert not target.exists()
