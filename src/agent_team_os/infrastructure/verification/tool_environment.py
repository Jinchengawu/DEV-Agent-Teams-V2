"""显式准备离线工具环境；资格化仅核对内容，不安装依赖。"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
from pathlib import Path

from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_json
from ...shared.verification import VerificationDependencyIdentity, VerificationFileIdentity

NODE_PACKAGES = {"typescript": "5.9.3", "vitest": "3.2.7", "vite": "7.3.6"}


def tool_error(detail: str) -> ProductError:
    return ProductError(
        code="WORKCELL_VERIFICATION_ENVIRONMENT_UNQUALIFIED",
        title="验证工具环境未就绪",
        detail=detail,
        repair="显式准备锁定的离线工具环境，再重新资格化。",
        status_code=409,
    )


def hash_tree(root: Path) -> str:
    root = root.resolve()
    if not root.is_dir():
        raise tool_error("工具目录不存在。")
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise tool_error("工具环境含越界符号链接。")
            entries.append((relative, "link:" + os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as stream:
                entries.append((relative, hashlib.file_digest(stream, "sha256").hexdigest()))
    return str(sha256_json(entries))


def node_modules_root() -> Path:
    configured = os.environ.get("AGENT_TEAM_OS_VERIFICATION_TOOLS_DIR")
    root = Path(configured) if configured else Path(".agent-team-os/verification-tools")
    return (root / "node_modules").resolve()


def prepare_node_environment(source: Path, target: Path) -> Path:
    """从已经安装的 pnpm 包复制依赖闭包，不联网，不执行包安装脚本。"""
    source = source.resolve()
    destination = target.resolve() / "node_modules"
    if destination.exists():
        raise tool_error("显式准备目标已存在，不能覆盖冻结工具环境。")
    for name, version in NODE_PACKAGES.items():
        metadata = json.loads((source / name / "package.json").read_text())
        if metadata.get("version") != version:
            raise tool_error(f"离线 {name} 版本不匹配。")
    destination.mkdir(parents=True)
    pending = [source / name for name in NODE_PACKAGES]
    copied: set[Path] = set()
    while pending:
        item = pending.pop()
        resolved = item.resolve()
        if not resolved.is_relative_to(source):
            raise tool_error("离线依赖指向源目录之外。")
        relative = item.relative_to(source)
        if item.is_symlink():
            out = destination / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            if not out.is_symlink() and not out.exists():
                out.symlink_to(os.readlink(item))
        parts = resolved.relative_to(source).parts
        bucket = source / parts[0] / parts[1] if parts[0] == ".pnpm" else resolved
        if bucket in copied:
            continue
        copied.add(bucket)
        out = destination / bucket.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            bucket,
            out,
            symlinks=True,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        pending.extend(path for path in bucket.rglob("*") if path.is_symlink())
    digest = hash_tree(destination)
    (destination.parent / "environment.json").write_text(
        json.dumps(
            {
                "contract_version": "verification-node-environment-v1",
                "packages": NODE_PACKAGES,
                "content_sha256": digest,
            },
            sort_keys=True,
        )
    )
    return destination


def _python_distribution_identity(name: str) -> VerificationDependencyIdentity:
    pending = {
        "jsonschema": [
            "jsonschema",
            "attrs",
            "jsonschema-specifications",
            "referencing",
            "rpds-py",
            "typing_extensions",
        ],
        "playwright": ["playwright", "greenlet", "pyee", "typing_extensions"],
    }[name].copy()
    seen: set[str] = set()
    files: dict[str, str] = {}
    versions: dict[str, str] = {}
    while pending:
        current = pending.pop()
        if current.lower() in seen:
            continue
        seen.add(current.lower())
        distribution = importlib.metadata.distribution(current)
        versions[distribution.metadata["Name"]] = distribution.version
        for item in distribution.files or ():
            path = Path(str(distribution.locate_file(item))).resolve()
            if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts:
                with path.open("rb") as stream:
                    files[str(path)] = hashlib.file_digest(stream, "sha256").hexdigest()
    return VerificationDependencyIdentity(
        name=name,
        version=importlib.metadata.version(name),
        root=str(Path(str(importlib.metadata.distribution(name).locate_file(""))).resolve()),
        content_sha256=sha256_json({"versions": versions, "files": files}),
    )


def chromium_identity() -> VerificationDependencyIdentity:
    dist = importlib.metadata.distribution("playwright")
    metadata = Path(str(dist.locate_file("playwright/driver/package/browsers.json")))
    browser = next(
        item for item in json.loads(metadata.read_text())["browsers"] if item["name"] == "chromium"
    )
    cache = (
        Path.home() / "Library/Caches/ms-playwright"
        if platform.system() == "Darwin"
        else Path.home() / ".cache/ms-playwright"
    )
    root = cache / ("chromium-" + browser["revision"])
    executables = tuple(root.rglob("Google Chrome for Testing")) or tuple(root.rglob("chrome"))
    executable = next((item for item in executables if item.is_file()), None)
    if executable is None:
        raise tool_error("缺少与 Playwright 锁定版本匹配的 Chromium。")
    return VerificationDependencyIdentity(
        name="chromium",
        version=browser["browserVersion"],
        root=str(executable.resolve()),
        content_sha256=sha256_json({"revision": browser["revision"], "tree": hash_tree(root)}),
    )


def inspect_dependencies(names: tuple[str, ...]) -> tuple[VerificationDependencyIdentity, ...]:
    output = []
    try:
        for name in names:
            if name == "runner":
                root = Path(__file__).parent / "runners"
                output.append(
                    VerificationDependencyIdentity(
                        name=name,
                        version="2",
                        root=str(root.resolve()),
                        content_sha256=Sha256.validate(hash_tree(root)),
                    )
                )
            elif name == "node_modules":
                root = node_modules_root()
                receipt = json.loads((root.parent / "environment.json").read_text())
                digest = hash_tree(root)
                if (
                    receipt.get("packages") != NODE_PACKAGES
                    or receipt.get("content_sha256") != digest
                ):
                    raise tool_error("离线 Node 工具环境与准备回执不一致。")
                for package, version in NODE_PACKAGES.items():
                    if (
                        json.loads((root / package / "package.json").read_text())["version"]
                        != version
                    ):
                        raise tool_error("Node 工具版本已改变。")
                output.append(
                    VerificationDependencyIdentity(
                        name=name,
                        version="1",
                        root=str(root),
                        content_sha256=Sha256.validate(digest),
                    )
                )
            elif name == "chromium":
                output.append(chromium_identity())
            elif name in {"jsonschema", "playwright"}:
                output.append(_python_distribution_identity(name))
            else:
                raise tool_error("产品没有发布该工具依赖。")
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError) as error:
        raise tool_error("无法完成工具依赖的内容资格检查。") from error
    return tuple(output)


def workspace_files(
    root: Path, names: tuple[str, ...], *, workcell_key: str | None = None
) -> tuple[VerificationFileIdentity, ...]:
    if workcell_key is not None:
        try:
            contract = json.loads((root / "verification.json").read_text())
        except (OSError, ValueError) as error:
            raise tool_error("仓库验证合同缺失或无效。") from error
        if contract != {"contract_id": "health-contract-v1", "workcell_key": workcell_key}:
            raise tool_error("仓库验证合同与产品方案职责不一致。")
    output = []
    for name in names:
        path = root / name
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise tool_error(f"缺少固定仓库配置或配置越界：{name}。")
        output.append(
            VerificationFileIdentity(
                path=name, sha256=Sha256.validate(hashlib.sha256(path.read_bytes()).hexdigest())
            )
        )
    return tuple(output)
