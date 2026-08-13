# ExecutionLab execution-envelope method

ExecutionLab answers a deliberately narrower question than “would this order have filled?” It
computes a content-addressed **possible execution envelope** using only an order, market evidence
available by evaluation time, a preregistered non-zero cost policy, and optional order-specific
queue evidence. It never emits a broker fill or realized P&L.

## Precision tiers

1. `quote_trade` uses bid/ask, visible side depth, recent volume, latency, and the fraction of the
   observation that overlaps the executable order window. Market-order capacity is the smaller of
   visible side depth and overlap-adjusted volume times the maximum participation rate.
2. `ohlcv_bar` bounds the price between the bar low and high, adds non-zero commission, spread,
   and square-root impact, and applies only the bar-volume fraction overlapping the order window.
   It cannot support limit or queue modeling because a bar does not expose contemporaneous quotes.
3. `reference_only` is the conservative fallback. It spreads estimated daily volume over an
   explicitly declared trading-session length and grants capacity only for executable seconds,
   capped by the fallback daily fraction. It uses explicit upper spread and impact assumptions.

All three tiers keep a guaranteed fill lower bound of zero without a broker confirmation. A
positive upper bound means only that the supplied evidence and policy do not rule out that
quantity.

## Limits, latency, and queue evidence

Arrival is `decision_at + latency`; expiration is arrival plus time in force. Evidence must
intersect that interval and be available by evaluation time. A limit order is marketable only
against the observed raw bid or ask, not against a price already inflated by commission. The
effective-price envelope then adds commission without pretending commission changed the venue
limit price.

A nonmarketable limit order receives zero possible capacity unless an order-specific
`QueueObservation` covers its full executable window. Queue capacity is bounded as:

```text
lower = max(0, executable_volume_lower - ahead_quantity_upper)
upper = max(0, executable_volume_upper - ahead_quantity_lower)
```

Both bounds are capped by the separate quote/volume market-capacity bound. Queue data is kept in
its own evidence class and provenance set; hidden liquidity, cancellations, routing, and venue
priority remain explicit limitations. Even with queue evidence, the guaranteed fill lower bound
stays zero.

## Temporal and truth boundaries

- A `latest_only` source cannot reconstruct an order decision before the source was retrieved.
- `source_set_historical_replay_eligible` describes only whether every supplied upstream source
  can participate in a historical replay. It does **not** mean the modeled fill occurred.
- Observed, reported, and extracted observations require source hashes and record IDs. Simulated
  fixtures carry no fake source.
- Borrow bps remain in the policy receipt but are not charged merely because an order is a sell;
  opening a short and its holding period require separate position evidence.
- Every envelope self-hash covers the order, clocks, tier, bounds, assumptions, evidence labels,
  source hashes, and limitations; altered content fails validation.

## Golden evidence

`scripts/build_executionlab_golden.py` writes
`verification/evidence/executionlab-golden.json`. Four deterministic synthetic cases cover quote
market execution, OHLCV bounds, reference-only fallback, and passive queue bounds. Each receipt
stores independently calculated expected values, engine values, absolute errors, and the full
hashed envelope. `scripts/verify_executionlab_golden.py` recomputes the outer receipt hash and all
numerical error gates.

These cases prove internal arithmetic and boundary behavior only. They are not public market
observations, broker fills, historical performance, live capacity, or external method review.
