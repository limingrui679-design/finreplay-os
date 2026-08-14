#!/usr/bin/env python3
"""Extract and seal two initial-release Census C30 construction levels."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import (
    CENSUS_C30_SOURCE_ID,
    ConstructionSpendingBoundaryInputLock,
)

ROLE_SELECTIONS = {
    "january_headline_level": ("2020-03-02", "2020-01"),
    "february_headline_level": ("2020-04-01", "2020-02"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="census-c30-2020-construction-spending-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="census-c30-2020-construction-spending-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="census-c30")
    parser.add_argument(
        "--title",
        default=(
            "Census C30 boundary before the March 2020 construction-spending level decline"
        ),
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-01T14:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T03:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/census-c30.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/census-c30-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    january_cutoff = _aware_datetime("2020-03-02T15:00:00Z")
    with TimeVault(args.timevault) as vault:
        releases = {
            "2020-03-02": vault.records_as_of(
                january_cutoff,
                source_ids=[CENSUS_C30_SOURCE_ID],
            ),
            "2020-04-01": vault.records_as_of(
                args.decision_time,
                source_ids=[CENSUS_C30_SOURCE_ID],
            ),
        }
    selected = {}
    for role, (release_date, reference_month) in ROLE_SELECTIONS.items():
        matches = tuple(
            record
            for record in releases[release_date]
            if record.entity_id == "census_c30:total_construction_value_put_in_place"
            and record.payload.get("metric")
            == "total_construction_saar_level_million_dollars"
            and record.payload.get("release_date") == release_date
            and record.payload.get("reference_month") == reference_month
            and record.payload.get("estimate_status") == "preliminary"
        )
        if len(matches) != 1:
            raise SystemExit(
                f"expected one initial C30 fact for {role}, found {len(matches)}"
            )
        selected[role] = matches[0]
    roles = {role: record.record_id for role, record in selected.items()}
    records = tuple(sorted(selected.values(), key=lambda record: record.record_id))
    lock = ConstructionSpendingBoundaryInputLock.create(
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
            "source_response_sha256s": sorted(
                {
                    digest
                    for record in records
                    for digest in (
                        record.payload["release_pdf_sha256"],
                        record.payload["release_xlsx_sha256"],
                    )
                }
            ),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the exact January 1,369,223-million-dollar and February "
                "1,366,697-million-dollar total-construction SAAR Table 1 levels from their "
                "initial archived Census C30 releases, both knowable at the April 1 decision "
                "time. Their 2,526-million-dollar difference compares two initial-release "
                "current-month levels; it is not the official February monthly change, which "
                "uses a revised January denominator. The lock excludes the May 1 March event, "
                "all later revisions, official sampling intervals as range inputs, real volume, "
                "projects, transactions, a probability distribution, forecast, calibrated "
                "interval, construction, inflation, pandemic or policy causality, recommendation, "
                "execution, investment performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("construction-spending input-lock destination has different bytes")
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
