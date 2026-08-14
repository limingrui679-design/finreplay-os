#!/usr/bin/env python3
"""Seal the May 26 FHFA HPI March change as a post-decision event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import FHFA_HPI_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="fhfa-hpi-2020-house-price-change-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-22T13:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-05-26T13:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fhfa-hpi.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/fhfa-hpi-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[FHFA_HPI_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "fhfa_hpi:us_purchase_only_seasonally_adjusted"
        and record.payload.get("release_date") == "2020-05-26"
        and record.payload.get("reference_month") == "2020-03"
        and record.payload.get("metric") == "us_purchase_only_hpi_monthly_change_basis_points"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March FHFA HPI event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the 10-basis-point March 2020 national purchase-only "
                "seasonally adjusted monthly HPI change from the May 26 FHFA report solely as "
                "a post-decision official event. That report's snapshot retains January at 50 "
                "basis points and revises February from 70 to 80 basis points; those values do "
                "not overwrite the initial-release input sequence. The official PDF currently "
                "available has metadata showing modification on June 15, 2020, so its exact "
                "current bytes are verified official archived evidence but are not represented "
                "as proof that the bytes were unchanged since May 26. The event and revisions "
                "are excluded from all ReplayPack inputs and do not establish forecast skill, "
                "calibrated coverage, universal home prices, property-level outcomes, a "
                "contemporaneous COVID effect, housing, credit, pandemic or policy causality, "
                "investment performance, external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("FHFA HPI event-lock destination has different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(f"records={len(lock.records)} lock_sha256={lock.lock_sha256} output={args.output}")


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
