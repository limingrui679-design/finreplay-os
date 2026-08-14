#!/usr/bin/env python3
"""Extract and seal the two CFTC TFF rows scheduled by decision time."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from finreplay.engines import TimeVault
from finreplay.scenarios import (
    CFTC_TFF_SCHEDULE_SOURCE_ID,
    CFTCOpenInterestBoundaryInputLock,
)
from finreplay.verification import verify_live_receipt

ROLE_DATES = {
    "july14_release": "2026-07-14",
    "july21_decision_release": "2026-07-21",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="cftc-tff-2026-two-year-note-open-interest-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="cftc-tff-2026-two-year-note-open-interest-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="cftc-tff")
    parser.add_argument(
        "--title",
        default="CFTC TFF UST 2-year open-interest boundary before July 28, 2026",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2026-07-24T19:30:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T09:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/cftc-tff-schedule.duckdb"),
    )
    parser.add_argument(
        "--supporting-receipt",
        type=Path,
        default=Path(
            "verification/supporting/cftc-tff-schedule/live/"
            "cftc.cot.tff_scheduled_ust2y-ea85ba99ecf5a7d7.json"
        ),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/cftc-tff-schedule"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/cftc-tff-2026/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    receipt = verify_live_receipt(args.supporting_receipt, raw_store=args.raw_store)
    receipt_payload = _json_object(
        json.loads(args.supporting_receipt.read_text(encoding="utf-8")),
        "supporting receipt",
    )
    receipt_sha256 = receipt_payload.get("receipt_sha256")
    if not isinstance(receipt_sha256, str):
        raise SystemExit("supporting receipt lacks a self-hash")
    if receipt.adapter_id != CFTC_TFF_SCHEDULE_SOURCE_ID:
        raise SystemExit("supporting receipt adapter does not match CFTC TFF source")
    if receipt.record_count != 3 or receipt.idempotent_records != 3:
        raise SystemExit("supporting receipt must be the verified idempotent three-row run")
    if not receipt.historical_replay_eligible or receipt.temporal_coverage != "immutable_event":
        raise SystemExit("supporting receipt temporal eligibility does not match")

    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[CFTC_TFF_SCHEDULE_SOURCE_ID],
        )
    by_date = {
        str(record.payload.get("report_date")): record
        for record in candidates
        if record.entity_id == "cftc_contract:042601"
        and record.payload.get("metric") == "open_interest_all_futures_only"
    }
    missing = sorted(
        report_date for report_date in ROLE_DATES.values() if report_date not in by_date
    )
    if missing:
        raise SystemExit(f"required CFTC TFF rows are absent: {missing}")
    roles = {role: by_date[report_date].record_id for role, report_date in ROLE_DATES.items()}
    records = tuple(
        sorted(
            (by_date[report_date] for report_date in ROLE_DATES.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = CFTCOpenInterestBoundaryInputLock.create(
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
            "source_response_sha256s": sorted(receipt.response_hashes),
            "supporting_receipt_sha256": receipt_sha256,
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the July 14 and July 21, 2026 CFTC Futures Only TFF UST "
                "2-year total open-interest observations scheduled to be available by the July "
                "24 boundary, and binds the five-response API, annual-file, release-schedule, "
                "COT-policy, and TFF-notes evidence chain. The exact 3:30 p.m. Eastern value is "
                "official scheduled availability at 0.98 confidence because the page calls the "
                "schedule tentative and CFTC exposes no row-level actual-publication log. The "
                "lock excludes the July 28 event, all category positions and trader counts as "
                "range inputs, face-value-to-notional conversion, directional exposure, trading "
                "intent, volume, executions, accounts, P&L, probability, calibrated coverage, "
                "forecast, causality, recommendation, investment performance, external review, "
                "deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("CFTC TFF input-lock destination has different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} source_hashes={len(lock.source_response_sha256s)} "
        f"lock_sha256={lock.lock_sha256} output={args.output}"
    )


def _json_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


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
