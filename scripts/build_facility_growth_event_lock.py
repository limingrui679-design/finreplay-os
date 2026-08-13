#!/usr/bin/env python3
"""Seal the March 30 H.4.1 BTFP Wednesday balance as a post-decision event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import FED_H41_BTFP_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="btfp-2023-early-growth-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-03-25T12:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2023-04-01T00:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fed-h41.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/btfp-growth-2023/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[FED_H41_BTFP_SOURCE_ID],
        )
    selected = tuple(
        sorted(
            (
                record
                for record in candidates
                if record.payload.get("release_date") == "2023-03-30"
                and record.payload.get("metric") == "wednesday_outstanding"
                and record.payload.get("program") == "Bank Term Funding Program"
            ),
            key=lambda record: record.record_id,
        )
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March 30 BTFP event fact, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the March 30, 2023 archived H.4.1 BTFP Wednesday balance "
                "solely as a post-decision official event marker. Its knowledge time is the "
                "adapter's two-day conservative bound, not a claimed intraday publication time. "
                "The record is excluded from ReplayPack inputs and does not establish forecast "
                "skill, calibrated coverage, borrower behavior, causal systemic stress, policy "
                "effectiveness, investment performance, or a complete H.4.1 account."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("facility growth event-lock destination contains different bytes")
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
