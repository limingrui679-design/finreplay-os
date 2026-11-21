"""Exact delay-cost regions for a vintage-aware, walk-forward release policy."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fractions import Fraction
from itertools import combinations, pairwise
from typing import TypedDict

from finreplay.engines.release_timing import (
    DecisionOrigin,
    LossCounts,
    ReleasePath,
    decision_origins,
)

IMMEDIATE = "act_immediately"
WAIT = "always_wait"
ADAPTIVE = "adaptive_margin"


class PolicyCounts(TypedDict):
    disagreements: int
    delayed_quarters: int


class CostRegion(TypedDict):
    start: str
    end: str
    include_start: bool
    include_end: bool
    retrained: PolicyCounts
    deferred_periods: list[str]
    winners: list[str]


class CostFrontier(TypedDict):
    complete_periods: int
    warmup_periods: int
    evaluated_periods: int
    first_period: str
    last_period: str
    minimum_history: int
    frozen_cost: str
    fixed_policies: dict[str, PolicyCounts]
    frozen_deferred_periods: list[str]
    regions: list[CostRegion]


def _crossings(lines: Iterable[LossCounts]) -> set[Fraction]:
    crossings = set()
    for left, right in combinations(lines, 2):
        if left.delayed_quarters != right.delayed_quarters:
            crossings.add(
                Fraction(
                    right.disagreements - left.disagreements,
                    left.delayed_quarters - right.delayed_quarters,
                )
            )
    return crossings


def _switch_cost(origin: DecisionOrigin) -> Fraction | None:
    """First cost at which this quarter issues immediately. None means defer throughout."""

    def waits(price: Fraction) -> bool:
        margin = origin.margin_at(price)
        return margin is None or abs(origin.path.first) < margin

    if not waits(Fraction(0)):
        return Fraction(0)
    if waits(Fraction(1)):
        return None
    # Larger margins defer a superset of cases. Their training-loss slopes are
    # nondecreasing, so the chosen margin can only shrink as price increases.
    # At an exact tie the smaller margin already wins.
    return min(p for p in _crossings(origin.counts_by_margin) if 0 < p <= 1 and not waits(p))


def _counts(quarters: Sequence[ReleasePath], deferred: set[str]) -> LossCounts:
    errors = 0
    for row in quarters:
        signal = row.next_vintage if row.period in deferred else row.first
        errors += (signal < 0) != (row.reference < 0)
    return LossCounts(errors, len(deferred))


def _fields(counts: LossCounts) -> PolicyCounts:
    return {"disagreements": counts.disagreements, "delayed_quarters": counts.delayed_quarters}


def evaluate_cost_frontier(
    paths: Sequence[ReleasePath],
    *,
    frozen_cost: Fraction = Fraction(1, 10),
    minimum_history: int = 40,
) -> CostFrontier:
    """Partition [0,1] into exact decision and held-out loss winner regions."""
    if not 0 <= frozen_cost <= 1:
        raise ValueError("Use a frozen cost between 0 and 1")
    origins = decision_origins(paths, minimum_history)
    if not origins:
        raise ValueError(
            "No eligible decision quarters after the maturity and history requirements"
        )
    quarters = [origin.path for origin in origins]
    switches = {origin.path.period: _switch_cost(origin) for origin in origins}

    def deferred_at(price: Fraction) -> set[str]:
        return {q for q, threshold in switches.items() if threshold is None or price < threshold}

    frozen = deferred_at(frozen_cost)
    immediate = _counts(quarters, set())
    wait = _counts(quarters, set(switches))
    cuts = {c for c in switches.values() if c is not None} | {Fraction(0), Fraction(1)}
    # Between action switches each strategy has a single linear held-out loss.
    # Its relative rank can change only at an intersection of those lines.
    for lo, hi in pairwise(sorted(cuts)):
        adaptive = _counts(quarters, deferred_at(lo))
        cuts.update(c for c in _crossings((immediate, wait, adaptive)) if lo < c < hi)

    regions: list[CostRegion] = []

    def record(lo: Fraction, hi: Fraction, closed: bool) -> None:
        price = (lo + hi) / 2
        deferred = deferred_at(price)
        adaptive = _counts(quarters, deferred)
        policies = {IMMEDIATE: immediate, WAIT: wait, ADAPTIVE: adaptive}
        scores = {name: line.weighted_numerator(price) for name, line in policies.items()}
        best = min(scores.values())
        winners = [name for name, score in scores.items() if score == best]
        periods = sorted(deferred)
        if (
            regions
            and regions[-1]["deferred_periods"] == periods
            and regions[-1]["winners"] == winners
        ):
            regions[-1]["end"] = str(hi)
            regions[-1]["include_end"] = closed
        else:
            regions.append(
                {
                    "start": str(lo),
                    "end": str(hi),
                    "include_start": closed,
                    "include_end": closed,
                    "retrained": _fields(adaptive),
                    "deferred_periods": periods,
                    "winners": winners,
                }
            )

    ordered = sorted(cuts)
    for lo, hi in pairwise(ordered):
        record(lo, lo, True)
        record(lo, hi, False)
    record(ordered[-1], ordered[-1], True)
    return {
        "complete_periods": len(paths),
        "warmup_periods": len(paths) - len(origins),
        "evaluated_periods": len(origins),
        "first_period": quarters[0].period,
        "last_period": quarters[-1].period,
        "minimum_history": minimum_history,
        "frozen_cost": str(frozen_cost),
        "fixed_policies": {
            IMMEDIATE: _fields(immediate),
            WAIT: _fields(wait),
            "frozen_at_reference": _fields(_counts(quarters, frozen)),
        },
        "frozen_deferred_periods": sorted(frozen),
        "regions": regions,
    }
