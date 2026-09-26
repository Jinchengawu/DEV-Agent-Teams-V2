import base64
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from agent_team_os.infrastructure.acwm import PipelineBindingResolutionError
from agent_team_os.modules.extensions import (
    ContentAddressedMethodPackStore,
    MethodEntry,
    MethodPackInstall,
)
from agent_team_os.preview import (
    CodexPreviewReadiness,
    _ensure_builtin_pipeline_for_preview,
    _inspect_method_pack_store,
    build_preview_app,
    ensure_console_built,
    main,
)
from agent_team_os.product_root import ProductRootError
from agent_team_os.readiness import DependencyCheck, ReadinessReport


def test_demo_checks_framework_lock_before_building_preview_app(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = ReadinessReport(
        status="not_ready",
        checks=(
            DependencyCheck(
                name="python:acwm-revision",
                status="failed",
                repair="安装锁定 ACWM Revision。",
            ),
        ),
    )
    monkeypatch.setattr(sys, "argv", ["agent-team-os", "demo"])
    monkeypatch.setattr(
        "agent_team_os.preview.CodexPreviewReadiness.inspect", lambda _self: report
    )
    monkeypatch.setattr(
        "agent_team_os.preview.build_preview_app",
        lambda: (_ for _ in ()).throw(
            AssertionError("Preview App must not be built before readiness")
        ),
    )

    with pytest.raises(SystemExit) as stopped:
        main()

    assert stopped.value.code == 2
    assert '"name": "python:acwm-revision"' in capsys.readouterr().out


def test_preview_stays_available_when_builtin_pipeline_binding_needs_repair(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class StaleBindingCatalog:
        def ensure_builtin_pipeline(self, _request: object, *, actor_id: str) -> None:
            assert actor_id == "system"
            raise PipelineBindingResolutionError(
                "Capability codex-backend binding is stale"
            )

    result = _ensure_builtin_pipeline_for_preview(StaleBindingCatalog(), object())

    assert result is None
    assert "codex-backend binding is stale" in caplog.text


def test_preview_readiness_blocks_when_locked_method_packs_are_not_installed(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[1]

    check = _inspect_method_pack_store(
        root / "config" / "method-packs-v050.json",
        tmp_path / "empty-method-store",
    )

    assert check.name == "method-packs:bmad-tea-v050"
    assert check.status == "missing"
    assert check.repair is not None and "install_method_packs.py" in check.repair
    assert not (tmp_path / "empty-method-store").exists()


def test_preview_app_construction_does_not_create_uninstalled_method_store(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    app = build_preview_app()
    assert app is not None
    assert data_root.exists()
    assert not (data_root / "method-packs").exists()


def test_preview_readiness_treats_verified_empty_private_store_as_missing(tmp_path: Path) -> None:
    store_root = tmp_path / "method-packs"
    store_root.mkdir(mode=0o700)
    before = store_root.lstat().st_ino
    check = _inspect_method_pack_store(
        Path(__file__).parents[1] / "config/method-packs-v050.json", store_root
    )
    assert check.status == "missing"
    assert check.repair is not None
    assert list(store_root.iterdir()) == []
    assert store_root.lstat().st_ino == before


@pytest.mark.parametrize("entry", ["orphan", ".install-in-progress"])
def test_preview_readiness_does_not_offer_install_for_nonempty_unlocked_store(
    tmp_path: Path, entry: str
) -> None:
    store_root = tmp_path / "method-packs"
    store_root.mkdir(mode=0o700)
    (store_root / entry).write_text("existing")
    check = _inspect_method_pack_store(
        Path(__file__).parents[1] / "config/method-packs-v050.json", store_root
    )
    assert check.status == "failed"
    assert check.repair is None
    assert sorted(path.name for path in store_root.iterdir()) == [entry]


def test_preview_readiness_rejects_symlink_or_public_store_without_repair(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    for store_root in (alias, real):
        if store_root == real:
            real.chmod(0o755)
        check = _inspect_method_pack_store(
            Path(__file__).parents[1] / "config/method-packs-v050.json", store_root
        )
        assert check.status == "failed"
        assert check.repair is None
    real.chmod(0o500)
    check = _inspect_method_pack_store(
        Path(__file__).parents[1] / "config/method-packs-v050.json", real
    )
    assert check.status == "failed" and check.repair is None


def test_preview_readiness_rejects_symlink_parent_and_unsafe_parent(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    unwritable = tmp_path / "unwritable"
    unwritable.mkdir(mode=0o500)
    for store_root in (alias / "missing", unsafe / "missing", unwritable / "missing"):
        check = _inspect_method_pack_store(
            Path(__file__).parents[1] / "config/method-packs-v050.json", store_root
        )
        assert check.status == "failed"
        assert check.repair is None
        assert not store_root.exists()


def test_preview_readiness_missing_snapshot_with_existing_lock_is_missing(tmp_path: Path) -> None:
    store = ContentAddressedMethodPackStore(tmp_path / "method-packs")
    with store._lock(exclusive=True):
        pass
    check = _inspect_method_pack_store(
        Path(__file__).parents[1] / "config/method-packs-v050.json", store.root
    )
    assert check.status == "missing"
    assert check.repair is not None


def test_preview_readiness_ready_for_qualified_store(tmp_path: Path) -> None:
    store = ContentAddressedMethodPackStore(tmp_path / "method-packs")
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, data in {
            "package/package.json": b'{"name":"test-method","version":"1.0.0"}',
            "package/skills/test/SKILL.md": b"# Test",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    archive_bytes = payload.getvalue()
    request = MethodPackInstall(
        package_name="test-method",
        package_version="1.0.0",
        tarball_uri="https://registry.npmjs.org/test-method/-/test-method-1.0.0.tgz",
        registry_integrity=(
            "sha512-" + base64.b64encode(hashlib.sha512(archive_bytes).digest()).decode()
        ),
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        method_entries=(MethodEntry(method_id="test", source_path="skills/test"),),
    )
    snapshot = store.install_archive(request, archive_bytes)
    lock_file = tmp_path / "method-lock.json"
    lock_file.write_text(
        json.dumps(
            {
                "policy_version": "method-pack-store-v1",
                "packages": [
                    {
                        "install": request.model_dump(mode="json"),
                        "expected_content_sha256": snapshot.content_sha256,
                        "expected_qualification_sha256": snapshot.qualification_sha256,
                    }
                ],
            }
        )
    )
    check = _inspect_method_pack_store(lock_file, store.root)
    assert check.status == "ready"
    assert check.repair is None


def test_preview_readiness_rejects_root_inode_replacement(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store_root = tmp_path / "method-packs"
    store_root.mkdir(mode=0o700)
    original = Path.lstat
    reads = 0

    def replace_before_final_check(path: Path):
        nonlocal reads
        if path == store_root:
            reads += 1
            if reads == 2:
                store_root.rename(tmp_path / "replaced")
                store_root.mkdir(mode=0o700)
        return original(path)

    monkeypatch.setattr(Path, "lstat", replace_before_final_check)
    check = _inspect_method_pack_store(
        Path(__file__).parents[1] / "config/method-packs-v050.json", store_root
    )
    assert reads >= 2
    assert check.status == "failed"
    assert check.repair is None
    assert list(store_root.iterdir()) == []


def test_preview_readiness_preserves_codex_model_compatibility_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = ReadinessReport(
        status="not_ready",
        checks=(
            DependencyCheck(name="python:acwm", status="ready"),
            DependencyCheck(name="python:agentscope", status="ready"),
            DependencyCheck(
                name="codex-cli-version",
                status="failed",
                repair="Install Codex CLI >= 0.153.4.",
            ),
            DependencyCheck(name="codex-login", status="ready"),
        ),
    )
    monkeypatch.setattr(
        "agent_team_os.preview.RuntimeReadiness.inspect", lambda _self: runtime
    )

    report = CodexPreviewReadiness().inspect()

    check = next(item for item in report.checks if item.name == "codex-cli-version")
    assert check.status == "failed"
    assert report.status == "not_ready"


def test_preview_readiness_validates_explicit_product_root(tmp_path: Path) -> None:
    with pytest.raises(ProductRootError):
        CodexPreviewReadiness(project_root=tmp_path)


def test_prebuilt_bundle_console_does_not_require_node_or_pnpm(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    (tmp_path / "console" / "dist").mkdir(parents=True)
    (tmp_path / "console" / "dist" / "index.html").write_text("ok", encoding="utf-8")
    monkeypatch.setattr("agent_team_os.preview.shutil.which", lambda _name: None)

    ensure_console_built(tmp_path)
