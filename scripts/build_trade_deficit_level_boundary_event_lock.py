#!/usr/bin/env python3
"""Seal the May 5 FT-900 March deficit as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import CENSUS_BEA_FT900_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="census-ft900-2020-trade-deficit-level-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-02T12:30:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-05-05T12:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/census-ft900.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/census-ft900-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[CENSUS_BEA_FT900_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "census_bea_ft900:us_goods_services_deficit"
        and record.payload.get("release_date") == "2020-05-05"
        and record.payload.get("reference_month") == "2020-03"
        and record.payload.get("metric") == "goods_services_deficit_level_million_dollars"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March FT-900 event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the May 5 joint Census/BEA FT-900 release's initial "
                "March 2020 goods-and-services deficit of 44,415 million dollars solely as a "
                "post-decision official event. It also preserves that release's unchanged "
                "45,482-million-dollar January snapshot and its revision of February from "
                "39,932 to 39,810 million dollars, a -122-million-dollar revision. The March "
                "event is 4,483 million dollars above the previously fixed 39,932 upper stress "
                "endpoint, but neither the event nor its revisions are ReplayPack inputs. "
                "Current PDF/XLS ZIP hashes prove present official retrieval rather than byte "
                "identity at release. The release's COVID statement concerns publication "
                "standards and establishes neither causality, complete response, nor unaffected "
                "measurement. The event does not establish forecast skill, calibrated coverage, "
                "probability, statistical significance, price-adjusted trade volume, trade-policy "
                "or pandemic causality, investment performance, deployment, external review, or "
                "user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("FT-900 event-lock destination has different bytes")
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
