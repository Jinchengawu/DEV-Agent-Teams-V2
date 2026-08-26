from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...shared.hashes import Sha256, sha256_json
from .domain import AgentProfileRevision


class RuntimeDispatchError(RuntimeError):
    """Stable fail-closed error raised before product projections are updated."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class RuntimeOutputArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_id: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    content: dict[str, object]


class RuntimeDispatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_identity: str = Field(min_length=1)
    artifacts: tuple[RuntimeOutputArtifact, ...]


class RuntimeDispatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: str = Field(min_length=1)
    binding_site: str = Field(min_length=1)
    workflow_mode: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    expected_artifact_contract_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    resolved_binding_hash: Sha256
    binding_snapshot: dict[str, object]
    inputs: tuple[RuntimeOutputArtifact, ...] = ()


class _DeploymentSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    adapter_id: str
    adapter_version: str
    profile_id: str
    profile_revision: int
    profile_sha256: Sha256
    policy_snapshot: dict[str, object]
    extension_snapshot: tuple[dict[str, object], ...] = ()


class _BindingSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    binding_fingerprint: Sha256
    workflow_mode: str


class RuntimeAdapterInvocation(BaseModel):
    """Validated immutable input visible to a concrete Runtime Adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: str
    binding_site: str
    workflow_mode: str
    expected_artifact_contract_id: str
    workspace_id: str
    instruction: str
    profile: AgentProfileRevision
    deployment_id: str
    deployment_policy: dict[str, object]
    extension_snapshot: tuple[dict[str, object], ...]
    resolved_binding_hash: Sha256
    inputs: tuple[RuntimeOutputArtifact, ...] = ()


class AgentRuntimeAdapter(Protocol):
    adapter_id: str
    workflow_mode: str

    async def execute(self, invocation: RuntimeAdapterInvocation) -> RuntimeDispatchResult: ...


class AgentRuntimeDispatcher:
    """Select and police a Runtime Adapter from an immutable Pipeline snapshot.

    Pipeline execution supplies one frozen binding plus the Stage objective. This
    module owns all consistency checks and is therefore the only seam where a
    published Agent Profile becomes an actual runtime instruction.
    """

    def __init__(self, adapters: Iterable[AgentRuntimeAdapter]) -> None:
        self._adapters: dict[tuple[str, str], AgentRuntimeAdapter] = {}
        for adapter in adapters:
            key = (adapter.adapter_id, adapter.workflow_mode)
            if key in self._adapters:
                raise ValueError(f"duplicate Runtime Adapter: {key[0]} / {key[1]}")
            self._adapters[key] = adapter

    async def dispatch(self, request: RuntimeDispatchRequest) -> RuntimeDispatchResult:
        profile, deployment, binding, runtime_identity = self._validate_snapshot(request)
        adapter = self._adapters.get((deployment.adapter_id, request.workflow_mode))
        if adapter is None:
            raise RuntimeDispatchError(
                "RUNTIME_ADAPTER_NOT_INSTALLED",
                f"{deployment.adapter_id} does not support {request.workflow_mode}",
            )
        invocation = RuntimeAdapterInvocation(
            delivery_id=request.delivery_id,
            binding_site=request.binding_site,
            workflow_mode=request.workflow_mode,
            expected_artifact_contract_id=request.expected_artifact_contract_id,
            workspace_id=request.workspace_id,
            instruction=self._instruction(profile, request.objective),
            profile=profile,
            deployment_id=deployment.id,
            deployment_policy=deployment.policy_snapshot,
            extension_snapshot=deployment.extension_snapshot,
            resolved_binding_hash=request.resolved_binding_hash,
            inputs=request.inputs,
        )
        result = await adapter.execute(invocation)
        if result.runtime_identity != runtime_identity:
            raise RuntimeDispatchError(
                "RUNTIME_IDENTITY_MISMATCH",
                "runtime evidence identity differs from the published snapshot",
            )
        if not result.artifacts or any(
            artifact.contract_id != request.expected_artifact_contract_id
            for artifact in result.artifacts
        ):
            raise RuntimeDispatchError(
                "ARTIFACT_CONTRACT_MISMATCH",
                f"runtime output does not satisfy {request.expected_artifact_contract_id}",
            )
        return result

    @staticmethod
    def _validate_snapshot(
        request: RuntimeDispatchRequest,
    ) -> tuple[AgentProfileRevision, _DeploymentSnapshot, _BindingSnapshot, str]:
        try:
            profile = AgentProfileRevision.model_validate(request.binding_snapshot["profile"])
            deployment = _DeploymentSnapshot.model_validate(request.binding_snapshot["deployment"])
            binding = _BindingSnapshot.model_validate(request.binding_snapshot["binding"])
            runtime_identity = str(request.binding_snapshot["runtime_identity"])
        except (KeyError, TypeError, ValidationError, ValueError) as error:
            raise RuntimeDispatchError(
                "RUNTIME_BINDING_SNAPSHOT_INVALID",
                "published Provider binding snapshot is incomplete",
            ) from error
        if not runtime_identity:
            raise RuntimeDispatchError(
                "RUNTIME_BINDING_SNAPSHOT_INVALID", "runtime identity is empty"
            )
        if profile.profile_id != deployment.profile_id or (
            profile.revision != deployment.profile_revision
        ):
            raise RuntimeDispatchError(
                "PROFILE_SNAPSHOT_REVISION_MISMATCH",
                "deployment does not reference the frozen Agent Profile revision",
            )
        if profile.sha256 != deployment.profile_sha256 or (
            sha256_json(profile.spec.model_dump(mode="json")) != profile.sha256
        ):
            raise RuntimeDispatchError(
                "PROFILE_SNAPSHOT_HASH_MISMATCH",
                "Agent Profile content does not match the deployment snapshot",
            )
        if binding.binding_fingerprint != request.resolved_binding_hash:
            raise RuntimeDispatchError(
                "PROVIDER_BINDING_HASH_MISMATCH",
                "Stage binding hash differs from the published Pipeline revision",
            )
        if binding.workflow_mode != request.workflow_mode:
            raise RuntimeDispatchError(
                "WORKFLOW_MODE_MISMATCH",
                "Stage Workflow Mode differs from the resolved Provider binding",
            )
        return profile, deployment, binding, runtime_identity

    @staticmethod
    def _instruction(profile: AgentProfileRevision, objective: str) -> str:
        custom = profile.spec.instructions.custom_text.strip()
        if not custom:
            raise RuntimeDispatchError(
                "AGENT_PROFILE_INSTRUCTIONS_EMPTY",
                "published Agent Profile does not contain executable instructions",
            )
        return f"{custom}\n\n本次 Stage 目标：\n{objective.strip()}"
