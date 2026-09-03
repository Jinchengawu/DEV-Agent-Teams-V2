"""Fail-closed checks for the real Agent-Team-OS runtime."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
import tomllib
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .delivery import DeliveryBuildIdentitySnapshot
from .shared.hashes import sha256_file, sha256_json


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DependencyCheck(ImmutableModel):
    name: str
    status: Literal["ready", "missing", "failed"]
    repair: str | None = None


class ReadinessReport(ImmutableModel):
    status: Literal["ready", "not_ready"]
    checks: tuple[DependencyCheck, ...]


class ReadinessProbe(Protocol):
    def inspect(self) -> ReadinessReport: ...


class FrameworkRevision(ImmutableModel):
    version: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class FrameworkLock(ImmutableModel):
    acwm: FrameworkRevision


def inspect_acwm_revision_lock(
    lock_path: Path,
    *,
    actual_revision: str | None = None,
    actual_worktree_dirty: bool | None = None,
) -> DependencyCheck:
    try:
        locked = FrameworkLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
        actual = actual_revision or imported_acwm_revision()
        dirty = (
            actual_worktree_dirty
            if actual_worktree_dirty is not None
            else (False if actual_revision is not None else imported_acwm_worktree_dirty())
        )
    except Exception:
        return DependencyCheck(
            name="python:acwm-revision",
            status="failed",
            repair="修复 config/framework-lock.json 并安装其指定的 ACWM Revision。",
        )
    dependency_repair = _project_acwm_dependency_repair(lock_path, locked.acwm)
    if dependency_repair is not None:
        return DependencyCheck(
            name="python:acwm-revision",
            status="failed",
            repair=dependency_repair,
        )
    if dirty:
        return DependencyCheck(
            name="python:acwm-revision",
            status="failed",
            repair=(
                "当前 ACWM editable dependency 存在未提交修改；"
                "发布门禁只接受可重放的干净 Revision。"
            ),
        )
    matches = actual == locked.acwm.revision
    return DependencyCheck(
        name="python:acwm-revision",
        status="ready" if matches else "failed",
        repair=(
            None
            if matches
            else (
                f"当前 ACWM {actual} 与锁定 Revision "
                f"{locked.acwm.revision} 不一致；重新执行锁定依赖安装。"
            )
        ),
    )


def snapshot_delivery_build_identity(
    project_root: Path,
    *,
    actual_acwm_revision: str | None = None,
    actual_acwm_worktree_dirty: bool | None = None,
) -> DeliveryBuildIdentitySnapshot:
    """Freeze the product and ACWM identity used to compile one Delivery."""

    product_revision = _git_output(project_root, "rev-parse", "HEAD")
    if len(product_revision) != 40:
        raise RuntimeError("Agent-Team-OS Git Revision is unavailable")
    product_worktree_clean = not _git_output(
        project_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
    )
    lock_path = project_root / "config" / "framework-lock.json"
    locked = FrameworkLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
    acwm_revision = actual_acwm_revision or imported_acwm_revision()
    dependency = inspect_acwm_revision_lock(
        lock_path,
        actual_revision=acwm_revision,
        actual_worktree_dirty=actual_acwm_worktree_dirty,
    )
    dependency_status: Literal["ready", "failed"] = (
        "ready" if dependency.status == "ready" else "failed"
    )
    framework_lock_sha256 = sha256_file(lock_path)
    payload = {
        "product_revision": product_revision,
        "product_worktree_clean": product_worktree_clean,
        "acwm_version": locked.acwm.version,
        "acwm_revision": acwm_revision,
        "framework_lock_sha256": framework_lock_sha256,
        "framework_dependency_status": dependency_status,
    }
    return DeliveryBuildIdentitySnapshot(
        product_revision=product_revision,
        product_worktree_clean=product_worktree_clean,
        acwm_version=locked.acwm.version,
        acwm_revision=acwm_revision,
        framework_lock_sha256=framework_lock_sha256,
        framework_dependency_status=dependency_status,
        snapshot_sha256=sha256_json(payload),
    )


def _git_output(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Agent-Team-OS Git identity is unavailable")
    return result.stdout.strip()


def _project_acwm_dependency_repair(
    lock_path: Path,
    locked: FrameworkRevision,
) -> str | None:
    """Verify that the product declaration resolves the same ACWM identity."""

    project_root = lock_path.parent.parent
    pyproject_path = project_root / "pyproject.toml"
    if lock_path.parent.name != "config" or not pyproject_path.is_file():
        return None
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        dependencies = pyproject["project"]["dependencies"]
        source = pyproject["tool"]["uv"]["sources"]["agent-capability-workflow-matrix"]
    except (KeyError, OSError, tomllib.TOMLDecodeError, TypeError):
        return "修复 pyproject.toml 中 ACWM 的精确版本与 Git Revision 声明。"
    expected_requirement = f"agent-capability-workflow-matrix=={locked.version}"
    if (
        not isinstance(dependencies, list)
        or any(not isinstance(item, str) for item in dependencies)
        or expected_requirement not in dependencies
    ):
        return (
            f"pyproject.toml 必须精确声明 {expected_requirement}，并与 framework-lock.json 一致。"
        )
    if not isinstance(source, dict) or source.get("rev") != locked.revision:
        return (
            "pyproject.toml 的 ACWM Git Revision 必须与 "
            f"framework-lock.json 的 {locked.revision} 一致。"
        )
    uv_lock_path = project_root / "uv.lock"
    try:
        uv_lock = tomllib.loads(uv_lock_path.read_text(encoding="utf-8"))
        packages = uv_lock["package"]
        if not isinstance(packages, list) or any(
            not isinstance(package, dict) for package in packages
        ):
            raise TypeError("uv.lock package entries must be tables")
        resolved = [
            package
            for package in packages
            if package.get("name") == "agent-capability-workflow-matrix"
        ]
    except (KeyError, OSError, tomllib.TOMLDecodeError, TypeError):
        return "修复 uv.lock，并重新解析 framework-lock.json 指定的 ACWM。"
    if len(resolved) != 1 or resolved[0].get("version") != locked.version:
        return f"uv.lock 必须唯一解析 agent-capability-workflow-matrix=={locked.version}。"
    resolved_source = resolved[0].get("source")
    resolved_git = resolved_source.get("git") if isinstance(resolved_source, dict) else None
    if not isinstance(resolved_git, str) or not all(
        marker in resolved_git for marker in (f"?rev={locked.revision}", f"#{locked.revision}")
    ):
        return f"uv.lock 的 ACWM 请求与解析 Revision 必须同时固定为 {locked.revision}。"
    return None


def imported_acwm_revision() -> str:
    repository = _editable_acwm_repository()
    if repository is not None:
        result = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    direct_url = importlib.metadata.distribution("agent-capability-workflow-matrix").read_text(
        "direct_url.json"
    )
    if direct_url:
        data = json.loads(direct_url)
        commit = data.get("vcs_info", {}).get("commit_id")
        if commit:
            return str(commit)
    return importlib.metadata.version("agent-capability-workflow-matrix")


def imported_acwm_worktree_dirty() -> bool:
    """Treat an editable ACWM checkout as ineligible when it is not reproducible."""

    repository = _editable_acwm_repository()
    if repository is None:
        return False
    result = subprocess.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=normal"),
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0 or bool(result.stdout.strip())


def _editable_acwm_repository() -> Path | None:
    package_file = getattr(import_module("acwm"), "__file__", None)
    if not isinstance(package_file, str):
        return None
    resolved_package = Path(package_file).resolve()
    for parent in resolved_package.parents:
        source_package = parent / "src" / "acwm"
        if (
            (parent / ".git").exists()
            and source_package.exists()
            and resolved_package.is_relative_to(source_package.resolve())
        ):
            return parent
    return None


class RuntimeReadiness:
    """Inspect identities and credentials without exposing secret values."""

    def __init__(self, *, planning_runtime_kind: Literal["hermes", "codex"] = "hermes") -> None:
        self.planning_runtime_kind = planning_runtime_kind

    def inspect(self) -> ReadinessReport:
        common = (
            self._package("acwm", "Install the locked ACWM dependency."),
            self._package("agentscope", "Run `uv sync --extra live`."),
            self._codex_login(),
        )
        planning = (
            (
                self._command("hermes", "Install Hermes CLI and ensure it is on PATH."),
                self._hermes_acp_protocol(),
                self._hermes_credentials(),
            )
            if self.planning_runtime_kind == "hermes"
            else ()
        )
        checks = (*common, *planning)
        return ReadinessReport(
            status="ready" if all(check.status == "ready" for check in checks) else "not_ready",
            checks=checks,
        )

    @staticmethod
    def _package(name: str, repair: str) -> DependencyCheck:
        return DependencyCheck(
            name=f"python:{name}",
            status="ready" if find_spec(name) is not None else "missing",
            repair=None if find_spec(name) is not None else repair,
        )

    @staticmethod
    def _command(name: str, repair: str) -> DependencyCheck:
        available = shutil.which(name) is not None
        return DependencyCheck(
            name=f"cli:{name}",
            status="ready" if available else "missing",
            repair=None if available else repair,
        )

    @staticmethod
    def _hermes_credentials() -> DependencyCheck:
        available = bool(os.environ.get("HERMES_API_KEY"))
        return DependencyCheck(
            name="hermes-credentials",
            status="ready" if available else "missing",
            repair=None if available else "Set HERMES_API_KEY for the Hermes model provider.",
        )

    @staticmethod
    def _hermes_acp_protocol() -> DependencyCheck:
        if shutil.which("hermes") is None:
            return DependencyCheck(
                name="hermes-acp-protocol",
                status="missing",
                repair="Install a Hermes CLI release that supports `hermes acp --check`.",
            )
        try:
            result = subprocess.run(
                ["hermes", "acp", "--check"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return DependencyCheck(
                name="hermes-acp-protocol",
                status="failed",
                repair="Run `hermes acp --check` and repair the ACP installation.",
            )
        return DependencyCheck(
            name="hermes-acp-protocol",
            status="ready" if result.returncode == 0 else "failed",
            repair=(
                None
                if result.returncode == 0
                else "Run `hermes acp --check` and repair the ACP installation."
            ),
        )

    @staticmethod
    def _codex_login() -> DependencyCheck:
        if shutil.which("codex") is None:
            return DependencyCheck(
                name="codex-login",
                status="missing",
                repair="Install Codex CLI and run `codex login`.",
            )
        try:
            result = subprocess.run(
                ["codex", "login", "status"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return DependencyCheck(
                name="codex-login",
                status="failed",
                repair="Run `codex login` and retry readiness.",
            )
        return DependencyCheck(
            name="codex-login",
            status="ready" if result.returncode == 0 else "failed",
            repair=None if result.returncode == 0 else "Run `codex login` and retry readiness.",
        )
