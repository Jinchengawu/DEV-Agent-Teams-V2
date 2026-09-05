from __future__ import annotations

import json
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_spec = spec_from_file_location(
    "live_checkpoint", Path(__file__).parents[1] / "scripts/browser_live_delivery_checkpoint.py"
)
assert _spec is not None and _spec.loader is not None
checkpoint = module_from_spec(_spec)
_spec.loader.exec_module(checkpoint)
CheckpointError = checkpoint.CheckpointError
project_checkpoint = checkpoint.project_checkpoint

PRODUCT_SHA = "1" * 40
HASH = "2" * 64
BASE_URL = "http://127.0.0.1:8765"


def _delivery(status: str = "awaiting_plan_decision") -> dict:
    return {
        "id": "delivery-1",
        "project_id": "project-1",
        "status": status,
        "version": 2,
        "planning_identity": "codex-cli",
        "execution_identity": None,
        "requirements": {"summary": "真实需求", "acceptance_criteria": []},
        "task": {"title": "任务合同"},
        "plan_gate": {
            "gate_id": "plan-1",
            "subject_kind": "plan",
            "artifact_id": "task-1",
            "subject_sha256": HASH,
            "revision": 1,
            "decision": None,
        },
        "delivery_execution_snapshot": {
            "snapshot_sha256": HASH,
            "project_id": "project-1",
            "pipeline_revision_id": "live-r2:2",
            "pipeline_revision_sha256": HASH,
            "team_template_revision_id": "team:1",
            "team_template_sha256": HASH,
            "build_identity": {
                "product_revision": PRODUCT_SHA,
                "product_worktree_clean": True,
                "acwm_revision": "3" * 40,
                "acwm_version": "0.5.1",
                "framework_lock_sha256": HASH,
                "framework_dependency_status": "ready",
                "snapshot_sha256": HASH,
            },
            "resolved_provider_bindings": {
                "planning": {
                    "runtime_identity": "codex-cli",
                    "binding": {"binding_fingerprint": HASH},
                    "deployment": {
                        "id": "codex",
                        "adapter_id": "codex.cli",
                        "provider_id": "codex-cli-provider",
                        "provider_fingerprint": HASH,
                    },
                },
            },
            "method_snapshot": {"snapshot_id": HASH, "qualification_sha256": HASH},
            "workspaces": [
                {
                    "workcell_key": key,
                    "workspace_binding_id": key,
                    "adapter_type": "external-git",
                    "base_revision": "4" * 40,
                    "verification_sha256": HASH,
                }
                for key in ("design", "frontend", "backend", "qa")
            ],
            "knowledge_context_bindings": {},
            "knowledge_contexts": {},
        },
    }


def _project(delivery: dict, *, previous: dict | None = None, release: dict | None = None) -> dict:
    return project_checkpoint(
        delivery=delivery,
        release=release or {"delivery_id": "delivery-1"},
        evidence=[],
        workcells=[],
        base_url=BASE_URL,
        project_id="project-1",
        delivery_id="delivery-1",
        expected_product_sha=PRODUCT_SHA,
        previous=previous,
    )


def test_plan_checkpoint_exposes_exact_reviewable_subject_without_claiming_acceptance() -> None:
    report = _project(_delivery())

    assert report["observation"] == "awaiting_human_decision"
    assert report["gate"]["subject_sha256"] == HASH
    assert report["gate"]["artifact_id"] == "task-1"
    assert report["gate"]["artifacts"][1]["json_pointer"] == "/task"
    assert report["links"]["delivery_ui"].endswith("/projects/project-1/deliveries/delivery-1")
    assert report["formal_release_acceptance"] == "not_evaluated"
    assert "requirements" not in report


@pytest.mark.parametrize("mutation", ["revision", "dirty", "deterministic", "project"])
def test_checkpoint_rejects_wrong_or_non_live_frozen_identity(mutation: str) -> None:
    delivery = _delivery()
    snapshot = delivery["delivery_execution_snapshot"]
    if mutation == "revision":
        snapshot["build_identity"]["product_revision"] = "5" * 40
    elif mutation == "dirty":
        snapshot["build_identity"]["product_worktree_clean"] = False
    elif mutation == "deterministic":
        snapshot["resolved_provider_bindings"]["planning"]["deployment"]["adapter_id"] = "fake"
    else:
        delivery["project_id"] = "other-project"

    with pytest.raises(CheckpointError):
        _project(delivery)


def test_replay_requires_same_frozen_identity_and_monotonic_delivery_version() -> None:
    previous = _project(_delivery())
    changed = deepcopy(_delivery())
    changed["delivery_execution_snapshot"]["snapshot_sha256"] = "6" * 64
    with pytest.raises(CheckpointError, match="FROZEN_IDENTITY_CHANGED"):
        _project(changed, previous=previous)
    changed = _delivery()
    changed["version"] = 1
    with pytest.raises(CheckpointError, match="VERSION_REGRESSED"):
        _project(changed, previous=previous)


def test_gate_progression_requires_actual_design_and_release_artifacts() -> None:
    delivery = _delivery("awaiting_design_decision")
    delivery["design_gate"] = {**delivery["plan_gate"], "artifact_id": "design-candidate"}
    with pytest.raises(CheckpointError, match="GATE_ARTIFACT_MISSING"):
        _project(delivery)
    delivery["workcell_candidates"] = {"design": {"candidate_id": "design-candidate"}}
    assert _project(delivery)["gate"]["artifacts"][0]["json_pointer"] == (
        "/workcell_candidates/design"
    )

    delivery["status"] = "awaiting_candidate_decision"
    delivery["candidate_gate"] = {**delivery["plan_gate"], "artifact_id": "bundle"}
    release = {"delivery_id": "delivery-1", "bundle": {"bundle_sha256": HASH}}
    report = _project(delivery, release=release)
    assert report["gate"]["kind"] == "release"
    assert report["gate"]["artifacts"][0]["json_pointer"] == "/bundle"
    release["bundle"]["bundle_sha256"] = "7" * 64
    with pytest.raises(CheckpointError, match="RELEASE_SUBJECT_MISMATCH"):
        _project(delivery, release=release)


def test_completed_requires_same_active_manifest_but_does_not_claim_live_passed() -> None:
    delivery = _delivery("completed")
    delivery["release_manifest_v2_sha256"] = HASH
    with pytest.raises(CheckpointError, match="COMPLETION_EVIDENCE_MISSING"):
        _project(delivery)
    release = {
        "delivery_id": "delivery-1",
        "manifest": {
            "project_id": "project-1",
            "delivery_id": "delivery-1",
            "status": "active",
            "manifest_sha256": HASH,
            "bundle_sha256": HASH,
        },
    }
    report = _project(delivery, release=release)
    assert report["observation"] == "completed_observed"
    assert report["formal_release_acceptance"] == "not_evaluated"
    assert report["release"]["manifest"]["manifest_sha256"] == HASH


@pytest.mark.parametrize("password", ["private-value", 'quote"and\\slash'])
def test_checkpoint_never_persists_password_even_in_allowlisted_api_field(
    tmp_path: Path,
    password: str,
) -> None:
    report = _project(_delivery())
    target = tmp_path / "checkpoint.json"
    checkpoint.write_checkpoint(target, report, password=password)
    previous_bytes = target.read_bytes()
    report["error_code"] = password
    with pytest.raises(CheckpointError, match="SECRET_PERSISTENCE_BLOCKED"):
        checkpoint.write_checkpoint(target, report, password=password)
    assert target.read_bytes() == previous_bytes


def test_projection_excludes_bodies_provider_secrets_and_session_state(tmp_path: Path) -> None:
    secret = "session-sensitive-value"
    delivery = _delivery()
    delivery["task"]["instructions"] = secret
    binding = delivery["delivery_execution_snapshot"]["resolved_provider_bindings"]["planning"]
    binding["deployment"]["policy_snapshot"] = {"credential_reference": secret}
    report = _project(delivery)
    target = tmp_path / "checkpoint.json"
    checkpoint.write_checkpoint(target, report, password=secret)
    assert secret not in target.read_text()
    assert not any(
        key in target.read_text() for key in ("cookies", "storage_state", "instructions")
    )
    assert json.loads(target.read_text())["gate"]["subject_sha256"] == HASH


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/deliveries"),
        ("POST", "/v1/deliveries/delivery-1/plan-decision"),
        ("POST", "/v1/deliveries/delivery-1/design-decision"),
        ("POST", "/v1/deliveries/delivery-1/candidate-decision"),
        ("POST", "/v1/releases/delivery-1/resume-forward"),
        ("POST", "/v1/deliveries/delivery-1/cancel"),
        ("POST", "/v1/auth/bootstrap"),
        ("DELETE", "/v1/deliveries/delivery-1"),
    ],
)
def test_observer_browser_cannot_mutate_product(method: str, path: str) -> None:
    assert not checkpoint.browser_request_allowed(method, BASE_URL + path, BASE_URL)
    assert checkpoint.browser_request_allowed("GET", BASE_URL + path, BASE_URL)
    assert checkpoint.browser_request_allowed("POST", BASE_URL + "/v1/auth/login", BASE_URL)
    assert not checkpoint.browser_request_allowed("GET", "https://other.example/", BASE_URL)


@pytest.mark.parametrize(
    "authenticated,has_input,content",
    [
        (False, False, "交付页面"),
        (True, True, "登录页面"),
        (True, False, "session-password"),
    ],
)
def test_screenshot_refuses_login_or_password_page(
    tmp_path: Path,
    authenticated: bool,
    has_input: bool,
    content: str,
) -> None:
    class Page:
        def locator(self, selector: str) -> Page:
            return self

        def count(self) -> int:
            return int(has_input)

        def content(self) -> str:
            return content

        def screenshot(self, **kwargs: object) -> None:
            pytest.fail("包含登录信息的页面不应截图")

    assert (
        checkpoint.safe_screenshot(
            Page(),
            tmp_path / "screen.png",
            password="session-password",
            authenticated=authenticated,
        )
        is False
    )
    assert not list(tmp_path.iterdir())


def test_screenshot_blocks_html_escaped_password_visible_on_product_page(tmp_path: Path) -> None:
    class Page:
        def locator(self, selector: str) -> Page:
            return self

        def count(self) -> int:
            return 0

        def content(self) -> str:
            return "<body>&lt;private-password&gt;</body>"

        def inner_text(self, selector: str) -> str:
            return "<private-password>"

        def screenshot(self, **kwargs: object) -> None:
            pytest.fail("HTML 转义不能绕过截图的密码阻断")

    assert checkpoint.safe_screenshot(
        Page(), tmp_path / "screen.png", password="<private-password>", authenticated=True
    ) is False
