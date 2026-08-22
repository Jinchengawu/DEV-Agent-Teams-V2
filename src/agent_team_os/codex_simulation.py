"""Temporary Codex-backed simulation of the Hermes planning roles.

The simulator preserves role boundaries and evidence identity. It is not valid
proof that a Hermes instance was called.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from acwm.adapters.agentscope_role_turn import AgentScopeRoleTurnAdapter
from acwm.adapters.codex_cli import CodexCLICapabilityAdapter
from acwm.config import CodexCLIConfig
from acwm.domain import (
    ResolvedCapability,
    ResolvedNode,
    ResolvedStage,
    ResolvedWorkflow,
    StageExecutionSpec,
    StageRunSpec,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from .delivery import PlanningServiceError, RequirementArtifact, TaskContract
from .shared.hashes import sha256_json


class CodexRoleRunner(Protocol):
    async def run(self, role: str, prompt: str) -> str: ...


class PlanningOutputError(PlanningServiceError):
    pass


class _TaskSemantics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    instructions: str
    acceptance_ids: tuple[str, ...]


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class CodexSimulatedHermesPlanning:
    evidence_identity = "codex-simulated-hermes"

    def __init__(self, runner: CodexRoleRunner) -> None:
        self._runner = runner

    async def analyze(self, user_request: str) -> RequirementArtifact:
        prompt = f"""You are temporarily simulating the Hermes PM role.
Return raw JSON only with: summary, non_goals, risks, acceptance_criteria.
Each acceptance criterion must have a stable id and a machine-verifiable statement.
Do not include permissions, commands, paths, markdown or commentary.

User request:
{user_request}
"""
        return await self._structured("hermes-pm-simulator", prompt, RequirementArtifact)

    async def plan(self, requirements: RequirementArtifact) -> TaskContract:
        prompt = f"""You are temporarily simulating the Hermes Project Admin role.
Return raw JSON only with: title, instructions, acceptance_ids.
Create exactly one bounded Backend task. Use only acceptance ids from the input.
The instructions must require a non-empty source change and corresponding machine test change.
Do not include permissions, commands, paths, system_policy, markdown or commentary.

Approved requirements:
{requirements.model_dump_json(indent=2)}
"""
        semantics = await self._structured("hermes-admin-simulator", prompt, _TaskSemantics)
        allowed_ids = {criterion.id for criterion in requirements.acceptance_criteria}
        if not semantics.acceptance_ids or not set(semantics.acceptance_ids) <= allowed_ids:
            raise PlanningOutputError("Task referenced unknown acceptance criteria")
        return TaskContract(
            title=semantics.title,
            instructions=semantics.instructions,
            acceptance_ids=semantics.acceptance_ids,
        )

    async def _structured(
        self, role: str, prompt: str, model: type[StructuredModel]
    ) -> StructuredModel:
        last_error: Exception | None = None
        for attempt in range(2):
            response = await self._runner.run(role, prompt)
            try:
                return model.model_validate_json(self._json_object(response))
            except (ValidationError, ValueError) as error:
                last_error = error
                prompt += (
                    "\n\nYour previous response violated the JSON contract. "
                    "Return one corrected raw JSON object only."
                )
                if attempt == 1:
                    break
        raise PlanningOutputError(
            "Codex simulator returned invalid structured output"
        ) from last_error

    @staticmethod
    def _json_object(response: str) -> str:
        start = response.find("{")
        end = response.rfind("}")
        if start < 0 or end < start:
            raise ValueError("No JSON object found")
        return response[start : end + 1]


class ACWMCodexRoleRunner:
    """AgentScope role turn backed by ACWM's controlled Codex Capability."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: CodexCLIConfig | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self._config = config or CodexCLIConfig(
            sandbox="read-only", timeout_seconds=120
        )
        self._adapter = CodexCLICapabilityAdapter(self._config)
        self._role_turn = AgentScopeRoleTurnAdapter()

    async def run(self, role: str, prompt: str) -> str:
        manifest = self._adapter.manifest
        capability = ResolvedCapability(
            capability_id=f"codex-{role}",
            capability_version="1.0.0",
            adapter_type=manifest.adapter_type,
            adapter_version=manifest.adapter_version,
            features=manifest.features,
            required_features=frozenset(),
            config_fingerprint=sha256_json(self._config.model_dump(mode="json")),
            policy_version="1.0",
            policy_fingerprint=sha256_json(
                {
                    "sandbox": "read-only",
                    "workspace": str(self.workspace),
                    "role": role,
                }
            ),
        )
        stage = ResolvedStage(
            stage_id=role,
            workflow=ResolvedWorkflow.from_manifest(self._role_turn.manifest),
            nodes=(
                ResolvedNode(
                    node_id=f"{role}:actor",
                    slot="actor",
                    workflow_mode=self._role_turn.manifest.mode_id,
                    workflow_version=self._role_turn.manifest.mode_version,
                    capability=capability,
                ),
            ),
        )
        spec = StageExecutionSpec(
            journey_id="codex-simulated-planning",
            attempt_id=str(uuid4()),
            objective=prompt,
            workspace=str(self.workspace),
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

        result = await self._role_turn.execute(spec, stage, CapabilityBoundary())
        return result.output

    async def close(self) -> None:
        await self._adapter.close()
