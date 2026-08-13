#!/usr/bin/env python3
"""Extract and seal seven SEC filing facts for a bank boundary replay."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import BankBoundaryInputLock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--artifact-prefix", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--issuer-slug", required=True)
    parser.add_argument("--issuer-label", required=True)
    parser.add_argument("--decision-time", type=_aware_datetime, required=True)
    parser.add_argument("--balance-date", type=_aware_datetime, required=True)
    parser.add_argument("--build-epoch", type=_aware_datetime, required=True)
    parser.add_argument("--accession", required=True)
    parser.add_argument("--assets-concept", default="Assets")
    parser.add_argument("--deposits-concept", default="Deposits")
    parser.add_argument("--equity-concept", default="StockholdersEquity")
    parser.add_argument("--htm-value-concept", default="HeldToMaturitySecurities")
    parser.add_argument(
        "--htm-loss-concept",
        default="HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss",
    )
    parser.add_argument("--afs-value-concept", default="AvailableForSaleSecuritiesDebtSecurities")
    parser.add_argument("--afs-loss-concept", required=True)
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/timevault.duckdb"),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    concepts = {
        "assets": args.assets_concept,
        "deposits": args.deposits_concept,
        "equity": args.equity_concept,
        "htm_value": args.htm_value_concept,
        "htm_loss": args.htm_loss_concept,
        "afs_value": args.afs_value_concept,
        "afs_loss": args.afs_loss_concept,
    }
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            valid_at=args.balance_date,
            source_ids=["sec.xbrl.companyfacts"],
        )
    balance_end = args.balance_date.date().isoformat()
    records = tuple(
        sorted(
            (
                record
                for record in candidates
                if record.payload.get("accn") == args.accession
                and record.payload.get("end") == balance_end
                and record.payload.get("concept") in set(concepts.values())
            ),
            key=lambda record: record.record_id,
        )
    )
    lock = BankBoundaryInputLock.create(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "replay_id": args.replay_id,
            "artifact_prefix": args.artifact_prefix,
            "title": args.title,
            "issuer_slug": args.issuer_slug,
            "issuer_label": args.issuer_label,
            "decision_time": args.decision_time,
            "balance_date": args.balance_date,
            "build_epoch": args.build_epoch,
            "selected_accession": args.accession,
            "concepts": concepts,
            "source_response_sha256": records[0].source.sha256 if records else "0" * 64,
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "These seven records are a minimal extracted lock of filer-reported SEC XBRL "
                "facts accepted before the replay decision time. The lock preserves identities, "
                "accession, knowledge timestamps, source URL, response hash, and evidence class. "
                "It is not the complete filing, a regulator finding, a causal explanation, a "
                "trading signal, or proof that a changing aggregation endpoint will reproduce "
                "the exact whole-response bytes indefinitely."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("bank input-lock destination contains different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} accession={lock.selected_accession} "
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
