"""B0 CI 静态 Bundle 审计的本地公共入口负例。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import stat
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from agent_team_os.delivery_bundle import PRODUCT_VERSION, build_delivery_bundle

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/audit_ci_bundle_artifact.py"
SPEC = importlib.util.spec_from_file_location("audit_ci_bundle_artifact", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)

FAKE_SECRET = "sk-proj-" + "A" * 32


def test_ci_uses_same_run_pytest_receipt_for_static_bundle_audit() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["quality"]["steps"]
    named = {step.get("name"): (index, step) for index, step in enumerate(steps)}

    pytest_index, pytest_step = named["Pytest"]
    build_index, _ = named["Build Python package"]
    console_index, _ = named["Build console"]
    audit_index, audit_step = named["Audit CI static delivery bundle"]
    junit_path = "$RUNNER_TEMP/agent-team-os-pytest.xml"

    assert pytest_step["run"] == f'uv run pytest -q --junitxml="{junit_path}"'
    assert pytest_index < build_index < console_index < audit_index
    audit_command = audit_step["run"]
    assert "scripts/build_delivery_bundle.py" in audit_command
    assert "scripts/audit_ci_bundle_artifact.py" in audit_command
    assert f'--pytest-junit "{junit_path}"' in audit_command
    assert "--allow-dirty" not in audit_command
    assert ">/dev/null 2>&1; then" in audit_command
    assert "b0_builder=failed" in audit_command
    assert "upload-artifact" not in (ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )


def _source(tmp_path: Path, *, area: str | None = None) -> tuple[Path, Path]:
    root = tmp_path / "source"
    (root / "config").mkdir(parents=True)
    for name in (
        "capabilities.yaml",
        "framework-lock.json",
        "journeys.yaml",
        "method-packs-v050.json",
    ):
        payload = f"{name}\n"
        if area == "config" and name == "capabilities.yaml":
            payload += FAKE_SECRET + "\n"
        (root / "config" / name).write_text(payload, encoding="utf-8")
    (root / "migrations").mkdir()
    (root / "migrations/0001_baseline.sql").write_text("SELECT 1;\n", encoding="utf-8")
    assets = root / "console/dist/assets"
    assets.mkdir(parents=True)
    (root / "console/dist/index.html").write_text(
        '<script src="/assets/app.js"></script>\n', encoding="utf-8"
    )
    js = "const headers = {Authorization: 'Bearer'}; const token = '';\n"
    if area == "js":
        js += f"const credential = '{FAKE_SECRET}';\n"
    (assets / "app.js").write_text(js, encoding="utf-8")
    dataset = root / "evaluation/datasets/agent-team-os-mvp/1.3.0"
    shutil.copytree(ROOT / "evaluation/datasets/agent-team-os-mvp/1.3.0", dataset)
    if area == "dataset":
        with (dataset / "README.md").open("a", encoding="utf-8") as output:
            output.write(f"\nSynthetic credential: {FAKE_SECRET}\n")
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, root / name)
    package = root / "src/agent_team_os"
    package.mkdir(parents=True)
    package_source = "# test package\n"
    if area == "wheel":
        package_source += f"SYNTHETIC_CREDENTIAL = '{FAKE_SECRET}'\n"
    (package / "__init__.py").write_text(package_source, encoding="utf-8")
    dist = root / "dist"
    dist.mkdir()
    wheel = dist / f"dev_agent_teams_v2-{PRODUCT_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.write(package / "__init__.py", "agent_team_os/__init__.py")
        archive.writestr(
            f"dev_agent_teams_v2-{PRODUCT_VERSION}.dist-info/METADATA",
            f"Name: dev-agent-teams-v2\nVersion: {PRODUCT_VERSION}\n",
        )
    return root, wheel


def _bundle(tmp_path: Path, *, area: str | None = None) -> Path:
    root, wheel = _source(tmp_path, area=area)
    return build_delivery_bundle(
        project_root=root,
        output_root=tmp_path / "output",
        wheel=wheel,
        git_revision="a" * 40,
        worktree_clean=True,
    ).bundle_root


def _refresh_manifest(bundle: Path, relative: str) -> None:
    manifest_file = bundle / "delivery-manifest.json"
    raw = json.loads(manifest_file.read_text(encoding="utf-8"))
    payload = (bundle / relative).read_bytes()
    for entry in raw["files"]:
        if entry["path"] == relative:
            entry["size"] = len(payload)
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
            break
    manifest_file.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_baseline_bundle_and_ordinary_token_field_names_pass(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    result = audit.audit_bundle(bundle, "a" * 40)
    assert result["manifest_files"] >= 10
    assert result["wheel_members"] == 2
    assert result["revision"] == "a" * 40


def test_current_source_config_with_synthetic_dist_text_passes(tmp_path: Path) -> None:
    """只覆盖当前源码/config 与合成 dist，不是实际 CI Bundle 正例。"""
    root, wheel = _source(tmp_path)
    for config in (ROOT / "config").iterdir():
        if config.name in {
            "capabilities.yaml", "framework-lock.json", "journeys.yaml",
            "method-packs-v050.json",
        }:
            shutil.copyfile(config, root / "config" / config.name)
    package = root / "src/agent_team_os"
    shutil.rmtree(package)
    shutil.copytree(ROOT / "src/agent_team_os", package)
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in sorted(package.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, "agent_team_os/" + path.relative_to(package).as_posix())
        archive.writestr(
            f"dev_agent_teams_v2-{PRODUCT_VERSION}.dist-info/METADATA",
            f"Name: dev-agent-teams-v2\nVersion: {PRODUCT_VERSION}\n",
        )
    bundle = build_delivery_bundle(
        project_root=root, output_root=tmp_path / "output", wheel=wheel,
        git_revision="a" * 40, worktree_clean=True,
    ).bundle_root
    assert audit.audit_bundle(bundle, "a" * 40)["wheel_members"] > 100


@pytest.mark.parametrize("area", ("config", "js", "wheel", "dataset"))
def test_synthetic_secret_in_each_text_area_is_rejected(
    tmp_path: Path, area: str
) -> None:
    bundle = _bundle(tmp_path, area=area)
    with pytest.raises(audit.AuditRejected) as caught:
        audit.audit_bundle(bundle, "a" * 40)
    assert str(caught.value) == "B0_SECRET_VALUE"
    assert FAKE_SECRET not in str(caught.value)


def test_secret_rejection_does_not_echo_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _bundle(tmp_path, area="js")
    junit = tmp_path / "pytest.xml"
    _write_expected_junit(junit)
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        sys, "argv",
        [
            str(SCRIPT), str(bundle), "--project-root", str(tmp_path / "source"),
            "--pytest-junit", str(junit), "--receipt", str(receipt),
            "--expected-revision", "a" * 40,
        ],
    )
    assert audit.main() == 1
    output = capsys.readouterr()
    assert "b0_audit=rejected; code=B0_SECRET_VALUE" in output.out
    assert output.err == ""
    assert FAKE_SECRET not in output.out + output.err
    assert not receipt.exists()


def test_audit_cli_writes_bounded_receipt_and_hash_only_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """合成输入只验证 CLI 合同，不代替 CI 当次 JUnit。"""
    root, wheel = _source(tmp_path)
    bundle = build_delivery_bundle(
        project_root=root, output_root=tmp_path / "output", wheel=wheel,
        git_revision="a" * 40, worktree_clean=True,
    ).bundle_root
    (root / "scripts").mkdir()
    for name in ("build_delivery_bundle.py", "verify_delivery_bundle.py"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    shutil.copyfile(
        ROOT / "src/agent_team_os/delivery_bundle.py",
        root / "src/agent_team_os/delivery_bundle.py",
    )
    (root / "dist" / f"dev_agent_teams_v2-{PRODUCT_VERSION}.tar.gz").write_bytes(
        b"synthetic sdist\n"
    )
    junit = tmp_path / "pytest.xml"
    _write_expected_junit(junit)
    receipt = tmp_path / "receipt.json"

    def command_result(
        argv: list[str], **_kwargs: object
    ) -> audit.subprocess.CompletedProcess[str]:
        if argv == ["git", "rev-parse", "HEAD"]:
            output = "a" * 40 + "\n"
        elif argv == ["git", "status", "--porcelain=v1"]:
            output = ""
        elif argv == ["uv", "--version"]:
            output = "uv 0.9.0\n"
        else:
            raise AssertionError(f"unexpected command: {argv!r}")
        return audit.subprocess.CompletedProcess(argv, 0, stdout=output)

    monkeypatch.setattr(audit.subprocess, "run", command_result)
    monkeypatch.setattr(
        sys, "argv",
        [str(SCRIPT), str(bundle), "--project-root", str(root),
         "--pytest-junit", str(junit), "--receipt", str(receipt),
         "--expected-revision", "a" * 40],
    )
    assert audit.main() == 0
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert saved["pytest_junit"]["sha256"] == hashlib.sha256(junit.read_bytes()).hexdigest()
    assert saved["pytest_junit"]["testcases"] == 1
    printed = capsys.readouterr().out
    assert "b0_audit=passed" in printed
    assert f"manifest_sha256={saved['bundle']['manifest_sha256']}" in printed
    assert f"pytest_junit_sha256={saved['pytest_junit']['sha256']}" in printed
    assert str(tmp_path) not in printed


def _write_expected_junit(path: Path) -> None:
    path.write_text(
        '<testsuite tests="1" skipped="1">'
        '<testcase classname="tests.integration.test_live_codex_simulated_planning" '
        'name="test_real_codex_can_simulate_bounded_planning_without_workspace_changes">'
        '<skipped message="set AGENT_TEAM_OS_LIVE_CODEX=1 to call the real Codex CLI"/>'
        "</testcase></testsuite>",
        encoding="utf-8",
    )


def test_only_the_expected_live_codex_skip_is_allowed(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    _write_expected_junit(junit)
    summary = audit.confirm_expected_skip(junit)
    assert summary["sha256"] == hashlib.sha256(junit.read_bytes()).hexdigest()
    assert summary["testcases"] == 1
    junit.write_text(
        junit.read_text(encoding="utf-8").replace(
            "test_real_codex_can_simulate_bounded_planning_without_workspace_changes",
            "test_unreviewed_skip",
        ),
        encoding="utf-8",
    )
    with pytest.raises(audit.AuditRejected, match="B0_PYTEST_SKIP_UNKNOWN"):
        audit.confirm_expected_skip(junit)
    _write_expected_junit(junit)
    junit.write_text(
        junit.read_text(encoding="utf-8").replace(
            'classname="tests.integration.test_live_codex_simulated_planning"',
            'classname="untrusted.tests.integration.test_live_codex_simulated_planning"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(audit.AuditRejected, match="B0_PYTEST_SKIP_UNKNOWN"):
        audit.confirm_expected_skip(junit)


def test_pytest_junit_symlink_is_rejected(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    _write_expected_junit(junit)
    alias = tmp_path / "alias.xml"
    alias.symlink_to(junit)
    with pytest.raises(audit.AuditRejected, match="B0_PYTEST_RECEIPT_INVALID"):
        audit.confirm_expected_skip(alias)


def test_pytest_junit_extra_skip_and_size_limit_fail_closed(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    _write_expected_junit(junit)
    xml = junit.read_text(encoding="utf-8")
    testcase = xml[xml.index("<testcase"):xml.index("</testcase>") + len("</testcase>")]
    junit.write_text(xml.replace("</testsuite>", testcase + "</testsuite>"), encoding="utf-8")
    with pytest.raises(audit.AuditRejected, match="B0_PYTEST_OUTCOME"):
        audit.confirm_expected_skip(junit)

    junit.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(audit.AuditRejected, match="B0_PYTEST_RECEIPT_LIMIT"):
        audit.confirm_expected_skip(junit)


def test_oversized_method_lock_is_rejected_before_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    lock = bundle / "config/method-packs-v050.json"
    lock.write_bytes(b"x" * 65)
    _refresh_manifest(bundle, "config/method-packs-v050.json")
    monkeypatch.setattr(audit, "MAX_FILE_BYTES", 64)
    with pytest.raises(audit.AuditRejected, match="B0_METHOD_LOCK_LIMIT"):
        audit.audit_bundle(bundle, "a" * 40)


def test_manifest_entry_limit_is_fail_closed(tmp_path: Path) -> None:
    root, wheel = _source(tmp_path)
    for index in range(201):
        (root / "console/dist/assets" / f"extra-{index}.js").write_text(
            "export {};\n", encoding="utf-8"
        )
    bundle = build_delivery_bundle(
        project_root=root, output_root=tmp_path / "output", wheel=wheel,
        git_revision="a" * 40, worktree_clean=True,
    ).bundle_root
    with pytest.raises(audit.AuditRejected, match="B0_MANIFEST_LIMIT"):
        audit.audit_bundle(bundle, "a" * 40)


def test_credential_shaped_filename_is_rejected(tmp_path: Path) -> None:
    root, wheel = _source(tmp_path)
    (root / "console/dist/assets" / f"{FAKE_SECRET}.js").write_text(
        "export {};\n", encoding="utf-8"
    )
    bundle = build_delivery_bundle(
        project_root=root, output_root=tmp_path / "output", wheel=wheel,
        git_revision="a" * 40, worktree_clean=True,
    ).bundle_root
    with pytest.raises(audit.AuditRejected, match="B0_SECRET_VALUE"):
        audit.audit_bundle(bundle, "a" * 40)


def test_token_field_with_specific_long_value_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    js = bundle / "console/dist/assets/app.js"
    js.write_text("const token = '" + "A" * 32 + "';\n", encoding="utf-8")
    _refresh_manifest(bundle, "console/dist/assets/app.js")
    with pytest.raises(audit.AuditRejected, match="B0_SECRET_VALUE"):
        audit.audit_bundle(bundle, "a" * 40)


@pytest.mark.parametrize(
    "expression",
    (
        "password = self.backend.get_password(self.service, username)",
        "password = credential_store.get_or_create(username)",
        "token = self._resolve_tenant_access_token()",
        "access_token = self.token_resolver.resolve_user_access_token(self.binding, actor)",
    ),
)
def test_long_source_attribute_expression_is_not_a_literal_secret(expression: str) -> None:
    audit._scan_text((expression + "\n").encode(), "agent_team_os/example.py")


@pytest.mark.parametrize(
    ("content", "name"),
    (
        ("password = '" + "A" * 32 + "'", "agent_team_os/example.py"),
        ("password: " + "A" * 32, "config/capabilities.yaml"),
        ("token = 'sk-proj-" + "A" * 32 + "'", "agent_team_os/example.py"),
        ("-----BEGIN PRIVATE KEY-----", "config/capabilities.yaml"),
        ("Authorization: Bearer " + "A" * 32, "config/capabilities.yaml"),
    ),
)
def test_concrete_secret_literals_still_fail_closed(content: str, name: str) -> None:
    with pytest.raises(audit.AuditRejected, match="B0_SECRET_VALUE"):
        audit._scan_text((content + "\n").encode(), name)


def test_unknown_binary_wheel_member_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    wheel = next((bundle / "backend").glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("package/unknown.bin", b"\x00\x01")
    _refresh_manifest(bundle, f"backend/{wheel.name}")
    with pytest.raises(audit.AuditRejected, match="B0_UNKNOWN_BINARY"):
        audit.audit_bundle(bundle, "a" * 40)


def test_wheel_member_limit_is_fail_closed(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    wheel = next((bundle / "backend").glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as archive:
        for index in range(400):
            archive.writestr(f"package/more-{index}.txt", "ok\n")
    _refresh_manifest(bundle, f"backend/{wheel.name}")
    with pytest.raises(audit.AuditRejected, match="B0_WHEEL_MEMBER_LIMIT"):
        audit.audit_bundle(bundle, "a" * 40)


def test_wheel_link_and_path_escape_are_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    wheel = next((bundle / "backend").glob("*.whl"))
    link = zipfile.ZipInfo("package/link.py")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(link, "target.py")
    _refresh_manifest(bundle, f"backend/{wheel.name}")
    with pytest.raises(audit.AuditRejected, match="B0_WHEEL_SPECIAL_FILE"):
        audit.audit_bundle(bundle, "a" * 40)
    wheel = next((bundle / "backend").glob("*.whl"))
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("agent_team_os/__init__.py", "# test package\n")
        archive.writestr("../outside.py", "x = 1\n")
    _refresh_manifest(bundle, f"backend/{wheel.name}")
    with pytest.raises(audit.AuditRejected, match="B0_PATH_INVALID"):
        audit.audit_bundle(bundle, "a" * 40)


def test_single_member_limit_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    wheel = next((bundle / "backend").glob("*.whl"))
    monkeypatch.setattr(audit, "MAX_FILE_BYTES", 8)
    with pytest.raises(audit.AuditRejected, match="B0_WHEEL_MEMBER_LIMIT"):
        audit._scan_wheel(wheel)


def test_nested_archive_in_wheel_is_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    wheel = next((bundle / "backend").glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("package/nested.zip", b"PK\x03\x04payload")
    _refresh_manifest(bundle, f"backend/{wheel.name}")
    with pytest.raises(audit.AuditRejected, match="B0_WHEEL_CONTENT"):
        audit.audit_bundle(bundle, "a" * 40)
