from agent_team_os.modules.delivery.pipeline_policy import (
    BackendDeliveryPipelinePolicy,
)


def _valid_definition() -> dict[str, object]:
    return {
        "id": "backend-delivery",
        "version": "4.0.0",
        "nodes": [
            {
                "id": "requirements",
                "kind": "stage",
                "workflow_mode": "agentscope.role-turn",
                "bindings": {"actor": "hermes-pm"},
            },
            {
                "id": "tasking",
                "kind": "stage",
                "workflow_mode": "agentscope.role-turn",
                "bindings": {"actor": "hermes-project-admin"},
            },
            {
                "id": "plan-gate",
                "kind": "approval_gate",
                "subject_kind": "delivery-plan",
            },
            {
                "id": "repair",
                "kind": "loop",
                "policy": {
                    "exit_condition": "machine-tests-passed",
                    "max_iterations": 3,
                    "timeout_seconds": 60,
                    "on_exhausted": "fail",
                },
                "nodes": [
                    {
                        "id": "code",
                        "kind": "stage",
                        "workflow_mode": "code-delivery",
                        "bindings": {"developer": "codex-backend"},
                    }
                ],
                "edges": [],
            },
            {
                "id": "candidate-gate",
                "kind": "approval_gate",
                "subject_kind": "candidate-change",
            },
        ],
        "edges": [
            {"source": "requirements", "target": "tasking"},
            {"source": "tasking", "target": "plan-gate"},
            {"source": "plan-gate", "target": "repair", "condition": "approved"},
            {"source": "repair", "target": "candidate-gate"},
        ],
    }


def test_backend_delivery_policy_accepts_complete_dag_with_code_loop() -> None:
    assert BackendDeliveryPipelinePolicy().validate(_valid_definition()) == ()


def test_backend_delivery_policy_rejects_missing_contract_and_unknown_signal() -> None:
    definition = _valid_definition()
    definition["nodes"] = [
        node
        for node in definition["nodes"]  # type: ignore[union-attr]
        if node.get("subject_kind") != "candidate-change"
        and "hermes-project-admin" not in node.get("bindings", {}).values()
    ]
    definition["edges"] = [
        {"source": "requirements", "target": "plan-gate", "condition": "magic"},
        {"source": "plan-gate", "target": "repair"},
    ]

    errors = BackendDeliveryPipelinePolicy().validate(definition)

    assert any("MISSING_CAPABILITY:hermes-project-admin" in error for error in errors)
    assert any("MISSING_GATE:candidate-change" in error for error in errors)
    assert any("UNKNOWN_CONDITION:magic" in error for error in errors)


def test_backend_delivery_policy_rejects_nested_human_gate() -> None:
    definition = _valid_definition()
    loop = next(
        node
        for node in definition["nodes"]  # type: ignore[union-attr]
        if node["kind"] == "loop"
    )
    loop["nodes"].append(  # type: ignore[union-attr]
        {
            "id": "nested-gate",
            "kind": "approval_gate",
            "subject_kind": "candidate-change",
        }
    )

    errors = BackendDeliveryPipelinePolicy().validate(definition)

    assert any("NESTED_GATE_UNSUPPORTED:nested-gate" in error for error in errors)
