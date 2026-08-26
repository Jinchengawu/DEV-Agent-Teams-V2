from __future__ import annotations

from acwm.domain import (
    ArtifactContract,
    ArtifactModality,
    CapabilityFeature,
    CapabilityProviderManifest,
    ProviderCapability,
)

from ...shared.hashes import Sha256
from .deployment_domain import ProviderManifestView


class ProviderManifestCatalog:
    def __init__(self) -> None:
        self._entries = {
            item.provider_id: item
            for item in (
                _codex_provider(),
                _hermes_provider(),
            )
        }

    def list(self) -> tuple[ProviderManifestView, ...]:
        return tuple(
            self._view(item) for item in sorted(self._entries.values(), key=lambda x: x.provider_id)
        )

    def get(self, provider_id: str) -> CapabilityProviderManifest:
        try:
            return self._entries[provider_id]
        except KeyError as error:
            raise KeyError(provider_id) from error

    def runtime_types(self, provider_id: str) -> tuple[str, ...]:
        if provider_id == "codex-cli-provider":
            return ("codex-cli",)
        if provider_id == "hermes-provider":
            return ("hermes-acp", "hermes-http")
        return ()

    def view(self, provider_id: str) -> ProviderManifestView:
        return self._view(self.get(provider_id))

    def _view(self, item: CapabilityProviderManifest) -> ProviderManifestView:
        return ProviderManifestView(
            id=item.provider_id,
            revision=item.provider_revision,
            fingerprint=Sha256.validate(item.manifest_fingerprint),
            runtime_types=self.runtime_types(item.provider_id),
            capabilities=tuple(
                {"id": capability.id, "version": capability.version}
                for capability in item.capabilities
            ),
            workflow_modes=item.workflow_modes,
            required_features=tuple(sorted(feature.value for feature in item.required_features)),
            input_contracts=tuple(
                contract.model_dump(mode="json") for contract in item.input_contracts
            ),
            output_contracts=tuple(
                contract.model_dump(mode="json") for contract in item.output_contracts
            ),
            permission_requirements=item.permission_requirements,
        )


def _codex_provider() -> CapabilityProviderManifest:
    return CapabilityProviderManifest.create(
        provider_id="codex-cli-provider",
        provider_revision="2",
        capabilities=tuple(
            ProviderCapability(id=capability_id, version="1.0.0")
            for capability_id in (
                "codex-backend",
                "design.system",
                "hermes-pm",
                "hermes-project-admin",
                "frontend.implementation",
                "testing.review",
                "product.analysis",
                "task.planning",
            )
        ),
        workflow_modes=("agentscope.role-turn", "code-delivery"),
        required_features=frozenset({CapabilityFeature.TEXT_FINAL}),
        optional_features=frozenset({CapabilityFeature.CWD_BINDING, CapabilityFeature.TOOL_EVENTS}),
        output_contracts=(
            ArtifactContract(
                id="agent-output",
                version="1.0.0",
                modalities=frozenset(
                    {ArtifactModality.TEXT, ArtifactModality.STRUCTURED, ArtifactModality.FILE}
                ),
            ),
        ),
        permission_requirements=("workspace:read",),
    )


def _hermes_provider() -> CapabilityProviderManifest:
    return CapabilityProviderManifest.create(
        provider_id="hermes-provider",
        provider_revision="1",
        capabilities=tuple(
            ProviderCapability(id=capability_id, version="1.0.0")
            for capability_id in (
                "hermes-pm",
                "hermes-project-admin",
                "product.analysis",
                "task.planning",
            )
        ),
        workflow_modes=("agentscope.role-turn",),
        required_features=frozenset({CapabilityFeature.TEXT_FINAL}),
        output_contracts=(
            ArtifactContract(
                id="agent-output",
                version="1.0.0",
                modalities=frozenset({ArtifactModality.TEXT, ArtifactModality.STRUCTURED}),
            ),
        ),
    )
