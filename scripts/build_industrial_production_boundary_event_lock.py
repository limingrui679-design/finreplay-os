#!/usr/bin/env python3
"""Seal the March 2020 G.17 change as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import FED_G17_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="fed-g17-2020-industrial-production-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-18T12:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-15T13:15:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fed-g17.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/fed-g17-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[FED_G17_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "federal_reserve_g17:total_industrial_production"
        and record.payload.get("release_date") == "2020-04-15"
        and record.payload.get("reference_month") == "2020-03"
        and record.payload.get("metric") == "total_industrial_production_monthly_change"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March G.17 production event fact, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the reported -5.4-percent total industrial-production "
                "change for March 2020 from the April 15 archived Federal Reserve G.17 release "
                "solely as a post-decision official event. It also preserves that release's "
                "revision of February from 0.6 to 0.5 percent. The event and revision are "
                "excluded from all ReplayPack inputs and do not establish forecast skill, "
                "calibrated coverage, plant or industrial causality, investment performance, "
                "external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("G.17 industrial-production event-lock destination has different bytes")
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
