from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from ...modules.workcells.stage_driver import (
    WorkcellAgentInvocation,
    WorkcellAgentOutput,
)
from ...shared.errors import ProductError


class CodexWorkcellAgent:
    """Invoke one observable Workcell AgentAttempt through Codex CLI.

    The product scheduler creates every Main/Child Run first. This adapter only
    executes that already-authorized attempt inside its assigned workspace and
    ephemeral Method Pack CODEX_HOME; it never spawns hidden children.
    """

    def __init__(
        self,
        *,
        command: tuple[str, ...] = ("codex",),
        timeout_seconds: int = 900,
        runtime_identity: str = "codex-cli",
    ) -> None:
        if not command:
            raise ValueError("Codex command cannot be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.runtime_identity = runtime_identity
        self._active: dict[str, asyncio.subprocess.Process] = {}
        self._overlay_lock = asyncio.Lock()
        self._overlay_leases: dict[Path, tuple[Path, int]] = {}

    async def run(self, invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
        sandbox = (
            "workspace-write" if invocation.workspace_access == "workspace_write" else "read-only"
        )
        environment = _codex_environment(invocation.environment)
        citation_contract = (
            "允许列表非空时，knowledge_citation_ids 必须至少包含其中一个 ID，"
            "不得返回空数组。"
            if invocation.allowed_knowledge_citation_ids
            else "允许列表为空时，knowledge_citation_ids 必须为空数组。"
        )
        instruction = (
            f"{invocation.instruction}\n\n"
            "本次调用就是一个已经登记的 AgentAttempt。不得派生子 Agent，不得使用 Party Mode，"
            "不得读取其他 Workcell Repository。最终只返回一个 JSON object，不要 Markdown。"
            "允许返回的 knowledge_citation_ids 为："
            f"{json.dumps(invocation.allowed_knowledge_citation_ids, ensure_ascii=False)}。"
            f"{citation_contract}"
        )
        command = (
            *self.command,
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            sandbox,
            "-C",
            str(invocation.workspace.resolve()),
            "-",
        )
        async with self._method_project_overlay(invocation, environment) as runtime_env:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=invocation.workspace,
                    env=runtime_env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as error:
                raise _error(
                    "CODEX_WORKCELL_PROCESS_START_FAILED",
                    "Codex CLI 无法启动。",
                ) from error
            self._active[invocation.agent_run_id] = process
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(instruction.encode()),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as error:
                process.kill()
                await process.wait()
                raise _error(
                    "CODEX_WORKCELL_ATTEMPT_TIMED_OUT",
                    "Codex AgentAttempt 超过冻结 Wall-clock Budget。",
                ) from error
            except asyncio.CancelledError:
                process.terminate()
                await process.wait()
                raise
            finally:
                self._active.pop(invocation.agent_run_id, None)
        if process.returncode != 0:
            detail = _redact(stderr.decode(errors="replace")[-4_000:])
            raise _error(
                "CODEX_WORKCELL_ATTEMPT_FAILED",
                f"Codex CLI 退出码 {process.returncode}：{detail}",
            )
        final = _final_messages(stdout.decode(errors="replace"))
        try:
            content = json.loads(final)
        except json.JSONDecodeError as error:
            raise _error(
                "CODEX_WORKCELL_OUTPUT_INVALID",
                "Codex AgentAttempt 最终输出不是单一 JSON object。",
            ) from error
        if not isinstance(content, dict):
            raise _error(
                "CODEX_WORKCELL_OUTPUT_INVALID",
                "Codex AgentAttempt 最终 JSON 必须是 object。",
            )
        raw_citations = content.pop("knowledge_citation_ids", [])
        if not isinstance(raw_citations, list) or any(
            not isinstance(item, str) or not item for item in raw_citations
        ):
            raise _error(
                "CODEX_WORKCELL_CITATIONS_INVALID",
                "knowledge_citation_ids 必须是非空字符串数组。",
            )
        return WorkcellAgentOutput(
            runtime_identity=self.runtime_identity,
            content=content,
            knowledge_citation_ids=tuple(sorted(set(raw_citations))),
        )

    @asynccontextmanager
    async def _method_project_overlay(
        self,
        invocation: WorkcellAgentInvocation,
        environment: dict[str, str],
    ) -> AsyncIterator[dict[str, str]]:
        runtime_source_raw = environment.pop(
            "AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE",
            "",
        ).strip()
        if not runtime_source_raw or invocation.method_id is None:
            yield environment
            return

        runtime_source = _validated_bmad_runtime_source(Path(runtime_source_raw))
        workspace = invocation.workspace.resolve()
        exclude_file = workspace / "_bmad" / ".git-exclude"
        async with self._overlay_lock:
            lease = self._overlay_leases.get(workspace)
            if lease is None:
                _install_bmad_project_overlay(workspace, runtime_source, invocation)
                self._overlay_leases[workspace] = (runtime_source, 1)
            else:
                leased_source, count = lease
                if leased_source != runtime_source:
                    raise _error(
                        "METHOD_WORKSPACE_OVERLAY_SOURCE_CONFLICT",
                        "同一 Workspace 的并发 AgentAttempt 引用了不同 BMAD Snapshot。",
                    )
                self._overlay_leases[workspace] = (leased_source, count + 1)
        try:
            yield _git_exclude_environment(environment, exclude_file)
        finally:
            async with self._overlay_lock:
                leased_source, count = self._overlay_leases[workspace]
                if count > 1:
                    self._overlay_leases[workspace] = (leased_source, count - 1)
                else:
                    self._overlay_leases.pop(workspace)
                    _remove_bmad_project_overlay(workspace)

    async def cancel(self, agent_run_id: str) -> bool:
        process = self._active.get(agent_run_id)
        if process is None or process.returncode is not None:
            return False
        process.terminate()
        await process.wait()
        return True

    async def close(self) -> None:
        processes = tuple(self._active.values())
        for process in processes:
            if process.returncode is None:
                process.terminate()
        if processes:
            await asyncio.gather(*(process.wait() for process in processes))


_BMAD_OVERLAY_MARKER = ".agent-team-os-project-support-v1"
_BMAD_OVERLAY_MARKER_CONTENT = "agent-team-os-project-support-v1\n"


def _validated_bmad_runtime_source(value: Path) -> Path:
    try:
        source = value.resolve(strict=True)
    except OSError as error:
        raise _error(
            "METHOD_WORKSPACE_OVERLAY_SOURCE_MISSING",
            "BMAD Project Support Snapshot 不存在或不可读。",
        ) from error
    required = (
        source / "scripts" / "render_skill.py",
        source / "scripts" / "config_utils.py",
    )
    if not source.is_dir() or not all(item.is_file() for item in required):
        raise _error(
            "METHOD_WORKSPACE_OVERLAY_SOURCE_INVALID",
            "BMAD Project Support Snapshot 缺少已验证的 Runtime 脚本。",
        )
    return source


def _install_bmad_project_overlay(
    workspace: Path,
    runtime_source: Path,
    invocation: WorkcellAgentInvocation,
) -> None:
    overlay = workspace / "_bmad"
    marker = overlay / _BMAD_OVERLAY_MARKER
    if overlay.exists() or overlay.is_symlink():
        if (
            not overlay.is_symlink()
            and marker.is_file()
            and marker.read_text(encoding="utf-8") == _BMAD_OVERLAY_MARKER_CONTENT
        ):
            shutil.rmtree(overlay)
        else:
            raise _error(
                "METHOD_WORKSPACE_OVERLAY_CONFLICT",
                "Workspace 已包含非产品托管的 _bmad，不能覆盖用户内容。",
            )
    overlay.mkdir()
    try:
        marker.write_text(_BMAD_OVERLAY_MARKER_CONTENT, encoding="utf-8")
        (overlay / ".git-exclude").write_text("/_bmad/\n", encoding="utf-8")
        (overlay / "scripts").symlink_to(
            runtime_source / "scripts",
            target_is_directory=True,
        )
        (overlay / "custom").mkdir()
        (overlay / "render").mkdir()
        runtime_artifacts = overlay / "runtime-artifacts"
        (runtime_artifacts / "planning").mkdir(parents=True)
        (runtime_artifacts / "implementation").mkdir()
        (runtime_artifacts / "test-artifacts").mkdir()
        (overlay / "config.toml").write_text(
            _bmad_config(invocation, runtime_artifacts),
            encoding="utf-8",
        )
        bmm = overlay / "bmm"
        bmm.mkdir()
        (bmm / "config.yaml").write_text(
            _bmad_module_config(invocation, runtime_artifacts),
            encoding="utf-8",
        )
        core = overlay / "core"
        core.mkdir()
        (core / "config.yaml").write_text(
            _bmad_module_config(invocation, runtime_artifacts),
            encoding="utf-8",
        )
        tea = overlay / "tea"
        tea.mkdir()
        (tea / "config.yaml").write_text(
            _tea_config(invocation, runtime_artifacts),
            encoding="utf-8",
        )
    except BaseException:
        shutil.rmtree(overlay, ignore_errors=True)
        raise


def _remove_bmad_project_overlay(workspace: Path) -> None:
    overlay = workspace / "_bmad"
    marker = overlay / _BMAD_OVERLAY_MARKER
    try:
        marker_content = marker.read_text(encoding="utf-8")
    except OSError as error:
        raise _error(
            "METHOD_WORKSPACE_OVERLAY_TAMPERED",
            "AgentAttempt 结束时 BMAD Project Support Overlay 标记丢失。",
        ) from error
    if overlay.is_symlink() or marker_content != _BMAD_OVERLAY_MARKER_CONTENT:
        raise _error(
            "METHOD_WORKSPACE_OVERLAY_TAMPERED",
            "AgentAttempt 修改了 BMAD Project Support Overlay 的产品标记。",
        )
    try:
        shutil.rmtree(overlay)
    except OSError as error:
        raise _error(
            "METHOD_WORKSPACE_OVERLAY_CLEANUP_FAILED",
            "BMAD Project Support Overlay 无法在 Candidate 冻结前移除。",
        ) from error


def _bmad_config(
    invocation: WorkcellAgentInvocation,
    runtime_artifacts: Path,
) -> str:
    values = {
        "user_name": "Agent-Team-OS",
        "project_name": f"{invocation.delivery_id}-{invocation.workcell_key}",
        "communication_language": "Simplified Chinese",
        "document_output_language": "Simplified Chinese",
        "output_folder": str(runtime_artifacts),
        "planning_artifacts": str(runtime_artifacts / "planning"),
        "implementation_artifacts": str(runtime_artifacts / "implementation"),
        "project_knowledge": str(invocation.workspace.resolve() / "docs"),
    }
    return "".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
        for key, value in values.items()
    )


def _tea_config(
    invocation: WorkcellAgentInvocation,
    runtime_artifacts: Path,
) -> str:
    values: dict[str, object] = {
        "user_name": "Agent-Team-OS",
        "project_name": f"{invocation.delivery_id}-{invocation.workcell_key}",
        "communication_language": "Simplified Chinese",
        "document_output_language": "Simplified Chinese",
        "output_folder": str(runtime_artifacts),
        "test_artifacts": str(runtime_artifacts / "test-artifacts"),
        "tea_use_playwright_utils": False,
        "tea_use_pactjs_utils": False,
        "tea_pact_mcp": "none",
        "tea_browser_automation": "none",
        "tea_execution_mode": "sequential",
        "tea_capability_probe": False,
        "test_stack_type": "auto",
        "ci_platform": "none",
        "test_framework": "auto",
    }
    return "".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
        for key, value in values.items()
    )


def _bmad_module_config(
    invocation: WorkcellAgentInvocation,
    runtime_artifacts: Path,
) -> str:
    values: dict[str, object] = {
        "user_name": "Agent-Team-OS",
        "project_name": f"{invocation.delivery_id}-{invocation.workcell_key}",
        "communication_language": "Simplified Chinese",
        "document_output_language": "Simplified Chinese",
        "output_folder": str(runtime_artifacts),
        "planning_artifacts": str(runtime_artifacts / "planning"),
        "implementation_artifacts": str(runtime_artifacts / "implementation"),
        "project_knowledge": str(invocation.workspace.resolve() / "docs"),
        "user_skill_level": "expert",
    }
    return "".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
        for key, value in values.items()
    )


def _git_exclude_environment(
    environment: dict[str, str],
    exclude_file: Path,
) -> dict[str, str]:
    runtime = dict(environment)
    raw_count = runtime.get("GIT_CONFIG_COUNT", "0")
    try:
        count = int(raw_count)
    except ValueError as error:
        raise _error(
            "METHOD_WORKSPACE_GIT_CONFIG_INVALID",
            "继承的 GIT_CONFIG_COUNT 不是有效整数。",
        ) from error
    if count < 0:
        raise _error(
            "METHOD_WORKSPACE_GIT_CONFIG_INVALID",
            "继承的 GIT_CONFIG_COUNT 不能为负数。",
        )
    runtime["GIT_CONFIG_COUNT"] = str(count + 1)
    runtime[f"GIT_CONFIG_KEY_{count}"] = "core.excludesFile"
    runtime[f"GIT_CONFIG_VALUE_{count}"] = str(exclude_file)
    return runtime


def _final_messages(stdout: str) -> str:
    messages: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                messages.append(text.strip())
    if not messages:
        raise _error(
            "CODEX_WORKCELL_OUTPUT_MISSING",
            "Codex CLI 没有产生最终 Agent Message。",
        )
    return messages[-1]


def _redact(value: str) -> str:
    redacted = value
    for name, marker in os.environ.items():
        if _is_sensitive_environment_name(name) and len(marker) >= 8:
            redacted = redacted.replace(marker, "[REDACTED]")
    redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"\bgh[a-z]_[A-Za-z0-9]{16,}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", redacted)
    return redacted


_INHERITED_CODEX_ENVIRONMENT = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TERM",
        "TMP",
        "TMPDIR",
        "TEMP",
        "USER",
    }
)


def _codex_environment(overrides: dict[str, str]) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in _INHERITED_CODEX_ENVIRONMENT
    }
    environment.update(
        {
            key: value
            for key, value in overrides.items()
            if not _is_sensitive_environment_name(key)
        }
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # ``_`` is shell bookkeeping, not part of the Provider contract.  When the
    # parent executable lives below a non-ASCII path, some macOS launch paths
    # expose it with surrogate-escaped bytes.  Codex's code-mode host currently
    # treats that value as UTF-8 and can panic before the requested tool runs.
    environment.pop("_", None)
    return {
        key: value
        for key, value in environment.items()
        if _is_utf8(key) and _is_utf8(value)
    }


def _is_sensitive_environment_name(name: str) -> bool:
    return any(
        fragment in name.upper()
        for fragment in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")
    )


def _is_utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Codex Workcell AgentAttempt 失败",
        detail=detail,
        repair="检查 Codex 登录、Method Overlay、Workspace 权限与冻结 Provider Binding。",
        status_code=409,
    )
