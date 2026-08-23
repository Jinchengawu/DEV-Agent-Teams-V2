"""Built-in Agent profiles and deployments shared by demo and release gates."""

from __future__ import annotations

from .application import AgentProfileCatalog
from .deployment_application import AgentDeploymentCatalog
from .deployment_domain import AgentDeploymentCreate
from .domain import AgentProfileCreate, AgentProfileSpec


def ensure_builtin_agent_deployments(
    profiles: AgentProfileCatalog,
    deployments: AgentDeploymentCatalog,
    *,
    planning_instance_id: str = "builtin:codex-simulated-hermes",
    execution_instance_id: str = "builtin:codex-cli",
) -> dict[str, str]:
    """Create immutable built-ins and return canonical Pipeline assignments."""
    policies = {
        "tool_policy_ref": "policy://builtin-tools@1",
        "resource_policy_ref": "policy://builtin-resources@1",
        "approval_policy_ref": "policy://candidate-approval@1",
        "memory_policy_ref": "policy://session-isolated@1",
        "delegation_policy_ref": "policy://no-delegation@1",
    }
    definitions = (
        (
            "builtin-planning-agent",
            "内置规划 Agent",
            ("hermes-pm", "hermes-project-admin"),
            planning_instance_id,
            "builtin-planning-deployment",
        ),
        (
            "builtin-backend-agent",
            "内置后端交付 Agent",
            ("codex-backend",),
            execution_instance_id,
            "builtin-backend-deployment",
        ),
    )
    existing_profiles = {item.id for item in profiles.list_profiles()}
    existing_deployments = {item.id: item for item in deployments.list()}
    for profile_id, name, capabilities, instance_id, deployment_id in definitions:
        if profile_id not in existing_profiles:
            created = profiles.create(
                AgentProfileCreate(
                    spec=AgentProfileSpec.model_validate(
                        {
                            "schema_version": "1",
                            "id": profile_id,
                            "name": name,
                            "description": "系统迁移的 V0.3 兼容角色",
                            "tags": ["builtin"],
                            "instructions": {
                                "custom_text": "遵守已发布 Pipeline 与系统安全策略",
                                "examples": [],
                            },
                            "capabilities": [
                                {"id": item, "version": ">=1,<2"}
                                for item in capabilities
                            ],
                            "policies": policies,
                            "isolation_preference": "shared",
                            "extensions": {"migration_source": "legacy-v0"},
                        }
                    )
                ),
                actor_id="system",
            )
            validated = profiles.validate_draft(
                profile_id,
                expected_version=created.draft.version,
                actor_id="system",
            )
            profiles.publish(
                profile_id,
                expected_version=validated.version,
                actor_id="system",
            )
        if deployment_id not in existing_deployments:
            created_deployment = deployments.create(
                AgentDeploymentCreate(
                    id=deployment_id,
                    name=name,
                    profile_id=profile_id,
                    profile_revision=1,
                    instance_id=instance_id,
                    provider_id="codex-cli-provider",
                ),
                actor_id="system",
            )
            qualified = deployments.qualify(
                deployment_id, created_deployment.version
            )
            if qualified.qualification_status != "qualified":
                raise RuntimeError(
                    "built-in Agent Deployment qualification failed: "
                    f"{qualified.qualification_errors}"
                )
            deployments.set_enabled(deployment_id, qualified.version, True)
    return {
        "requirements.actor": "builtin-planning-deployment",
        "tasking.actor": "builtin-planning-deployment",
        "code-repair/delivery.developer": "builtin-backend-deployment",
    }
