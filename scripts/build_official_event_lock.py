#!/usr/bin/env python3
"""Build a content-addressed post-decision event lock from TimeVault records."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--decision-time", type=_aware_datetime, required=True)
    parser.add_argument("--knowledge-cutoff", type=_aware_datetime, required=True)
    parser.add_argument("--record-id", action="append", required=True)
    parser.add_argument(
        "--source-id",
        action="append",
        default=None,
        help="Official adapter source ID; repeat for multiple sources.",
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/timevault.duckdb"),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    requested_ids = tuple(sorted(set(args.record_id)))
    if len(requested_ids) != len(args.record_id):
        raise SystemExit("--record-id values must be unique")
    source_ids = args.source_id or ["sec.edgar.submissions"]
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=source_ids,
        )
    selected = tuple(
        sorted(
            (record for record in candidates if record.record_id in requested_ids),
            key=lambda record: record.record_id,
        )
    )
    selected_ids = tuple(record.record_id for record in selected)
    if selected_ids != requested_ids:
        missing = sorted(set(requested_ids) - set(selected_ids))
        raise SystemExit(f"event records not found by knowledge cutoff: {missing}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [record.model_dump(mode="json") for record in selected],
            "claim_boundary": (
                "This lock preserves immutable official event metadata and its exact knowledge "
                "time solely as a post-decision event marker. It is excluded from replay "
                "decision inputs and does not establish causality, filing-content accuracy, "
                "market impact, regulator conclusions, or a complete account of the event."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("event-lock destination contains different bytes")
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
