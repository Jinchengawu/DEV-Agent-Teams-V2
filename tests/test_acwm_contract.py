from pathlib import Path

from acwm.config import load_capabilities, load_journeys
from acwm.domain import ApprovalGateDefinition, LoopDefinition, StageDefinition


def test_v2_delivery_journey_uses_acwm_without_copying_its_control_plane() -> None:
    root = Path(__file__).parents[1]

    capabilities = load_capabilities(root / "config" / "capabilities.yaml")
    journey = load_journeys(root / "config" / "journeys.yaml")["backend-delivery"]

    assert tuple(sorted(capabilities.descriptors)) == (
        "codex-backend",
        "hermes-pm",
        "hermes-project-admin",
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
