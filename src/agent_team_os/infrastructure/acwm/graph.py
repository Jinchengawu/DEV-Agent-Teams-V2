"""ACWM v0.4 adapters for product-owned Pipeline governance."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any, Protocol, cast

from ...modules.orchestration import GraphCompilation


class PipelineBindingResolutionError(ValueError):
    """A published graph cannot snapshot its product-owned runtime bindings."""


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


class ACWMPipelineGraphRuntime:
    """Calls ACWM reducers while keeping their contracts authoritative upstream."""

    def create(
        self, run_id: str, compiled_graph: dict[str, object]
    ) -> dict[str, object]:
        domain = import_module("acwm.domain")
        graph_type = self._required(domain, "CompiledJourneyGraph")
        create_run = self._required(domain, "create_graph_run")
        graph = graph_type.model_validate(compiled_graph)
        run = create_run(run_id, graph)
        return cast(dict[str, object], run.model_dump(mode="json"))

    def transition(
        self,
        snapshot: dict[str, object],
        *,
        command: str,
        node_id: str,
        body_node_id: str | None = None,
        activated_conditions: tuple[str, ...] = (),
        exit_condition_met: bool | None = None,
    ) -> dict[str, object]:
        domain = import_module("acwm.domain")
        run_type = self._required(domain, "GraphRun")
        run = run_type.model_validate(snapshot)
        if command == "start":
            updated = self._required(domain, "start_graph_node")(run, node_id)
        elif command == "succeed":
            updated = self._required(domain, "succeed_graph_node")(
                run, node_id, activated_conditions=set(activated_conditions)
            )
        elif command == "start-loop-iteration":
            updated = self._required(domain, "start_loop_iteration")(run, node_id)
        elif command == "complete-loop-iteration":
            if exit_condition_met is None:
                raise ValueError("Loop completion requires exit_condition_met")
            updated = self._required(domain, "complete_loop_iteration")(
                run, node_id, exit_condition_met=exit_condition_met
            )
        elif command in {"start-loop-body-node", "succeed-loop-body-node"}:
            if body_node_id is None:
                raise ValueError("Loop body transition requires body_node_id")
            reducer_name = (
                "start_loop_body_node"
                if command == "start-loop-body-node"
                else "succeed_loop_body_node"
            )
            keywords = (
                {"activated_conditions": set(activated_conditions)}
                if command == "succeed-loop-body-node"
                else {}
            )
            updated = self._required(domain, reducer_name)(
                run, node_id, body_node_id, **keywords
            )
        elif command == "fail":
            updated = self._required(domain, "fail_graph_node")(run, node_id)
        elif command == "cancel":
            updated = self._required(domain, "cancel_graph_run")(run)
        else:
            raise ValueError(f"Unsupported ACWM graph transition: {command}")
        return cast(dict[str, object], updated.model_dump(mode="json"))

    @staticmethod
    def _required(domain: object, name: str) -> Any:
        value = getattr(domain, name, None)
        if value is None:
            raise ValueError(
                "ACWM_GRAPH_RUNTIME_UNAVAILABLE: install the pinned ACWM v0.4 revision"
            )
        return value


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
            try:
                binding = cast(BindingRecord, self.get_binding(capability_id))
            except KeyError as error:
                raise PipelineBindingResolutionError(
                    f"Capability {capability_id} has no binding"
                ) from error
            try:
                instance = cast(InstanceRecord, self.get_instance(binding.instance_id))
            except KeyError as error:
                raise PipelineBindingResolutionError(
                    f"Capability {capability_id} references a missing instance"
                ) from error
            if not instance.enabled or instance.health.status != "ready":
                raise PipelineBindingResolutionError(
                    f"Capability {capability_id} is not bound to a ready instance"
                )
            if binding.instance_version != instance.version:
                raise PipelineBindingResolutionError(
                    f"Capability {capability_id} binding is stale"
                )
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
