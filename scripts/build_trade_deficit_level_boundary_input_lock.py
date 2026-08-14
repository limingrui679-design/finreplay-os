#!/usr/bin/env python3
"""Extract and seal the two pre-decision joint Census/BEA FT-900 releases."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import (
    CENSUS_BEA_FT900_SOURCE_ID,
    TradeDeficitLevelBoundaryInputLock,
)

ROLE_SELECTIONS = {
    "january_release_snapshot": ("2020-03-06", "2020-01"),
    "february_decision_snapshot": ("2020-04-02", "2020-02"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="census-ft900-2020-trade-deficit-level-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="census-ft900-2020-trade-deficit-level-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="census-ft900")
    parser.add_argument(
        "--title",
        default=("Census/BEA FT-900 boundary before the March 2020 trade-deficit widening"),
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-02T12:30:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T07:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/census-ft900.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/census-ft900-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[CENSUS_BEA_FT900_SOURCE_ID],
        )
    selected = {}
    for role, (release_date, reference_month) in ROLE_SELECTIONS.items():
        matches = tuple(
            record
            for record in candidates
            if record.entity_id == "census_bea_ft900:us_goods_services_deficit"
            and record.payload.get("metric") == "goods_services_deficit_level_million_dollars"
            and record.payload.get("release_date") == release_date
            and record.payload.get("reference_month") == reference_month
        )
        if len(matches) != 1:
            raise SystemExit(f"expected one joint FT-900 fact for {role}, found {len(matches)}")
        selected[role] = matches[0]

    roles = {role: record.record_id for role, record in selected.items()}
    records = tuple(sorted(selected.values(), key=lambda record: record.record_id))
    source_hashes = sorted(
        {
            str(record.payload[field])
            for record in records
            for field in ("release_pdf_sha256", "release_xls_zip_sha256")
        }
    )
    lock = TradeDeficitLevelBoundaryInputLock.create(
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
            "source_response_sha256s": source_hashes,
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves paired official PDF and XLS ZIP evidence for the March 6 "
                "January-data and April 2 February-data joint Census/BEA FT-900 releases. "
                "At the decision time, the April 2 snapshot reported revised January at "
                "45,482 million dollars and initial February at 39,932 million dollars; their "
                "5,550-million-dollar decline is the only numerical change used to construct "
                "the later stress range. The January release's 45,338-million-dollar initial "
                "value remains revision lineage and does not set an endpoint. The lock excludes "
                "the May 5 March event, its February revision, and every later-known value. "
                "Current archive hashes prove present official retrieval, not byte identity at "
                "historical release. The series is seasonally adjusted but not price adjusted; "
                "goods-document completeness does not remove nonsampling or services-estimation "
                "limitations. This lock contains no official forecast, probability, confidence "
                "interval, statistical-significance result, calibrated coverage, trade-policy "
                "or COVID effect, causal model, trading recommendation, deployment, external "
                "review, or user-impact claim."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("FT-900 input-lock destination has different bytes")
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
