"""ACWM Workflow Manifest for one product-observed Agent Workcell."""

from acwm.domain import CapabilityFeature, WorkflowBindingSlot, WorkflowManifest


class WorkcellTeamWorkflowAdapter:
    """Declare binding slots while product code owns observable child scheduling.

    This adapter intentionally exposes only the upstream ACWM manifest. Workcell
    planning, delegation, verification and synthesis run behind the product-owned
    Workcell Execution Module so no hidden AgentScope child tree can bypass the
    control plane.
    """

    manifest = WorkflowManifest(
        mode_id="agentscope.workcell-team",
        mode_version="1.0.0",
        adapter_type="agentscope",
        adapter_version="2.0.6",
        resumable=False,
        bindings={
            slot: WorkflowBindingSlot(
                required_features=frozenset(
                    {
                        CapabilityFeature.TEXT_FINAL,
                        CapabilityFeature.CWD_BINDING,
                    }
                ),
                optional_features=frozenset({CapabilityFeature.TOOL_EVENTS}),
            )
            for slot in ("main", "delegate_1", "delegate_2", "delegate_3")
        },
    )
