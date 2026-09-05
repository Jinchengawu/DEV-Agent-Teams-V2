"""资格化只读取已验证 Git Revision 的配置字节，不 checkout 或执行仓库代码。"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from ..git import ExternalGitBinding, external_git_environment
from .tool_environment import tool_error


@contextmanager
def read_configuration(
    binding: ExternalGitBinding,
    revision: str,
    config_paths: tuple[str, ...],
) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="agent-team-os-verification-config-") as directory:
        root = Path(directory).resolve()
        repository = root / "repository.git"
        config = root / "configuration"
        config.mkdir()
        with external_git_environment(binding.credential_reference) as environment:
            environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")

            def git(*args: str) -> bytes:
                completed = subprocess.run(
                    ("git", *args), env=environment, capture_output=True, timeout=30, check=False
                )
                if completed.returncode:
                    raise tool_error("无法读取已验证 Revision 的仓库配置。")
                return completed.stdout

            git(
                "clone",
                "--bare",
                "--no-local",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                "main",
                "--",
                binding.remote_uri,
                str(repository),
            )
            current = (
                git("--git-dir", str(repository), "rev-parse", "refs/heads/main").decode().strip()
            )
            if current != revision:
                raise tool_error("main 已偏离 Git 资格中的 Revision，必须重新验证 Workspace。")
            for name in config_paths:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts:
                    raise tool_error("产品配置路径无效。")
                entry = git("--git-dir", str(repository), "ls-tree", revision, "--", name).decode()
                if not entry.startswith("100644 blob ") and not entry.startswith("100755 blob "):
                    raise tool_error("冻结配置不是普通文件。")
                target = config / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(git("--git-dir", str(repository), "show", f"{revision}:{name}"))
        yield config
