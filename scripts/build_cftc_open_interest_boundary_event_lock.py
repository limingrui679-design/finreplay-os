#!/usr/bin/env python3
"""Seal the July 28 CFTC TFF row as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import CFTC_TFF_SCHEDULE_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="cftc-tff-2026-two-year-note-open-interest-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2026-07-24T19:30:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2026-07-31T19:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/cftc-tff-schedule.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/cftc-tff-2026/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[CFTC_TFF_SCHEDULE_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "cftc_contract:042601"
        and record.payload.get("report_date") == "2026-07-28"
        and record.payload.get("metric") == "open_interest_all_futures_only"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one July 28 CFTC TFF event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the July 28, 2026 CFTC Futures Only TFF UST 2-year total "
                "open-interest value of 4,406,588 contracts solely as a post-decision official "
                "event scheduled for July 31 at 3:30 p.m. Eastern. The 71,513-contract increase "
                "and the 71,513-contract excess above the fixed 4,335,075 upper endpoint are "
                "retained for an exact breach check without widening the range. The schedule "
                "remains tentative and lacks a row-level actual-publication log. The event and "
                "all category positions and trader counts are excluded from ReplayPack inputs "
                "and do not establish forecast skill, calibrated coverage, directional exposure, "
                "trading intent, notional, volume, executions, P&L, causality, investment "
                "performance, external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("CFTC TFF event-lock destination has different bytes")
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
