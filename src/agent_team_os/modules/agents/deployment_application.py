from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol

from acwm.application import DefaultProviderResolver, ProviderResolutionRequest
from acwm.domain import (
    CapabilityFeature,
    CapabilityProviderManifest,
    ProviderBindingSite,
    ResolvedCapability,
    WorkflowRequirements,
)

from ...shared.errors import ProductError
from ...shared.hashes import Sha256
from .application import AgentProfileCatalog
from .deployment_domain import (
    AgentDeployment,
    AgentDeploymentCreate,
    AgentDeploymentPatch,
)
from .deployment_repository import SQLiteAgentDeploymentRepository
from .provider_manifests import ProviderManifestCatalog


class RuntimeInstanceCatalogPort(Protocol):
    def get_instance(self, instance_id: str) -> Any: ...


class AgentDeploymentCatalog:
    def __init__(
        self,
        repository: SQLiteAgentDeploymentRepository,
        profiles: AgentProfileCatalog,
        instances: RuntimeInstanceCatalogPort,
        providers: ProviderManifestCatalog,
    ) -> None:
        self.repository = repository
        self.profiles = profiles
        self.instances = instances
        self.providers = providers

    def create(self, request: AgentDeploymentCreate, *, actor_id: str) -> AgentDeployment:
        revision = self.profiles.get_revision(request.profile_id, request.profile_revision)
        instance = self._instance(request.instance_id)
        provider = self._provider(request.provider_id)
        if instance.runtime_type not in self.providers.runtime_types(request.provider_id):
            raise self._conflict(
                "AGENT_DEPLOYMENT_RUNTIME_INCOMPATIBLE",
                "运行实例类型与 Provider 不兼容。",
                "选择 Provider 支持的运行实例类型。",
            )
        now = datetime.now(UTC)
        deployment = AgentDeployment(
            id=request.id,
            name=request.name,
            profile_id=request.profile_id,
            profile_revision=request.profile_revision,
            profile_sha256=revision.sha256,
            capability_requirements=revision.spec.capabilities,
            instance_id=instance.id,
            instance_version=instance.version,
            adapter_id=instance.adapter_id or "unknown",
            adapter_version=instance.adapter_version or "unknown",
            provider_id=provider.provider_id,
            provider_revision=provider.provider_revision,
            provider_fingerprint=Sha256.validate(provider.manifest_fingerprint),
            isolation_mode=revision.spec.isolation_preference,
            policy_snapshot=revision.spec.policies.model_dump(mode="json"),
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        try:
            return self.repository.create(deployment)
        except Exception as error:
            if "UNIQUE" in str(error):
                raise self._conflict(
                    "AGENT_DEPLOYMENT_EXISTS",
                    "同名 Deployment 已经存在。",
                    "使用新的 Deployment ID。",
                ) from error
            raise

    def list(self) -> tuple[AgentDeployment, ...]:
        return self.repository.list()

    def get(self, deployment_id: str) -> AgentDeployment:
        try:
            return self.repository.get(deployment_id)
        except KeyError as error:
            raise ProductError(
                code="AGENT_DEPLOYMENT_NOT_FOUND",
                title="Agent 部署不存在",
                detail=f"没有找到部署 {deployment_id}。",
                repair="刷新部署列表后重新选择。",
                status_code=404,
            ) from error

    def patch(self, deployment_id: str, request: AgentDeploymentPatch) -> AgentDeployment:
        current = self.get(deployment_id)
        self._version(current, request.expected_version)
        profile_id = request.profile_id or current.profile_id
        profile_revision = request.profile_revision or current.profile_revision
        instance_id = request.instance_id or current.instance_id
        provider_id = request.provider_id or current.provider_id
        revision = self.profiles.get_revision(profile_id, profile_revision)
        instance = self._instance(instance_id)
        provider = self._provider(provider_id)
        if instance.runtime_type not in self.providers.runtime_types(provider_id):
            raise self._conflict(
                "AGENT_DEPLOYMENT_RUNTIME_INCOMPATIBLE",
                "运行实例类型与 Provider 不兼容。",
                "选择 Provider 支持的运行实例类型。",
            )
        updated = current.model_copy(
            update={
                "name": request.name or current.name,
                "profile_id": profile_id,
                "profile_revision": profile_revision,
                "profile_sha256": revision.sha256,
                "capability_requirements": revision.spec.capabilities,
                "instance_id": instance_id,
                "instance_version": instance.version,
                "adapter_id": instance.adapter_id or "unknown",
                "adapter_version": instance.adapter_version or "unknown",
                "provider_id": provider_id,
                "provider_revision": provider.provider_revision,
                "provider_fingerprint": provider.manifest_fingerprint,
                "isolation_mode": revision.spec.isolation_preference,
                "policy_snapshot": revision.spec.policies.model_dump(mode="json"),
                "qualification_status": "unknown",
                "qualification_errors": (),
                "enabled": False,
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._save(current, updated, "agent-deployment.updated")
        return updated

    def qualify(self, deployment_id: str, expected_version: int) -> AgentDeployment:
        current = self.get(deployment_id)
        self._version(current, expected_version)
        errors = self._qualification_errors(current)
        updated = current.model_copy(
            update={
                "qualification_status": "qualified" if not errors else "failed",
                "qualification_errors": errors,
                "enabled": current.enabled if not errors else False,
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._save(current, updated, "agent-deployment.qualified")
        return updated

    def set_enabled(
        self, deployment_id: str, expected_version: int, enabled: bool
    ) -> AgentDeployment:
        current = self.get(deployment_id)
        self._version(current, expected_version)
        if enabled and current.qualification_status != "qualified":
            raise self._conflict(
                "AGENT_DEPLOYMENT_NOT_QUALIFIED",
                "Deployment 尚未通过资格检查。",
                "先执行资格检查并修复全部错误。",
            )
        updated = current.model_copy(
            update={
                "enabled": enabled,
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._save(
            current,
            updated,
            "agent-deployment.enabled" if enabled else "agent-deployment.disabled",
        )
        return updated

    def _qualification_errors(self, deployment: AgentDeployment) -> tuple[str, ...]:
        errors: list[str] = []
        revision = self.profiles.get_revision(deployment.profile_id, deployment.profile_revision)
        instance = self._instance(deployment.instance_id)
        provider = self._provider(deployment.provider_id)
        if revision.sha256 != deployment.profile_sha256:
            errors.append("AGENT_PROFILE_REVISION_STALE")
        if revision.spec.capabilities != deployment.capability_requirements:
            errors.append("AGENT_PROFILE_CAPABILITY_SNAPSHOT_STALE")
        if instance.version != deployment.instance_version:
            errors.append("RUNTIME_INSTANCE_VERSION_STALE")
        if not instance.enabled or getattr(instance.health, "status", None) != "ready":
            errors.append("RUNTIME_INSTANCE_NOT_READY")
        if (
            instance.adapter_id != deployment.adapter_id
            or instance.adapter_version != deployment.adapter_version
        ):
            errors.append("RUNTIME_ADAPTER_VERSION_STALE")
        if provider.manifest_fingerprint != deployment.provider_fingerprint:
            errors.append("PROVIDER_MANIFEST_STALE")
        if deployment.isolation_mode == "dedicated" and any(
            item.id != deployment.id and item.instance_id == deployment.instance_id and item.enabled
            for item in self.repository.list()
        ):
            errors.append("DEDICATED_INSTANCE_ALREADY_SHARED")
        granted = frozenset({"workspace:read", "workspace:write"})
        for requirement in revision.spec.capabilities:
            feature_values = frozenset(instance.features)
            resolved = ResolvedCapability(
                capability_id=requirement.id,
                capability_version=self._provider_capability_version(provider, requirement.id),
                adapter_type=deployment.adapter_id,
                adapter_version=deployment.adapter_version,
                features=frozenset(CapabilityFeature(value) for value in feature_values),
                required_features=provider.required_features,
                config_fingerprint=_fingerprint(instance.connection),
                policy_version="1",
                policy_fingerprint=_fingerprint(deployment.policy_snapshot),
            )
            report = DefaultProviderResolver({}).inspect(
                ProviderResolutionRequest(
                    site=ProviderBindingSite(
                        node_path="qualification",
                        stage_id="qualification",
                        slot=requirement.id,
                    ),
                    capability=resolved,
                    requirements=WorkflowRequirements(
                        mode_id=provider.workflow_modes[0],
                        mode_version="1.0.0",
                        required=provider.required_features,
                    ),
                    provider=provider,
                    capability_version_constraint=requirement.version,
                    granted_permissions=granted,
                )
            )
            errors.extend(issue.code for issue in report.issues)
        return tuple(dict.fromkeys(errors))

    @staticmethod
    def _provider_capability_version(
        provider: CapabilityProviderManifest, capability_id: str
    ) -> str:
        for capability in provider.capabilities:
            if capability.id == capability_id:
                return capability.version
        return "0.0.0"

    def _instance(self, instance_id: str) -> Any:
        try:
            return self.instances.get_instance(instance_id)
        except KeyError as error:
            raise ProductError(
                code="RUNTIME_INSTANCE_NOT_FOUND",
                title="运行实例不存在",
                detail=f"没有找到运行实例 {instance_id}。",
                repair="刷新运行实例列表后重新选择。",
                status_code=404,
            ) from error

    def _provider(self, provider_id: str) -> CapabilityProviderManifest:
        try:
            return self.providers.get(provider_id)
        except KeyError as error:
            raise ProductError(
                code="PROVIDER_MANIFEST_NOT_FOUND",
                title="Provider Manifest 不存在",
                detail=f"没有找到 Provider {provider_id}。",
                repair="刷新 Provider 列表后重新选择。",
                status_code=404,
            ) from error

    def _save(self, current: AgentDeployment, updated: AgentDeployment, event_type: str) -> None:
        if not self.repository.compare_and_swap(current.version, updated, event_type):
            latest = self.get(current.id)
            raise ProductError(
                code="AGENT_DEPLOYMENT_VERSION_CONFLICT",
                title="Agent 部署版本冲突",
                detail="当前部署已被其他操作更新。",
                repair="刷新部署详情后重新提交。",
                expected_version=current.version,
                actual_version=latest.version,
            )

    @staticmethod
    def _version(current: AgentDeployment, expected: int) -> None:
        if current.version != expected:
            raise ProductError(
                code="AGENT_DEPLOYMENT_VERSION_CONFLICT",
                title="Agent 部署版本冲突",
                detail="当前部署已被其他操作更新。",
                repair="刷新部署详情后重新提交。",
                expected_version=expected,
                actual_version=current.version,
            )

    @staticmethod
    def _conflict(code: str, detail: str, repair: str) -> ProductError:
        return ProductError(code=code, title="Agent 部署不可用", detail=detail, repair=repair)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
