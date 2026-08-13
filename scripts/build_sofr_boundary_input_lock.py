#!/usr/bin/env python3
"""Extract and seal two pre-decision final historical SOFR rates."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import NYFED_SOFR_SOURCE_ID, SOFRBoundaryInputLock

ROLE_DATES = {
    "september13_rate": "2019-09-13",
    "september16_rate": "2019-09-16",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="nyfed-sofr-2019-spike-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="nyfed-sofr-2019-spike-boundary-v1")
    parser.add_argument("--artifact-prefix", default="nyfed-sofr")
    parser.add_argument(
        "--title",
        default="New York Fed SOFR level boundary before the September 2019 spike",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2019-09-17T20:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T06:35:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/nyfed-sofr-history.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/nyfed-sofr-2019/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[NYFED_SOFR_SOURCE_ID],
        )
    by_date = {
        str(record.payload.get("effective_date")): record
        for record in candidates
        if record.entity_id == "nyfed_reference_rate:SOFR"
        and record.payload.get("rate_type") == "SOFR"
    }
    missing = sorted(
        effective_date for effective_date in ROLE_DATES.values() if effective_date not in by_date
    )
    if missing:
        raise SystemExit(f"required final SOFR facts are absent before decision: {missing}")
    roles = {
        role: by_date[effective_date].record_id
        for role, effective_date in ROLE_DATES.items()
    }
    records = tuple(
        sorted(
            (by_date[effective_date] for effective_date in ROLE_DATES.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = SOFRBoundaryInputLock.create(
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
                "This lock preserves the final SOFR rates effective September 13 and 16, 2019 "
                "from two official New York Fed historical API responses conservatively final "
                "before the replay decision time. It excludes the September 17 event rate, "
                "ancillary lagged summary statistics, positions, transaction-level data, a "
                "probability distribution, forecast, calibrated interval, repo-market causal "
                "attribution, policy recommendation, execution, investment performance, external "
                "review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("SOFR boundary input-lock destination contains different bytes")
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
