from __future__ import annotations

import asyncio
import json
import os
import re

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

    async def run(self, invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
        sandbox = (
            "workspace-write" if invocation.workspace_access == "workspace_write" else "read-only"
        )
        environment = {**os.environ, **invocation.environment}
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
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=invocation.workspace,
                env=environment,
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
    sensitive_names = ("TOKEN", "SECRET", "PASSWORD", "API_KEY")
    for name, marker in os.environ.items():
        if any(fragment in name.upper() for fragment in sensitive_names) and len(marker) >= 8:
            redacted = redacted.replace(marker, "[REDACTED]")
    redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"\bgh[a-z]_[A-Za-z0-9]{16,}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", redacted)
    return redacted


def _error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Codex Workcell AgentAttempt 失败",
        detail=detail,
        repair="检查 Codex 登录、Method Overlay、Workspace 权限与冻结 Provider Binding。",
        status_code=409,
    )
