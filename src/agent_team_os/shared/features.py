from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict

from .errors import ProductError


class FeatureFlags(BaseModel):
    """Explicit v0.5.1 rollout boundaries; disabled means no implicit runtime use."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feishu_tenant_sync_v1: bool = False
    knowledge_hybrid_index_v1: bool = False
    delivery_knowledge_context_v1: bool = False

    @classmethod
    def from_environment(cls) -> FeatureFlags:
        flags = cls(
            feishu_tenant_sync_v1=_enabled(
                os.environ.get("AGENT_TEAM_OS_FEATURE_FEISHU_TENANT_SYNC_V1")
            ),
            knowledge_hybrid_index_v1=_enabled(
                os.environ.get("AGENT_TEAM_OS_FEATURE_KNOWLEDGE_HYBRID_INDEX_V1")
            ),
            delivery_knowledge_context_v1=_enabled(
                os.environ.get("AGENT_TEAM_OS_FEATURE_DELIVERY_KNOWLEDGE_CONTEXT_V1")
            ),
        )
        flags.require_valid_dependencies()
        return flags

    def require_valid_dependencies(self) -> None:
        if self.knowledge_hybrid_index_v1 and not self.feishu_tenant_sync_v1:
            raise _dependency_error("knowledge_hybrid_index_v1 requires feishu_tenant_sync_v1")
        if self.delivery_knowledge_context_v1 and not self.knowledge_hybrid_index_v1:
            raise _dependency_error(
                "delivery_knowledge_context_v1 requires knowledge_hybrid_index_v1"
            )


def _enabled(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise _dependency_error(f"invalid feature flag value: {value!r}")


def _dependency_error(detail: str) -> ProductError:
    return ProductError(
        code="FEATURE_FLAG_CONFIGURATION_INVALID",
        title="Feature Flag 配置无效",
        detail=detail,
        repair="按 Gate A → Gate B → Gate C 顺序启用 v0.5.1 Feature Flag。",
        status_code=503,
    )
