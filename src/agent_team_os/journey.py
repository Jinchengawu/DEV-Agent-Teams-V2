"""Resolve and fingerprint the authoritative ACWM backend-delivery Journey."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from acwm.adapters.agentscope_role_turn import AgentScopeRoleTurnAdapter
from acwm.adapters.code_delivery import CodeDeliveryWorkflowAdapter
from acwm.adapters.codex_cli import CodexCLICapabilityAdapter
from acwm.application.runtime import DefaultCapabilityRuntime
from acwm.application.workflow_runtime import DefaultWorkflowRuntime
from acwm.config import CodexCLIConfig, load_capabilities, load_journeys
from acwm.domain import JourneyDefinition


def resolve_backend_delivery_fingerprint(config_root: Path) -> str:
    definition = load_journeys(config_root / "journeys.yaml")["backend-delivery"]
    return resolve_journey_fingerprint(config_root, definition)


def resolve_journey_fingerprint(config_root: Path, definition: JourneyDefinition) -> str:
    catalog = load_capabilities(config_root / "capabilities.yaml")
    adapters = {
        "hermes-pm": CodexCLICapabilityAdapter(
            CodexCLIConfig(sandbox="read-only", timeout_seconds=120)
        ),
        "hermes-project-admin": CodexCLICapabilityAdapter(
            CodexCLIConfig(sandbox="read-only", timeout_seconds=120)
        ),
        "codex-backend": CodexCLICapabilityAdapter(
            CodexCLIConfig(sandbox="workspace-write", timeout_seconds=180)
        ),
    }
    capabilities = DefaultCapabilityRuntime(catalog=catalog, adapters=adapters, event_sink=None)
    workflows = DefaultWorkflowRuntime(
        capability_runtime=capabilities,
        adapters={
            "agentscope.role-turn": AgentScopeRoleTurnAdapter(),
            "code-delivery": CodeDeliveryWorkflowAdapter(),
        },
        validators={"backend-candidate-v1": _ResolutionOnlyValidator()},
    )
    resolved = workflows.resolve_journey(definition)
    encoded = json.dumps(
        resolved.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class _ResolutionOnlyValidator:
    async def validate(self, stage: Any, result: Any) -> Any:
        raise RuntimeError("resolution-only validator must not execute")
