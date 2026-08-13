#!/usr/bin/env python3
"""Seal the March 21 claims value as a post-decision official event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import DOL_UI_CLAIMS_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="dol-ui-2020-initial-claims-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-20T12:00:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-26T12:46:21Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/dol-ui-claims.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/dol-ui-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[DOL_UI_CLAIMS_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "dol_ui_claims:united_states"
        and record.payload.get("release_date") == "2020-03-26"
        and record.payload.get("week_ending") == "2020-03-21"
        and record.payload.get("metric") == "seasonally_adjusted_initial_claims"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one March 21 DOL claims event fact, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the DOL advance seasonally adjusted initial-claims value "
                "for the week ending March 21, 2020 solely as a post-decision official event. "
                "The March 26 PDF became knowable only after the replay decision and also "
                "preserves its revision of the prior week from 281,000 to 282,000. It is "
                "excluded from all ReplayPack inputs and does not establish forecast skill, "
                "calibrated coverage, pandemic or labor-market causality, policy effectiveness, "
                "investment performance, external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("DOL initial-claims event-lock destination contains different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} lock_sha256={lock.lock_sha256} output={args.output}"
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
