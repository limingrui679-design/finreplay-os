#!/usr/bin/env python3
"""Build the installable adapter/scenario catalogs from verified repository evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from finreplay.engines import CompiledReplayPack

REPOSITORY = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = REPOSITORY / "src/finreplay/resources"

_PUBLISHERS = {
    "bls": "U.S. Bureau of Labor Statistics",
    "cftc": "U.S. Commodity Futures Trading Commission",
    "fdic": "Federal Deposit Insurance Corporation",
    "nyfed": "Federal Reserve Bank of New York",
    "sec": "U.S. Securities and Exchange Commission",
    "treasury": "U.S. Department of the Treasury",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    adapter_payload = _adapter_catalog()
    scenario_payload, input_locks = _scenario_catalog()
    outputs = {
        RESOURCE_ROOT / "adapter-catalog.json": _serialize(adapter_payload),
        RESOURCE_ROOT / "scenario-catalog.json": _serialize(scenario_payload),
        REPOSITORY / "docs/catalog-matrix.md": _catalog_matrix(adapter_payload, scenario_payload),
    }
    for source, relative in input_locks:
        outputs[RESOURCE_ROOT / relative] = source.read_bytes()

    if args.write:
        for destination, content in outputs.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        expected_locks = {
            destination for destination in outputs if "scenarios" in destination.parts
        }
        _remove_stale_input_locks(expected_locks)
        print(
            f"catalogs_written=true adapters={adapter_payload['adapter_count']} "
            f"scenarios={scenario_payload['scenario_count']} input_locks={len(input_locks)}"
        )
        return

    mismatches = [
        destination.relative_to(REPOSITORY).as_posix()
        for destination, expected in outputs.items()
        if not destination.is_file() or destination.read_bytes() != expected
    ]
    if mismatches:
        raise SystemExit(f"installable catalogs are stale: {', '.join(mismatches)}")
    print(
        f"catalogs_current=true adapters={adapter_payload['adapter_count']} "
        f"scenarios={scenario_payload['scenario_count']} input_locks={len(input_locks)}"
    )


def _adapter_catalog() -> dict[str, Any]:
    source = REPOSITORY / "verification/live/latest-summary.json"
    summary = _load_json(source)
    adapters = []
    for item in summary["adapters"]:
        adapter_id = str(item["adapter_id"])
        namespace = adapter_id.split(".", 1)[0]
        adapters.append(
            {
                "adapter_id": adapter_id,
                "title": _humanize_adapter_id(adapter_id),
                "publisher": _PUBLISHERS.get(namespace, namespace.upper()),
                "temporal_coverage": item["temporal_coverage"],
                "historical_replay_eligible": item["historical_replay_eligible"],
                "record_count": item["record_count"],
                "retrieved_at": item["retrieved_at"],
                "receipt": item["receipt"],
            }
        )
    adapters.sort(key=lambda item: item["adapter_id"])
    coverage_counts: dict[str, int] = {}
    for item in adapters:
        key = str(item["temporal_coverage"])
        coverage_counts[key] = coverage_counts.get(key, 0) + 1
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "catalog_kind": "formal_live_adapter_user_catalog",
        "adapter_count": len(adapters),
        "historical_replay_eligible_count": sum(
            bool(item["historical_replay_eligible"]) for item in adapters
        ),
        "temporal_coverage_counts": dict(sorted(coverage_counts.items())),
        "source_path": source.relative_to(REPOSITORY).as_posix(),
        "source_sha256": _file_sha256(source),
        "claim_boundary": (
            "This installable catalog describes the 30 formal live-validation adapters at the "
            "recorded retrieval times. A validated live response is not automatically eligible "
            "for historical replay. Scenario-bounded archive connectors are a separate surface."
        ),
        "adapters": adapters,
    }
    payload["catalog_sha256"] = _hash(payload)
    return payload


def _scenario_catalog() -> tuple[dict[str, Any], list[tuple[Path, Path]]]:
    summary_path = REPOSITORY / "verification/scenarios/latest-summary.json"
    summary = _load_json(summary_path)
    entries = []
    input_locks: list[tuple[Path, Path]] = []
    for summary_item in summary["scenarios"]:
        proof_path = REPOSITORY / "verification/scenarios/proofs" / summary_item["proof"]
        proof = _load_json(proof_path)
        pack_directory = REPOSITORY / proof["pack_directory"]
        report_path = pack_directory / "report.json"
        report = CompiledReplayPack.model_validate_json(report_path.read_text(encoding="utf-8"))
        loader_name, builder_name = _runner_names(REPOSITORY / proof["build_script"]["path"])
        input_lock = REPOSITORY / proof["input_locks"][0]["path"]
        slug = input_lock.parent.name
        resource_path = Path("scenarios") / slug / "input-lock.json"
        input_locks.append((input_lock, resource_path))
        entries.append(
            {
                "slug": slug,
                "scenario_id": summary_item["scenario_id"],
                "scenario_version": summary_item["scenario_version"],
                "replay_id": summary_item["replay_id"],
                "title": report.spec.title,
                "mode": summary_item["mode"],
                "decision_time": summary_item["decision_time"],
                "code_commit": report.spec.code_commit,
                "distinct_input_records": summary_item["distinct_input_records"],
                "source_set_historical_replay_eligible": (
                    report.source_set_historical_replay_eligible
                ),
                "pack_sha256": summary_item["pack_sha256"],
                "trace_id": summary_item["trace_id"],
                "proof_sha256": summary_item["proof_sha256"],
                "proof_path": proof_path.relative_to(REPOSITORY).as_posix(),
                "report_path": report_path.relative_to(REPOSITORY).as_posix(),
                "input_lock_resource": resource_path.as_posix(),
                "input_lock_sha256": _file_sha256(input_lock),
                "loader": loader_name,
                "builder": builder_name,
            }
        )
    entries.sort(key=lambda item: item["scenario_id"])
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "catalog_kind": "installable_offline_scenario_runner_catalog",
        "scenario_count": len(entries),
        "source_path": summary_path.relative_to(REPOSITORY).as_posix(),
        "source_sha256": _file_sha256(summary_path),
        "claim_boundary": (
            "Every bundled scenario input lock is copied byte-for-byte from a counted, internally "
            "verified repository proof. Running it reproduces repository behavior locally; it "
            "does not create external method validation, deployment, investment performance, or "
            "real-world impact evidence."
        ),
        "scenarios": entries,
    }
    payload["catalog_sha256"] = _hash(payload)
    return payload, input_locks


def _runner_names(path: Path) -> tuple[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "finreplay.scenarios":
            imported.extend(alias.name for alias in node.names)
    loaders = [
        name for name in imported if name.startswith("load_") and name.endswith("_input_lock")
    ]
    builders = [
        name for name in imported if name.startswith("build_") and name.endswith("_replay_spec")
    ]
    if len(loaders) != 1 or len(builders) != 1:
        raise ValueError(f"unable to identify one scenario runner pair in {path}")
    return loaders[0], builders[0]


def _remove_stale_input_locks(expected: set[Path]) -> None:
    root = RESOURCE_ROOT / "scenarios"
    if not root.exists():
        return
    for path in root.glob("*/input-lock.json"):
        if path not in expected:
            path.unlink()
            if not any(path.parent.iterdir()):
                path.parent.rmdir()


def _humanize_adapter_id(adapter_id: str) -> str:
    words = adapter_id.replace("_", " ").replace(".", " · ").split()
    return " ".join(word.upper() if len(word) <= 4 else word.title() for word in words)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _serialize(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _catalog_matrix(adapter_payload: dict[str, Any], scenario_payload: dict[str, Any]) -> bytes:
    lines = [
        "# Catalog and eligibility matrix",
        "",
        "This file is generated by `python scripts/build_user_catalogs.py --write`. Do not edit",
        "the counted rows by hand. Live validation and historical replay eligibility are separate",
        "claims; scenario-bounded archive connectors are not silently counted as formal adapters.",
        "",
        "## Formal live-validation adapters",
        "",
        "| Adapter ID | Publisher | Temporal coverage | Historical replay eligible | "
        "Records | Receipt |",
        "|---|---|---|---:|---:|---|",
    ]
    for entry in adapter_payload["adapters"]:
        lines.append(
            f"| `{entry['adapter_id']}` | {entry['publisher']} | "
            f"`{entry['temporal_coverage']}` | "
            f"{str(entry['historical_replay_eligible']).lower()} | "
            f"{entry['record_count']} | `{entry['receipt']}` |"
        )
    lines.extend(
        [
            "",
            f"Count: **{adapter_payload['adapter_count']}** formal live adapters; "
            f"**{adapter_payload['historical_replay_eligible_count']}** are historical-replay "
            "eligible in the recorded catalog.",
            "",
            "## Installable offline scenarios",
            "",
            "| Slug | Scenario ID | Mode | Decision time | Locked records | Proof |",
            "|---|---|---|---|---:|---|",
        ]
    )
    for entry in scenario_payload["scenarios"]:
        lines.append(
            f"| `{entry['slug']}` | `{entry['scenario_id']}` | `{entry['mode']}` | "
            f"{entry['decision_time']} | {entry['distinct_input_records']} | "
            f"[`{Path(entry['proof_path']).name}`](../{entry['proof_path']}) |"
        )
    lines.extend(
        [
            "",
            f"Count: **{scenario_payload['scenario_count']}** bundled offline scenario runners.",
            "",
            "## Claim boundary",
            "",
            adapter_payload["claim_boundary"],
            "",
            scenario_payload["claim_boundary"],
            "",
        ]
    )
    return "\n".join(lines).encode()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


if __name__ == "__main__":
    main()
