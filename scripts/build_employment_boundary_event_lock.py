#!/usr/bin/env python3
"""Seal the March 10 BLS payroll headline as a post-decision event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import BLS_EMPLOYMENT_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="bls-2023-payroll-release-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-02-04T12:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2023-03-10T13:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/bls-employment.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/bls-payroll-2023/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[BLS_EMPLOYMENT_SOURCE_ID],
        )
    selected = tuple(
        sorted(
            (
                record
                for record in candidates
                if record.payload.get("release_date") == "2023-03-10"
                and record.payload.get("metric") == "nonfarm_payroll_change"
                and record.payload.get("report_period") == "2023-02"
            ),
            key=lambda record: record.record_id,
        )
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March 10 BLS payroll event fact, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the March 10, 2023 archived BLS headline payroll change for "
                "February solely as a post-decision official event marker. Its knowledge time is "
                "the page's explicit 8:30 a.m. Eastern embargo end. The fact is excluded from "
                "ReplayPack inputs and does not establish forecast skill, calibrated coverage, "
                "stationarity across benchmark changes, causal labor-market attribution, policy "
                "or investment performance, external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("employment boundary event-lock destination contains different bytes")
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
