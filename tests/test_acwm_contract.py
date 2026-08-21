from pathlib import Path

from acwm.config import load_capabilities, load_journeys
from acwm.domain import ApprovalGateDefinition, StageDefinition


def test_v2_delivery_journey_uses_acwm_without_copying_its_control_plane() -> None:
    root = Path(__file__).parents[1]

    capabilities = load_capabilities(root / "config" / "capabilities.yaml")
    journey = load_journeys(root / "config" / "journeys.yaml")["backend-delivery"]

    assert tuple(sorted(capabilities.descriptors)) == (
        "codex-backend",
        "hermes-pm",
        "hermes-project-admin",
    )
    assert [step.id for step in journey.steps] == [
        "requirements",
        "tasking",
        "approve-plan",
        "delivery",
        "approve-candidate",
    ]
    assert [step.id for step in journey.steps if isinstance(step, StageDefinition)] == [
        "requirements",
        "tasking",
        "delivery",
    ]
    gate_subjects = [
        step.subject_kind
        for step in journey.steps
        if isinstance(step, ApprovalGateDefinition)
    ]
    assert gate_subjects == [
        "delivery-plan",
        "candidate-change",
    ]
