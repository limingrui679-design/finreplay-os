#!/usr/bin/env python3
"""Seal the March 2020 NRS level as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import CENSUS_NRS_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="census-nrs-2020-new-home-sales-level-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-24T14:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-23T14:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/census-nrs.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/census-nrs-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[CENSUS_NRS_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "census_hud_nrs:new_single_family_houses_sold_us"
        and record.payload.get("release_date") == "2020-04-23"
        and record.payload.get("reference_month") == "2020-03"
        and record.payload.get("metric") == "new_single_family_houses_sold_sa_annual_rate"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March NRS sales event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the preliminary 627,000-unit March 2020 new single-family "
                "houses sold SAAR headline from the April 23 archived Census/HUD NRS release "
                "solely as a post-decision official event. It also preserves that release's "
                "revision of February from its earlier 765,000 headline to 741,000 and its "
                "reported 15.4 percent monthly decline with a 14.8-point 90-percent sampling "
                "margin. The event, revision, and official sampling interval are excluded from "
                "all ReplayPack inputs and do not establish forecast skill, calibrated coverage, "
                "actual monthly transactions, property, builder, buyer, mortgage, closing, "
                "regional, housing-market, pandemic or policy causality, investment performance, "
                "external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("new-home-sales event-lock destination has different bytes")
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
