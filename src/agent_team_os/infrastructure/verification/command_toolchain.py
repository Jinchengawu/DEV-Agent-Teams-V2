from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, cast

from ...shared.errors import ProductError
from ...shared.hashes import Sha256
from ...shared.verification import VerificationToolIdentity


def verification_environment(additions: dict[str, str] | None = None) -> dict[str, str]:
    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TERM",
        "TMP",
        "TMPDIR",
        "TEMP",
        "USER",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    for key, value in (additions or {}).items():
        if key not in {"CI", "PYTHONDONTWRITEBYTECODE"} or value != "1":
            raise _error(
                "WORKCELL_VERIFICATION_ENVIRONMENT_INVALID", "产品验证环境不允许该键或值。"
            )
        environment[key] = value
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


class LocalVerificationToolchain:
    def inspect(self, name: str) -> VerificationToolIdentity:
        executable = (
            sys.executable if name == "python" else shutil.which("node") if name == "node" else None
        )
        if executable is None:
            raise _error("WORKCELL_VERIFICATION_TOOL_MISSING", f"缺少产品验证工具：{name}。")
        resolved = Path(executable).resolve()
        try:
            with tempfile.TemporaryDirectory(prefix="agent-team-os-tool-probe-") as directory:
                result = subprocess.run(
                    (str(resolved), "--version"),
                    cwd=directory,
                    env=verification_environment(),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            version = (result.stdout + result.stderr).strip()
            match = (
                re.fullmatch(r"Python (\d+)\.(\d+)\.(\d+)(?:[^\n]*)", version)
                if name == "python"
                else re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
            )
            if result.returncode != 0 or match is None:
                raise _error("WORKCELL_VERIFICATION_TOOL_UNQUALIFIED", f"{name} 版本探针无效。")
            major, minor = int(match.group(1)), int(match.group(2))
            if (name == "python" and (major != 3 or minor < 11)) or (name == "node" and major < 18):
                raise _error(
                    "WORKCELL_VERIFICATION_TOOL_UNQUALIFIED", f"{name} 版本不满足产品验证方案。"
                )
            with resolved.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except (OSError, subprocess.SubprocessError) as error:
            raise _error(
                "WORKCELL_VERIFICATION_TOOL_UNQUALIFIED",
                f"无法完成 {name} 的版本与二进制资格探针。",
            ) from error
        return VerificationToolIdentity(
            name=cast(Literal["python", "node"], name),
            executable=str(resolved),
            version=version,
            executable_sha256=Sha256.validate(digest),
        )


def _error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="验证工具链未就绪",
        detail=detail,
        repair="安装受支持的 Python 3.11+ 或 Node 18+ 后重新验证工具链。",
        status_code=409,
    )
