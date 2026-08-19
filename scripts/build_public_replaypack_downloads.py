#!/usr/bin/env python3
"""Build public ReplayPack downloads and the validated scenario explorer data."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from finreplay.catalog import ScenarioExplorerCatalog, load_scenario_explorer_catalog
from finreplay.engines import CompiledReplayPack, ReplayStudio

REPOSITORY = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = REPOSITORY / "web/public/replaypacks"
SITE_DATA = REPOSITORY / "web/data/scenarios.json"
CATALOG_PATH = REPOSITORY / "src/finreplay/resources/scenario-catalog.json"
EXPLORER_PATH = REPOSITORY / "src/finreplay/resources/scenario-explorer.json"
EXPLORER_DOC = REPOSITORY / "docs/scenario-explorer.md"


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
    if args.write:
        explorer_payload = _load_json(EXPLORER_PATH)
        explorer_payload.pop("catalog_sha256", None)
        explorer_payload["catalog_sha256"] = _hash(explorer_payload)
        explorer = ScenarioExplorerCatalog.model_validate(explorer_payload)
    else:
        explorer = load_scenario_explorer_catalog()
    explorer_bytes = _serialize(explorer.model_dump(mode="json"))
    explorer_file_sha256 = _sha256(explorer_bytes)
    presentation_by_slug = {entry.slug: entry for entry in explorer.scenarios}
    catalog_slugs = {str(entry["slug"]) for entry in entries}
    if catalog_slugs != set(presentation_by_slug):
        raise SystemExit("site explorer and installable scenario catalog differ")

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
            presentation = presentation_by_slug[slug]
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
                    "id": presentation.order,
                    "title": presentation.public_title,
                    "publisher": presentation.publisher,
                    "family": presentation.family,
                    "result": presentation.result,
                    "tone": presentation.tone,
                    "primaryMethod": presentation.primary_method,
                    "decisionQuestion": presentation.decision_question,
                    "lensIds": list(presentation.lens_ids),
                    "slug": slug,
                    "fullTitle": report.spec.title,
                    "scenarioId": entry["scenario_id"],
                    "scenarioVersion": entry["scenario_version"],
                    "replayId": entry["replay_id"],
                    "mode": entry["mode"],
                    "decisionTime": entry["decision_time"],
                    "decisionDate": str(entry["decision_time"]).split("T", 1)[0],
                    "inputRecords": entry["distinct_input_records"],
                    "historicalReplayEligible": entry[
                        "source_set_historical_replay_eligible"
                    ],
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
    site_catalog = {
        "schemaVersion": explorer.schema_version,
        "scenarioCount": explorer.scenario_count,
        "claimBoundary": explorer.claim_boundary,
        "sourceCatalogSha256": _file_sha256(CATALOG_PATH),
        "explorerCatalogSha256": explorer.catalog_sha256,
        "explorerFileSha256": explorer_file_sha256,
        "lenses": [
            {
                "lensId": lens.lens_id,
                "label": lens.label,
                "shortLabel": lens.short_label,
                "question": lens.question,
                "description": lens.description,
            }
            for lens in explorer.lenses
        ],
        "pathways": [
            {
                "pathwayId": pathway.pathway_id,
                "title": pathway.title,
                "description": pathway.description,
                "lensIds": list(pathway.lens_ids),
                "scenarioSlugs": list(pathway.scenario_slugs),
            }
            for pathway in explorer.pathways
        ],
        "scenarios": site_scenarios,
    }
    outputs = {
        **bundles,
        EXPLORER_PATH: explorer_bytes,
        PUBLIC_ROOT / "manifest.json": _serialize(manifest),
        SITE_DATA: _serialize(site_catalog),
        EXPLORER_DOC: _explorer_markdown(explorer, explorer_file_sha256),
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
            f"lenses={len(explorer.lenses)} pathways={len(explorer.pathways)} "
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
        f"lenses={len(explorer.lenses)} pathways={len(explorer.pathways)} "
        f"manifest_sha256={manifest['manifest_sha256']}"
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _explorer_markdown(
    explorer: ScenarioExplorerCatalog,
    explorer_file_sha256: str,
) -> bytes:
    lens_by_id = {lens.lens_id: lens for lens in explorer.lenses}
    lines = [
        "# Scenario explorer",
        "",
        "This file is generated by `python scripts/build_public_replaypack_downloads.py --write`",
        "from the validated package resource `src/finreplay/resources/scenario-explorer.json`.",
        "It complements the capability catalog rather than replacing it:",
        "",
        "- capability scope says whether support is direct, transferable, or boundary-only;",
        "- analytical dimensions describe what a case touches; and",
        "- pathways provide a cross-case reading order without creating a new result.",
        "",
        "## Analytical dimensions",
        "",
    ]
    for lens in explorer.lenses:
        lines.extend(
            [
                f"### {lens.label}",
                "",
                f"**Question:** {lens.question}",
                "",
                lens.description,
                "",
            ]
        )
    lines.extend(["## Curated pathways", ""])
    for pathway in explorer.pathways:
        dimensions = ", ".join(lens_by_id[lens_id].short_label for lens_id in pathway.lens_ids)
        lines.extend(
            [
                f"### {pathway.title}",
                "",
                pathway.description,
                "",
                f"Dimensions: {dimensions}",
                "",
                *[
                    f"- [`{slug}`](scenarios/{slug}.md)"
                    for slug in pathway.scenario_slugs
                ],
                "",
            ]
        )
    lines.extend(
        [
            "## Case index",
            "",
            "| # | Scenario | Primary method | Decision question |",
            "|---:|---|---|---|",
        ]
    )
    for scenario in sorted(explorer.scenarios, key=lambda item: item.order):
        lines.append(
            f"| {scenario.order} | [`{scenario.slug}`](scenarios/{scenario.slug}.md) | "
            f"{scenario.primary_method} | {scenario.decision_question} |"
        )
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            explorer.claim_boundary,
            "",
            f"Source SHA-256: `{explorer_file_sha256}`",
            "",
        ]
    )
    return "\n".join(lines).encode()


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
