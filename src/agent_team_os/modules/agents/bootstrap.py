"""Built-in Agent profiles and deployments shared by demo and release gates."""

from __future__ import annotations

from .application import AgentProfileCatalog
from .deployment_application import AgentDeploymentCatalog
from .deployment_domain import AgentDeploymentCreate, AgentDeploymentPatch
from .domain import (
    AgentProfileCreate,
    AgentProfileDraftPatch,
    AgentProfileSpec,
)


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
            (
                "hermes-pm",
                "hermes-project-admin",
                "product.analysis",
                "task.planning",
            ),
            planning_instance_id,
            "builtin-planning-deployment",
            (
                "把用户意图收敛为有边界、可审查、可机器验收的产品需求与单一任务；"
                "明确非目标、风险和稳定验收 ID。"
            ),
            (),
        ),
        (
            "builtin-backend-agent",
            "内置后端交付 Agent",
            ("codex-backend",),
            execution_instance_id,
            "builtin-backend-deployment",
            (
                "只在系统授予的后端仓库和允许路径内实现任务，保留真实 Git 差异，"
                "并以固定机器测试结果为准。"
            ),
            (),
        ),
        (
            "builtin-design-agent",
            "内置 UI 设计 Agent",
            ("design.system",),
            execution_instance_id,
            "builtin-design-deployment",
            (
                "把已批准需求转化为可追溯的中文 UI 规范、交互状态和验收约束；"
                "不得把概念稿冒充已实现界面。"
            ),
            (
                {
                    "id": "open-design",
                    "kind": "skill",
                    "version": ">=1,<2",
                    "optional": True,
                },
            ),
        ),
        (
            "builtin-frontend-agent",
            "内置前端开发 Agent",
            ("frontend.implementation",),
            execution_instance_id,
            "builtin-frontend-deployment",
            "依据已批准需求和设计规范实现中文前端交互，覆盖加载、空、错误、冲突和就绪状态，并连接真实接口。",
            (),
        ),
        (
            "builtin-qa-agent",
            "内置测试审查 Agent",
            ("testing.review",),
            execution_instance_id,
            "builtin-qa-deployment",
            (
                "独立补充跨模块验收测试和可审计测试报告；不得修改产品实现来掩盖失败，"
                "也不得把 skipped 或 WARN 计为通过。"
            ),
            (),
        ),
    )
    existing_profiles = {item.id: item for item in profiles.list_profiles()}
    existing_deployments = {item.id: item for item in deployments.list()}
    for (
        profile_id,
        name,
        capabilities,
        instance_id,
        deployment_id,
        instructions,
        extension_requirements,
    ) in definitions:
        provider_id = _provider_id_for_instance(deployments, instance_id)
        desired_spec = AgentProfileSpec.model_validate(
            {
                "schema_version": "1",
                "id": profile_id,
                "name": name,
                "description": "系统内置的五角色交付角色",
                "tags": ["builtin"],
                "instructions": {
                    "custom_text": ("遵守已发布 Pipeline 与系统安全策略。\n" + instructions),
                    "examples": [],
                },
                "capabilities": [{"id": item, "version": ">=1,<2"} for item in capabilities],
                "policies": policies,
                "isolation_preference": "shared",
                "extensions": {
                    "runtime_extensions": list(extension_requirements),
                    "migration_source": "builtin-v1",
                },
            }
        )
        if profile_id not in existing_profiles:
            created = profiles.create(
                AgentProfileCreate(spec=desired_spec),
                actor_id="system",
            )
            validated = profiles.validate_draft(
                profile_id,
                expected_version=created.draft.version,
                actor_id="system",
            )
            published = profiles.publish(
                profile_id,
                expected_version=validated.version,
                actor_id="system",
            )
            profile_revision = published.revision
        else:
            profile_revision = _ensure_builtin_profile_revision(
                profiles, profile_id=profile_id, desired_spec=desired_spec
            )
        if deployment_id not in existing_deployments:
            created_deployment = deployments.create(
                AgentDeploymentCreate(
                    id=deployment_id,
                    name=name,
                    profile_id=profile_id,
                    profile_revision=profile_revision,
                    instance_id=instance_id,
                    provider_id=provider_id,
                ),
                actor_id="system",
            )
            qualified = deployments.qualify(deployment_id, created_deployment.version)
            if qualified.qualification_status != "qualified":
                raise RuntimeError(
                    "built-in Agent Deployment qualification failed: "
                    f"{qualified.qualification_errors}"
                )
            deployments.set_enabled(deployment_id, qualified.version, True)
        else:
            _refresh_builtin_deployment(
                deployments,
                profiles,
                deployment_id=deployment_id,
                profile_id=profile_id,
                profile_revision=profile_revision,
                instance_id=instance_id,
                provider_id=provider_id,
            )
    return {
        "requirements.actor": "builtin-planning-deployment",
        "tasking.actor": "builtin-planning-deployment",
        "code-repair/delivery.developer": "builtin-backend-deployment",
    }


def _refresh_builtin_deployment(
    deployments: AgentDeploymentCatalog,
    profiles: AgentProfileCatalog,
    *,
    deployment_id: str,
    profile_id: str,
    profile_revision: int,
    instance_id: str,
    provider_id: str,
) -> None:
    current = deployments.get(deployment_id)
    profile = profiles.get_revision(profile_id, profile_revision)
    instance = deployments.instances.get_instance(instance_id)
    provider = deployments.providers.get(provider_id)
    stale = any(
        (
            current.profile_sha256 != profile.sha256,
            current.instance_version != instance.version,
            current.adapter_id != (instance.adapter_id or "unknown"),
            current.adapter_version != (instance.adapter_version or "unknown"),
            current.provider_id != provider_id,
            current.provider_fingerprint != provider.manifest_fingerprint,
        )
    )
    if stale:
        current = deployments.patch(
            deployment_id,
            AgentDeploymentPatch(
                expected_version=current.version,
                profile_id=profile_id,
                profile_revision=profile_revision,
                instance_id=instance_id,
                provider_id=provider_id,
            ),
        )
    if current.qualification_status != "qualified":
        current = deployments.qualify(deployment_id, current.version)
        if current.qualification_status != "qualified":
            raise RuntimeError(
                f"built-in Agent Deployment qualification failed: {current.qualification_errors}"
            )
    if not current.enabled:
        deployments.set_enabled(deployment_id, current.version, True)


def _provider_id_for_instance(
    deployments: AgentDeploymentCatalog,
    instance_id: str,
) -> str:
    runtime_type = deployments.instances.get_instance(instance_id).runtime_type
    if runtime_type in {"hermes-acp", "hermes-http"}:
        return "hermes-provider"
    return "codex-cli-provider"


def _ensure_builtin_profile_revision(
    profiles: AgentProfileCatalog,
    *,
    profile_id: str,
    desired_spec: AgentProfileSpec,
) -> int:
    profile = next(item for item in profiles.list_profiles() if item.id == profile_id)
    if profile.latest_revision is not None:
        latest = profiles.get_revision(profile_id, profile.latest_revision)
        if latest.spec == desired_spec:
            return latest.revision
    draft = profiles.get_draft(profile_id)
    if draft.spec != desired_spec:
        draft = profiles.patch_draft(
            profile_id,
            AgentProfileDraftPatch(
                expected_version=draft.version,
                spec=desired_spec,
            ),
            actor_id="system",
        )
    if draft.validation_status != "valid":
        draft = profiles.validate_draft(
            profile_id,
            expected_version=draft.version,
            actor_id="system",
        )
    return profiles.publish(
        profile_id,
        expected_version=draft.version,
        actor_id="system",
    ).revision


def ensure_builtin_fullstack_agent_deployments(
    profiles: AgentProfileCatalog,
    deployments: AgentDeploymentCatalog,
    *,
    planning_instance_id: str = "builtin:codex-simulated-hermes",
    execution_instance_id: str = "builtin:codex-cli",
) -> dict[str, str]:
    """Return the five-role assignments for the full-stack product Pipeline."""
    ensure_builtin_agent_deployments(
        profiles,
        deployments,
        planning_instance_id=planning_instance_id,
        execution_instance_id=execution_instance_id,
    )
    return {
        "requirements.actor": "builtin-planning-deployment",
        "tasking.actor": "builtin-planning-deployment",
        "design.developer": "builtin-design-deployment",
        "implementation-repair/backend.developer": "builtin-backend-deployment",
        "implementation-repair/frontend.developer": "builtin-frontend-deployment",
        "implementation-repair/qa.developer": "builtin-qa-deployment",
    }


def ensure_builtin_workcell_agent_deployments(
    profiles: AgentProfileCatalog,
    deployments: AgentDeploymentCatalog,
    *,
    planning_instance_id: str = "builtin:codex-simulated-hermes",
    execution_instance_id: str = "builtin:codex-cli",
) -> dict[str, str]:
    """Create v0.5 Workcell Main/Delegate deployments and return all frozen Stage slots."""
    base = ensure_builtin_agent_deployments(
        profiles,
        deployments,
        planning_instance_id=planning_instance_id,
        execution_instance_id=execution_instance_id,
    )
    method_lock = {
        "policy_version": "method-pack-set-v1",
        "packages": {
            "bmad-method": "6.11.0",
            "bmad-method-test-architecture-enterprise": "1.23.4",
        },
    }
    policies = {
        "tool_policy_ref": "policy://builtin-tools@1",
        "resource_policy_ref": "policy://artifact-envelope-only@1",
        "approval_policy_ref": "policy://release-gate@2",
        "memory_policy_ref": "policy://session-isolated@1",
        "delegation_policy_ref": "policy://product-workcell-one-level@1",
    }
    definitions = (
        (
            "builtin-workcell-lead-v1",
            "Workcell Lead v1",
            "workcell.lead",
            "builtin-workcell-lead-deployment",
            (
                "只根据冻结 Snapshot 规划最多三个一级 Child；综合机器验证与结构化审查，"
                "不能覆盖失败或 Blocking Finding。"
            ),
        ),
        (
            "builtin-workcell-delegate-v1",
            "Workcell Delegate v1",
            "workcell.delegate",
            "builtin-workcell-delegate-deployment",
            (
                "只执行产品分配的单一 Method Entry 与 Workspace Access；"
                "不得派生 Child，不得读取其他 Workcell Repository。"
            ),
        ),
    )
    existing_profiles = {item.id for item in profiles.list_profiles()}
    existing_deployments = {item.id for item in deployments.list()}
    for profile_id, name, capability, deployment_id, instructions in definitions:
        desired_spec = AgentProfileSpec.model_validate(
            {
                "schema_version": "1",
                "id": profile_id,
                "name": name,
                "description": "v0.5 Agent Workcell Kernel 内置角色",
                "tags": ["builtin", "workcell", "v0.5"],
                "instructions": {
                    "custom_text": instructions,
                    "examples": [],
                },
                "capabilities": [{"id": capability, "version": ">=1,<2"}],
                "policies": policies,
                "isolation_preference": "shared",
                "extensions": {
                    "runtime_extensions": [],
                    "method_pack_lock": method_lock,
                    "migration_source": "builtin-workcell-v1",
                },
            }
        )
        if profile_id not in existing_profiles:
            created = profiles.create(AgentProfileCreate(spec=desired_spec), actor_id="system")
            validated = profiles.validate_draft(
                profile_id,
                expected_version=created.draft.version,
                actor_id="system",
            )
            profile_revision = profiles.publish(
                profile_id,
                expected_version=validated.version,
                actor_id="system",
            ).revision
        else:
            profile_revision = _ensure_builtin_profile_revision(
                profiles,
                profile_id=profile_id,
                desired_spec=desired_spec,
            )
        if deployment_id not in existing_deployments:
            created_deployment = deployments.create(
                AgentDeploymentCreate(
                    id=deployment_id,
                    name=name,
                    profile_id=profile_id,
                    profile_revision=profile_revision,
                    instance_id=execution_instance_id,
                    provider_id=_provider_id_for_instance(deployments, execution_instance_id),
                ),
                actor_id="system",
            )
            qualified = deployments.qualify(deployment_id, created_deployment.version)
            if qualified.qualification_status != "qualified":
                raise RuntimeError(
                    "built-in Workcell Deployment qualification failed: "
                    f"{qualified.qualification_errors}"
                )
            deployments.set_enabled(deployment_id, qualified.version, True)
        else:
            _refresh_builtin_deployment(
                deployments,
                profiles,
                deployment_id=deployment_id,
                profile_id=profile_id,
                profile_revision=profile_revision,
                instance_id=execution_instance_id,
                provider_id=_provider_id_for_instance(deployments, execution_instance_id),
            )
    assignments = dict(base)
    assignments.pop("code-repair/delivery.developer", None)
    for stage_path in (
        "design-repair/design",
        "qa-preparation-repair/qa-preparation",
        "frontend-repair/frontend",
        "backend-repair/backend",
        "qa-delivery-repair/qa-delivery",
    ):
        assignments[f"{stage_path}.main"] = "builtin-workcell-lead-deployment"
        for slot in ("delegate_1", "delegate_2", "delegate_3"):
            assignments[f"{stage_path}.{slot}"] = "builtin-workcell-delegate-deployment"
    return assignments
