#!/usr/bin/env python3
"""Build deterministic hand-calculated ExecutionLab golden evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from finreplay.contracts import CostModel, EvidenceClass
from finreplay.engines import (
    ExecutionLab,
    ExecutionPolicy,
    ExecutionPrecision,
    MarketObservation,
    OrderKind,
    OrderSide,
    OrderSpec,
    QueueObservation,
)

DECISION = datetime(2023, 3, 8, 15, tzinfo=UTC)
EVALUATED = DECISION + timedelta(minutes=1)
TOLERANCE = 1e-12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/executionlab-golden.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    policy = ExecutionPolicy(
        cost_model=CostModel(
            commission_bps=1.0,
            half_spread_bps=3.0,
            market_impact_bps=4.0,
            borrow_bps_annual=50.0,
            max_participation_rate=0.1,
        ),
        impact_lower_multiplier=0.5,
        impact_upper_multiplier=2.0,
        fallback_half_spread_upper_bps=100.0,
        fallback_impact_upper_bps=250.0,
        fallback_daily_capacity_fraction=0.25,
        fallback_trading_session_seconds=23_400,
    )
    lab = ExecutionLab()
    quote = _quote_observation()
    market_order = _order("golden-market-buy")
    market_envelope = lab.estimate(
        order=market_order,
        observation=quote,
        policy=policy,
        evaluated_at=EVALUATED,
    )
    overlap_fraction = 0.9
    quote_capacity = 10_000.0 * overlap_fraction * 0.1
    impact_base = 4.0 * math.sqrt(500.0 / quote_capacity)
    quote_lower = 101.0 * (1.0 + (1.0 + impact_base * 0.5) / 10_000)
    quote_upper = 101.0 * (1.0 + (1.0 + impact_base * 2.0) / 10_000)
    market_expected = {
        "capacity_shares": quote_capacity,
        "fill_quantity_upper": 500.0,
        "effective_price_lower": quote_lower,
        "effective_price_upper": quote_upper,
        "total_cost_usd_upper": 500.0 * (quote_upper - 100.0),
    }

    bar_order = _order("golden-bar-buy")
    bar_envelope = lab.estimate(
        order=bar_order,
        observation=_bar_observation(),
        policy=policy,
        evaluated_at=EVALUATED,
    )
    bar_impact = 4.0 * math.sqrt(500.0 / 900.0)
    bar_expected = {
        "capacity_shares": 900.0,
        "effective_price_lower": 95.0
        * (1.0 + (1.0 + 3.0 + bar_impact * 0.5) / 10_000),
        "effective_price_upper": 105.0
        * (1.0 + (1.0 + 3.0 + bar_impact * 2.0) / 10_000),
    }

    fallback_order = _order("golden-fallback-buy")
    fallback_envelope = lab.estimate(
        order=fallback_order,
        observation=_fallback_observation(),
        policy=policy,
        evaluated_at=EVALUATED,
    )
    fallback_expected = {
        "capacity_shares": 100_000.0 * 0.1 * 0.9 / 23_400,
        "effective_price_lower": 100.0 * (1.0 + 4.0 / 10_000),
        "effective_price_upper": 100.0 * (1.0 + 351.0 / 10_000),
    }

    passive_order = _order(
        "golden-passive-buy",
        kind=OrderKind.LIMIT,
        limit_price=99.0,
    )
    passive_envelope = lab.estimate(
        order=passive_order,
        observation=quote.model_copy(update={"observation_id": "golden-quote-passive"}),
        policy=policy,
        evaluated_at=EVALUATED,
        queue=QueueObservation(
            queue_observation_id="golden-queue-passive",
            order_id=passive_order.order_id,
            interval_start=DECISION,
            interval_end=DECISION + timedelta(seconds=1),
            available_at=DECISION + timedelta(seconds=1),
            ahead_quantity_lower=100.0,
            ahead_quantity_upper=200.0,
            executable_volume_lower=500.0,
            executable_volume_upper=800.0,
            evidence_class=EvidenceClass.SIMULATED,
            source_record_ids=(),
            sources=(),
            limitations=(
                "Synthetic queue fixture tests arithmetic, not an observed venue queue.",
            ),
        ),
    )
    passive_expected = {
        "modeled_capacity_lower_shares": 300.0,
        "capacity_shares": 700.0,
        "effective_price_lower": 99.0 * 1.0001,
        "effective_price_upper": 99.0 * 1.0001,
    }

    cases = [
        _case("quote_market_buy", market_envelope, market_expected),
        _case("ohlcv_market_buy", bar_envelope, bar_expected),
        _case("reference_only_market_buy", fallback_envelope, fallback_expected),
        _case("quote_passive_limit_buy", passive_envelope, passive_expected),
    ]
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "evidence_kind": "synthetic_hand_calculated_golden",
        "claim_boundary": (
            "These deterministic synthetic cases prove internal execution-envelope arithmetic, "
            "latency-window scaling, non-zero costs, conservative fallback, passive queue bounds, "
            "and content hashing. They are not public market observations, historical returns, "
            "broker fills, or proof of live capacity."
        ),
        "tolerance": TOLERANCE,
        "policy": policy.model_dump(mode="json"),
        "all_within_tolerance": all(case["within_tolerance"] for case in cases),
        "cases": cases,
    }
    if not payload["all_within_tolerance"]:
        raise SystemExit("golden calculation mismatch")
    payload["receipt_sha256"] = _sha256(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    print(f"cases={len(cases)} all_within_tolerance=true receipt={args.output}")


def _order(
    order_id: str,
    *,
    kind: OrderKind = OrderKind.MARKET,
    limit_price: float | None = None,
) -> OrderSpec:
    return OrderSpec(
        order_id=order_id,
        instrument_id="synthetic:golden-security",
        side=OrderSide.BUY,
        kind=kind,
        quantity=500.0,
        decision_at=DECISION,
        latency_ms=100,
        time_in_force_ms=900,
        limit_price=limit_price,
    )


def _base_observation(
    observation_id: str,
    precision: ExecutionPrecision,
    **fields: Any,
) -> MarketObservation:
    return MarketObservation(
        observation_id=observation_id,
        instrument_id="synthetic:golden-security",
        precision=precision,
        interval_start=DECISION,
        interval_end=DECISION + timedelta(seconds=1),
        available_at=DECISION + timedelta(seconds=1),
        reference_price=100.0,
        evidence_class=EvidenceClass.SIMULATED,
        source_record_ids=(),
        sources=(),
        limitations=("Synthetic arithmetic fixture; no real market evidence.",),
        **fields,
    )


def _quote_observation() -> MarketObservation:
    return _base_observation(
        "golden-quote-market",
        ExecutionPrecision.QUOTE_TRADE,
        bid=99.0,
        ask=101.0,
        bid_depth=1_000.0,
        ask_depth=1_000.0,
        recent_volume=10_000.0,
    )


def _bar_observation() -> MarketObservation:
    return _base_observation(
        "golden-ohlcv-market",
        ExecutionPrecision.OHLCV_BAR,
        open_price=100.0,
        high_price=105.0,
        low_price=95.0,
        close_price=102.0,
        bar_volume=10_000.0,
    )


def _fallback_observation() -> MarketObservation:
    return _base_observation(
        "golden-reference-market",
        ExecutionPrecision.REFERENCE_ONLY,
        estimated_daily_volume=100_000.0,
    )


def _case(name: str, envelope: Any, expected: dict[str, float]) -> dict[str, Any]:
    actual = {key: float(getattr(envelope, key)) for key in expected}
    absolute_error = {key: abs(actual[key] - value) for key, value in expected.items()}
    return {
        "case_id": name,
        "expected": expected,
        "actual": actual,
        "absolute_error": absolute_error,
        "within_tolerance": all(value <= TOLERANCE for value in absolute_error.values()),
        "envelope": envelope.model_dump(mode="json"),
    }


def _sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


if __name__ == "__main__":
    main()
