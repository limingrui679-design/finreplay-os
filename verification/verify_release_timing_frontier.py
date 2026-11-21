"""Exhaustively audit the exact public-archive frontier with an independent integer refit."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from math import gcd
from pathlib import Path

import numpy as np

from finreplay.engines.release_timing import read_paths
from finreplay.engines.release_timing_frontier import evaluate_cost_frontier

PANEL = Path(__file__).resolve().parents[1] / "examples/release_timing/quarterly_output.csv"
MARGINS = tuple(Decimal(value) for value in ("0", ".25", ".5", "1", "2", "Infinity"))
POLICIES = ("act_immediately", "always_wait", "adaptive_margin")


@dataclass(frozen=True)
class Observation:
    label: str
    quarter: int
    first: Decimal
    second: Decimal
    reference: Decimal


def public_observations() -> list[Observation]:
    """Independently derive three annual rates from the six published within-vintage levels."""

    def growth(fields: dict[str, str], age: int) -> Decimal:
        ratio = Decimal(fields[f"age{age}_current"]) / Decimal(fields[f"age{age}_previous"])
        return 100 * (ratio**4 - 1)

    observations = []
    with PANEL.open(encoding="utf-8", newline="") as handle:
        for fields in csv.DictReader(handle):
            if not all(fields.values()):
                continue
            label = fields["period"]
            ordinal = int(label[:4]) * 4 + int(label[-1]) - 1
            observations.append(
                Observation(label, ordinal, growth(fields, 1), growth(fields, 2), growth(fields, 5))
            )
    return sorted(observations, key=lambda observation: observation.quarter)


def main() -> None:
    with PANEL.open(encoding="utf-8", newline="") as handle:
        paths, _ = read_paths(handle)
    report = evaluate_cost_frontier(paths)

    rows = public_observations()
    # Every crossing is a difference of error counts divided by a difference of
    # delay counts. Neither difference can exceed the total number of complete rows.
    boundaries = sorted(
        Fraction(i, j) for j in range(1, len(rows) + 1) for i in range(j + 1) if gcd(i, j) == 1
    )
    prices = sorted([*boundaries, *((a + b) / 2 for a, b in pairwise(boundaries))])
    num, den = np.array([(p.numerator, p.denominator) for p in prices], dtype=np.int64).T

    waits = np.array([[abs(row.first) < margin for margin in MARGINS] for row in rows])
    flags = np.array(
        [
            [(row.second if wait else row.first) < 0 for wait in actions]
            for row, actions in zip(rows, waits, strict=True)
        ]
    )
    wrong = flags != np.array([[row.reference < 0] for row in rows])
    names = []
    action_columns = []
    error_counts = np.zeros(len(prices), dtype=np.int64)
    eligible = np.zeros(len(rows), dtype=bool)

    for i, row in enumerate(rows):
        mature = np.array([old.quarter + 5 < row.quarter + 1 for old in rows])
        if mature.sum() < 40:
            continue
        eligible[i] = True
        training_errors = wrong[mature].sum(axis=0)
        training_delays = waits[mature].sum(axis=0)
        # Fit all six margins at every price. argmin implements first-margin ties.
        winner = (training_errors[:, None] * den + training_delays[:, None] * num).argmin(axis=0)
        action_columns.append(waits[i, winner])
        error_counts += wrong[i, winner]
        names.append(row.label)

    deferred = np.array(action_columns).T
    fixed_scores = (
        wrong[eligible, 0].sum() * den,
        wrong[eligible, -1].sum() * den + len(names) * num,
    )
    adaptive_scores = error_counts * den + deferred.sum(axis=1) * num
    scores = np.stack([*fixed_scores, adaptive_scores], axis=1)
    winners = scores == scores.min(axis=1)[:, None]
    coverage = np.zeros(len(prices), dtype=int)
    for region in report["regions"]:
        lo, hi = Fraction(region["start"]), Fraction(region["end"])
        left = num * lo.denominator - den * lo.numerator
        right = num * hi.denominator - den * hi.numerator
        mask = (left >= 0 if region["include_start"] else left > 0) & (
            right <= 0 if region["include_end"] else right < 0
        )
        coverage += mask
        expected_periods = set(region["deferred_periods"])
        expected_actions = np.array([q in expected_periods for q in names])
        expected_winners = np.array([p in region["winners"] for p in POLICIES])
        checks = (
            mask.any(),
            len(expected_periods) == region["retrained"]["delayed_quarters"],
            np.all(deferred[mask] == expected_actions),
            np.all(error_counts[mask] == region["retrained"]["disagreements"]),
            np.all(winners[mask] == expected_winners),
        )
        if not all(checks):
            raise SystemExit(f"Independent refit disagreed with the reported region {lo} to {hi}")
    if not np.all(coverage == 1):
        raise SystemExit("Some audited prices occur in zero or multiple reported regions")
    print(
        json.dumps(
            {
                "boundary_candidates": len(boundaries),
                "interval_midpoints": len(boundaries) - 1,
                "exact_prices": len(prices),
                "target_quarters": len(names),
                "quarter_price_decisions_verified": int(deferred.size),
                "regions_verified": len(report["regions"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
