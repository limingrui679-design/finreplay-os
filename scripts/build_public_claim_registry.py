#!/usr/bin/env python3
"""Build a self-hashed registry for FinReplay's public quantitative claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from finreplay.engines import CompiledReplayPack
from finreplay.scale import load_sec_log_scale_manifest

_AFFIRMATIVE_INFLATION_PATTERNS = {
    "production_deployment": re.compile(
        r"\b(?:FinReplay(?: OS)?|the (?:platform|system)|we) "
        r"(?:is|was|has been|have been) (?:successfully )?deployed (?:in|to) production\b",
        re.IGNORECASE,
    ),
    "real_client_use": re.compile(
        r"\b(?:FinReplay(?: OS)?|the (?:platform|system)|we) "
        r"(?:serves|served|is used by|has) (?:paying |real |institutional )?clients\b",
        re.IGNORECASE,
    ),
    "investment_performance": re.compile(
        r"\b(?:FinReplay(?: OS)?|the (?:platform|strategy)|we) "
        r"(?:achieved|generated|delivered) .{0,40}\b(?:return|profit|P&L)\b",
        re.IGNORECASE,
    ),
    "external_validation": re.compile(
        r"\b(?:FinReplay(?: OS)?|the (?:platform|system)) "
        r"(?:is|was|has been) externally (?:validated|certified|reviewed)\b",
        re.IGNORECASE,
    ),
    "real_user_adoption": re.compile(
        r"\b(?:FinReplay(?: OS)?|the (?:platform|system)) "
        r"(?:has|serves) (?:real |active |paying )+users\b",
        re.IGNORECASE,
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/claims/public-claims.json"),
    )
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    readme_path = repository / "README.md"
    readme = readme_path.read_text(encoding="utf-8")

    seven_engine_path = repository / "verification/replaypacks/svb-2023-seven-engine/report.json"
    seven_engine_pack = CompiledReplayPack.model_validate_json(
        seven_engine_path.read_text(encoding="utf-8")
    )
    engine_count = len(seven_engine_pack.engine_artifact_counts)
    if engine_count != 7 or any(
        count != 1 for count in seven_engine_pack.engine_artifact_counts.values()
    ):
        raise SystemExit("seven-engine ReplayPack does not contain exactly one artifact per engine")

    live_summary_path = repository / "verification/live/latest-summary.json"
    live_summary = _load_json(live_summary_path)
    adapter_count = len(live_summary["adapters"])
    scenario_summary_path = repository / "verification/scenarios/latest-summary.json"
    scenario_summary = _load_json(scenario_summary_path)
    scenario_count = len(scenario_summary["scenarios"])
    scale_path = repository / "verification/scale/sec-edgar/latest-scale-manifest.json"
    scale = load_sec_log_scale_manifest(scale_path)
    expected_values = {
        "engine_count": 7,
        "adapter_count": 30,
        "scenario_count": 30,
        "scale_partition_count": 244,
        "scale_physical_row_count": 1_014_736_394,
    }
    observed_values = {
        "engine_count": engine_count,
        "adapter_count": adapter_count,
        "scenario_count": scenario_count,
        "scale_partition_count": scale.partition_count,
        "scale_physical_row_count": scale.total_distinct_physical_rows,
    }
    if observed_values != expected_values or not scale.target_met:
        raise SystemExit("headline public values differ from their committed machine evidence")
    for text in (
        "Seven connected engines",
        "30 live-validated",
        "30/30 internally replay-proven",
        "244 continuous",
        "1,014,736,394",
        "Public demo and external review | Not achieved",
    ):
        if text not in readme:
            raise SystemExit(f"README public claim is missing or changed: {text}")

    pack_entries: list[dict[str, Any]] = []
    evidence_classes: set[str] = set()
    public_claim_count = 0
    report_paths = sorted((repository / "verification/replaypacks").glob("*/report.json"))
    for path in report_paths:
        pack = CompiledReplayPack.model_validate_json(path.read_text(encoding="utf-8"))
        if "not " not in pack.spec.claim_boundary.lower() and "does not" not in (
            pack.spec.claim_boundary.lower()
        ):
            raise SystemExit(f"ReplayPack lacks an explicit negative claim boundary: {path}")
        public_claim_count += len(pack.spec.claims)
        evidence_classes.update(claim.evidence_class.value for claim in pack.spec.claims)
        pack_entries.append(
            {
                "path": path.relative_to(repository).as_posix(),
                "file_sha256": _file_sha256(path),
                "pack_sha256": pack.pack_sha256,
                "trace_id": pack.trace_id,
                "claim_count": len(pack.spec.claims),
                "claim_ids_sha256": _hash([claim.claim_id for claim in pack.spec.claims]),
                "evidence_classes": sorted(
                    {claim.evidence_class.value for claim in pack.spec.claims}
                ),
            }
        )

    scan_paths = _public_text_paths(repository)
    violations = _scan_inflated_claims(scan_paths, repository)
    if violations:
        raise SystemExit(f"affirmative unsupported public claims found: {violations}")
    text_hashes = [
        {
            "path": path.relative_to(repository).as_posix(),
            "sha256": _file_sha256(path),
        }
        for path in scan_paths
    ]
    headline_claims = [
        _claim(
            "seven-connected-engines",
            "README.md",
            engine_count,
            seven_engine_path,
            repository,
            "len(engine_artifact_counts), with every count equal to one",
        ),
        _claim(
            "official-adapters",
            "README.md",
            adapter_count,
            live_summary_path,
            repository,
            "len(adapters)",
        ),
        _claim(
            "replay-proven-scenarios",
            "README.md",
            scenario_count,
            scenario_summary_path,
            repository,
            "len(scenarios)",
        ),
        _claim(
            "sec-scale-partitions",
            "README.md",
            scale.partition_count,
            scale_path,
            repository,
            "partition_count",
        ),
        _claim(
            "sec-scale-physical-rows",
            "README.md",
            scale.total_distinct_physical_rows,
            scale_path,
            repository,
            "total_distinct_physical_rows with target_met=true",
        ),
    ]
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "registry_kind": "public_claim_traceability_and_inflation_guard",
        "builder_path": "scripts/build_public_claim_registry.py",
        "builder_sha256": _file_sha256(Path(__file__).resolve()),
        "headline_claims": headline_claims,
        "replaypack_surface": {
            "report_count": len(pack_entries),
            "public_claim_count": public_claim_count,
            "evidence_classes": sorted(evidence_classes),
            "reports": pack_entries,
        },
        "boundary_scan": {
            "scanned_text_file_count": len(scan_paths),
            "scanned_text_set_sha256": _hash(text_hashes),
            "rule_ids": sorted(_AFFIRMATIVE_INFLATION_PATTERNS),
            "violations": [],
            "required_readme_boundaries": [
                "Public-data cases are not clients; historical replays are not live trading.",
                "Simulated P&L is not investment performance.",
                (
                    "Tests and hashes prove internal behavior, not source authenticity "
                    "or real-world impact."
                ),
                "Public demo and external review | Not achieved",
            ],
        },
        "claim_boundary": (
            "This registry binds the README's headline quantitative claims to committed machine "
            "evidence and revalidates every structured ReplayPack claim against its support "
            "artifact and evidence class. The text scan rejects a bounded set of affirmative "
            "deployment, client, performance, validation, and adoption phrases; it is not a "
            "general natural-language proof and cannot establish external review, deployment, "
            "adoption, users, financial performance, or real-world impact."
        ),
    }
    required_boundaries = payload["boundary_scan"]["required_readme_boundaries"]
    if any(boundary not in readme for boundary in required_boundaries):
        raise SystemExit("README truth boundary is missing")
    payload["registry_sha256"] = _hash(payload)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"headline_claims={len(headline_claims)} reports={len(pack_entries)} "
        f"public_claims={public_claim_count} violations=0 "
        f"registry_sha256={payload['registry_sha256']}"
    )


def _claim(
    claim_id: str,
    public_artifact: str,
    observed_value: int,
    evidence_path: Path,
    repository: Path,
    extraction_rule: str,
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "public_artifact": public_artifact,
        "observed_value": observed_value,
        "evidence_path": evidence_path.relative_to(repository).as_posix(),
        "evidence_sha256": _file_sha256(evidence_path),
        "extraction_rule": extraction_rule,
    }


def _public_text_paths(repository: Path) -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    paths: list[Path] = []
    for raw in tracked:
        if not raw:
            continue
        relative = raw.decode()
        path = repository / relative
        if relative == "README.md" or (
            relative.startswith("docs/") and path.suffix == ".md"
        ) or (
            relative.startswith("verification/replaypacks/")
            and path.suffix in {".md", ".html", ".json"}
        ):
            paths.append(path)
    return sorted(paths)


def _scan_inflated_claims(paths: list[Path], repository: Path) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for rule_id, pattern in _AFFIRMATIVE_INFLATION_PATTERNS.items():
            for match in pattern.finditer(text):
                violations.append(
                    {
                        "rule_id": rule_id,
                        "path": path.relative_to(repository).as_posix(),
                        "line": text.count("\n", 0, match.start()) + 1,
                        "match": match.group(0),
                    }
                )
    return violations


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


if __name__ == "__main__":
    main()
