#!/usr/bin/env python3
"""Seal the May 7 G.19 March value as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import FED_G19_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="fed-g19-2020-revolving-credit-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-07T19:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-05-07T19:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fed-g19.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/fed-g19-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[FED_G19_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "federal_reserve_g19:revolving_consumer_credit"
        and record.payload.get("release_date") == "2020-05-07"
        and record.payload.get("reference_month") == "2020-03"
        and record.payload.get("metric") == "revolving_consumer_credit_percent_change_annual_rate"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March G.19 consumer-credit event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the preliminary -30.9 percent March 2020 revolving-"
                "credit simple annual rate from the May 7 archived Federal Reserve G.19 "
                "release solely as a post-decision official event. The same release snapshot "
                "records January at -3.7 percent and February at 3.6 percent, each a 1.0 "
                "percentage-point downward revision from the April decision snapshot. The "
                "event and revisions are excluded from all ReplayPack inputs and do not "
                "establish forecast skill, calibrated coverage, household, card-spending, "
                "consumer, pandemic, policy, or lender causality, investment performance, "
                "external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("G.19 consumer-credit event-lock destination has different bytes")
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
