from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_team_os.delivery import DeliveryWorkspaceSnapshot
from agent_team_os.infrastructure.git import ExternalCandidateEvidence
from agent_team_os.infrastructure.verification import tool_environment
from agent_team_os.infrastructure.verification.command_toolchain import LocalVerificationToolchain
from agent_team_os.modules.workcells.execution_domain import WorkcellWorkspaceSnapshot
from agent_team_os.modules.workcells.stage_driver import CommandWorkcellMachineVerifier
from agent_team_os.modules.workcells.verification_application import (
    VerificationProfileCatalog,
    validate_test_result,
)
from agent_team_os.modules.workcells.verification_evidence import passed_counts
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json


def test_product_profiles_refuse_zero_tests_and_do_not_publish_unqualified_pnpm() -> None:
    catalog = VerificationProfileCatalog()
    assert {"python-unittest-v1", "node-native-test-v1"}.issubset(
        item.id for item in catalog.list()
    )
    assert "pnpm-project-check-v1" not in {item.id for item in catalog.list()}
    assert not validate_test_result("python-unittest-v1", "Ran 0 tests in 0.000s\n\nOK\n")
    assert validate_test_result("python-unittest-v1", "Ran 2 tests in 0.001s\n\nOK\n")
    assert not validate_test_result(
        "python-unittest-v1", "OK\nRan 1 test in 0.001s\n\nOK (skipped=1)\n"
    )
    assert not validate_test_result("node-native-test-v1", "# tests 0\n# pass 0\n# fail 0\n")
    assert validate_test_result(
        "node-native-test-v1",
        "# tests 2\n# pass 2\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n",
    )


def test_frontend_profile_freezes_testing_library_dom_capability() -> None:
    profile = VerificationProfileCatalog().get("frontend-ts-vite-vitest-v1")
    assert profile.revision == 2
    assert "Testing Library DOM" in profile.name
    assert tool_environment.NODE_PACKAGES["@testing-library/dom"] == "10.4.1"


def test_prepare_node_environment_links_exact_nested_scoped_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    direct = source / "alpha"
    direct.mkdir()
    (direct / "package.json").write_text(json.dumps({"name": "alpha", "version": "1.0.0"}))
    nested = source / ".pnpm/pkg@2.0.0/node_modules/@scope/pkg"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text(
        json.dumps({"name": "@scope/pkg", "version": "2.0.0"})
    )
    alias = source / ".pnpm/consumer@1.0.0/node_modules/@scope/pkg"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(nested)
    monkeypatch.setattr(
        tool_environment,
        "NODE_PACKAGES",
        {"alpha": "1.0.0", "@scope/pkg": "2.0.0"},
    )

    node_modules = tool_environment.prepare_node_environment(source, tmp_path / "target")

    linked = node_modules / "@scope/pkg"
    assert linked.is_symlink()
    assert linked.resolve() == (node_modules / nested.relative_to(source)).resolve()
    assert json.loads((linked / "package.json").read_text())["version"] == "2.0.0"
    receipt = json.loads((node_modules.parent / "environment.json").read_text())
    assert receipt["packages"] == {"alpha": "1.0.0", "@scope/pkg": "2.0.0"}


def test_qa_result_contract_accepts_versioned_acceptance_case_names() -> None:
    case_ids = (
        "test_health_e2e.HealthE2E.test_qa_001_all_states_schema_version_and_accessible_labels",
        "test_health_e2e.HealthE2E.test_qa_002_get_head_cache_control_status_and_empty_body",
        "test_health_e2e.HealthE2E.test_qa_003_invalid_status_preserves_error_semantics",
        "test_health_e2e.HealthE2E.test_qa_004_request_and_schema_failure_degrade_accessibly",
        "test_health_e2e.HealthE2E.test_repository_identity_matches_bundle",
    )
    assert passed_counts("qa", (5, 5, 0, 0, case_ids))
    assert not passed_counts("qa", (4, 4, 0, 0, case_ids[:3] + case_ids[4:]))
    lookalike = (case_ids[0].replace("test_qa_001_", "test_qa_0010_"), *case_ids[1:])
    assert not passed_counts("qa", (5, 5, 0, 0, lookalike))


def test_qa_result_contract_keeps_legacy_health_case_compatibility() -> None:
    case_ids = tuple(
        "test_health_e2e.HealthE2E." + name
        for name in ("test_ok", "test_degraded", "test_unavailable", "test_invalid_response")
    )
    assert passed_counts("qa", (4, 4, 0, 0, case_ids))


def test_qualification_binds_product_profile_and_rejects_self_consistent_forgery() -> None:
    catalog = VerificationProfileCatalog()
    toolchain = LocalVerificationToolchain()
    snapshot = catalog.qualify("python-unittest-v1", toolchain)
    catalog.validate(snapshot, toolchain)
    forged = snapshot.model_copy(
        update={
            "profile": snapshot.profile.model_copy(update={"commands": (("python", "-c", "pass"),)})
        }
    )
    forged_hash = sha256_json(forged.profile.model_dump(mode="json"))
    forged = forged.model_copy(
        update={
            "profile_sha256": forged_hash,
            "qualification_sha256": sha256_json(
                {
                    "profile_sha256": forged_hash,
                    "tools": [tool.model_dump(mode="json") for tool in forged.tools],
                }
            ),
        }
    )
    with pytest.raises(ProductError, match="产品"):
        catalog.validate(forged, toolchain)


def test_qualification_runs_version_probe_without_repository_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "do-not-run"
    (tmp_path / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('bad')")
    monkeypatch.chdir(tmp_path)
    snapshot = VerificationProfileCatalog().qualify(
        "python-unittest-v1", LocalVerificationToolchain()
    )
    assert snapshot.tools[0].name == "python"
    assert snapshot.tools[0].version.startswith("Python 3.")
    assert not marker.exists()


def test_current_tool_drift_does_not_invalidate_historical_qualification() -> None:
    catalog = VerificationProfileCatalog()
    frozen = catalog.qualify("python-unittest-v1", LocalVerificationToolchain())

    class ChangedToolchain:
        def inspect(self, _name: str):
            return frozen.tools[0].model_copy(update={"executable_sha256": "0" * 64})

    catalog.validate_frozen(frozen)
    with pytest.raises(ProductError) as changed:
        catalog.validate(frozen, ChangedToolchain())
    assert changed.value.code == "WORKCELL_VERIFICATION_QUALIFICATION_CHANGED"


def test_legacy_workspace_snapshot_preserves_hash_input_and_missing_profile_fails_closed() -> None:
    old = {
        "workspace_binding_id": "workspace",
        "kind": "git_repository_v1",
        "adapter_type": "external-git",
        "repository_uri": "https://github.com/org/repo.git",
        "base_revision": "1" * 40,
        "verification_sha256": "2" * 64,
    }
    assert WorkcellWorkspaceSnapshot.model_validate(old).model_dump(mode="json") == old
    delivery_old = {"workcell_key": "backend", **old}
    assert (
        DeliveryWorkspaceSnapshot.model_validate(delivery_old).model_dump(mode="json")
        == delivery_old
    )
    for snapshot_model in (DeliveryWorkspaceSnapshot, WorkcellWorkspaceSnapshot):
        schema = snapshot_model.model_json_schema(mode="serialization")
        assert schema["properties"]["repository_uri"]["type"] == "string"
        assert schema["properties"]["base_revision"]["type"] == "string"
        assert "verification_profile" in schema["properties"]
    with pytest.raises(ProductError) as missing:
        VerificationProfileCatalog().validate(None, LocalVerificationToolchain())
    assert missing.value.code == "WORKCELL_VERIFICATION_PROFILE_REQUIRED"


@pytest.mark.parametrize("profile_id", ["python-unittest-v1", "node-native-test-v1"])
def test_real_profile_commands_reject_empty_suite_and_accept_actual_tests(
    tmp_path: Path,
    profile_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = VerificationProfileCatalog().qualify(profile_id, LocalVerificationToolchain())
    candidate = ExternalCandidateEvidence(
        base_revision="1" * 40,
        candidate_revision="2" * 40,
        diff_sha256="3" * 64,
        candidate_branch="candidate",
        changed_files=("tests/test_one.py",),
    )
    verifier = CommandWorkcellMachineVerifier()
    tests = tmp_path / "tests"
    tests.mkdir()
    empty = asyncio.run(
        verifier.verify(
            workcell_key="backend", workspace=tmp_path, candidate=candidate, profile=profile
        )
    )
    assert empty.status == "failed"
    assert empty.report["commands"][0]["exit_code"] in {0, 5}
    assert empty.report["commands"][0]["result_contract_passed"] is False
    monkeypatch.setenv("AGENT_TEAM_OS_GITHUB_TOKEN", "must-not-reach-test")
    if profile_id == "python-unittest-v1":
        (tests / "test_one.py").write_text(
            "import os, unittest\nclass One(unittest.TestCase):\n def test_one(self):\n"
            "  self.assertIsNone(os.getenv('AGENT_TEAM_OS_GITHUB_TOKEN'))\n"
        )
    else:
        (tests / "one.test.js").write_text(
            "const test = require('node:test');\nconst assert = require('node:assert/strict');\n"
            "test('one', () => assert.equal(process.env.AGENT_TEAM_OS_GITHUB_TOKEN, undefined));\n"
        )
    outcome = asyncio.run(
        verifier.verify(
            workcell_key="backend", workspace=tmp_path, candidate=candidate, profile=profile
        )
    )
    assert outcome.status == "passed", outcome.report
    assert outcome.report["profile_sha256"] == profile.profile_sha256
    assert outcome.report["qualification_sha256"] == profile.qualification_sha256
    assert not list(tmp_path.rglob("*.pyc"))


def test_profile_timeout_is_failed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    short_profile = (
        VerificationProfileCatalog()
        .get("python-unittest-v1")
        .model_copy(update={"timeout_seconds": 1})
    )
    monkeypatch.setattr(VerificationProfileCatalog, "list", lambda self: (short_profile,))
    profile = VerificationProfileCatalog().qualify(
        "python-unittest-v1", LocalVerificationToolchain()
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_slow.py").write_text(
        "import unittest,time\nfrom pathlib import Path\n"
        "class Slow(unittest.TestCase):\n def test_slow(self):\n"
        "  time.sleep(2)\n  Path('after_timeout').write_text('unexpected')\n"
    )
    outcome = asyncio.run(
        CommandWorkcellMachineVerifier().verify(
            workcell_key="backend",
            workspace=tmp_path,
            candidate=ExternalCandidateEvidence(
                base_revision="1" * 40,
                candidate_revision="2" * 40,
                diff_sha256="3" * 64,
                candidate_branch="candidate",
                changed_files=("tests/test_one.py",),
            ),
            profile=profile,
        )
    )
    assert outcome.status == "failed"
    assert outcome.report["commands"][0]["error_code"] == "WORKCELL_MACHINE_VERIFICATION_TIMEOUT"
    asyncio.run(asyncio.sleep(1.2))
    assert not (tmp_path / "after_timeout").exists()


def test_python_runner_ignores_candidate_module_shadowing_and_keeps_project_imports(
    tmp_path: Path,
) -> None:
    profile = VerificationProfileCatalog().qualify(
        "python-unittest-v1", LocalVerificationToolchain()
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "unittest.py").write_text("print('Ran 1 test in 0.000s\\n\\nOK')\n")
    (tmp_path / "sitecustomize.py").write_text("raise RuntimeError('candidate startup hook')\n")

    def verify():
        return asyncio.run(
            CommandWorkcellMachineVerifier().verify(
                workcell_key="backend",
                workspace=tmp_path,
                profile=profile,
                candidate=_candidate(),
            )
        )

    assert verify().status == "failed"
    (tmp_path / "src").mkdir()
    (tmp_path / "src/module.py").write_text("VALUE = 42\n")
    (tmp_path / "tests/test_imports.py").write_text(
        "import unittest\nfrom src.module import VALUE\n"
        "class Imports(unittest.TestCase):\n def test_value(self):\n"
        "  self.assertEqual(VALUE, 42)\n"
    )
    assert verify().status == "passed"
    assert not list(tmp_path.rglob("*.pyc"))


def test_cancelling_verification_stops_process_group_before_returning(tmp_path: Path) -> None:
    profile = VerificationProfileCatalog().qualify(
        "python-unittest-v1", LocalVerificationToolchain()
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_slow.py").write_text(
        "import unittest,time,subprocess,sys,signal\nfrom pathlib import Path\n"
        "class Slow(unittest.TestCase):\n def test_slow(self):\n"
        "  signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "  subprocess.Popen([sys.executable, '-c', "
        '"import time;from pathlib import Path;time.sleep(1.5);'
        "Path('child_after_cancel').write_text('unexpected')\"])\n"
        "  Path('started').write_text('ready')\n"
        "  time.sleep(1.5)\n  Path('after_cancel').write_text('unexpected')\n"
    )

    async def cancel_and_observe() -> None:
        task = asyncio.create_task(
            CommandWorkcellMachineVerifier().verify(
                workcell_key="backend",
                workspace=tmp_path,
                profile=profile,
                candidate=_candidate(),
            )
        )
        for _ in range(200):
            if (tmp_path / "started").exists():
                break
            await asyncio.sleep(0.01)
        assert (tmp_path / "started").exists()
        task.cancel()
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(1.7)
        assert not (tmp_path / "after_cancel").exists()
        assert not (tmp_path / "child_after_cancel").exists()

    asyncio.run(cancel_and_observe())


def _candidate() -> ExternalCandidateEvidence:
    return ExternalCandidateEvidence(
        base_revision="1" * 40,
        candidate_revision="2" * 40,
        diff_sha256="3" * 64,
        candidate_branch="candidate",
        changed_files=("tests/test_one.py",),
    )


def test_successful_verification_reaps_detached_output_child_before_returning(
    tmp_path: Path,
) -> None:
    profile = VerificationProfileCatalog().qualify(
        "python-unittest-v1", LocalVerificationToolchain()
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_child.py").write_text(
        "import unittest,subprocess,sys\n"
        "class Child(unittest.TestCase):\n def test_spawn(self):\n"
        "  subprocess.Popen([sys.executable, '-c', "
        '"import time;from pathlib import Path;time.sleep(0.7);'
        "Path('orphan_after_success').write_text('unexpected')\"], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    )
    outcome = asyncio.run(
        CommandWorkcellMachineVerifier().verify(
            workcell_key="backend", workspace=tmp_path, profile=profile, candidate=_candidate()
        )
    )
    assert outcome.status == "passed", outcome.report
    asyncio.run(asyncio.sleep(0.9))
    assert not (tmp_path / "orphan_after_success").exists()
