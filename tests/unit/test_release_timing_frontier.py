"""Check the frontier against an independent, price-by-price walk-forward implementation."""

import json
import random
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

import pytest
from typer.testing import CliRunner

from finreplay.cli import app
from finreplay.engines.release_timing import ReleasePath, quarter_index, read_paths
from finreplay.engines.release_timing_frontier import (
    CostFrontier,
    CostRegion,
    evaluate_cost_frontier,
)

PANEL = Path(__file__).resolve().parents[2] / "examples/release_timing/quarterly_output.csv"


def at(report: CostFrontier, price: Fraction) -> CostRegion:
    hits = []
    for region in report["regions"]:
        low, high = Fraction(region["start"]), Fraction(region["end"])
        if (low < price or (low == price and region["include_start"])) and (
            price < high or (price == high and region["include_end"])
        ):
            hits.append(region)
    assert len(hits) == 1
    return hits[0]


def direct_fit(
    rows: list[ReleasePath], history: int, price: Fraction
) -> tuple[list[str], int, list[str]]:
    # Independent oracle enumerates complete historical predictions under each
    # candidate for this price. It shares neither the frontier nor the tally code.
    delayed = []
    adaptive_errors = immediate_errors = late_errors = eligible = 0
    margins = [Decimal(value) for value in ("0", "0.25", "0.5", "1", "2", "Infinity")]
    for i, current in enumerate(rows):
        available = [
            r for r in rows[:i] if quarter_index(r.period) + 5 < quarter_index(current.period) + 1
        ]
        if len(available) < history:
            continue
        best_loss = None
        best_margin = margins[0]
        for margin in margins:
            loss = 0
            for prior in available:
                wait = abs(prior.first) < margin
                flag = prior.next_vintage < 0 if wait else prior.first < 0
                loss += (flag != (prior.reference < 0)) * price.denominator
                loss += wait * price.numerator
            if best_loss is None or loss < best_loss:
                best_loss, best_margin = loss, margin
        early_wrong = (current.first < 0) != (current.reference < 0)
        late_wrong = (current.next_vintage < 0) != (current.reference < 0)
        wait = abs(current.first) < best_margin
        if wait:
            delayed.append(current.period)
        adaptive_errors += late_wrong if wait else early_wrong
        immediate_errors += early_wrong
        late_errors += late_wrong
        eligible += 1
    losses = {
        "act_immediately": Fraction(immediate_errors),
        "always_wait": late_errors + price * eligible,
        "adaptive_margin": adaptive_errors + price * len(delayed),
    }
    return (
        delayed,
        adaptive_errors,
        [key for key, value in losses.items() if value == min(losses.values())],
    )


def public_paths() -> list[ReleasePath]:
    with PANEL.open() as source:
        paths, _ = read_paths(source)
    return paths


def test_archive_winners_include_exact_ties_and_a_high_cost_shared_policy() -> None:
    report = evaluate_cost_frontier(public_paths())
    assert (report["evaluated_periods"], report["first_period"], report["last_period"]) == (
        195,
        "1976Q3",
        "2025Q2",
    )
    assert len(report["regions"]) == 16
    for cost, expected in [
        ("0", ["always_wait"]),
        ("1/176", ["always_wait", "adaptive_margin"]),
        ("1/100", ["adaptive_margin"]),
        ("1/7", ["act_immediately", "adaptive_margin"]),
        ("1/5", ["act_immediately"]),
        ("1/2", ["act_immediately", "adaptive_margin"]),
        ("1", ["act_immediately", "adaptive_margin"]),
    ]:
        assert at(report, Fraction(cost))["winners"] == expected
    for p in [
        Fraction(0),
        Fraction(1, 176),
        Fraction(1, 7),
        Fraction(1, 5),
        Fraction(1, 2),
        Fraction(1),
    ]:
        deferred, errors, winners = direct_fit(public_paths(), 40, p)
        region = at(report, p)
        assert (
            region["deferred_periods"],
            region["retrained"]["disagreements"],
            region["winners"],
        ) == (deferred, errors, winners)
    frozen = report["fixed_policies"]["frozen_at_reference"]
    assert frozen == {"disagreements": 5, "delayed_quarters": 7}
    quarter = at(report, Fraction(1, 4))
    assert quarter["retrained"] == {"disagreements": 5, "delayed_quarters": 6}
    assert len(set(report["frozen_deferred_periods"]) - set(quarter["deferred_periods"])) == 1
    alternate = evaluate_cost_frontier(public_paths(), frozen_cost=Fraction(1, 4))
    assert alternate["fixed_policies"]["frozen_at_reference"] == quarter["retrained"]
    assert alternate["frozen_deferred_periods"] == quarter["deferred_periods"]
    assert alternate["regions"] == report["regions"]


@pytest.mark.parametrize("seed", [7, 23, 59])
def test_piecewise_solver_agrees_with_direct_fits_on_diverse_release_histories(seed: int) -> None:
    rng = random.Random(seed)
    near_zero = [Decimal(value) for value in ("-2", "-1", "-0.25", "0", "0.24", "0.5", "1", "3")]
    rows = [
        ReleasePath(f"{1970 + i // 4}Q{i % 4 + 1}", *(rng.choice(near_zero) for _ in range(3)))
        for i in range(33)
    ]
    report = evaluate_cost_frontier(list(reversed(rows)), minimum_history=5)
    boundaries = sorted(
        {
            value
            for region in report["regions"]
            for value in (Fraction(region["start"]), Fraction(region["end"]))
        }
    )
    prices = set(boundaries) | {(a + b) / 2 for a, b in pairwise(boundaries)}
    prices |= {Fraction(i, 29) for i in range(30)}
    for p in prices:
        delayed, errors, winners = direct_fit(rows, 5, p)
        region = at(report, p)
        assert region["deferred_periods"] == delayed
        assert region["retrained"] == {"disagreements": errors, "delayed_quarters": len(delayed)}
        assert region["winners"] == winners


def test_unpublished_revisions_cannot_change_the_last_delay_decision() -> None:
    rows = public_paths()[:80]
    target = rows[-1].period
    altered = [
        replace(row, reference=-row.reference)
        if quarter_index(row.period) + 5 >= quarter_index(target) + 1
        else row
        for row in rows
    ]
    altered[-1] = replace(altered[-1], next_vintage=-altered[-1].next_vintage)
    before, after = evaluate_cost_frontier(rows), evaluate_cost_frontier(altered)
    boundaries = sorted(
        {
            value
            for report in (before, after)
            for region in report["regions"]
            for value in (Fraction(region["start"]), Fraction(region["end"]))
        }
    )
    for p in {*boundaries, *((a + b) / 2 for a, b in pairwise(boundaries))}:
        assert (target in at(before, p)["deferred_periods"]) == (
            target in at(after, p)["deferred_periods"]
        )


def test_cli_exposes_exact_fractions_and_rejects_empty_or_invalid_domains() -> None:
    runner = CliRunner()
    for bad in ["1/0", "-0.01", "1.1", "NaN"]:
        assert (
            runner.invoke(
                app, ["release-timing-frontier", str(PANEL), "--frozen-cost", bad]
            ).exit_code
            != 0
        )
    valid = runner.invoke(app, ["release-timing-frontier", str(PANEL), "--frozen-cost", "1/7"])
    assert valid.exit_code == 0
    result = json.loads(valid.stdout)
    assert result["frozen_cost"] == "1/7"
    assert len(result["excluded_incomplete_periods"]) == 6
    with pytest.raises(ValueError, match="No eligible"):
        evaluate_cost_frontier([])
