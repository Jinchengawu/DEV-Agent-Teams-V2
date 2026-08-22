"""Real code delivery boundaries backed by ACWM, Codex and Git."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from acwm.adapters.code_delivery import CodeDeliveryWorkflowAdapter
from acwm.adapters.codex_cli import CodexCLICapabilityAdapter
from acwm.config import CodexCLIConfig
from acwm.domain import (
    ResolvedCapability,
    ResolvedNode,
    ResolvedStage,
    ResolvedWorkflow,
    StageExecutionSpec,
    StageRunSpec,
    StopRequested,
)

from .delivery import (
    ApplyReceipt,
    CandidateChange,
    TaskContract,
    VerificationRun,
)
from .git_sandbox import GitSandbox, SandboxPolicy


class WorkspaceAgent(Protocol):
    evidence_identity: str

    async def run(self, *, instruction: str, workspace: Path) -> str: ...


class GitCodeExecutor:
    def __init__(self, sandbox: GitSandbox, agent: WorkspaceAgent) -> None:
        self._sandbox = sandbox
        self._agent = agent
        self.evidence_identity = agent.evidence_identity

    async def execute(
        self, task: TaskContract, workspace_id: str, delivery_id: str
    ) -> CandidateChange:
        if workspace_id != "backend-demo":
            raise ValueError("only the built-in backend-demo workspace is supported")
        base_revision = self._sandbox.main_revision()
        worktree = self._sandbox.create_worktree(delivery_id, base_revision)
        policy = SandboxPolicy(
            allowed_paths=task.system_policy.allowed_paths,
            verification_command=(
                "python",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-v",
            ),
        )
        instruction = (
            f"Implement this approved Backend task:\n{task.instructions}\n\n"
            f"Acceptance IDs: {', '.join(task.acceptance_ids)}\n"
            f"You may modify only: {', '.join(policy.allowed_paths)}.\n"
            "Do not install dependencies, change manifests, or run user-provided commands. "
            "Use only the Python standard library."
        )
        await self._agent.run(instruction=instruction, workspace=worktree)
        return self._sandbox.create_candidate(
            delivery_id=delivery_id,
            base_revision=base_revision,
            policy=policy,
        )


class GitCandidateVerifier:
    def __init__(self, sandbox: GitSandbox) -> None:
        self._sandbox = sandbox

    async def verify(
        self, candidate: CandidateChange, task: TaskContract, workspace_id: str
    ) -> VerificationRun:
        if workspace_id != "backend-demo":
            raise ValueError("only the built-in backend-demo workspace is supported")
        policy = SandboxPolicy(allowed_paths=task.system_policy.allowed_paths)
        return self._sandbox.verify_candidate(
            candidate, policy=policy, acceptance_ids=task.acceptance_ids
        )


class GitCandidateApplier:
    def __init__(self, sandbox: GitSandbox) -> None:
        self._sandbox = sandbox

    async def apply(self, candidate: CandidateChange, workspace_id: str) -> ApplyReceipt:
        if workspace_id != "backend-demo":
            raise ValueError("only the built-in backend-demo workspace is supported")
        return self._sandbox.apply_candidate(candidate)


class ACWMCodexWorkspaceAgent:
    """Run the ACWM code-delivery Workflow with a workspace-write Codex node."""

    evidence_identity = "codex-cli"

    def __init__(self, config: CodexCLIConfig | None = None) -> None:
        self._adapter = CodexCLICapabilityAdapter(
            config or CodexCLIConfig(sandbox="workspace-write", timeout_seconds=180)
        )
        self._workflow = CodeDeliveryWorkflowAdapter()

    async def run(self, *, instruction: str, workspace: Path) -> str:
        manifest = self._adapter.manifest
        capability = ResolvedCapability(
            capability_id="codex-backend",
            capability_version="1.0.0",
            adapter_type=manifest.adapter_type,
            adapter_version=manifest.adapter_version,
            features=manifest.features,
            required_features=frozenset(),
            config_fingerprint="0" * 64,
            policy_version="1.0",
            policy_fingerprint="0" * 64,
        )
        stage = ResolvedStage(
            stage_id="delivery",
            workflow=ResolvedWorkflow.from_manifest(self._workflow.manifest),
            nodes=(
                ResolvedNode(
                    node_id="delivery:developer",
                    slot="developer",
                    workflow_mode=self._workflow.manifest.mode_id,
                    workflow_version=self._workflow.manifest.mode_version,
                    capability=capability,
                ),
            ),
        )
        spec = StageExecutionSpec(
            journey_id="backend-delivery",
            attempt_id=str(uuid4()),
            objective=instruction,
            workspace=str(workspace.resolve()),
        )

        class CapabilityBoundary:
            @asynccontextmanager
            async def stage(boundary_self, run_spec: StageRunSpec) -> AsyncIterator[Any]:
                async with self._adapter.stage(run_spec, emit) as exchange:
                    yield exchange

        async def emit(
            _event: str,
            _payload: dict[str, Any] | None,
            _metadata: dict[str, Any] | None,
        ) -> None:
            return None

        try:
            result = await self._workflow.execute(spec, stage, CapabilityBoundary())
        except asyncio.CancelledError:
            await self._adapter.signal(
                StopRequested(attempt_id=spec.attempt_id, reason="delivery_cancelled")
            )
            raise
        return result.output

    async def close(self) -> None:
        await self._adapter.close()
