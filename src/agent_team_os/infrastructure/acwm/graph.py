"""ACWM v0.4 adapters for product-owned Pipeline governance."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast

from ...modules.orchestration import GraphCompilation


class ACWMGraphCompiler:
    def compile(self, definition: dict[str, object]) -> GraphCompilation:
        domain = import_module("acwm.domain")
        journey_type = getattr(domain, "JourneyDefinition", None)
        compile_graph = getattr(domain, "compile_journey_graph", None)
        if journey_type is None or compile_graph is None:
            raise ValueError(
                "ACWM_GRAPH_RUNTIME_UNAVAILABLE: install the pinned ACWM v0.4 revision"
            )
        parsed = journey_type.model_validate(definition)
        compiled = compile_graph(parsed)
        return GraphCompilation(
            graph=cast(dict[str, object], compiled.model_dump(mode="json")),
            fingerprint=str(compiled.fingerprint),
            capability_ids=tuple(sorted(_capability_ids(definition))),
        )


class BindingRecord(Protocol):
    instance_id: str
    instance_version: int


class HealthRecord(Protocol):
    status: str
    identity: str | None


class InstanceRecord(Protocol):
    id: str
    version: int
    runtime_type: str
    enabled: bool
    health: HealthRecord


class ControlPlaneBindingResolver:
    def __init__(
        self,
        get_binding: Callable[[str], object],
        get_instance: Callable[[str], object],
    ) -> None:
        self.get_binding = get_binding
        self.get_instance = get_instance

    def snapshot(
        self, capability_ids: tuple[str, ...]
    ) -> dict[str, dict[str, object]]:
        snapshot: dict[str, dict[str, object]] = {}
        for capability_id in capability_ids:
            binding = cast(BindingRecord, self.get_binding(capability_id))
            instance = cast(InstanceRecord, self.get_instance(binding.instance_id))
            if not instance.enabled or instance.health.status != "ready":
                raise ValueError(f"Capability {capability_id} is not bound to a ready instance")
            if binding.instance_version != instance.version:
                raise ValueError(f"Capability {capability_id} binding is stale")
            snapshot[capability_id] = {
                "instance_id": instance.id,
                "instance_version": instance.version,
                "runtime_type": instance.runtime_type,
                "identity": instance.health.identity,
            }
        return snapshot


def _capability_ids(definition: dict[str, object]) -> set[str]:
    result: set[str] = set()
    raw_nodes = definition.get("nodes") or definition.get("steps") or []
    if not isinstance(raw_nodes, list | tuple):
        return result
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            continue
        bindings = raw_node.get("bindings")
        if isinstance(bindings, dict):
            result.update(str(value) for value in bindings.values())
        if raw_node.get("kind") == "loop":
            result.update(_capability_ids({"nodes": raw_node.get("nodes", [])}))
    return result
