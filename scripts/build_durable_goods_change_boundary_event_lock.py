#!/usr/bin/env python3
"""Seal the April 24 Census M3 March change as a post-decision event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import CENSUS_DURABLE_GOODS_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="census-m3-2020-durable-goods-change-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-25T12:30:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-24T12:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/census-m3-durable-goods.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/census-m3-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[CENSUS_DURABLE_GOODS_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "census_m3:total_durable_goods_new_orders"
        and record.payload.get("release_date") == "2020-04-24"
        and record.payload.get("reference_month") == "2020-03"
        and record.payload.get("metric")
        == "total_durable_goods_new_orders_monthly_change_basis_points"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March Census M3 durable-goods event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the -1,440-basis-point March 2020 total durable-goods "
                "new-orders monthly change and 213,184-million-dollar level from the April 24 "
                "Census M3 advance report solely as a post-decision official event. That "
                "report retains January at +10 basis points and revises February from +120 "
                "to +110 basis points; those values do not overwrite the first-report input "
                "sequence. The current PDF has May 27 modification metadata, so its exact "
                "bytes are verified present official evidence rather than proof of unchanged "
                "release-time bytes. The report's COVID-19 statement concerns publication "
                "standards and does not establish causality or unaffected measurement. The "
                "event and revisions are excluded from all ReplayPack inputs and do not "
                "establish forecast skill, calibrated coverage, probability, price-adjusted "
                "output, manufacturing, inflation, pandemic, policy, firm or sector causality, "
                "investment performance, external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("Census M3 event-lock destination has different bytes")
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
