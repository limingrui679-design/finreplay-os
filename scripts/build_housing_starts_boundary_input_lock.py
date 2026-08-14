#!/usr/bin/env python3
"""Extract and seal two pre-decision Census/HUD NRC headline levels."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import CENSUS_NRC_SOURCE_ID, HousingStartsBoundaryInputLock

ROLE_MONTHS = {
    "january_headline_level": "2020-01",
    "february_headline_level": "2020-02",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="census-nrc-2020-housing-starts-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="census-nrc-2020-housing-starts-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="census-nrc")
    parser.add_argument(
        "--title",
        default="Census/HUD NRC boundary before the March 2020 housing-starts decline",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-19T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T14:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/census-nrc.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/census-nrc-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[CENSUS_NRC_SOURCE_ID],
        )
    by_month = {
        str(record.payload.get("reference_month")): record
        for record in candidates
        if record.entity_id == "census_hud_nrc:privately_owned_housing_starts_total"
        and record.payload.get("metric")
        == "privately_owned_total_housing_starts_sa_annual_rate"
    }
    missing = sorted(month for month in ROLE_MONTHS.values() if month not in by_month)
    if missing:
        raise SystemExit(f"required Census/HUD NRC facts are absent: {missing}")
    roles = {role: by_month[month].record_id for role, month in ROLE_MONTHS.items()}
    records = tuple(
        sorted(
            (by_month[month] for month in ROLE_MONTHS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = HousingStartsBoundaryInputLock.create(
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
                "This lock preserves January and February 2020 preliminary total housing-starts "
                "SAAR headline levels from two official archived Census/HUD NRC PDFs knowable "
                "before the decision time. The 32,000-unit difference compares those two "
                "release-time headline levels; it is not the official month-over-month change, "
                "which uses a revised prior-month estimate. The lock excludes the April 16 "
                "March event, the later February revision, official sampling intervals as range "
                "inputs, projects, builders, properties, a probability distribution, forecast, "
                "calibrated interval, pandemic or policy causality, recommendation, execution, "
                "investment performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("housing-starts input-lock destination has different bytes")
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
