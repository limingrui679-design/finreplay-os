from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import HttpUrl, ValidationError

from finreplay.contracts import (
    CostModel,
    EvidenceClass,
    LicenseClass,
    SourceReference,
    TemporalCoverage,
)
from finreplay.engines import (
    ExecutionEnvelope,
    ExecutionError,
    ExecutionLab,
    ExecutionPolicy,
    ExecutionPrecision,
    ExecutionStatus,
    MarketObservation,
    OrderKind,
    OrderSide,
    OrderSpec,
    QueueObservation,
)

DECISION = datetime(2023, 3, 8, 15, tzinfo=UTC)
RETRIEVED = datetime(2023, 3, 8, 15, 1, tzinfo=UTC)


def source(
    *,
    digit: str = "1",
    coverage: TemporalCoverage = TemporalCoverage.IMMUTABLE_EVENT,
    retrieved_at: datetime = RETRIEVED,
) -> SourceReference:
    return SourceReference(
        source_id="official.market.fixture",
        publisher="Official Market Fixture Publisher",
        url=HttpUrl("https://example.gov/market.json"),
        retrieved_at=retrieved_at,
        source_version=f"fixture-{digit}",
        sha256=digit * 64,
        license_class=LicenseClass.REDISTRIBUTABLE,
        temporal_coverage=coverage,
        vintage_as_of=None if coverage is TemporalCoverage.LATEST_ONLY else retrieved_at,
        redistribution_note="Deterministic test fixture only.",
    )


def cost_model() -> CostModel:
    return CostModel(
        commission_bps=1.0,
        half_spread_bps=3.0,
        market_impact_bps=4.0,
        borrow_bps_annual=50.0,
        max_participation_rate=0.1,
    )


def policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        cost_model=cost_model(),
        impact_lower_multiplier=0.5,
        impact_upper_multiplier=2.0,
        fallback_half_spread_upper_bps=100.0,
        fallback_impact_upper_bps=250.0,
        fallback_daily_capacity_fraction=0.25,
    )


def order(
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: float = 500.0,
    limit_price: float | None = None,
    latency_ms: int = 100,
    time_in_force_ms: int = 900,
) -> OrderSpec:
    return OrderSpec(
        order_id=f"order-{side.value}",
        instrument_id="security:test",
        side=side,
        kind=OrderKind.LIMIT if limit_price is not None else OrderKind.MARKET,
        quantity=quantity,
        decision_at=DECISION,
        latency_ms=latency_ms,
        time_in_force_ms=time_in_force_ms,
        limit_price=limit_price,
    )


def observation(
    precision: ExecutionPrecision = ExecutionPrecision.QUOTE_TRADE,
    **updates: Any,
) -> MarketObservation:
    values: dict[str, Any] = {
        "observation_id": f"observation-{precision.value}",
        "instrument_id": "security:test",
        "precision": precision,
        "interval_start": DECISION,
        "interval_end": DECISION + timedelta(seconds=1),
        "available_at": RETRIEVED,
        "reference_price": 100.0,
        "evidence_class": EvidenceClass.OBSERVED,
        "source_record_ids": ("official:market:1",),
        "sources": (source(),),
        "limitations": ("Fixture does not contain venue queue position.",),
    }
    if precision is ExecutionPrecision.QUOTE_TRADE:
        values.update(
            bid=99.0,
            ask=101.0,
            bid_depth=1_000.0,
            ask_depth=1_000.0,
            recent_volume=10_000.0,
        )
    elif precision is ExecutionPrecision.OHLCV_BAR:
        values.update(
            open_price=100.0,
            high_price=105.0,
            low_price=95.0,
            close_price=102.0,
            bar_volume=10_000.0,
        )
    else:
        values.update(estimated_daily_volume=100_000.0)
    values.update(updates)
    return MarketObservation(**values)


def queue_observation(
    *,
    side: OrderSide = OrderSide.BUY,
    ahead_lower: float = 100.0,
    ahead_upper: float = 200.0,
    volume_lower: float = 500.0,
    volume_upper: float = 800.0,
    **updates: Any,
) -> QueueObservation:
    values: dict[str, Any] = {
        "queue_observation_id": f"queue-{side.value}",
        "order_id": f"order-{side.value}",
        "interval_start": DECISION,
        "interval_end": DECISION + timedelta(seconds=1),
        "available_at": RETRIEVED,
        "ahead_quantity_lower": ahead_lower,
        "ahead_quantity_upper": ahead_upper,
        "executable_volume_lower": volume_lower,
        "executable_volume_upper": volume_upper,
        "evidence_class": EvidenceClass.OBSERVED,
        "source_record_ids": ("official:queue:1",),
        "sources": (source(digit="2"),),
        "limitations": ("Fixture does not include hidden venue liquidity.",),
    }
    values.update(updates)
    return QueueObservation(**values)


def estimate(
    *,
    execution_order: OrderSpec | None = None,
    market: MarketObservation | None = None,
    queue: QueueObservation | None = None,
) -> ExecutionEnvelope:
    return ExecutionLab().estimate(
        order=execution_order or order(),
        observation=market or observation(),
        policy=policy(),
        evaluated_at=RETRIEVED,
        queue=queue,
    )


def test_quote_tier_matches_hand_calculation_and_is_deterministic() -> None:
    first = estimate()
    second = estimate()

    # The executable window overlaps 90% of the one-second observation, so the
    # volume capacity is 10,000 * 0.9 * 10% = 900 shares.
    impact_base = 4.0 * (500.0 / 900.0) ** 0.5
    expected_lower = 101.0 * (1.0 + (1.0 + impact_base * 0.5) / 10_000)
    expected_upper = 101.0 * (1.0 + (1.0 + impact_base * 2.0) / 10_000)

    assert first == second
    assert first.status is ExecutionStatus.BOUNDED
    assert first.precision is ExecutionPrecision.QUOTE_TRADE
    assert first.fill_quantity_lower == 0
    assert first.fill_quantity_upper == 500
    assert first.capacity_shares == pytest.approx(900)
    assert first.effective_price_lower == pytest.approx(expected_lower)
    assert first.effective_price_upper == pytest.approx(expected_upper)
    assert first.total_cost_usd_lower == 0
    assert first.total_cost_usd_upper == pytest.approx(500 * (expected_upper - 100))
    assert first.slippage_bps_lower is not None
    assert first.slippage_bps_lower > 100
    assert first.source_set_historical_replay_eligible is True
    assert first.assumptions["observation_overlap_fraction"] == pytest.approx(0.9)
    assert any("simulated" in limitation for limitation in first.limitations)


def test_quote_sell_uses_bid_and_preserves_ordered_cost_bounds() -> None:
    result = estimate(execution_order=order(side=OrderSide.SELL))
    assert result.status is ExecutionStatus.BOUNDED
    assert result.effective_price_lower is not None
    assert result.effective_price_upper is not None
    assert result.effective_price_lower < result.effective_price_upper < 100
    assert result.slippage_bps_lower is not None
    assert result.slippage_bps_upper is not None
    assert 0 < result.slippage_bps_lower < result.slippage_bps_upper
    assert result.total_cost_usd_upper > 0


def test_ohlcv_tier_uses_only_overlap_adjusted_bar_capacity() -> None:
    result = estimate(market=observation(ExecutionPrecision.OHLCV_BAR))
    assert result.precision is ExecutionPrecision.OHLCV_BAR
    assert result.capacity_shares == pytest.approx(900)
    assert result.effective_price_lower is not None
    assert result.effective_price_upper is not None
    assert result.effective_price_lower > 95
    assert result.effective_price_upper > 105
    assert result.assumptions["capacity_basis"] == "overlap_adjusted_bar_volume"


def test_reference_only_fallback_is_explicitly_conservative() -> None:
    result = estimate(market=observation(ExecutionPrecision.REFERENCE_ONLY))
    assert result.precision is ExecutionPrecision.REFERENCE_ONLY
    # The fallback has no intraday volume curve. It spreads ADV over the declared 6.5-hour
    # session and grants only the 0.9 executable seconds: 100,000 * 10% * 0.9 / 23,400.
    assert result.capacity_shares == pytest.approx(100_000 * 0.1 * 0.9 / 23_400)
    assert result.fill_quantity_upper == pytest.approx(result.capacity_shares)
    assert result.effective_price_lower == pytest.approx(100.04)
    assert result.effective_price_upper == pytest.approx(103.51)
    assert result.slippage_bps_upper == pytest.approx(351)
    assert result.assumptions["fallback_session_fraction"] == pytest.approx(0.9 / 23_400)
    assert result.assumptions["capacity_basis"] == "estimated_daily_volume_executable_seconds"


def test_partial_window_scales_capacity_instead_of_using_full_bar() -> None:
    short_order = order(time_in_force_ms=100)
    result = estimate(execution_order=short_order)
    assert result.assumptions["observation_overlap_fraction"] == pytest.approx(0.1)
    assert result.capacity_shares == pytest.approx(100)
    assert result.fill_quantity_upper == pytest.approx(100)


def test_zero_market_volume_returns_no_capacity_not_a_fill() -> None:
    empty = observation(recent_volume=0.0, ask_depth=0.0)
    result = estimate(market=empty)
    assert result.status is ExecutionStatus.NO_CAPACITY
    assert result.fill_quantity_upper == 0
    assert result.effective_price_lower is None
    assert result.effective_price_upper is None
    assert result.total_cost_usd_upper == 0


@pytest.mark.parametrize(
    ("side", "limit_price"),
    [(OrderSide.BUY, 100.0), (OrderSide.SELL, 100.0)],
)
def test_nonmarketable_limit_is_not_filled(side: OrderSide, limit_price: float) -> None:
    result = estimate(execution_order=order(side=side, limit_price=limit_price))
    assert result.status is ExecutionStatus.LIMIT_NOT_MARKETABLE
    assert result.capacity_shares == 0
    assert result.fill_quantity_upper == 0


@pytest.mark.parametrize(
    ("side", "limit_price"),
    [(OrderSide.BUY, 101.03), (OrderSide.SELL, 98.97)],
)
def test_marketable_limit_caps_the_effective_price_envelope(
    side: OrderSide, limit_price: float
) -> None:
    result = estimate(execution_order=order(side=side, limit_price=limit_price))
    assert result.status is ExecutionStatus.BOUNDED
    if side is OrderSide.BUY:
        assert result.effective_price_upper == pytest.approx(limit_price * 1.0001)
    else:
        assert result.effective_price_lower == pytest.approx(limit_price * 0.9999)


@pytest.mark.parametrize(
    ("side", "limit_price"),
    [(OrderSide.BUY, 99.0), (OrderSide.SELL, 101.0)],
)
def test_passive_limit_uses_explicit_queue_capacity_bounds(
    side: OrderSide, limit_price: float
) -> None:
    result = estimate(
        execution_order=order(side=side, limit_price=limit_price),
        queue=queue_observation(side=side),
    )
    assert result.status is ExecutionStatus.BOUNDED
    assert result.queue_observation_id == f"queue-{side.value}"
    assert result.modeled_capacity_lower_shares == pytest.approx(300)
    assert result.capacity_shares == pytest.approx(700)
    assert result.fill_quantity_lower == 0
    assert result.fill_quantity_upper == 500
    assert result.assumptions["queue_capacity_lower_shares"] == pytest.approx(300)
    assert result.assumptions["queue_capacity_upper_shares"] == pytest.approx(700)
    assert result.source_hashes == ("1" * 64, "2" * 64)
    assert "hidden venue liquidity" in result.limitations[-2]


def test_passive_queue_with_no_volume_beyond_ahead_position_returns_no_capacity() -> None:
    result = estimate(
        execution_order=order(limit_price=99.0),
        queue=queue_observation(
            ahead_lower=500,
            ahead_upper=600,
            volume_lower=100,
            volume_upper=400,
        ),
    )
    assert result.status is ExecutionStatus.NO_CAPACITY
    assert result.modeled_capacity_lower_shares == 0
    assert result.capacity_shares == 0
    assert result.fill_quantity_upper == 0


@pytest.mark.parametrize(
    ("execution_order", "market", "queue", "match"),
    [
        (
            order(limit_price=99.0),
            observation(),
            queue_observation(order_id="different-order"),
            "different order",
        ),
        (
            order(limit_price=99.0),
            observation(),
            queue_observation(available_at=RETRIEVED + timedelta(seconds=1)),
            "not available",
        ),
        (
            order(limit_price=99.0),
            observation(),
            queue_observation(interval_start=DECISION + timedelta(milliseconds=200)),
            "cover the full",
        ),
        (
            order(limit_price=90.0),
            observation(ExecutionPrecision.OHLCV_BAR),
            queue_observation(),
            "require quote_trade",
        ),
        (
            order(limit_price=102.0),
            observation(),
            queue_observation(),
            "marketable limit",
        ),
        (
            order(),
            observation(),
            queue_observation(),
            "market orders",
        ),
    ],
)
def test_queue_evidence_must_match_order_window_and_execution_mode(
    execution_order: OrderSpec,
    market: MarketObservation,
    queue: QueueObservation,
    match: str,
) -> None:
    with pytest.raises(ExecutionError, match=match):
        estimate(execution_order=execution_order, market=market, queue=queue)


def test_queue_contract_rejects_impossible_bounds_and_unsourced_observation() -> None:
    with pytest.raises(ValidationError, match="ahead upper"):
        queue_observation(ahead_lower=2, ahead_upper=1)
    with pytest.raises(ValidationError, match="executable volume upper"):
        queue_observation(volume_lower=2, volume_upper=1)
    with pytest.raises(ValidationError, match="requires provenance"):
        queue_observation(sources=(), source_record_ids=())
    with pytest.raises(ValidationError, match="finite"):
        queue_observation(ahead_upper=float("inf"))


def test_latest_only_source_cannot_reconstruct_a_pre_retrieval_decision() -> None:
    latest = source(coverage=TemporalCoverage.LATEST_ONLY)
    with pytest.raises(ValidationError, match="cannot be available before its retrieval"):
        observation(sources=(latest,), available_at=DECISION)

    current_market = observation(
        sources=(latest,),
        available_at=RETRIEVED,
        interval_start=RETRIEVED,
        interval_end=RETRIEVED + timedelta(seconds=1),
    )
    with pytest.raises(ExecutionError, match="cannot reconstruct a decision before retrieval"):
        estimate(market=current_market)


def test_current_latest_only_order_is_allowed_but_not_historical_replay_eligible() -> None:
    current_decision = RETRIEVED
    latest = source(coverage=TemporalCoverage.LATEST_ONLY)
    current_order = order().model_copy(update={"decision_at": current_decision})
    current_market = observation(
        sources=(latest,),
        available_at=RETRIEVED,
        interval_start=current_decision,
        interval_end=current_decision + timedelta(seconds=1),
    )
    result = ExecutionLab().estimate(
        order=current_order,
        observation=current_market,
        policy=policy(),
        evaluated_at=current_decision + timedelta(minutes=1),
    )
    assert result.source_set_historical_replay_eligible is False
    assert "latest-only" in result.limitations[-1]


@pytest.mark.parametrize(
    ("execution_order", "market", "evaluated_at", "match"),
    [
        (
            order(),
            observation(instrument_id="security:other"),
            RETRIEVED,
            "instruments differ",
        ),
        (
            order(),
            observation(available_at=RETRIEVED + timedelta(seconds=1)),
            RETRIEVED,
            "not available",
        ),
        (
            order(),
            observation(interval_start=DECISION + timedelta(seconds=2),
                        interval_end=DECISION + timedelta(seconds=3)),
            RETRIEVED,
            "does not intersect",
        ),
        (
            order(),
            observation(),
            DECISION + timedelta(milliseconds=500),
            "precedes the end",
        ),
    ],
)
def test_unusable_market_evidence_fails_closed(
    execution_order: OrderSpec,
    market: MarketObservation,
    evaluated_at: datetime,
    match: str,
) -> None:
    with pytest.raises(ExecutionError, match=match):
        ExecutionLab().estimate(
            order=execution_order,
            observation=market,
            policy=policy(),
            evaluated_at=evaluated_at,
        )


@pytest.mark.parametrize(
    ("precision", "updates", "match"),
    [
        (ExecutionPrecision.QUOTE_TRADE, {"ask": None}, "requires bid"),
        (ExecutionPrecision.QUOTE_TRADE, {"bid": 102.0}, "ask must not"),
        (ExecutionPrecision.QUOTE_TRADE, {"reference_price": 98.0}, "inside bid/ask"),
        (ExecutionPrecision.OHLCV_BAR, {"bar_volume": None}, "requires open"),
        (ExecutionPrecision.OHLCV_BAR, {"high_price": 94.0}, "high must not"),
        (ExecutionPrecision.OHLCV_BAR, {"reference_price": 106.0}, "inside low/high"),
        (ExecutionPrecision.REFERENCE_ONLY, {"estimated_daily_volume": None}, "requires"),
    ],
)
def test_precision_tiers_reject_missing_or_incoherent_fields(
    precision: ExecutionPrecision, updates: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        observation(precision, **updates)


def test_observed_extracted_and_reported_evidence_require_provenance() -> None:
    for evidence in (
        EvidenceClass.OBSERVED,
        EvidenceClass.REPORTED,
        EvidenceClass.EXTRACTED,
    ):
        with pytest.raises(ValidationError, match="requires provenance"):
            observation(evidence_class=evidence, sources=(), source_record_ids=())


def test_simulated_fallback_has_no_fake_source_and_is_not_historical() -> None:
    simulated = observation(
        ExecutionPrecision.REFERENCE_ONLY,
        evidence_class=EvidenceClass.SIMULATED,
        sources=(),
        source_record_ids=(),
        available_at=DECISION,
    )
    result = estimate(market=simulated)
    assert result.source_hashes == ()
    assert result.source_record_ids == ()
    assert result.source_set_historical_replay_eligible is False


def test_contracts_reject_invalid_times_duplicates_and_nonfinite_values() -> None:
    values = order().model_dump()
    values["decision_at"] = DECISION.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="timezone-aware"):
        OrderSpec.model_validate(values)
    with pytest.raises(ValidationError, match="finite"):
        order(quantity=float("inf"))
    with pytest.raises(ValidationError, match="interval_end"):
        observation(interval_end=DECISION)
    with pytest.raises(ValidationError, match="unique"):
        observation(source_record_ids=("same", "same"))
    with pytest.raises(ValidationError, match="unique content hashes"):
        observation(sources=(source(), source()))
    with pytest.raises(ValidationError, match="finite"):
        observation(reference_price=float("inf"))
    with pytest.raises(ValidationError, match="upper multiplier"):
        ExecutionPolicy(
            cost_model=cost_model(),
            impact_lower_multiplier=2,
            impact_upper_multiplier=1,
        )


def test_envelope_hash_rejects_tampering_and_is_timezone_canonical() -> None:
    original = estimate()
    equivalent_order = order().model_copy(
        update={"decision_at": DECISION.astimezone(timezone(timedelta(hours=-5)))}
    )
    equivalent_market = observation(
        interval_start=DECISION.astimezone(timezone(timedelta(hours=-5))),
        interval_end=(DECISION + timedelta(seconds=1)).astimezone(
            timezone(timedelta(hours=-5))
        ),
        available_at=RETRIEVED.astimezone(timezone(timedelta(hours=-5))),
        sources=(
            source(
                retrieved_at=RETRIEVED.astimezone(timezone(timedelta(hours=-5))),
            ),
        ),
    )
    equivalent = ExecutionLab().estimate(
        order=equivalent_order,
        observation=equivalent_market,
        policy=policy(),
        evaluated_at=RETRIEVED.astimezone(timezone(timedelta(hours=-5))),
    )
    assert equivalent.envelope_sha256 == original.envelope_sha256

    values = original.model_dump()
    values["fill_quantity_upper"] = 400
    with pytest.raises(ValidationError, match="does not match"):
        ExecutionEnvelope.model_validate(values)
