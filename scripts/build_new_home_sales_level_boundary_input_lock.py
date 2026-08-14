#!/usr/bin/env python3
"""Extract and seal two pre-decision Census/HUD NRS release snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import CENSUS_NRS_SOURCE_ID, NewHomeSalesLevelBoundaryInputLock

ROLE_MONTHS = {
    "january_release_snapshot": "2020-01",
    "february_decision_snapshot": "2020-02",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="census-nrs-2020-new-home-sales-level-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="census-nrs-2020-new-home-sales-level-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="census-nrs")
    parser.add_argument(
        "--title",
        default="Census/HUD NRS boundary before the March 2020 new-home-sales decline",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-24T14:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T07:15:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/census-nrs.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/census-nrs-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[CENSUS_NRS_SOURCE_ID],
        )
    by_month = {
        str(record.payload.get("reference_month")): record
        for record in candidates
        if record.entity_id == "census_hud_nrs:new_single_family_houses_sold_us"
        and record.payload.get("metric") == "new_single_family_houses_sold_sa_annual_rate"
    }
    missing = sorted(month for month in ROLE_MONTHS.values() if month not in by_month)
    if missing:
        raise SystemExit(f"required Census/HUD NRS facts are absent: {missing}")
    roles = {role: by_month[month].record_id for role, month in ROLE_MONTHS.items()}
    records = tuple(
        sorted(
            (by_month[month] for month in ROLE_MONTHS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = NewHomeSalesLevelBoundaryInputLock.create(
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
                "This lock preserves the January initial and complete March 24 February-data "
                "Census/HUD NRS snapshots knowable at the decision boundary. Range construction "
                "uses only the revised January 800,000 and initial February 765,000 SAAR values "
                "co-published in the decision snapshot; January's earlier 764,000 value is "
                "revision lineage only. The lock excludes the April 23 March event, February's "
                "later 741,000 revision, official 90-percent sampling intervals as range inputs, "
                "actual monthly transaction counts, properties, builders, buyers, mortgages, "
                "closings, a probability distribution, forecast, calibrated interval, housing-"
                "market, pandemic or policy causality, recommendation, execution, investment "
                "performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("new-home-sales input-lock destination has different bytes")
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
