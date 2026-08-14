#!/usr/bin/env python3
"""Seal the March 20 working-gas stock as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import EIA_WNGSR_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="eia-wngsr-2020-working-gas-stock-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-19T14:30:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-26T14:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/eia-wngsr.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/eia-wngsr-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[EIA_WNGSR_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "eia_series:wngsr_working_gas_lower_48"
        and record.payload.get("release_date") == "2020-03-26"
        and record.payload.get("week_ending") == "2020-03-20"
        and record.payload.get("metric") == "working_gas_in_underground_storage_lower_48"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March 20 WNGSR event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the original 2,005-Bcf Lower 48 working-gas estimate for "
                "the week ending March 20, released March 26, solely as a post-decision event. "
                "Its reported -29 Bcf net change, 0.5 percent coefficient of variation, and "
                "0.8 Bcf weekly-net-change standard error remain source metadata. The event is "
                "excluded from all ReplayPack inputs and does not establish forecast skill, "
                "calibrated coverage, facility or flow measurements, storage constraints, "
                "market or pandemic causality, investment performance, external review, "
                "deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("working-gas event-lock destination has different bytes")
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
