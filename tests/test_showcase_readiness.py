from agent_team_os.showcase_readiness import ShowcaseFacts, evaluate_showcase_readiness


def test_empty_runtime_is_not_showcase_ready() -> None:
    report = evaluate_showcase_readiness(ShowcaseFacts())

    assert report.status == "not_ready"
    assert report.proof_scope == "deterministic_showcase_only"
    assert {check.id for check in report.checks if not check.passed} >= {
        "completed_delivery",
        "workcell_execution",
        "four_repository_release",
    }


def test_complete_deterministic_workcell_delivery_is_showcase_ready() -> None:
    report = evaluate_showcase_readiness(
        ShowcaseFacts(
            completed_deliveries=1,
            succeeded_workcell_runs=5,
            distinct_workcells=4,
            agent_runs=22,
            child_agent_runs=17,
            succeeded_agent_attempts=27,
            verified_candidates=4,
            distinct_candidate_workcells=4,
            pull_request_receipts=4,
            remote_apply_receipts=4,
            active_release_manifests=1,
            verified_evidence=6,
        )
    )

    assert report.status == "ready"
    assert all(check.passed for check in report.checks)
    assert report.live_accepted is False


def test_missing_child_observability_cannot_be_hidden_by_release_counts() -> None:
    report = evaluate_showcase_readiness(
        ShowcaseFacts(
            completed_deliveries=1,
            succeeded_workcell_runs=5,
            distinct_workcells=4,
            agent_runs=5,
            child_agent_runs=0,
            succeeded_agent_attempts=10,
            verified_candidates=4,
            distinct_candidate_workcells=4,
            pull_request_receipts=4,
            remote_apply_receipts=4,
            active_release_manifests=1,
            verified_evidence=6,
        )
    )

    assert report.status == "not_ready"
    agent_check = next(check for check in report.checks if check.id == "agent_observability")
    assert agent_check.passed is False
