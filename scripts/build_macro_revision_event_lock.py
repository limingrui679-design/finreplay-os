#!/usr/bin/env python3
"""Seal the post-decision ALFRED Q4 second-estimate vintage as an event lock."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import ALFRED_GDP_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="gdp-2022q4-revision-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-02-01T12:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2023-02-25T00:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/alfred.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/gdp-revision-2022q4/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[ALFRED_GDP_SOURCE_ID],
        )
    selected = tuple(
        sorted(
            (
                record
                for record in candidates
                if record.payload.get("series_id") == "GDP"
                and record.payload.get("vintage_date") == "2023-02-23"
                and record.payload.get("observation_date") == "2022-10-01"
            ),
            key=lambda record: record.record_id,
        )
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one Q4 second-estimate event record, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the official ALFRED Q4 2022 GDP value at the February 23, "
                "2023 vintage solely as a post-decision event marker. Its knowledge time is the "
                "adapter's deterministic two-day conservative bound, not an exact intraday "
                "release timestamp. The record is excluded from ReplayPack inputs and does not "
                "establish forecast skill, causal explanation, final GDP, policy impact, or "
                "investment performance."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("macro revision event-lock destination contains different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} lock_sha256={lock.lock_sha256} output={args.output}"
    )


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
