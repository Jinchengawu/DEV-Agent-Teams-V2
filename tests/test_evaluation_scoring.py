import pytest

from agent_team_os.modules.evaluation import (
    ast_match,
    cohens_kappa,
    pairwise_rates,
    percentile,
    quasi_exact_match,
    wilson_interval,
)


def test_ast_match_normalizes_json_and_parallel_order() -> None:
    expected = (
        {"name": "read", "arguments": {"id": "a", "limit": 2}},
        {"name": "read", "arguments": {"id": "b", "limit": 1}},
    )
    actual = (
        {"name": "read", "arguments": '{"limit":1,"id":"b"}'},
        {"name": "read", "arguments": {"limit": 2, "id": "a"}},
    )

    assert ast_match(actual, expected, parallel=True)
    assert not ast_match(actual, expected, parallel=False)
    assert not ast_match(
        ({"name": "read", "arguments": "not-json"},),
        expected[:1],
        parallel=False,
    )


def test_quasi_exact_match_is_type_aware() -> None:
    assert quasi_exact_match("Completed!", "completed", "text")
    assert quasi_exact_match("1,024.00", 1024, "number")
    assert quasi_exact_match("2026-08-24", "2026-08-24", "date")
    assert quasi_exact_match(["verify", "apply"], ["apply", "verify"], "list")
    assert not quasi_exact_match("forty two", 42, "number")


def test_pairwise_rates_keep_ties_and_denominators_visible() -> None:
    rates = pairwise_rates(("win", "tie", "loss", "win"))

    assert rates == {
        "wins": 2,
        "ties": 1,
        "losses": 1,
        "win_rate": 2 / 3,
        "non_loss_rate": 3 / 4,
    }


def test_percentiles_and_human_agreement_are_deterministic() -> None:
    assert percentile((1.0, 2.0, 3.0, 4.0), 95) == 3.85
    agreement, kappa = cohens_kappa(("win", "tie", "loss"), ("win", "loss", "loss"))

    assert agreement == 2 / 3
    assert kappa == pytest.approx(0.5)
    assert wilson_interval(100, 100) == pytest.approx((0.9630065017930143, 0.9999999999999999))
