from __future__ import annotations

from acwm.adapters.agentscope_role_turn import AgentScopeRoleTurnAdapter
from acwm.adapters.code_delivery import CodeDeliveryWorkflowAdapter
from acwm.application import DefaultProviderResolver, enumerate_provider_binding_sites
from acwm.domain import (
    CapabilityFeature,
    JourneyDefinition,
    LoopDefinition,
    ResolvedCapability,
    StageDefinition,
)

from ...modules.agents import AgentDeploymentCatalog, ProviderManifestCatalog
from ...shared.hashes import sha256_json
from .graph import PipelineBindingResolutionError


class AgentDeploymentBindingResolver:
    """Product assignment selection delegating semantic checks to ACWM."""

    def __init__(
        self,
        deployments: AgentDeploymentCatalog,
        providers: ProviderManifestCatalog,
    ) -> None:
        self.deployments = deployments
        self.providers = providers
        self._workflow_manifests = {
            item.mode_id: item
            for item in (
                AgentScopeRoleTurnAdapter.manifest,
                CodeDeliveryWorkflowAdapter.manifest,
            )
        }

    def snapshot(
        self,
        definition: dict[str, object],
        assignments: dict[str, str],
    ) -> dict[str, dict[str, object]]:
        journey = JourneyDefinition.model_validate(definition)
        sites = enumerate_provider_binding_sites(journey)
        required = {site.reference for site in sites}
        missing = sorted(required - set(assignments))
        unknown = sorted(set(assignments) - required)
        if missing:
            raise PipelineBindingResolutionError(
                "PROVIDER_ASSIGNMENT_MISSING: " + ", ".join(missing)
            )
        if unknown:
            raise PipelineBindingResolutionError(
                "PROVIDER_ASSIGNMENT_UNKNOWN: " + ", ".join(unknown)
            )
        stages = _stage_lookup(journey)
        snapshot: dict[str, dict[str, object]] = {}
        for site in sites:
            stage = stages[site.node_path]
            capability_id = stage.bindings[site.slot]
            deployment = self.deployments.get(assignments[site.reference])
            if not deployment.enabled or deployment.qualification_status != "qualified":
                raise PipelineBindingResolutionError(
                    f"AGENT_DEPLOYMENT_NOT_EXECUTABLE: {deployment.id}"
                )
            instance = self.deployments.instances.get_instance(deployment.instance_id)
            if instance.version != deployment.instance_version:
                raise PipelineBindingResolutionError(
                    f"RUNTIME_INSTANCE_VERSION_STALE: {deployment.id}"
                )
            if not instance.enabled or instance.health.status != "ready":
                raise PipelineBindingResolutionError(
                    f"RUNTIME_INSTANCE_NOT_READY: {deployment.id}"
                )
            profile = self.deployments.profiles.get_revision(
                deployment.profile_id, deployment.profile_revision
            )
            requirement = next(
                (
                    item
                    for item in profile.spec.capabilities
                    if item.id == capability_id
                ),
                None,
            )
            if requirement is None:
                raise PipelineBindingResolutionError(
                    f"AGENT_PROFILE_CAPABILITY_MISSING: {site.reference}"
                )
            provider = self.providers.get(deployment.provider_id)
            manifest = self._workflow_manifests.get(stage.workflow_mode)
            if manifest is None or site.slot not in manifest.bindings:
                raise PipelineBindingResolutionError(
                    f"WORKFLOW_BINDING_SLOT_UNKNOWN: {site.reference}"
                )
            workflow_requirements = manifest.bindings[site.slot].requirements(
                manifest.mode_id, manifest.mode_version
            )
            provider_version = next(
                (
                    item.version
                    for item in provider.capabilities
                    if item.id == capability_id
                ),
                "0.0.0",
            )
            try:
                features = frozenset(
                    CapabilityFeature(value) for value in instance.features
                )
            except ValueError as error:
                raise PipelineBindingResolutionError(
                    f"RUNTIME_FEATURE_UNKNOWN: {deployment.id}"
                ) from error
            capability = ResolvedCapability(
                capability_id=capability_id,
                capability_version=provider_version,
                adapter_type=deployment.adapter_id,
                adapter_version=deployment.adapter_version,
                features=features,
                required_features=workflow_requirements.required,
                config_fingerprint=sha256_json(
                    {
                        "instance_id": deployment.instance_id,
                        "instance_version": deployment.instance_version,
                    }
                ),
                policy_version="1",
                policy_fingerprint=sha256_json(deployment.policy_snapshot),
            )
            resolver = DefaultProviderResolver(
                {site.reference: provider},
                version_constraints={site.reference: requirement.version},
                granted_permissions={
                    site.reference: frozenset({"workspace:read", "workspace:write"})
                },
            )
            try:
                binding = resolver.resolve(site, capability, workflow_requirements)
            except ValueError as error:
                code = getattr(error, "code", "PROVIDER_INCOMPATIBLE")
                raise PipelineBindingResolutionError(
                    f"{code}: {site.reference}: {error}"
                ) from error
            snapshot[site.reference] = {
                "deployment": deployment.model_dump(mode="json"),
                "runtime_identity": instance.health.identity,
                "binding": binding.model_dump(mode="json"),
            }
        return snapshot


def _stage_lookup(definition: JourneyDefinition) -> dict[str, StageDefinition]:
    result: dict[str, StageDefinition] = {}
    for node in definition.graph_nodes:
        if isinstance(node, StageDefinition):
            result[node.id] = node
        elif isinstance(node, LoopDefinition):
            for child in node.nodes:
                if isinstance(child, StageDefinition):
                    result[f"{node.id}/{child.id}"] = child
    return result
