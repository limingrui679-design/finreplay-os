#!/usr/bin/env python3
"""Seal the March 2020 all-export change as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import BLS_EXPORT_PRICE_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="bls-export-prices-2020-all-exports-change-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-13T12:30:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-14T12:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/bls-export-prices.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/bls-export-prices-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[BLS_EXPORT_PRICE_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "bls_export_price_index:all_exports_united_states"
        and record.payload.get("release_date") == "2020-04-14"
        and record.payload.get("reference_month") == "2020-03"
        and record.payload.get("metric") == "all_exports_monthly_change_not_seasonally_adjusted"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March export-price event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the originally released -160-basis-point March 2020 "
                "all-export monthly change solely as a post-decision event. It also retains "
                "the unchanged -110-basis-point February value and zero revision from the "
                "locked first report. The event is inside the fixed -290-to--110-basis-point "
                "range, 130 basis points above its lower endpoint and 50 below its upper "
                "endpoint. This labelled post-event inclusion does not become forecast "
                "success and does not widen the range. The event is excluded from every "
                "ReplayPack input and does not establish calibrated coverage, export quantity "
                "or nominal export value, tariff or PPI effects, exporter or firm behavior, "
                "pandemic causality, investment "
                "performance, external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("BLS export-price event-lock destination has different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(f"records={len(lock.records)} lock_sha256={lock.lock_sha256} output={args.output}")


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


if __name__ == "__main__":
    main()
