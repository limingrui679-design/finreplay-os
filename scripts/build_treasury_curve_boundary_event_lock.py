#!/usr/bin/env python3
"""Seal the March 15 DGS2/DGS10 pair as a post-decision event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import ALFRED_TREASURY_YIELD_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="treasury-curve-2023-inversion-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-03-16T12:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2023-03-18T00:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/alfred-treasury-yields.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/treasury-curve-2023/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[ALFRED_TREASURY_YIELD_SOURCE_ID],
        )
    selected = tuple(
        sorted(
            (
                record
                for record in candidates
                if record.payload.get("observation_date") == "2023-03-15"
                and record.payload.get("vintage_date") == "2023-03-16"
                and record.payload.get("series_id") in {"DGS2", "DGS10"}
            ),
            key=lambda record: record.record_id,
        )
    )
    if len(selected) != 2:
        raise SystemExit(
            f"expected two March 15 Treasury-yield event facts, found {len(selected)}"
        )
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [record.model_dump(mode="json") for record in selected],
            "claim_boundary": (
                "This lock preserves the March 15, 2023 DGS2 and DGS10 observations from their "
                "March 16 native ALFRED vintages solely as a post-decision official event pair. "
                "The conservative knowledge time is 00:00 UTC two calendar days after the "
                "vintage date. Both facts are excluded from ReplayPack inputs. Their derived "
                "spread does not establish forecast skill, calibrated coverage, recession or "
                "banking causality, policy effectiveness, investment performance, external "
                "review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("Treasury-curve boundary event-lock destination contains different bytes")
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
