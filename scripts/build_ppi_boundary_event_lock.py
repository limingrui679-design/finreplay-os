#!/usr/bin/env python3
"""Seal the April 2020 BLS PPI change as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import BLS_PPI_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="bls-ppi-2020-final-demand-change-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-09T12:30:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-05-13T12:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/bls-ppi.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/bls-ppi-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[BLS_PPI_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "bls_ppi:final_demand_united_states"
        and record.payload.get("release_date") == "2020-05-13"
        and record.payload.get("reference_month") == "2020-04"
        and record.payload.get("metric")
        == "final_demand_monthly_change_seasonally_adjusted"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one April BLS PPI event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the originally released -130-basis-point April 2020 "
                "final-demand monthly change solely as a post-decision event. The unchanged "
                "-20-basis-point March prior value and the 110-basis-point shortfall below the "
                "fixed lower endpoint are retained for an exact breach check. The event is "
                "excluded from all ReplayPack inputs and does not establish forecast skill, "
                "calibrated coverage, producer-level behavior, pandemic or market causality, "
                "investment performance, external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("BLS PPI event-lock destination has different bytes")
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
