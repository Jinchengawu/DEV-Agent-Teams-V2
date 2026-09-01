from pathlib import Path

from acwm.config import load_capabilities, load_journeys
from acwm.domain import (
    ApprovalGateDefinition,
    CapabilityFeature,
    LoopDefinition,
    ResolvedWorkflow,
    StageDefinition,
)

from agent_team_os.infrastructure.acwm import WorkcellTeamWorkflowAdapter


def test_workcell_team_workflow_exposes_four_pre_resolved_acwm_slots() -> None:
    manifest = WorkcellTeamWorkflowAdapter.manifest

    assert manifest.mode_id == "agentscope.workcell-team"
    assert manifest.resumable is False
    assert tuple(manifest.bindings) == (
        "main",
        "delegate_1",
        "delegate_2",
        "delegate_3",
    )
    assert all(slot.required for slot in manifest.bindings.values())
    assert all(
        CapabilityFeature.TEXT_FINAL in slot.required_features
        for slot in manifest.bindings.values()
    )
    assert ResolvedWorkflow.from_manifest(manifest).manifest_fingerprint


def test_v2_delivery_journey_uses_acwm_without_copying_its_control_plane() -> None:
    root = Path(__file__).parents[1]

    capabilities = load_capabilities(root / "config" / "capabilities.yaml")
    journey = load_journeys(root / "config" / "journeys.yaml")["backend-delivery"]

    assert tuple(sorted(capabilities.descriptors)) == (
        "codex-backend",
        "design.system",
        "frontend.implementation",
        "hermes-pm",
        "hermes-project-admin",
        "testing.review",
        "workcell.delegate",
        "workcell.lead",
    )
    assert [node.id for node in journey.nodes] == [
        "requirements",
        "tasking",
        "approve-plan",
        "code-repair",
        "approve-candidate",
    ]
    assert [node.id for node in journey.nodes if isinstance(node, StageDefinition)] == [
        "requirements",
        "tasking",
    ]
    loop = next(node for node in journey.nodes if isinstance(node, LoopDefinition))
    assert loop.policy.exit_condition == "machine-tests-passed"
    assert loop.policy.max_iterations == 3
    assert [node.id for node in loop.nodes] == ["delivery"]
    gate_subjects = [
        node.subject_kind
        for node in journey.nodes
        if isinstance(node, ApprovalGateDefinition)
    ]
    assert gate_subjects == [
        "delivery-plan",
        "candidate-change",
    ]
    assert [(edge.source, edge.target, edge.condition) for edge in journey.edges] == [
        ("requirements", "tasking", None),
        ("tasking", "approve-plan", None),
        ("approve-plan", "code-repair", "plan-approved"),
        ("code-repair", "approve-candidate", None),
    ]


def test_fullstack_product_journey_keeps_three_gates_and_bounded_parallel_repair() -> None:
    root = Path(__file__).parents[1]
    journey = load_journeys(root / "config" / "journeys.yaml")[
        "fullstack-product-delivery"
    ]

    assert [node.id for node in journey.nodes] == [
        "requirements",
        "tasking",
        "approve-plan",
        "design",
        "approve-design",
        "implementation-repair",
        "approve-release",
    ]
    loop = next(node for node in journey.nodes if isinstance(node, LoopDefinition))
    assert loop.policy.exit_condition == "release-bundle-verified"
    assert loop.policy.max_iterations == 3
    assert [node.id for node in loop.nodes] == ["backend", "frontend", "qa"]
    assert [(edge.source, edge.target) for edge in loop.edges] == [
        ("backend", "qa"),
        ("frontend", "qa"),
    ]
    assert [
        node.subject_kind
        for node in journey.nodes
        if isinstance(node, ApprovalGateDefinition)
    ] == ["delivery-plan", "design-candidate", "release-bundle"]


def test_v050_workcell_journey_keeps_four_repositories_and_parallel_fe_be() -> None:
    root = Path(__file__).parents[1]
    journey = load_journeys(root / "config" / "journeys.yaml")[
        "agent-workcell-delivery"
    ]

    assert [node.id for node in journey.nodes] == [
        "requirements",
        "tasking",
        "approve-plan",
        "design-repair",
        "approve-design",
        "qa-preparation-repair",
        "frontend-repair",
        "backend-repair",
        "qa-delivery-repair",
        "approve-release",
    ]
    loops = {
        node.id: node for node in journey.nodes if isinstance(node, LoopDefinition)
    }
    assert {
        loop_id: (loop.policy.exit_condition, loop.policy.max_iterations)
        for loop_id, loop in loops.items()
    } == {
        "design-repair": ("design-workcell-passed", 3),
        "qa-preparation-repair": ("qa-preparation-artifacts-passed", 2),
        "frontend-repair": ("frontend-candidate-passed", 3),
        "backend-repair": ("backend-candidate-passed", 3),
        "qa-delivery-repair": ("qa-candidate-passed", 3),
    }
    assert all(
        child.workflow_mode == "agentscope.workcell-team"
        for loop in loops.values()
        for child in loop.nodes
        if isinstance(child, StageDefinition)
    )
    edges = {(edge.source, edge.target) for edge in journey.edges}
    assert ("qa-preparation-repair", "frontend-repair") in edges
    assert ("qa-preparation-repair", "backend-repair") in edges
    assert ("frontend-repair", "qa-delivery-repair") in edges
    assert ("backend-repair", "qa-delivery-repair") in edges
