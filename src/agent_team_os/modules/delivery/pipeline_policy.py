"""Product compatibility policy for executable Backend Delivery Pipelines."""

from __future__ import annotations

from typing import cast


class BackendDeliveryPipelinePolicy:
    legacy_capabilities = frozenset({"hermes-pm", "hermes-project-admin", "codex-backend"})
    fullstack_capabilities = frozenset(
        {
            "hermes-pm",
            "hermes-project-admin",
            "design.system",
            "codex-backend",
            "frontend.implementation",
            "testing.review",
        }
    )
    emitted_conditions = frozenset(
        {
            "requirements-ready",
            "task-ready",
            "planning-complete",
            "approved",
            "plan-approved",
            "tests-passed",
            "machine-tests-passed",
            "candidate-verified",
            "candidate-produced",
            "tests-failed",
            "accepted",
            "candidate-accepted",
            "design-approved",
            "backend-candidate-verified",
            "design-candidate-verified",
            "frontend-candidate-verified",
            "qa-candidate-verified",
            "backend-tests-failed",
            "design-tests-failed",
            "frontend-tests-failed",
            "qa-tests-failed",
            "release-bundle-verified",
        }
    )

    def validate(self, definition: dict[str, object]) -> tuple[str, ...]:
        nodes = _items(definition.get("nodes") or definition.get("steps"))
        capabilities = _capabilities(nodes)
        outer_gates = tuple(
            str(node.get("subject_kind")) for node in nodes if node.get("kind") == "approval_gate"
        )
        errors: list[str] = []
        fullstack = bool(capabilities & (self.fullstack_capabilities - self.legacy_capabilities))
        required_capabilities = (
            self.fullstack_capabilities if fullstack else self.legacy_capabilities
        )
        required_gate_subjects = (
            frozenset({"delivery-plan", "design-candidate", "release-bundle"})
            if fullstack
            else frozenset({"delivery-plan", "candidate-change"})
        )
        for capability in sorted(required_capabilities - capabilities):
            errors.append(
                f"DELIVERY_PIPELINE_MISSING_CAPABILITY:{capability}:"
                f"缺少必要 Capability {capability}"
            )
        for subject in sorted(required_gate_subjects):
            count = outer_gates.count(subject)
            if count == 0:
                errors.append(
                    f"DELIVERY_PIPELINE_MISSING_GATE:{subject}:缺少必要审批 Gate {subject}"
                )
            elif count > 1:
                errors.append(
                    f"DELIVERY_PIPELINE_DUPLICATE_GATE:{subject}:审批 Gate {subject} 只能出现一次"
                )
        for node in nodes:
            if node.get("kind") != "loop":
                continue
            for nested in _items(node.get("nodes")):
                if nested.get("kind") == "approval_gate":
                    errors.append(
                        "DELIVERY_PIPELINE_NESTED_GATE_UNSUPPORTED:"
                        f"{nested.get('id')}:人工 Gate 必须放在 LOOP 外部"
                    )
        for condition in sorted(_conditions(definition)):
            if condition not in self.emitted_conditions:
                errors.append(
                    f"DELIVERY_PIPELINE_UNKNOWN_CONDITION:{condition}:"
                    "分支条件没有对应的产品运行信号"
                )
        return tuple(errors)


def _items(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(cast(dict[str, object], item) for item in value if isinstance(item, dict))


def _capabilities(nodes: tuple[dict[str, object], ...]) -> frozenset[str]:
    result: set[str] = set()
    for node in nodes:
        bindings = node.get("bindings")
        if isinstance(bindings, dict):
            result.update(str(value) for value in bindings.values())
        if node.get("kind") == "loop":
            result.update(_capabilities(_items(node.get("nodes"))))
    return frozenset(result)


def _conditions(definition: dict[str, object]) -> frozenset[str]:
    result: set[str] = set()
    for edge in _items(definition.get("edges")):
        condition = edge.get("condition")
        if isinstance(condition, str) and condition:
            result.add(condition)
    for node in _items(definition.get("nodes") or definition.get("steps")):
        if node.get("kind") == "loop":
            result.update(_conditions(node))
            policy = node.get("policy")
            if isinstance(policy, dict):
                exit_condition = policy.get("exit_condition")
                if isinstance(exit_condition, str) and exit_condition:
                    result.add(exit_condition)
    return frozenset(result)
