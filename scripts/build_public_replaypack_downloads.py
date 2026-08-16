#!/usr/bin/env python3
"""Build the public per-scenario ReplayPack downloads and site data."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from finreplay.engines import CompiledReplayPack, ReplayStudio

REPOSITORY = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = REPOSITORY / "web/public/replaypacks"
SITE_DATA = REPOSITORY / "web/data/scenarios.json"
CATALOG_PATH = REPOSITORY / "src/finreplay/resources/scenario-catalog.json"


def _card(
    order: int,
    title: str,
    publisher: str,
    family: str,
    result: str,
    tone: str,
) -> dict[str, Any]:
    return {
        "id": order,
        "title": title,
        "publisher": publisher,
        "family": family,
        "result": result,
        "tone": tone,
    }


_PRESENTATION: dict[str, dict[str, Any]] = {
    "svb-2023": _card(
        1,
        "SVB funding boundary",
        "SEC",
        "Banking",
        "7-engine flow · later 8-K isolated",
        "boundary",
    ),
    "pacwest-2023": _card(
        2, "PacWest funding boundary", "SEC", "Banking", "Later filing isolated", "boundary"
    ),
    "western-alliance-2023": _card(
        3,
        "Western Alliance deposit boundary",
        "SEC",
        "Banking",
        "17:08 filing stayed later",
        "boundary",
    ),
    "gdp-revision-2022q4": _card(
        4,
        "2022 Q4 GDP revision",
        "BEA · ALFRED",
        "Macro",
        "Inside range · evaluation only",
        "inside",
    ),
    "btfp-growth-2023": _card(
        5,
        "BTFP early growth",
        "Federal Reserve",
        "Banking",
        "Inside range · evaluation only",
        "inside",
    ),
    "bls-payroll-2023": _card(
        6, "Payroll release", "BLS", "Macro", "Inside range · evaluation only", "inside"
    ),
    "fomc-target-2023": _card(
        7,
        "FOMC target range",
        "Federal Reserve",
        "Rates",
        "Inside range · evaluation only",
        "inside",
    ),
    "bls-cpi-2023": _card(
        8, "CPI release snapshot", "BLS", "Macro", "Inside range · evaluation only", "inside"
    ),
    "treasury-curve-2023": _card(
        9, "2Y-10Y Treasury curve", "ALFRED", "Rates", "+6 bp upper breach", "breach"
    ),
    "treasury-tga-2023": _card(
        10,
        "Treasury cash balance",
        "U.S. Treasury",
        "Rates",
        "Inside range · baseline miss visible",
        "inside",
    ),
    "nyfed-sofr-2019": _card(
        11, "SOFR spike", "New York Fed", "Rates", "+282 bp upper breach", "breach"
    ),
    "eia-wpsr-2020": _card(
        12, "Commercial crude stocks", "EIA", "Macro", "+15,022 thousand-barrel breach", "breach"
    ),
    "dol-ui-2020": _card(
        13, "Initial unemployment claims", "U.S. DOL", "Macro", "+2,932,000-person breach", "breach"
    ),
    "treasury-auction-2020": _card(
        14, "91-day Treasury auction", "TreasuryDirect", "Rates", "-19 bp lower breach", "breach"
    ),
    "bea-pio-2020": _card(
        15, "Personal saving rate", "BEA", "Macro", "+460 bp upper breach", "breach"
    ),
    "fed-g17-2020": _card(
        16, "Industrial production", "Federal Reserve", "Macro", "-600 bp lower breach", "breach"
    ),
    "census-marts-2020": _card(
        17, "Retail sales", "U.S. Census", "Macro", "-740 bp lower breach", "breach"
    ),
    "census-nrc-2020": _card(
        18, "Housing starts", "Census · HUD", "Macro", "-383,000-unit breach", "breach"
    ),
    "fed-g19-2020": _card(
        19,
        "Revolving consumer credit",
        "Federal Reserve",
        "Macro",
        "-3,550 bp lower breach",
        "breach",
    ),
    "census-c30-2020": _card(
        20, "Construction spending", "U.S. Census", "Macro", "-$3,659M lower breach", "breach"
    ),
    "fhfa-hpi-2020": _card(
        21, "Purchase-only house prices", "FHFA", "Macro", "-60 bp lower breach", "breach"
    ),
    "census-m3-2020": _card(
        22, "Durable-goods orders", "U.S. Census", "Macro", "-1,560 bp lower breach", "breach"
    ),
    "census-ft900-2020": _card(
        23, "Trade deficit", "Census · BEA", "Macro", "+$4,483M upper breach", "breach"
    ),
    "census-nrs-2020": _card(
        24, "New-home sales", "Census · HUD", "Macro", "-103,000-unit breach", "breach"
    ),
    "eia-wngsr-2020": _card(
        25, "Working-gas stocks", "EIA", "Macro", "-20 Bcf lower breach", "breach"
    ),
    "bls-ppi-2020": _card(26, "Producer prices", "BLS", "Macro", "-110 bp lower breach", "breach"),
    "cftc-tff-2026": _card(
        27, "UST 2Y open interest", "CFTC", "Regulatory", "+71,513-contract breach", "breach"
    ),
    "fed-h41-liquidity-swaps-2020": _card(
        28,
        "Central-bank liquidity swaps",
        "Federal Reserve",
        "Banking",
        "Inside range · no success claim",
        "inside",
    ),
    "bls-import-prices-2020": _card(
        29, "All-import prices", "BLS", "Macro", "-130 bp lower breach", "breach"
    ),
    "bls-export-prices-2020": _card(
        30, "All-export prices", "BLS", "Macro", "Inside range · no success claim", "inside"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    catalog = _load_json(CATALOG_PATH)
    entries = catalog["scenarios"]
    if {entry["slug"] for entry in entries} != set(_PRESENTATION):
        raise SystemExit("site presentation map and installable scenario catalog differ")

    bundles: dict[Path, bytes] = {}
    site_scenarios: list[dict[str, Any]] = []
    manifest_entries: list[dict[str, Any]] = []
    studio = ReplayStudio()
    with tempfile.TemporaryDirectory(prefix="finreplay-public-packs-") as directory:
        temporary = Path(directory)
        for entry in entries:
            slug = str(entry["slug"])
            report_path = REPOSITORY / entry["report_path"]
            pack_root = report_path.parent
            receipt = studio.verify(pack_root)
            if receipt.pack_sha256 != entry["pack_sha256"]:
                raise SystemExit(f"recorded pack hash mismatch for {slug}")
            archive_path = temporary / f"{slug}.zip"
            studio.archive(pack_root, archive_path)
            archive = archive_path.read_bytes()
            destination = PUBLIC_ROOT / archive_path.name
            bundles[destination] = archive
            archive_sha256 = _sha256(archive)
            report = CompiledReplayPack.model_validate_json(report_path.read_text())
            presentation = _PRESENTATION[slug]
            download_path = f"/replaypacks/{slug}.zip"
            manifest_entries.append(
                {
                    "slug": slug,
                    "bytes": len(archive),
                    "sha256": archive_sha256,
                    "pack_sha256": receipt.pack_sha256,
                    "trace_id": receipt.trace_id,
                    "download_path": download_path,
                }
            )
            site_scenarios.append(
                {
                    **presentation,
                    "slug": slug,
                    "fullTitle": report.spec.title,
                    "scenarioId": entry["scenario_id"],
                    "scenarioVersion": entry["scenario_version"],
                    "replayId": entry["replay_id"],
                    "mode": entry["mode"],
                    "decisionTime": entry["decision_time"],
                    "decisionDate": str(entry["decision_time"]).split("T", 1)[0],
                    "inputRecords": entry["distinct_input_records"],
                    "historicalReplayEligible": entry["source_set_historical_replay_eligible"],
                    "codeCommit": entry["code_commit"],
                    "packSha256": entry["pack_sha256"],
                    "traceId": entry["trace_id"],
                    "proofSha256": entry["proof_sha256"],
                    "inputLockSha256": entry["input_lock_sha256"],
                    "claimBoundary": report.spec.claim_boundary,
                    "engineCounts": report.engine_artifact_counts,
                    "claims": [
                        {
                            "claimId": claim.claim_id,
                            "statement": claim.statement,
                            "evidenceClass": claim.evidence_class.value,
                            "boundary": claim.boundary,
                            "limitations": list(claim.limitations),
                        }
                        for claim in report.spec.claims
                    ],
                    "proofPath": entry["proof_path"],
                    "reportPath": entry["report_path"],
                    "documentationPath": f"docs/scenarios/{slug}.md",
                    "downloadPath": download_path,
                    "downloadBytes": len(archive),
                    "downloadSha256": archive_sha256,
                }
            )

    site_scenarios.sort(key=lambda item: int(item["id"]))
    manifest_entries.sort(key=lambda item: str(item["slug"]))
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "manifest_kind": "public_deterministic_scenario_replaypack_downloads",
        "scenario_count": len(manifest_entries),
        "source_catalog_path": CATALOG_PATH.relative_to(REPOSITORY).as_posix(),
        "source_catalog_sha256": _file_sha256(CATALOG_PATH),
        "bundles": manifest_entries,
        "claim_boundary": (
            "Each ZIP is a deterministic archive of an internally verified public-data "
            "ReplayPack. Availability and hash identity do not establish external method review, "
            "source authenticity, client work, investment performance, adoption, or impact."
        ),
    }
    manifest["manifest_sha256"] = _hash(manifest)
    outputs = {
        **bundles,
        PUBLIC_ROOT / "manifest.json": _serialize(manifest),
        SITE_DATA: _serialize(site_scenarios),
    }

    if args.write:
        for destination, content in outputs.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        expected_zips = {path for path in outputs if path.suffix == ".zip"}
        for path in PUBLIC_ROOT.glob("*.zip"):
            if path not in expected_zips:
                path.unlink()
        print(
            f"public_replaypacks_written=true scenarios={len(site_scenarios)} "
            f"manifest_sha256={manifest['manifest_sha256']}"
        )
        return

    mismatches = [
        destination.relative_to(REPOSITORY).as_posix()
        for destination, expected in outputs.items()
        if not destination.is_file() or destination.read_bytes() != expected
    ]
    if mismatches:
        raise SystemExit(f"public scenario downloads are stale: {', '.join(mismatches)}")
    print(
        f"public_replaypacks_current=true scenarios={len(site_scenarios)} "
        f"manifest_sha256={manifest['manifest_sha256']}"
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _serialize(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return _sha256(canonical)


if __name__ == "__main__":
    main()
