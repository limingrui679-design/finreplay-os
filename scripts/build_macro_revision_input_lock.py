#!/usr/bin/env python3
"""Extract and seal four pre-decision ALFRED GDP vintage facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import ALFRED_GDP_SOURCE_ID, MacroRevisionInputLock

ROLE_POINTS = {
    "q3_advance": ("2022-10-27", "2022-07-01"),
    "q3_second": ("2022-11-30", "2022-07-01"),
    "q3_predecision": ("2023-01-26", "2022-07-01"),
    "q4_advance": ("2023-01-26", "2022-10-01"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="gdp-2022q4-revision-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="gdp-2022q4-revision-boundary-v1")
    parser.add_argument("--artifact-prefix", default="gdp-revision")
    parser.add_argument(
        "--title",
        default="U.S. GDP 2022 Q4 advance-to-second-estimate revision boundary",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-02-01T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T04:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/alfred.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/gdp-revision-2022q4/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[ALFRED_GDP_SOURCE_ID],
        )
    by_point = {
        (str(record.payload.get("vintage_date")), str(record.payload.get("observation_date"))): (
            record
        )
        for record in candidates
        if record.payload.get("series_id") == "GDP"
    }
    missing = sorted(point for point in ROLE_POINTS.values() if point not in by_point)
    if missing:
        raise SystemExit(f"required ALFRED vintage points are absent before decision: {missing}")
    roles = {role: by_point[point].record_id for role, point in ROLE_POINTS.items()}
    records = tuple(
        sorted(
            (by_point[point] for point in ROLE_POINTS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = MacroRevisionInputLock.create(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "replay_id": args.replay_id,
            "artifact_prefix": args.artifact_prefix,
            "title": args.title,
            "decision_time": args.decision_time,
            "build_epoch": args.build_epoch,
            "series_id": "GDP",
            "roles": roles,
            "source_response_sha256s": sorted({record.source.sha256 for record in records}),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves four minimal ALFRED GDP facts from three explicitly named "
                "vintages that were conservatively knowable before the replay decision time. It "
                "does not include the later Q4 second estimate, a complete GDP series, an exact "
                "intraday release timestamp, a probability distribution, a forecast, or evidence "
                "of investment or policy performance."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("macro revision input-lock destination contains different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} source_hashes={len(lock.source_response_sha256s)} "
        f"lock_sha256={lock.lock_sha256} output={args.output}"
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
