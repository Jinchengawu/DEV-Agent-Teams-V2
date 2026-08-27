from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal


def normalize_tool_call(call: dict[str, object]) -> tuple[str, str]:
    name = call.get("name")
    arguments = call.get("arguments", {})
    if not isinstance(name, str) or not name:
        raise ValueError("tool call requires a name")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as error:
            raise ValueError("tool arguments are not valid JSON") from error
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return name, json.dumps(arguments, sort_keys=True, separators=(",", ":"))


def ast_match(
    actual: tuple[dict[str, object], ...],
    expected: tuple[dict[str, object], ...],
    *,
    parallel: bool,
) -> bool:
    try:
        normalized_actual = tuple(normalize_tool_call(item) for item in actual)
        normalized_expected = tuple(normalize_tool_call(item) for item in expected)
    except ValueError:
        return False
    if parallel:
        return Counter(normalized_actual) == Counter(normalized_expected)
    return normalized_actual == normalized_expected


def quasi_exact_match(actual: object, expected: object, expected_type: str) -> bool:
    if expected_type == "number":
        try:
            return Decimal(str(actual).replace(",", "")) == Decimal(str(expected).replace(",", ""))
        except InvalidOperation:
            return False
    if expected_type == "date":
        return _date(actual) == _date(expected)
    if expected_type == "list":
        if not isinstance(actual, (list, tuple)) or not isinstance(expected, (list, tuple)):
            return False
        return Counter(_text(item) for item in actual) == Counter(_text(item) for item in expected)
    return _text(actual) == _text(expected)


def percentile(values: tuple[float, ...], percentage: float) -> float | None:
    if not values:
        return None
    if percentage < 0 or percentage > 100:
        raise ValueError("percentage must be between 0 and 100")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentage / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def wilson_interval(
    successes: int, total: int, z: float = 1.959963984540054
) -> tuple[float, float] | None:
    if total == 0:
        return None
    if successes < 0 or successes > total:
        raise ValueError("successes must be between zero and total")
    proportion = successes / total
    denominator = 1 + z**2 / total
    centre = (proportion + z**2 / (2 * total)) / denominator
    margin = (
        z * math.sqrt((proportion * (1 - proportion) + z**2 / (4 * total)) / total) / denominator
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


def pairwise_rates(outcomes: tuple[str, ...]) -> dict[str, float | int | None]:
    wins = outcomes.count("win")
    ties = outcomes.count("tie")
    losses = outcomes.count("loss")
    decisive = wins + losses
    total = wins + ties + losses
    return {
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "win_rate": wins / decisive if decisive else None,
        "non_loss_rate": (wins + ties) / total if total else None,
    }


def cohens_kappa(
    judge: tuple[Literal["win", "tie", "loss"], ...],
    human: tuple[Literal["win", "tie", "loss"], ...],
) -> tuple[float | None, float | None]:
    if len(judge) != len(human):
        raise ValueError("judge and human review lengths differ")
    if not judge:
        return None, None
    agreement = sum(left == right for left, right in zip(judge, human, strict=True)) / len(judge)
    labels = ("win", "tie", "loss")
    expected = sum(
        (judge.count(label) / len(judge)) * (human.count(label) / len(human)) for label in labels
    )
    if expected == 1:
        return agreement, 1.0
    return agreement, (agreement - expected) / (1 - expected)


def _text(value: object) -> str:
    return re.sub(r"[^\w]+", " ", str(value).strip().casefold()).strip()


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None
