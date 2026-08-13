#!/usr/bin/env python3
"""Extract and seal two pre-decision Treasury DTS TGA balance facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import TREASURY_DTS_SOURCE_ID, TGACashBoundaryInputLock

ROLE_DATES = {
    "may31_closing": "2023-05-31",
    "june01_closing": "2023-06-01",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="treasury-tga-2023-cash-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="treasury-tga-2023-cash-boundary-v1")
    parser.add_argument("--artifact-prefix", default="treasury-tga")
    parser.add_argument(
        "--title",
        default="U.S. Treasury General Account cash-balance boundary, June 2023",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-06-02T21:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T06:10:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/treasury-dts.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/treasury-tga-2023/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[TREASURY_DTS_SOURCE_ID],
        )
    by_date = {
        str(record.payload.get("report_date")): record
        for record in candidates
        if record.entity_id == "us_treasury:treasury_general_account"
        and record.payload.get("metric") == "tga_closing_balance"
    }
    missing = sorted(
        report_date for report_date in ROLE_DATES.values() if report_date not in by_date
    )
    if missing:
        raise SystemExit(f"required Treasury DTS facts are absent before decision: {missing}")
    roles = {role: by_date[report_date].record_id for role, report_date in ROLE_DATES.items()}
    records = tuple(
        sorted(
            (by_date[report_date] for report_date in ROLE_DATES.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = TGACashBoundaryInputLock.create(
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
                "This lock preserves the May 31 and June 1, 2023 TGA closing balances from two "
                "date-stamped Daily Treasury Statement PDFs conservatively knowable before the "
                "replay decision time. It excludes the June 2 event balance, all other DTS rows, "
                "a probability distribution, forecast, calibrated interval, debt-limit causal "
                "attribution, fiscal-solvency conclusion, policy recommendation, execution, "
                "investment performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("TGA cash boundary input-lock destination contains different bytes")
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
