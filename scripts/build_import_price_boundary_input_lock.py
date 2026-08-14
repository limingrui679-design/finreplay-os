#!/usr/bin/env python3
"""Extract and seal the two BLS all-import releases known at decision time."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import (
    BLS_IMPORT_PRICE_INPUT_RESPONSE_SHA256S,
    BLS_IMPORT_PRICE_SOURCE_ID,
    BLS_IMPORT_PRICE_SUPPORTING_RECEIPT_SHA256,
    ImportPriceBoundaryInputLock,
)

ROLE_MONTHS = {
    "january_release": "2020-01",
    "february_decision_release": "2020-02",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="bls-import-prices-2020-all-imports-change-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="bls-import-prices-2020-all-imports-change-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="bls-import-price")
    parser.add_argument(
        "--title",
        default="BLS all-import price monthly-change boundary before March 2020",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-13T12:30:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T11:15:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/bls-import-prices.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/bls-import-prices-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[BLS_IMPORT_PRICE_SOURCE_ID],
        )
    by_month = {
        str(record.payload.get("reference_month")): record
        for record in candidates
        if record.entity_id == "bls_import_price_index:all_imports_united_states"
        and record.payload.get("metric")
        == "all_imports_monthly_change_not_seasonally_adjusted"
    }
    missing = sorted(month for month in ROLE_MONTHS.values() if month not in by_month)
    if missing:
        raise SystemExit(f"required BLS import-price facts are absent: {missing}")
    roles = {role: by_month[month].record_id for role, month in ROLE_MONTHS.items()}
    records = tuple(
        sorted(
            (by_month[month] for month in ROLE_MONTHS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = ImportPriceBoundaryInputLock.create(
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
            "source_response_sha256s": BLS_IMPORT_PRICE_INPUT_RESPONSE_SHA256S,
            "supporting_receipt_sha256": (
                BLS_IMPORT_PRICE_SUPPORTING_RECEIPT_SHA256
            ),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the January and February 2020 BLS all-import monthly "
                "changes knowable at the March 13 embargo end and binds each archived HTML/PDF "
                "pair plus the idempotent six-response supporting receipt. It retains the "
                "+10-basis-point January revision as lineage but does not use that revision, "
                "index levels, annual changes, or detailed categories to set an endpoint. It "
                "excludes the March event, later revisions, importer or shipment records, "
                "quantities, nominal trade values, tariffs, CPI, firm results, P&L, a "
                "probability distribution, forecast, calibrated interval, pandemic or price "
                "causality, recommendation, execution, investment performance, external "
                "review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("BLS import-price input-lock destination has different bytes")
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
