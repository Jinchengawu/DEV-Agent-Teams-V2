import asyncio
import json
from dataclasses import dataclass, field

import pytest

from agent_team_os.modules.agents import (
    AgentProfileSpec,
    AgentRuntimeDispatcher,
    RuntimeAdapterInvocation,
    RuntimeDispatchError,
    RuntimeDispatchRequest,
    RuntimeDispatchResult,
    RuntimeOutputArtifact,
)
from agent_team_os.shared.hashes import sha256_json


def _profile_revision(*, instruction: str = "只实现经过批准的前端变更") -> dict[str, object]:
    spec = AgentProfileSpec.model_validate(
        {
            "schema_version": "1",
            "id": "frontend-engineer",
            "name": "前端开发工程师",
            "description": "负责前端实现",
            "tags": ["frontend"],
            "instructions": {
                "custom_text": instruction,
                "examples": [],
            },
            "capabilities": [{"id": "frontend.implementation", "version": ">=1,<2"}],
            "policies": {
                "tool_policy_ref": "policy://frontend-tools@1",
                "resource_policy_ref": "policy://frontend-resources@1",
                "approval_policy_ref": "policy://candidate-approval@1",
                "memory_policy_ref": "policy://session-isolated@1",
                "delegation_policy_ref": "policy://no-delegation@1",
            },
            "isolation_preference": "shared",
            "extensions": {},
        }
    )
    spec_json = spec.model_dump(mode="json")
    return {
        "profile_id": "frontend-engineer",
        "revision": 2,
        "spec": spec_json,
        "canonical_json": json.dumps(
            spec_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        "sha256": sha256_json(spec_json),
        "published_by": "architect",
        "published_at": "2026-08-26T00:00:00Z",
    }


def _binding_snapshot(*, workflow_mode: str = "agentscope.role-turn") -> dict[str, object]:
    profile = _profile_revision()
    binding_hash = "b" * 64
    return {
        "profile": profile,
        "deployment": {
            "id": "frontend-deployment",
            "adapter_id": "codex.cli",
            "adapter_version": "1.0.0",
            "profile_id": "frontend-engineer",
            "profile_revision": 2,
            "profile_sha256": profile["sha256"],
            "policy_snapshot": {"workspace": "project-repository"},
        },
        "runtime_identity": "codex-cli",
        "binding": {
            "binding_fingerprint": binding_hash,
            "workflow_mode": workflow_mode,
        },
    }


@dataclass
class _RecordingAdapter:
    adapter_id: str
    workflow_mode: str
    calls: list[RuntimeAdapterInvocation] = field(default_factory=list)

    async def execute(self, invocation: RuntimeAdapterInvocation) -> RuntimeDispatchResult:
        self.calls.append(invocation)
        return RuntimeDispatchResult(
            runtime_identity="codex-cli",
            artifacts=(
                RuntimeOutputArtifact(
                    contract_id="frontend-candidate-v1",
                    media_type="application/json",
                    content={"candidate_revision": "candidate-1"},
                ),
            ),
        )


def test_dispatcher_uses_frozen_adapter_workflow_and_profile_instruction() -> None:
    async def scenario() -> None:
        role_turn = _RecordingAdapter("codex.cli", "agentscope.role-turn")
        workspace_write = _RecordingAdapter("codex.cli", "code-delivery")
        dispatcher = AgentRuntimeDispatcher((role_turn, workspace_write))

        result = await dispatcher.dispatch(
            RuntimeDispatchRequest(
                delivery_id="delivery-1",
                binding_site="frontend.actor",
                workflow_mode="agentscope.role-turn",
                objective="实现项目详情页",
                expected_artifact_contract_id="frontend-candidate-v1",
                workspace_id="project:pj1:frontend",
                resolved_binding_hash="b" * 64,
                binding_snapshot=_binding_snapshot(),
            )
        )

        assert result.runtime_identity == "codex-cli"
        assert len(role_turn.calls) == 1
        assert workspace_write.calls == []
        invocation = role_turn.calls[0]
        assert invocation.profile.spec.instructions.custom_text == ("只实现经过批准的前端变更")
        assert "只实现经过批准的前端变更" in invocation.instruction
        assert "实现项目详情页" in invocation.instruction

    asyncio.run(scenario())


def test_dispatcher_fails_closed_on_profile_or_runtime_identity_drift() -> None:
    async def profile_drift() -> None:
        adapter = _RecordingAdapter("codex.cli", "agentscope.role-turn")
        snapshot = _binding_snapshot()
        deployment = snapshot["deployment"]
        assert isinstance(deployment, dict)
        deployment["profile_sha256"] = "1" * 64

        with pytest.raises(RuntimeDispatchError, match="PROFILE_SNAPSHOT_HASH_MISMATCH"):
            await AgentRuntimeDispatcher((adapter,)).dispatch(
                RuntimeDispatchRequest(
                    delivery_id="delivery-1",
                    binding_site="frontend.actor",
                    workflow_mode="agentscope.role-turn",
                    objective="实现项目详情页",
                    expected_artifact_contract_id="frontend-candidate-v1",
                    workspace_id="project:pj1:frontend",
                    resolved_binding_hash="b" * 64,
                    binding_snapshot=snapshot,
                )
            )

    asyncio.run(profile_drift())


def test_dispatcher_rejects_artifact_contract_drift() -> None:
    async def scenario() -> None:
        adapter = _RecordingAdapter("codex.cli", "agentscope.role-turn")
        with pytest.raises(RuntimeDispatchError, match="ARTIFACT_CONTRACT_MISMATCH"):
            await AgentRuntimeDispatcher((adapter,)).dispatch(
                RuntimeDispatchRequest(
                    delivery_id="delivery-1",
                    binding_site="frontend.actor",
                    workflow_mode="agentscope.role-turn",
                    objective="实现项目详情页",
                    expected_artifact_contract_id="design-spec-v1",
                    workspace_id="project:pj1:frontend",
                    resolved_binding_hash="b" * 64,
                    binding_snapshot=_binding_snapshot(),
                )
            )

    asyncio.run(scenario())
