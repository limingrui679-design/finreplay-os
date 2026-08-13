#!/usr/bin/env python3
"""Extract and seal four pre-decision native-vintage Treasury-yield facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import ALFRED_TREASURY_YIELD_SOURCE_ID, TreasuryCurveBoundaryInputLock

ROLE_POINTS = {
    "march08_two_year": ("2023-03-08", "DGS2"),
    "march08_ten_year": ("2023-03-08", "DGS10"),
    "march13_two_year": ("2023-03-13", "DGS2"),
    "march13_ten_year": ("2023-03-13", "DGS10"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="treasury-curve-2023-inversion-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="treasury-curve-2023-inversion-boundary-v1")
    parser.add_argument("--artifact-prefix", default="treasury-curve")
    parser.add_argument(
        "--title",
        default="U.S. Treasury 2-year/10-year curve inversion boundary, March 2023",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-03-16T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T05:40:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/alfred-treasury-yields.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/treasury-curve-2023/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[ALFRED_TREASURY_YIELD_SOURCE_ID],
        )
    by_point = {
        (
            str(record.payload.get("observation_date")),
            str(record.payload.get("series_id")),
        ): record
        for record in candidates
        if record.entity_id in {"fred_series:DGS2", "fred_series:DGS10"}
    }
    missing = sorted(point for point in ROLE_POINTS.values() if point not in by_point)
    if missing:
        raise SystemExit(f"required Treasury-yield facts are absent before decision: {missing}")
    roles = {role: by_point[point].record_id for role, point in ROLE_POINTS.items()}
    records = tuple(
        sorted(
            (by_point[point] for point in ROLE_POINTS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = TreasuryCurveBoundaryInputLock.create(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "replay_id": args.replay_id,
            "artifact_prefix": args.artifact_prefix,
            "title": args.title,
            "decision_time": args.decision_time,
            "build_epoch": args.build_epoch,
            "roles": roles,
            "source_response_sha256s": sorted({record.source.sha256 for record in records}),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves DGS2 and DGS10 values observed on March 8 and March 13, 2023 "
                "from four explicitly selected native ALFRED vintages conservatively knowable "
                "before the replay decision time. It excludes both March 15 event yields, other "
                "maturities and dates, security trades, positions, flows, a probability "
                "distribution, forecast, calibrated interval, causal banking or policy "
                "attribution, and investment performance. The DGS10-minus-DGS2 spreads are "
                "derived downstream rather than relabelled as reported source facts."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("Treasury-curve boundary input-lock destination contains different bytes")
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
