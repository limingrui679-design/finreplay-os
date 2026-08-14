#!/usr/bin/env python3
"""Extract and seal two initial Census M3 durable-goods changes."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import (
    CENSUS_DURABLE_GOODS_SOURCE_ID,
    DurableGoodsChangeBoundaryInputLock,
)

ROLE_SELECTIONS = {
    "january_initial_change": ("2020-02-27", "2020-01"),
    "february_initial_change": ("2020-03-25", "2020-02"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="census-m3-2020-durable-goods-change-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="census-m3-2020-durable-goods-change-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="census-m3")
    parser.add_argument(
        "--title",
        default=("Census M3 boundary before the March 2020 durable-goods new-orders collapse"),
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-25T12:30:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T05:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/census-m3-durable-goods.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/census-m3-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    january_cutoff = _aware_datetime("2020-02-27T13:30:00Z")
    with TimeVault(args.timevault) as vault:
        releases = {
            "2020-02-27": vault.records_as_of(
                january_cutoff,
                source_ids=[CENSUS_DURABLE_GOODS_SOURCE_ID],
            ),
            "2020-03-25": vault.records_as_of(
                args.decision_time,
                source_ids=[CENSUS_DURABLE_GOODS_SOURCE_ID],
            ),
        }
    selected = {}
    for role, (release_date, reference_month) in ROLE_SELECTIONS.items():
        matches = tuple(
            record
            for record in releases[release_date]
            if record.entity_id == "census_m3:total_durable_goods_new_orders"
            and record.payload.get("metric")
            == "total_durable_goods_new_orders_monthly_change_basis_points"
            and record.payload.get("release_date") == release_date
            and record.payload.get("reference_month") == reference_month
        )
        if len(matches) != 1:
            raise SystemExit(
                f"expected one initial Census M3 fact for {role}, found {len(matches)}"
            )
        selected[role] = matches[0]
    roles = {role: record.record_id for role, record in selected.items()}
    records = tuple(sorted(selected.values(), key=lambda record: record.record_id))
    lock = DurableGoodsChangeBoundaryInputLock.create(
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
            "source_evidence_sha256s": sorted({record.source.sha256 for record in records}),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the exact -20-basis-point January and "
                "+120-basis-point February 2020 total durable-goods new-orders monthly "
                "changes from their first verified Census M3 advance reports, both knowable "
                "at the March 25 decision time. Their 140-basis-point difference compares two "
                "initial-release changes. The lock excludes the April 24 March event, that "
                "report's January and February revision snapshot, and all later revisions. "
                "The current archived PDFs have post-release modification metadata, so their "
                "hashes prove present official evidence rather than byte identity at release. "
                "M3 is not a probability sample and is not adjusted for price changes. The "
                "lock excludes a probability distribution, confidence interval, statistical "
                "significance, forecast, calibrated coverage, manufacturing, inflation, "
                "pandemic or policy causality, recommendation, execution, investment "
                "performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("Census M3 input-lock destination has different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} source_hashes={len(lock.source_evidence_sha256s)} "
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
