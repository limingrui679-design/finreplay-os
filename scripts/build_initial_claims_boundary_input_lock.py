#!/usr/bin/env python3
"""Extract and seal two pre-decision archived DOL initial-claims facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import DOL_UI_CLAIMS_SOURCE_ID, InitialClaimsBoundaryInputLock

ROLE_RELEASES = {
    "march07_claims": "2020-03-12",
    "march14_claims": "2020-03-19",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="dol-ui-2020-initial-claims-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="dol-ui-2020-initial-claims-boundary-v1")
    parser.add_argument("--artifact-prefix", default="dol-ui")
    parser.add_argument(
        "--title",
        default="DOL initial-claims boundary before the March 2020 record surge",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-20T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T07:15:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/dol-ui-claims.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/dol-ui-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[DOL_UI_CLAIMS_SOURCE_ID],
        )
    by_release = {
        str(record.payload.get("release_date")): record
        for record in candidates
        if record.entity_id == "dol_ui_claims:united_states"
        and record.payload.get("metric") == "seasonally_adjusted_initial_claims"
    }
    missing = sorted(
        release_date
        for release_date in ROLE_RELEASES.values()
        if release_date not in by_release
    )
    if missing:
        raise SystemExit(f"required DOL claims facts are absent before decision: {missing}")
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
    lock = InitialClaimsBoundaryInputLock.create(
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
            "source_response_sha256s": sorted(
                {record.source.sha256 for record in records}
            ),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the DOL seasonally adjusted advance initial-claims values "
                "for weeks ending March 7 and 14, 2020 from two official archived PDFs knowable "
                "before the replay decision time. It preserves the March 19 annual seasonal-"
                "factor revision warning and excludes the March 21 event value, individual "
                "claimants, employers, a probability distribution, forecast, calibrated "
                "interval, pandemic or labor-market causal attribution, policy recommendation, "
                "execution, investment performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("DOL initial-claims input-lock destination contains different bytes")
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
