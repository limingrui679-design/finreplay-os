"""Walk-forward comparison of early, delayed and selective quarterly release decisions."""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TextIO, TypedDict

ZERO = Decimal(0)
# A None margin defers every observation. Earlier action wins a training-loss tie.
MARGINS = (ZERO, Decimal("0.25"), Decimal("0.5"), Decimal(1), Decimal(2), None)
AGES = (1, 2, 5)


class TimingDecision(TypedDict):
    period: str
    training_periods: int
    last_training_period: str
    margin: str
    delayed: bool
    contraction_flag: bool
    immediate_contraction: bool
    next_vintage_contraction: bool
    age5_reference_contraction: bool


class PolicyTotal(TypedDict):
    disagreements: int
    delayed_quarters: int
    loss: str


class PairedComparison(TypedDict):
    corrected_disagreements: int
    introduced_disagreements: int
    net_disagreements_avoided: int
    extra_quarters_waited: int


class TimingEvaluation(TypedDict):
    complete_periods: int
    warmup_periods: int
    evaluated_periods: int
    delay_cost: str
    minimum_history: int
    learned_margins: dict[str, int]
    paired_vs_immediate: PairedComparison
    policies: dict[str, PolicyTotal]
    decisions: list[TimingDecision]


def quarter_index(label: str) -> int:
    match = re.fullmatch(r"([0-9]{4})Q([1-4])", label)
    if not match:
        raise ValueError(f"Invalid quarter {label!r}; expected YYYYQ1 through YYYYQ4")
    return int(match[1]) * 4 + int(match[2]) - 1


@dataclass(frozen=True, slots=True)
class ReleasePath:
    period: str
    first: Decimal
    next_vintage: Decimal
    reference: Decimal


def read_paths(handle: TextIO) -> tuple[list[ReleasePath], list[str]]:
    """Derive each rate from two levels in the same vintage; retain missing-path counts."""
    reader = csv.DictReader(handle)
    expected = ["period", *(f"age{a}_{side}" for a in AGES for side in ("previous", "current"))]
    if reader.fieldnames != expected:
        raise ValueError("Expected quarterly level columns: " + ",".join(expected))
    paths: list[ReleasePath] = []
    excluded: list[str] = []
    seen: set[str] = set()
    for row in reader:
        period = row["period"]
        quarter_index(period)
        if period in seen:
            raise ValueError(f"Duplicate observation {period}")
        seen.add(period)
        if None in row or any(row[k] is None for k in expected):
            raise ValueError(f"Malformed row for {period}")
        rates = []
        for age in AGES:
            pair = [row[f"age{age}_{s}"].strip() for s in ("previous", "current")]
            try:
                levels = [Decimal(value) for value in pair if value]
            except InvalidOperation as exc:
                raise ValueError(f"Invalid numeric level for {period}") from exc
            if any(not val.is_finite() or val <= 0 for val in levels):
                raise ValueError(f"Levels must be finite and positive for {period}")
            if len(levels) == 2:
                rates.append(((levels[1] / levels[0]) ** 4 - 1) * 100)
        if len(rates) == 3:
            paths.append(ReleasePath(period, *rates))
        else:
            excluded.append(period)
    return paths, excluded


def action(path: ReleasePath, margin: Decimal | None) -> tuple[bool, bool]:
    delay = margin is None or abs(path.first) < margin
    value = path.next_vintage if delay else path.first
    return value < ZERO, delay


def _tally(path: ReleasePath, margin: Decimal | None) -> tuple[int, int]:
    contraction, delayed = action(path, margin)
    return int(contraction != (path.reference < ZERO)), int(delayed)


def evaluate_timing(
    paths: Sequence[ReleasePath],
    *,
    delay_cost: Decimal = Decimal("0.10"),
    minimum_history: int = 40,
) -> TimingEvaluation:
    """Learn a delay margin on matured earlier outcomes, then score identical held-out quarters."""
    if not delay_cost.is_finite() or not ZERO <= delay_cost <= 1 or minimum_history < 1:
        raise ValueError("Use a delay cost in [0,1] and a positive minimum history")
    ordered = sorted(((quarter_index(row.period), row) for row in paths), key=lambda item: item[0])
    if len({quarter for quarter, _ in ordered}) != len(ordered):
        raise ValueError("Duplicate observation quarters")
    if any(not x.is_finite() for _, p in ordered for x in (p.first, p.next_vintage, p.reference)):
        raise ValueError("Growth rates must be finite")
    history_loss = [ZERO] * len(MARGINS)
    matured = 0
    totals = {name: [0, 0] for name in ("act_immediately", "always_wait", "adaptive_margin")}
    selected: Counter[str] = Counter()
    corrected = introduced = 0
    trace: list[TimingDecision] = []
    for quarter, path in ordered:
        # Age-five reference must have been published before the new age-one decision quarter.
        while matured < len(ordered) and ordered[matured][0] + 5 < quarter + 1:
            prior = ordered[matured][1]
            for idx, margin in enumerate(MARGINS):
                wrong, waited = _tally(prior, margin)
                history_loss[idx] += wrong + delay_cost * waited
            matured += 1
        if matured < minimum_history:
            continue
        chosen = MARGINS[min(range(len(MARGINS)), key=history_loss.__getitem__)]
        label = str(chosen) if chosen is not None else "always_wait"
        selected[label] += 1
        for name, policy in (
            ("act_immediately", ZERO),
            ("always_wait", None),
            ("adaptive_margin", chosen),
        ):
            errors, delays = _tally(path, policy)
            totals[name][0] += errors
            totals[name][1] += delays
        signal, deferred = action(path, chosen)
        reference = path.reference < ZERO
        immediate = path.first < ZERO
        corrected += int(immediate != reference and signal == reference)
        introduced += int(immediate == reference and signal != reference)
        trace.append(
            {
                "period": path.period,
                "training_periods": matured,
                "last_training_period": ordered[matured - 1][1].period,
                "margin": label,
                "delayed": deferred,
                "contraction_flag": signal,
                "immediate_contraction": immediate,
                "next_vintage_contraction": path.next_vintage < ZERO,
                "age5_reference_contraction": path.reference < ZERO,
            }
        )

    def summarize(counts: Iterable[int]) -> PolicyTotal:
        errors, delays = counts
        return {
            "disagreements": errors,
            "delayed_quarters": delays,
            "loss": str(errors + delay_cost * delays),
        }

    return {
        "complete_periods": len(ordered),
        "warmup_periods": len(ordered) - len(trace),
        "evaluated_periods": len(trace),
        "delay_cost": str(delay_cost),
        "minimum_history": minimum_history,
        "learned_margins": dict(selected),
        "paired_vs_immediate": {
            "corrected_disagreements": corrected,
            "introduced_disagreements": introduced,
            "net_disagreements_avoided": corrected - introduced,
            "extra_quarters_waited": totals["adaptive_margin"][1],
        },
        "policies": {name: summarize(counts) for name, counts in totals.items()},
        "decisions": trace,
    }
