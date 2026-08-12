#!/usr/bin/env python3
"""Build current and historical-safe SVB MarketTwin receipts from stored official facts."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finreplay.contracts import EvidenceClass
from finreplay.engines import (
    MarketEdge,
    MarketNode,
    MarketTwin,
    NodeKind,
    TemporalEvidence,
    TimeVault,
)

SVB_DECISION = datetime(2023, 3, 8, 18, tzinfo=UTC)
SVB_BALANCE_DATE = datetime(2022, 12, 31, tzinfo=UTC)
TREASURY_DATE = datetime(2023, 3, 8, tzinfo=UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timevault", type=Path, default=Path("data/silver/timevault.duckdb"))
    parser.add_argument("--market-twin", type=Path, default=Path("data/silver/markettwin.duckdb"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/svb-markettwin.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    now = datetime.now(UTC)
    with TimeVault(args.timevault) as vault:
        sec = vault.records_as_of(
            SVB_DECISION,
            valid_at=SVB_BALANCE_DATE,
            source_ids=["sec.xbrl.companyfacts"],
        )
        fdic = vault.records_as_of(
            now,
            valid_at=SVB_BALANCE_DATE,
            source_ids=["fdic.bankfind.financials"],
            allow_latest_only=True,
        )
        treasury = vault.records_as_of(
            now,
            valid_at=TREASURY_DATE,
            source_ids=["treasury.fiscaldata.debt_to_penny"],
            allow_latest_only=True,
        )
    sec_by_concept = {
        str(record.payload.get("concept")): record
        for record in sec
        if record.payload.get("frame") == "CY2022Q4I"
    }
    required = {
        "Assets",
        "StockholdersEquity",
        "HeldToMaturitySecurities",
        "HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss",
        "AvailableForSaleSecuritiesDebtSecurities",
        "AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax",
        "Deposits",
    }
    missing = required - set(sec_by_concept)
    if missing:
        raise SystemExit(f"missing required SEC concepts: {sorted(missing)}")
    fdic_row = next(
        (record for record in fdic if record.payload.get("REPDTE") == "20221231"), None
    )
    if fdic_row is None or fdic_row.payload.get("EQTOT") is None:
        raise SystemExit("missing current FDIC 2022-12-31 row with EQTOT")
    treasury_row = next(
        (record for record in treasury if record.payload.get("record_date") == "2023-03-08"),
        None,
    )
    if treasury_row is None:
        raise SystemExit("missing current Treasury Debt-to-Penny 2023-03-08 row")

    sec_assets = sec_by_concept["Assets"]
    sec_equity = sec_by_concept["StockholdersEquity"]
    sec_htm = sec_by_concept["HeldToMaturitySecurities"]
    sec_htm_loss = sec_by_concept[
        "HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"
    ]
    sec_afs = sec_by_concept["AvailableForSaleSecuritiesDebtSecurities"]
    sec_afs_loss = sec_by_concept[
        "AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax"
    ]
    sec_deposits = sec_by_concept["Deposits"]
    available_at = max(
        record.interval.available_at
        for record in (
            sec_assets,
            sec_equity,
            sec_htm,
            sec_htm_loss,
            sec_afs,
            sec_afs_loss,
            sec_deposits,
        )
    )
    sec_temporal = TemporalEvidence(
        valid_from=SVB_BALANCE_DATE,
        available_at=available_at,
    )
    current_temporal = TemporalEvidence(
        valid_from=SVB_BALANCE_DATE,
        available_at=max(fdic_row.interval.available_at, treasury_row.interval.available_at),
    )
    nodes = (
        MarketNode(
            node_id="issuer:svb-financial-group",
            label="SVB Financial Group",
            kind=NodeKind.ISSUER,
            loss_absorption_usd=float(sec_equity.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=sec_temporal,
            source=sec_equity.source,
            attributes={
                "assets_usd": int(sec_assets.payload["val"]),
                "deposits_usd": int(sec_deposits.payload["val"]),
                "sec_accession": str(sec_assets.payload["accn"]),
            },
        ),
        MarketNode(
            node_id="security:svb-htm-portfolio",
            label="SVB held-to-maturity securities portfolio",
            kind=NodeKind.SECURITY,
            loss_absorption_usd=float(sec_htm.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=sec_temporal,
            source=sec_htm.source,
            attributes={
                "carrying_value_usd": int(sec_htm.payload["val"]),
                "unrecognized_loss_usd": int(sec_htm_loss.payload["val"]),
            },
        ),
        MarketNode(
            node_id="security:svb-afs-portfolio",
            label="SVB available-for-sale securities portfolio",
            kind=NodeKind.SECURITY,
            loss_absorption_usd=float(sec_afs.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=sec_temporal,
            source=sec_afs.source,
            attributes={
                "fair_value_usd": int(sec_afs.payload["val"]),
                "gross_unrealized_loss_usd": int(sec_afs_loss.payload["val"]),
            },
        ),
        MarketNode(
            node_id="bank:svb-bank-fdic-24735",
            label="Silicon Valley Bank (FDIC CERT 24735 current snapshot)",
            kind=NodeKind.BANK,
            loss_absorption_usd=float(fdic_row.payload["EQTOT"]) * 1_000.0,
            evidence_class=EvidenceClass.REPORTED,
            temporal=current_temporal,
            source=fdic_row.source,
            attributes={
                "assets_thousands_usd": int(fdic_row.payload["ASSET"]),
                "deposits_thousands_usd": int(fdic_row.payload["DEP"]),
                "uninsured_deposits_thousands_usd": int(fdic_row.payload["DEPUNINS"]),
                "latest_only": True,
            },
        ),
        MarketNode(
            node_id="government:us-treasury-debt",
            label="U.S. Treasury public debt (current API view of 2023-03-08 row)",
            kind=NodeKind.GOVERNMENT,
            loss_absorption_usd=float(treasury_row.payload["tot_pub_debt_out_amt"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=current_temporal,
            source=treasury_row.source,
            attributes={
                "debt_held_public_usd": str(treasury_row.payload["debt_held_public_amt"]),
                "latest_only": True,
            },
        ),
    )
    edges = (
        MarketEdge(
            edge_id="svb-issuer-holds-htm",
            source_node="security:svb-htm-portfolio",
            target_node="issuer:svb-financial-group",
            relation="reported portfolio exposure",
            exposure_lower_usd=float(sec_htm.payload["val"]),
            exposure_upper_usd=float(sec_htm.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            confidence=1.0,
            temporal=sec_temporal,
            source=sec_htm.source,
        ),
        MarketEdge(
            edge_id="svb-issuer-holds-afs",
            source_node="security:svb-afs-portfolio",
            target_node="issuer:svb-financial-group",
            relation="reported portfolio exposure",
            exposure_lower_usd=float(sec_afs.payload["val"]),
            exposure_upper_usd=float(sec_afs.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            confidence=1.0,
            temporal=sec_temporal,
            source=sec_afs.source,
        ),
        # These identity/context links are inferred bounds and use zero financial exposure;
        # they prove a multi-source graph without pretending that entity resolution itself is an
        # official relational assertion or that Treasury debt is an SVB-specific exposure.
        MarketEdge(
            edge_id="svb-bank-to-parent-identity-bound",
            source_node="bank:svb-bank-fdic-24735",
            target_node="issuer:svb-financial-group",
            relation="bounded entity-resolution context",
            exposure_lower_usd=0.0,
            exposure_upper_usd=0.0,
            evidence_class=EvidenceClass.INFERRED,
            confidence=0.95,
            temporal=current_temporal,
            attributes={"not_an_official_ownership_assertion": True},
        ),
        MarketEdge(
            edge_id="treasury-to-htm-market-context",
            source_node="government:us-treasury-debt",
            target_node="security:svb-htm-portfolio",
            relation="macro debt-market context only",
            exposure_lower_usd=0.0,
            exposure_upper_usd=0.0,
            evidence_class=EvidenceClass.INFERRED,
            confidence=0.5,
            temporal=current_temporal,
            attributes={"not_a_security_composition_claim": True},
        ),
    )
    args.market_twin.parent.mkdir(parents=True, exist_ok=True)
    with MarketTwin(args.market_twin) as twin:
        append = twin.append(nodes=nodes, edges=edges)
        historical = twin.snapshot(
            decision_time=SVB_DECISION,
            valid_at=SVB_BALANCE_DATE,
        )
        current = twin.snapshot(
            decision_time=now,
            valid_at=SVB_BALANCE_DATE,
            allow_latest_only=True,
        )
        shock = twin.propagate(
            historical,
            initial_shocks={"security:svb-htm-portfolio": 0.10},
        )
        manifest = twin.manifest()
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "The historical-safe snapshot excludes all latest-only FDIC/Treasury objects. The "
            "current multi-source snapshot proves graph construction from current official public "
            "responses, not a 2023 point-in-time FDIC/Treasury vintage or causal contagion model."
        ),
        "generated_at": now.isoformat(),
        "append": asdict(append),
        "manifest": asdict(manifest),
        "historical_safe_snapshot": historical.model_dump(mode="json"),
        "current_multisource_snapshot": current.model_dump(mode="json"),
        "bounded_shock": shock.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    payload["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    print(
        f"historical_nodes={len(historical.nodes)} historical_edges={len(historical.edges)} "
        f"current_nodes={len(current.nodes)} current_edges={len(current.edges)} "
        f"inserted_nodes={append.inserted_nodes} inserted_edges={append.inserted_edges} "
        f"receipt={args.output}"
    )


if __name__ == "__main__":
    main()
