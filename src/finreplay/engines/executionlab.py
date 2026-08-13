"""Evidence-tiered execution envelopes with non-zero costs, latency, and capacity."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finreplay.contracts import (
    CostModel,
    EvidenceClass,
    SourceReference,
    TemporalCoverage,
)


class ExecutionError(RuntimeError):
    """Raised when execution cannot be bounded without inventing market evidence."""


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderKind(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class ExecutionPrecision(StrEnum):
    QUOTE_TRADE = "quote_trade"
    OHLCV_BAR = "ohlcv_bar"
    REFERENCE_ONLY = "reference_only"


class ExecutionStatus(StrEnum):
    BOUNDED = "bounded"
    NO_CAPACITY = "no_capacity"
    LIMIT_NOT_MARKETABLE = "limit_not_marketable"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrderSpec(_StrictModel):
    """Order intent before latency and market-capacity constraints are applied."""

    order_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    instrument_id: str = Field(min_length=1, max_length=200)
    side: OrderSide
    kind: OrderKind = OrderKind.MARKET
    quantity: float = Field(gt=0.0)
    decision_at: datetime
    latency_ms: int = Field(ge=0, le=86_400_000)
    time_in_force_ms: int = Field(gt=0, le=86_400_000)
    limit_price: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def validate_order(self) -> OrderSpec:
        _require_aware(self.decision_at, "decision_at")
        if not math.isfinite(self.quantity):
            raise ValueError("quantity must be finite")
        if self.limit_price is not None and not math.isfinite(self.limit_price):
            raise ValueError("limit_price must be finite")
        if self.kind is OrderKind.MARKET and self.limit_price is not None:
            raise ValueError("market orders cannot specify limit_price")
        if self.kind is OrderKind.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        return self


class MarketObservation(_StrictModel):
    """One market-data observation with tier-specific fields and both event/knowledge time."""

    observation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    instrument_id: str = Field(min_length=1, max_length=200)
    precision: ExecutionPrecision
    interval_start: datetime
    interval_end: datetime
    available_at: datetime
    reference_price: float = Field(gt=0.0)
    bid: float | None = Field(default=None, gt=0.0)
    ask: float | None = Field(default=None, gt=0.0)
    bid_depth: float | None = Field(default=None, ge=0.0)
    ask_depth: float | None = Field(default=None, ge=0.0)
    recent_volume: float | None = Field(default=None, ge=0.0)
    open_price: float | None = Field(default=None, gt=0.0)
    high_price: float | None = Field(default=None, gt=0.0)
    low_price: float | None = Field(default=None, gt=0.0)
    close_price: float | None = Field(default=None, gt=0.0)
    bar_volume: float | None = Field(default=None, ge=0.0)
    estimated_daily_volume: float | None = Field(default=None, gt=0.0)
    evidence_class: EvidenceClass
    source_record_ids: tuple[str, ...]
    sources: tuple[SourceReference, ...]
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_observation(self) -> MarketObservation:
        for name in ("interval_start", "interval_end", "available_at"):
            _require_aware(getattr(self, name), name)
        if self.interval_end <= self.interval_start:
            raise ValueError("interval_end must be after interval_start")
        numeric_values = (
            self.reference_price,
            self.bid,
            self.ask,
            self.bid_depth,
            self.ask_depth,
            self.recent_volume,
            self.open_price,
            self.high_price,
            self.low_price,
            self.close_price,
            self.bar_volume,
            self.estimated_daily_volume,
        )
        if any(value is not None and not math.isfinite(value) for value in numeric_values):
            raise ValueError("market observation numeric fields must be finite")
        if any(not item.strip() for item in self.limitations):
            raise ValueError("limitations must be non-empty")
        _validate_provenance(
            evidence_class=self.evidence_class,
            source_record_ids=self.source_record_ids,
            sources=self.sources,
        )
        if any(
            source.temporal_coverage is TemporalCoverage.LATEST_ONLY
            and self.available_at < source.retrieved_at
            for source in self.sources
        ):
            raise ValueError(
                "latest_only market evidence cannot be available before its retrieval time"
            )
        self._validate_tier()
        return self

    def _validate_tier(self) -> None:
        if self.precision is ExecutionPrecision.QUOTE_TRADE:
            required = (self.bid, self.ask, self.bid_depth, self.ask_depth, self.recent_volume)
            if any(value is None for value in required):
                raise ValueError("quote_trade requires bid, ask, depths, and recent volume")
            assert self.bid is not None
            assert self.ask is not None
            if self.ask < self.bid:
                raise ValueError("ask must not be below bid")
            if not self.bid <= self.reference_price <= self.ask:
                raise ValueError("quote reference_price must lie inside bid/ask")
        elif self.precision is ExecutionPrecision.OHLCV_BAR:
            required = (
                self.open_price,
                self.high_price,
                self.low_price,
                self.close_price,
                self.bar_volume,
            )
            if any(value is None for value in required):
                raise ValueError("ohlcv_bar requires open, high, low, close, and volume")
            assert self.low_price is not None
            assert self.high_price is not None
            assert self.open_price is not None
            assert self.close_price is not None
            if self.high_price < self.low_price:
                raise ValueError("OHLCV high must not be below low")
            if not (
                self.low_price <= self.open_price <= self.high_price
                and self.low_price <= self.close_price <= self.high_price
                and self.low_price <= self.reference_price <= self.high_price
            ):
                raise ValueError("OHLCV open, close, and reference must lie inside low/high")
        elif self.estimated_daily_volume is None:
            raise ValueError("reference_only requires estimated_daily_volume")


class ExecutionPolicy(_StrictModel):
    """Conservative uncertainty multipliers layered over a preregistered cost model."""

    cost_model: CostModel
    impact_lower_multiplier: float = Field(default=0.5, ge=0.0)
    impact_upper_multiplier: float = Field(default=2.0, ge=0.0)
    fallback_half_spread_upper_bps: float = Field(default=100.0, gt=0.0)
    fallback_impact_upper_bps: float = Field(default=250.0, gt=0.0)
    fallback_daily_capacity_fraction: float = Field(default=0.25, gt=0.0, le=1.0)
    fallback_trading_session_seconds: int = Field(default=23_400, ge=60, le=86_400)

    @model_validator(mode="after")
    def validate_policy(self) -> ExecutionPolicy:
        if self.impact_upper_multiplier < self.impact_lower_multiplier:
            raise ValueError("impact upper multiplier must not be below lower multiplier")
        finite = (
            self.cost_model.commission_bps,
            self.cost_model.half_spread_bps,
            self.cost_model.market_impact_bps,
            self.cost_model.borrow_bps_annual,
            self.cost_model.max_participation_rate,
            self.impact_lower_multiplier,
            self.impact_upper_multiplier,
            self.fallback_half_spread_upper_bps,
            self.fallback_impact_upper_bps,
            self.fallback_daily_capacity_fraction,
        )
        if any(not math.isfinite(value) for value in finite):
            raise ValueError("execution policy values must be finite")
        return self


class QueueObservation(_StrictModel):
    """Order-specific price-time-priority evidence for one passive limit window."""

    queue_observation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    order_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    interval_start: datetime
    interval_end: datetime
    available_at: datetime
    ahead_quantity_lower: float = Field(ge=0.0)
    ahead_quantity_upper: float = Field(ge=0.0)
    executable_volume_lower: float = Field(ge=0.0)
    executable_volume_upper: float = Field(ge=0.0)
    evidence_class: EvidenceClass
    source_record_ids: tuple[str, ...]
    sources: tuple[SourceReference, ...]
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_queue(self) -> QueueObservation:
        for name in ("interval_start", "interval_end", "available_at"):
            _require_aware(getattr(self, name), name)
        if self.interval_end <= self.interval_start:
            raise ValueError("queue interval_end must be after interval_start")
        if self.ahead_quantity_upper < self.ahead_quantity_lower:
            raise ValueError("queue ahead upper bound must not be below lower bound")
        if self.executable_volume_upper < self.executable_volume_lower:
            raise ValueError("executable volume upper bound must not be below lower bound")
        numeric_values = (
            self.ahead_quantity_lower,
            self.ahead_quantity_upper,
            self.executable_volume_lower,
            self.executable_volume_upper,
        )
        if any(not math.isfinite(value) for value in numeric_values):
            raise ValueError("queue numeric values must be finite")
        _validate_provenance(
            evidence_class=self.evidence_class,
            source_record_ids=self.source_record_ids,
            sources=self.sources,
        )
        if any(not item.strip() for item in self.limitations):
            raise ValueError("queue limitations must be non-empty")
        if any(
            source.temporal_coverage is TemporalCoverage.LATEST_ONLY
            and self.available_at < source.retrieved_at
            for source in self.sources
        ):
            raise ValueError(
                "latest_only queue evidence cannot be available before its retrieval time"
            )
        return self


class ExecutionEnvelope(_StrictModel):
    order_id: str
    instrument_id: str
    side: OrderSide
    kind: OrderKind
    requested_quantity: float
    limit_price: float | None
    observation_id: str
    queue_observation_id: str | None
    precision: ExecutionPrecision
    status: ExecutionStatus
    decision_at: datetime
    arrival_at: datetime
    expires_at: datetime
    evaluated_at: datetime
    observation_interval_start: datetime
    observation_interval_end: datetime
    observation_available_at: datetime
    reference_price: float
    fill_quantity_lower: float
    fill_quantity_upper: float
    effective_price_lower: float | None
    effective_price_upper: float | None
    total_cost_usd_lower: float
    total_cost_usd_upper: float
    slippage_bps_lower: float | None
    slippage_bps_upper: float | None
    modeled_capacity_lower_shares: float
    capacity_shares: float
    evidence_class: EvidenceClass
    queue_evidence_class: EvidenceClass | None
    source_set_historical_replay_eligible: bool
    source_record_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]
    assumptions: dict[str, float | int | str]
    limitations: tuple[str, ...] = Field(min_length=1)
    envelope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_envelope(self) -> ExecutionEnvelope:
        for name in (
            "decision_at",
            "arrival_at",
            "expires_at",
            "evaluated_at",
            "observation_interval_start",
            "observation_interval_end",
            "observation_available_at",
        ):
            _require_aware(getattr(self, name), name)
        if not self.decision_at <= self.arrival_at < self.expires_at:
            raise ValueError("invalid executable order timeline")
        if self.observation_interval_end <= self.observation_interval_start:
            raise ValueError("invalid observation interval")
        if not 0 <= self.fill_quantity_lower <= self.fill_quantity_upper:
            raise ValueError("invalid fill quantity bounds")
        if self.fill_quantity_upper > self.requested_quantity:
            raise ValueError("fill upper bound cannot exceed requested quantity")
        if not 0 <= self.modeled_capacity_lower_shares <= self.capacity_shares:
            raise ValueError("invalid modeled capacity bounds")
        numeric_values = (
            self.requested_quantity,
            self.limit_price,
            self.reference_price,
            self.fill_quantity_lower,
            self.fill_quantity_upper,
            self.effective_price_lower,
            self.effective_price_upper,
            self.total_cost_usd_lower,
            self.total_cost_usd_upper,
            self.slippage_bps_lower,
            self.slippage_bps_upper,
            self.modeled_capacity_lower_shares,
            self.capacity_shares,
        )
        if any(value is not None and not math.isfinite(value) for value in numeric_values):
            raise ValueError("execution envelope numeric values must be finite")
        if (
            self.total_cost_usd_lower < 0
            or self.total_cost_usd_upper < self.total_cost_usd_lower
        ):
            raise ValueError("invalid total cost bounds")
        if self.fill_quantity_upper == 0:
            if self.effective_price_lower is not None or self.effective_price_upper is not None:
                raise ValueError("zero-capacity envelope cannot have an effective price")
            if self.status is ExecutionStatus.BOUNDED:
                raise ValueError("bounded envelope must have positive fill capacity")
            if self.total_cost_usd_lower != 0 or self.total_cost_usd_upper != 0:
                raise ValueError("zero-capacity envelope must have zero cost bounds")
        elif (
            self.effective_price_lower is None
            or self.effective_price_upper is None
            or self.effective_price_upper < self.effective_price_lower
        ):
            raise ValueError("positive-capacity envelope requires ordered price bounds")
        elif self.status is not ExecutionStatus.BOUNDED:
            raise ValueError("positive-capacity envelope must have bounded status")
        expected_hash = hashlib.sha256(
            _canonical_json(_execution_envelope_payload(self)).encode()
        ).hexdigest()
        if self.envelope_sha256 != expected_hash:
            raise ValueError("envelope_sha256 does not match envelope content")
        return self


class ExecutionLab:
    """Produce a conservative execution envelope from the best available evidence tier."""

    def estimate(
        self,
        *,
        order: OrderSpec,
        observation: MarketObservation,
        policy: ExecutionPolicy,
        evaluated_at: datetime,
        queue: QueueObservation | None = None,
    ) -> ExecutionEnvelope:
        _require_aware(evaluated_at, "evaluated_at")
        if order.instrument_id != observation.instrument_id:
            raise ExecutionError("order and market observation instruments differ")
        arrival = order.decision_at + timedelta(milliseconds=order.latency_ms)
        expires = arrival + timedelta(milliseconds=order.time_in_force_ms)
        if evaluated_at < expires:
            raise ExecutionError("evaluation time precedes the end of the executable order window")
        latest_only_sources = tuple(
            source
            for source in observation.sources
            if source.temporal_coverage is TemporalCoverage.LATEST_ONLY
        )
        if any(order.decision_at < source.retrieved_at for source in latest_only_sources):
            raise ExecutionError(
                "latest_only market evidence cannot reconstruct a decision before retrieval"
            )
        if observation.available_at > evaluated_at:
            raise ExecutionError("market evidence was not available by evaluation time")
        if observation.interval_end > evaluated_at:
            raise ExecutionError("market observation extends beyond evaluation time")
        if (
            order.kind is OrderKind.LIMIT
            and observation.precision is not ExecutionPrecision.QUOTE_TRADE
        ):
            raise ExecutionError("limit orders require quote_trade precision")
        overlap_start = max(observation.interval_start, arrival)
        overlap_end = min(observation.interval_end, expires)
        if overlap_end <= overlap_start:
            raise ExecutionError(
                "market observation does not intersect the executable order window"
            )
        observation_seconds = (
            observation.interval_end - observation.interval_start
        ).total_seconds()
        overlap_seconds = (overlap_end - overlap_start).total_seconds()
        overlap_fraction = overlap_seconds / observation_seconds
        capacity, price_lower, price_upper, assumptions = self._tier_bounds(
            order=order,
            observation=observation,
            policy=policy,
            overlap_seconds=overlap_seconds,
            overlap_fraction=overlap_fraction,
        )
        modeled_capacity_lower = 0.0
        status = ExecutionStatus.BOUNDED
        if capacity <= 0:
            status = ExecutionStatus.NO_CAPACITY
        elif order.kind is OrderKind.LIMIT:
            assert order.limit_price is not None
            if not _limit_marketable(order=order, observation=observation):
                if queue is None:
                    status = ExecutionStatus.LIMIT_NOT_MARKETABLE
                    capacity = 0.0
                    price_lower = None
                    price_upper = None
                else:
                    queue_bounds = self._passive_limit_bounds(
                        order=order,
                        observation=observation,
                        policy=policy,
                        queue=queue,
                        evaluated_at=evaluated_at,
                        arrival=arrival,
                        expires=expires,
                        market_capacity_upper=capacity,
                    )
                    (
                        modeled_capacity_lower,
                        capacity,
                        price_lower,
                        price_upper,
                        queue_assumptions,
                    ) = queue_bounds
                    assumptions.update(queue_assumptions)
                    if capacity <= 0:
                        status = ExecutionStatus.NO_CAPACITY
            elif order.side is OrderSide.BUY:
                if queue is not None:
                    raise ExecutionError(
                        "marketable limit orders cannot use passive queue evidence"
                    )
                assert price_upper is not None
                assert price_lower is not None
                commission_multiplier = 1.0 + policy.cost_model.commission_bps / 10_000
                price_upper = min(
                    price_upper,
                    order.limit_price * commission_multiplier,
                )
                price_lower = min(price_lower, price_upper)
            else:
                if queue is not None:
                    raise ExecutionError(
                        "marketable limit orders cannot use passive queue evidence"
                    )
                assert price_lower is not None
                assert price_upper is not None
                commission_multiplier = 1.0 - policy.cost_model.commission_bps / 10_000
                price_lower = max(
                    price_lower,
                    order.limit_price * commission_multiplier,
                )
                price_upper = max(price_upper, price_lower)
        elif queue is not None:
            raise ExecutionError("market orders cannot use passive queue evidence")
        fill_upper = min(order.quantity, capacity)
        # Market observations and cost models are not broker confirmations. Without actual fill
        # evidence the guaranteed lower quantity is zero, even at the highest precision tier.
        fill_lower = 0.0
        if fill_upper <= 0:
            price_lower = None
            price_upper = None
            cost_lower = 0.0
            cost_upper = 0.0
            slippage_lower = None
            slippage_upper = None
        else:
            assert price_lower is not None
            assert price_upper is not None
            reference = observation.reference_price
            commission_floor_per_unit = (
                reference * policy.cost_model.commission_bps / 10_000
            )
            if order.side is OrderSide.BUY:
                cost_lower = fill_lower * max(0.0, price_lower - reference)
                cost_upper = fill_upper * max(
                    commission_floor_per_unit,
                    price_upper - reference,
                )
                slippage_lower = (price_lower / reference - 1.0) * 10_000
                slippage_upper = (price_upper / reference - 1.0) * 10_000
            else:
                cost_lower = fill_lower * max(0.0, reference - price_upper)
                cost_upper = fill_upper * max(
                    commission_floor_per_unit,
                    reference - price_lower,
                )
                slippage_lower = (1.0 - price_upper / reference) * 10_000
                slippage_upper = (1.0 - price_lower / reference) * 10_000
        combined_sources = (*observation.sources, *(queue.sources if queue is not None else ()))
        combined_source_ids = (
            *observation.source_record_ids,
            *(queue.source_record_ids if queue is not None else ()),
        )
        evidence_classes = {observation.evidence_class}
        if queue is not None:
            evidence_classes.add(queue.evidence_class)
        component_sources_present = bool(observation.sources) and (
            queue is None or bool(queue.sources)
        )
        source_set_historical_replay_eligible = component_sources_present and all(
            source.temporal_coverage is not TemporalCoverage.LATEST_ONLY
            for source in combined_sources
        ) and EvidenceClass.SIMULATED not in evidence_classes
        automatic_limitations = [
            "The envelope is simulated from market evidence and assumptions; it is not a "
            "broker fill, realized P&L, or proof of executable live capacity.",
            "The guaranteed fill lower bound is zero because no broker execution confirmation "
            "was supplied; modeled market or queue evidence only bounds possible capacity.",
            "Borrow cost is retained as a policy assumption but is not applied to this order "
            "because order side alone does not prove a newly opened short position or its "
            "holding period.",
        ]
        if queue is not None:
            automatic_limitations.extend(queue.limitations)
            automatic_limitations.append(
                "Queue evidence narrows modeled passive capacity, but cancellations, hidden "
                "liquidity, venue priority, and broker routing can still prevent a fill."
            )
        if not source_set_historical_replay_eligible:
            automatic_limitations.append(
                "The source set does not establish a point-in-time historical replay: it is "
                "latest-only, simulated, or lacks an upstream source receipt."
            )
        payload: dict[str, Any] = {
            "order_id": order.order_id,
            "instrument_id": order.instrument_id,
            "side": order.side.value,
            "kind": order.kind.value,
            "requested_quantity": order.quantity,
            "limit_price": order.limit_price,
            "observation_id": observation.observation_id,
            "queue_observation_id": (
                queue.queue_observation_id if queue is not None else None
            ),
            "precision": observation.precision.value,
            "status": status.value,
            "decision_at": _canonical_datetime(order.decision_at),
            "arrival_at": _canonical_datetime(arrival),
            "expires_at": _canonical_datetime(expires),
            "evaluated_at": _canonical_datetime(evaluated_at),
            "observation_interval_start": _canonical_datetime(observation.interval_start),
            "observation_interval_end": _canonical_datetime(observation.interval_end),
            "observation_available_at": _canonical_datetime(observation.available_at),
            "reference_price": observation.reference_price,
            "fill_quantity_lower": fill_lower,
            "fill_quantity_upper": fill_upper,
            "effective_price_lower": price_lower,
            "effective_price_upper": price_upper,
            "total_cost_usd_lower": cost_lower,
            "total_cost_usd_upper": cost_upper,
            "slippage_bps_lower": slippage_lower,
            "slippage_bps_upper": slippage_upper,
            "modeled_capacity_lower_shares": modeled_capacity_lower,
            "capacity_shares": capacity,
            "evidence_class": observation.evidence_class.value,
            "queue_evidence_class": (
                queue.evidence_class.value if queue is not None else None
            ),
            "source_set_historical_replay_eligible": (
                source_set_historical_replay_eligible
            ),
            "source_record_ids": sorted(set(combined_source_ids)),
            "source_hashes": sorted({source.sha256 for source in combined_sources}),
            "assumptions": assumptions,
            "limitations": [
                *observation.limitations,
                *automatic_limitations,
            ],
        }
        envelope_hash = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
        return ExecutionEnvelope(
            **payload,
            envelope_sha256=envelope_hash,
        )

    @staticmethod
    def _passive_limit_bounds(
        *,
        order: OrderSpec,
        observation: MarketObservation,
        policy: ExecutionPolicy,
        queue: QueueObservation,
        evaluated_at: datetime,
        arrival: datetime,
        expires: datetime,
        market_capacity_upper: float,
    ) -> tuple[float, float, float, float, dict[str, float | int | str]]:
        if observation.precision is not ExecutionPrecision.QUOTE_TRADE:
            raise ExecutionError("passive queue modeling requires quote_trade precision")
        if queue.order_id != order.order_id:
            raise ExecutionError("queue observation belongs to a different order")
        if queue.available_at > evaluated_at or queue.interval_end > evaluated_at:
            raise ExecutionError("queue evidence was not available by evaluation time")
        if queue.interval_start > arrival or queue.interval_end < expires:
            raise ExecutionError("queue observation must cover the full executable order window")
        if any(
            order.decision_at < source.retrieved_at
            for source in queue.sources
            if source.temporal_coverage is TemporalCoverage.LATEST_ONLY
        ):
            raise ExecutionError(
                "latest_only queue evidence cannot reconstruct a decision before retrieval"
            )
        assert order.limit_price is not None
        assert observation.bid is not None
        assert observation.ask is not None
        if order.side is OrderSide.BUY and order.limit_price >= observation.ask:
            raise ExecutionError("passive buy limit must remain below the observed ask")
        if order.side is OrderSide.SELL and order.limit_price <= observation.bid:
            raise ExecutionError("passive sell limit must remain above the observed bid")
        capacity_lower = min(
            market_capacity_upper,
            max(
                0.0,
                queue.executable_volume_lower - queue.ahead_quantity_upper,
            ),
        )
        capacity_upper = min(
            market_capacity_upper,
            max(
                0.0,
                queue.executable_volume_upper - queue.ahead_quantity_lower,
            ),
        )
        passive_cost_bps = policy.cost_model.commission_bps
        if order.side is OrderSide.BUY:
            passive_price = order.limit_price * (1.0 + passive_cost_bps / 10_000)
        else:
            passive_price = order.limit_price * (1.0 - passive_cost_bps / 10_000)
        return capacity_lower, capacity_upper, passive_price, passive_price, {
            "queue_ahead_lower_shares": queue.ahead_quantity_lower,
            "queue_ahead_upper_shares": queue.ahead_quantity_upper,
            "queue_executable_volume_lower_shares": queue.executable_volume_lower,
            "queue_executable_volume_upper_shares": queue.executable_volume_upper,
            "queue_capacity_lower_shares": capacity_lower,
            "queue_capacity_upper_shares": capacity_upper,
            "passive_price_time_priority_assumption": "explicit_queue_interval",
        }

    @staticmethod
    def _tier_bounds(
        *,
        order: OrderSpec,
        observation: MarketObservation,
        policy: ExecutionPolicy,
        overlap_seconds: float,
        overlap_fraction: float,
    ) -> tuple[float, float | None, float | None, dict[str, float | int | str]]:
        model = policy.cost_model
        reference = observation.reference_price
        if observation.precision is ExecutionPrecision.QUOTE_TRADE:
            assert observation.bid is not None
            assert observation.ask is not None
            assert observation.bid_depth is not None
            assert observation.ask_depth is not None
            assert observation.recent_volume is not None
            visible_depth = (
                observation.ask_depth if order.side is OrderSide.BUY else observation.bid_depth
            )
            window_volume = observation.recent_volume * overlap_fraction
            volume_capacity = window_volume * model.max_participation_rate
            capacity = min(visible_depth, volume_capacity)
            observed_half_spread_bps = (
                (observation.ask - observation.bid) / 2.0 / reference * 10_000
            )
            half_spread_bps = max(model.half_spread_bps, observed_half_spread_bps)
            participation = min(
                1.0,
                order.quantity / max(volume_capacity, order.quantity),
            )
            impact_base = model.market_impact_bps * math.sqrt(participation)
            impact_lower = impact_base * policy.impact_lower_multiplier
            impact_upper = impact_base * policy.impact_upper_multiplier
            if order.side is OrderSide.BUY:
                executable_quote = max(
                    observation.ask,
                    reference * (1.0 + model.half_spread_bps / 10_000),
                )
                lower = executable_quote * (
                    1.0 + (model.commission_bps + impact_lower) / 10_000
                )
                upper = executable_quote * (
                    1.0 + (model.commission_bps + impact_upper) / 10_000
                )
            else:
                executable_quote = min(
                    observation.bid,
                    reference * (1.0 - model.half_spread_bps / 10_000),
                )
                lower = executable_quote * (
                    1.0 - (model.commission_bps + impact_upper) / 10_000
                )
                upper = executable_quote * (
                    1.0 - (model.commission_bps + impact_lower) / 10_000
                )
            return capacity, lower, upper, {
                **_common_assumptions(model, overlap_seconds, overlap_fraction),
                "observed_half_spread_bps": observed_half_spread_bps,
                "effective_half_spread_bps": half_spread_bps,
                "impact_lower_bps": impact_lower,
                "impact_upper_bps": impact_upper,
                "capacity_basis": "visible_depth_and_overlap_adjusted_recent_volume",
            }
        if observation.precision is ExecutionPrecision.OHLCV_BAR:
            assert observation.low_price is not None
            assert observation.high_price is not None
            assert observation.bar_volume is not None
            window_volume = observation.bar_volume * overlap_fraction
            capacity = window_volume * model.max_participation_rate
            participation = min(1.0, order.quantity / max(capacity, order.quantity))
            impact_base = model.market_impact_bps * math.sqrt(participation)
            lower_cost_bps = (
                model.commission_bps
                + model.half_spread_bps
                + impact_base * policy.impact_lower_multiplier
            )
            upper_cost_bps = (
                model.commission_bps
                + model.half_spread_bps
                + impact_base * policy.impact_upper_multiplier
            )
            if order.side is OrderSide.BUY:
                lower = observation.low_price * (1.0 + lower_cost_bps / 10_000)
                upper = observation.high_price * (1.0 + upper_cost_bps / 10_000)
            else:
                lower = observation.low_price * (1.0 - upper_cost_bps / 10_000)
                upper = observation.high_price * (1.0 - lower_cost_bps / 10_000)
            return capacity, lower, upper, {
                **_common_assumptions(model, overlap_seconds, overlap_fraction),
                "assumed_half_spread_bps": model.half_spread_bps,
                "impact_lower_bps": impact_base * policy.impact_lower_multiplier,
                "impact_upper_bps": impact_base * policy.impact_upper_multiplier,
                "capacity_basis": "overlap_adjusted_bar_volume",
            }
        assert observation.estimated_daily_volume is not None
        session_fraction = min(
            overlap_seconds / policy.fallback_trading_session_seconds,
            policy.fallback_daily_capacity_fraction,
        )
        capacity = (
            observation.estimated_daily_volume
            * model.max_participation_rate
            * session_fraction
        )
        lower_cost_bps = model.commission_bps + model.half_spread_bps
        upper_cost_bps = (
            model.commission_bps
            + max(model.half_spread_bps, policy.fallback_half_spread_upper_bps)
            + max(model.market_impact_bps, policy.fallback_impact_upper_bps)
        )
        if order.side is OrderSide.BUY:
            lower = reference * (1.0 + lower_cost_bps / 10_000)
            upper = reference * (1.0 + upper_cost_bps / 10_000)
        else:
            lower = reference * (1.0 - upper_cost_bps / 10_000)
            upper = reference * (1.0 - lower_cost_bps / 10_000)
        return capacity, lower, upper, {
            **_common_assumptions(model, overlap_seconds, overlap_fraction),
            "half_spread_lower_bps": model.half_spread_bps,
            "half_spread_upper_bps": policy.fallback_half_spread_upper_bps,
            "impact_lower_bps": model.market_impact_bps,
            "impact_upper_bps": policy.fallback_impact_upper_bps,
            "fallback_trading_session_seconds": policy.fallback_trading_session_seconds,
            "fallback_session_fraction": session_fraction,
            "capacity_basis": "estimated_daily_volume_executable_seconds",
        }


def _limit_marketable(*, order: OrderSpec, observation: MarketObservation) -> bool:
    assert order.limit_price is not None
    assert observation.bid is not None
    assert observation.ask is not None
    if order.side is OrderSide.BUY:
        return order.limit_price >= observation.ask
    return order.limit_price <= observation.bid


def _validate_provenance(
    *,
    evidence_class: EvidenceClass,
    source_record_ids: tuple[str, ...],
    sources: tuple[SourceReference, ...],
) -> None:
    if any(not item.strip() for item in source_record_ids):
        raise ValueError("source_record_ids must be non-empty")
    if len(set(source_record_ids)) != len(source_record_ids):
        raise ValueError("source_record_ids must be unique")
    source_hashes = [source.sha256 for source in sources]
    if len(set(source_hashes)) != len(source_hashes):
        raise ValueError("sources must have unique content hashes")
    if evidence_class in {
        EvidenceClass.OBSERVED,
        EvidenceClass.REPORTED,
        EvidenceClass.EXTRACTED,
    } and (not sources or not source_record_ids):
        raise ValueError("observed, reported, and extracted evidence requires provenance")


def _common_assumptions(
    model: CostModel,
    overlap_seconds: float,
    overlap_fraction: float,
) -> dict[str, float | int | str]:
    return {
        "commission_bps": model.commission_bps,
        "model_half_spread_bps": model.half_spread_bps,
        "model_market_impact_bps": model.market_impact_bps,
        "borrow_bps_annual": model.borrow_bps_annual,
        "max_participation_rate": model.max_participation_rate,
        "observation_overlap_seconds": overlap_seconds,
        "observation_overlap_fraction": overlap_fraction,
    }


def _execution_envelope_payload(envelope: ExecutionEnvelope) -> dict[str, Any]:
    payload = envelope.model_dump(mode="json", exclude={"envelope_sha256"})
    for field_name in (
        "decision_at",
        "arrival_at",
        "expires_at",
        "evaluated_at",
        "observation_interval_start",
        "observation_interval_end",
        "observation_available_at",
    ):
        payload[field_name] = _canonical_datetime(getattr(envelope, field_name))
    # Provenance order is semantic-set order, not caller insertion order.
    payload["source_record_ids"] = sorted(payload["source_record_ids"])
    payload["source_hashes"] = sorted(payload["source_hashes"])
    return payload


def _canonical_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
