#!/usr/bin/env python3
"""Extract and seal two pre-decision archived EIA commercial-crude-stock facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import EIA_WPSR_SOURCE_ID, EIACrudeStockBoundaryInputLock

ROLE_RELEASES = {
    "april03_stock": "2020-04-08",
    "april10_stock": "2020-04-15",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="eia-wpsr-2020-crude-stock-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="eia-wpsr-2020-crude-stock-boundary-v1")
    parser.add_argument("--artifact-prefix", default="eia-wpsr")
    parser.add_argument(
        "--title",
        default="EIA commercial crude stock boundary before the April 2020 inventory build",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-16T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T06:50:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/eia-wpsr.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/eia-wpsr-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[EIA_WPSR_SOURCE_ID],
        )
    by_release = {
        str(record.payload.get("release_date")): record
        for record in candidates
        if record.entity_id
        == "eia_series:weekly_us_commercial_crude_stocks_excluding_spr"
        and record.payload.get("metric") == "commercial_crude_stocks_excluding_spr"
    }
    missing = sorted(
        release_date
        for release_date in ROLE_RELEASES.values()
        if release_date not in by_release
    )
    if missing:
        raise SystemExit(f"required EIA WPSR facts are absent before decision: {missing}")
    roles = {
        role: by_release[release_date].record_id
        for role, release_date in ROLE_RELEASES.items()
    }
    records = tuple(
        sorted(
            (by_release[release_date] for release_date in ROLE_RELEASES.values()),
            key=lambda record: record.record_id,
        )
    )
    pdf_hashes = [record.payload.get("release_pdf_sha256") for record in records]
    if any(not isinstance(value, str) for value in pdf_hashes):
        raise SystemExit("required EIA WPSR release PDF hashes are absent")
    lock = EIACrudeStockBoundaryInputLock.create(
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
            "release_pdf_sha256s": sorted(pdf_hashes),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the WPSR Table 4 U.S. commercial crude stocks excluding "
                "SPR for weeks ending April 3 and 10, 2020 from two paired official EIA archive "
                "CSV/PDF releases conservatively knowable before the replay decision time. It "
                "excludes the April 17 event stock, facility-level data, transactions, flows, "
                "storage-capacity utilization, a probability distribution, forecast, calibrated "
                "interval, pandemic or oil-market causal attribution, policy recommendation, "
                "execution, investment performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("EIA crude-stock input-lock destination contains different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} source_hashes={len(lock.source_response_sha256s)} "
        f"pdf_hashes={len(lock.release_pdf_sha256s)} lock_sha256={lock.lock_sha256} "
        f"output={args.output}"
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
