#!/usr/bin/env python3
"""Extract and seal the April 7 G.19 revolving-credit decision view."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import FED_G19_SOURCE_ID, ConsumerCreditBoundaryInputLock

ROLE_MONTHS = {
    "january_revised_change": "2020-01",
    "february_preliminary_change": "2020-02",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="fed-g19-2020-revolving-credit-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="fed-g19-2020-revolving-credit-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="fed-g19")
    parser.add_argument(
        "--title",
        default=(
            "Federal Reserve G.19 boundary before the March 2020 revolving-credit contraction"
        ),
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-07T19:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T02:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fed-g19.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/fed-g19-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[FED_G19_SOURCE_ID],
        )
    by_month = {
        str(record.payload.get("reference_month")): record
        for record in candidates
        if record.entity_id == "federal_reserve_g19:revolving_consumer_credit"
        and record.payload.get("metric") == "revolving_consumer_credit_percent_change_annual_rate"
        and record.payload.get("release_date") == "2020-04-07"
    }
    missing = sorted(month for month in ROLE_MONTHS.values() if month not in by_month)
    if missing:
        raise SystemExit(f"required Federal Reserve G.19 facts are absent: {missing}")
    roles = {role: by_month[month].record_id for role, month in ROLE_MONTHS.items()}
    records = tuple(
        sorted(
            (by_month[month] for month in ROLE_MONTHS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = ConsumerCreditBoundaryInputLock.create(
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
                "This lock preserves the January revised -2.7 percent and February "
                "preliminary 4.6 percent revolving-credit simple annual rates in the official "
                "April 7, 2020 Federal Reserve G.19 table, both knowable at the exact 3 p.m. "
                "Eastern decision time. It excludes the May 7 March event, later January and "
                "February revisions, rounded headline fractions as substitutes for table "
                "values, households, card accounts, transactions, a probability distribution, "
                "forecast, calibrated interval, consumer, pandemic or policy causality, "
                "recommendation, execution, investment performance, external review, "
                "deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("G.19 consumer-credit input-lock destination has different bytes")
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
