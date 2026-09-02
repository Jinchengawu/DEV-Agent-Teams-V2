from __future__ import annotations

import pytest
from pytest import MonkeyPatch

from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.features import FeatureFlags

_FLAG_NAMES = (
    "AGENT_TEAM_OS_FEATURE_FEISHU_TENANT_SYNC_V1",
    "AGENT_TEAM_OS_FEATURE_KNOWLEDGE_HYBRID_INDEX_V1",
    "AGENT_TEAM_OS_FEATURE_DELIVERY_KNOWLEDGE_CONTEXT_V1",
)


def test_v051_features_are_disabled_by_default(monkeypatch: MonkeyPatch) -> None:
    for name in _FLAG_NAMES:
        monkeypatch.delenv(name, raising=False)

    flags = FeatureFlags.from_environment()

    assert flags == FeatureFlags()


def test_delivery_context_flag_requires_gate_a_and_gate_b(monkeypatch: MonkeyPatch) -> None:
    for name in _FLAG_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_TEAM_OS_FEATURE_DELIVERY_KNOWLEDGE_CONTEXT_V1", "true")

    with pytest.raises(ProductError) as invalid:
        FeatureFlags.from_environment()

    assert invalid.value.code == "FEATURE_FLAG_CONFIGURATION_INVALID"


def test_all_v051_features_can_be_enabled_explicitly(monkeypatch: MonkeyPatch) -> None:
    for name in _FLAG_NAMES:
        monkeypatch.setenv(name, "1")

    flags = FeatureFlags.from_environment()

    assert flags.feishu_tenant_sync_v1
    assert flags.knowledge_hybrid_index_v1
    assert flags.delivery_knowledge_context_v1
