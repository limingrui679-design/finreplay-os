# ADR-0001: Truth boundaries are part of the type system

- Status: accepted
- Date: 2026-08-12

## Context

Historical financial research fails when revised values leak into earlier decisions, inferred
relationships look observed, a simulated execution path looks like a trade, or an implementation
plan looks shipped. Documentation alone does not reliably prevent these category errors.

## Decision

FinReplay OS encodes the following boundaries in persisted, strict contracts:

1. Economic validity time is separate from publication, availability, revision, and ingestion time.
2. Evidence is one of `observed`, `reported`, `extracted`, `inferred`, or `simulated`.
3. Artifact maturity is one of `planned`, `contract_validated`, `fixture_validated`,
   `live_validated`, `reproduced`, or `externally_validated`.
4. Data redistribution is one of `redistributable`, `download_only`, `derived_only`,
   `bring_your_own`, or `review_required`.
5. An observed/reported graph edge requires a source object.
6. A strategy trial requires a positive trading friction and an explicitly declared attempt count.
7. A machine manifest measures records and bytes; README prose is not scale evidence.

Pydantic models reject extra fields and are immutable after validation. Schema evolution requires an
explicit semantic version and migration rather than silent acceptance.

## Consequences

- Adapters require more metadata than a normal market-data downloader.
- A missing publication time blocks point-in-time use or lowers the availability confidence.
- Some attractive scenarios remain bounded or simulated because free public data is incomplete.
- More results will be rejected, but accepted results are easier to audit and reproduce.

