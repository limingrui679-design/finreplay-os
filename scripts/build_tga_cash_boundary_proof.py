#!/usr/bin/env python3
"""Build and verify the eight-gate proof for the Treasury TGA boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import CompiledReplayPack, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    load_tga_cash_boundary_input_lock,
    seal_scenario_proof,
    verify_scenario_proof,
)

REQUIRED_ASSERTIONS = (
    "all_artifacts_reproduced",
    "byte_identical_directory_rebuilds",
    "byte_identical_zip_rebuilds",
    "compiled_pack_matches_rebuild",
    "cross_engine_trace_is_stable",
    "distinct_locked_inputs_equal_two",
    "future_event_excluded_from_bound_construction",
    "historical_source_sets_eligible",
    "naive_baseline_is_latest_known_tga_persistence",
    "no_probability_is_assigned",
    "only_relevant_engines_present",
    "post_decision_event_is_disjoint",
    "post_decision_event_is_exact_archived_dts_fact",
    "post_decision_event_timing_is_later",
    "post_event_differs_from_persistence_baseline",
    "reported_post_event_balance_inside_declared_range",
    "simulation_remains_visible",
    "trialcourt_rejects_retrospective_attempt",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--input-lock", type=Path, required=True)
    parser.add_argument("--event-lock", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--rebuild-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repository_root.expanduser().resolve()
    input_path = _resolve(root, args.input_lock)
    event_path = _resolve(root, args.event_lock)
    pack_path = _resolve(root, args.pack)
    rebuild_path = _resolve(root, args.rebuild_receipt)
    output_path = _resolve_output(root, args.output)
    build_script = root / "scripts/build_tga_cash_boundary_replaypack.py"
    verify_script = root / "scripts/verify_tga_cash_boundary_replaypack.py"

    lock = load_tga_cash_boundary_input_lock(input_path)
    event_lock = OfficialEventLock.model_validate_json(event_path.read_text(encoding="utf-8"))
    receipt = ReplayStudio().verify(pack_path)
    compiled = CompiledReplayPack.model_validate_json(
        (pack_path / "report.json").read_text(encoding="utf-8")
    )
    if (compiled.spec.scenario_id, compiled.spec.scenario_version, compiled.spec.replay_id) != (
        lock.scenario_id,
        lock.scenario_version,
        lock.replay_id,
    ):
        raise SystemExit("ReplayPack identity does not match TGA cash boundary input lock")
    prefix = lock.artifact_prefix
    record_ids = tuple(sorted(record.record_id for record in lock.records))
    event_record_ids = tuple(sorted(record.record_id for record in event_lock.records))
    proof = seal_scenario_proof(
        {
            "schema_version": "1.1.0",
            "scenario_id": lock.scenario_id,
            "scenario_version": lock.scenario_version,
            "replay_id": lock.replay_id,
            "pack_directory": _relative(root, pack_path),
            "expected_pack_sha256": compiled.pack_sha256,
            "expected_trace_id": compiled.trace_id,
            "expected_input_manifest_sha256": compiled.input_manifest_sha256,
            "official_adapter_inventory": (
                "verification/supporting/treasury-dts/latest-summary.json"
            ),
            "input_locks": [
                {
                    "path": _relative(root, input_path),
                    "sha256": _file_hash(input_path),
                    "lock_sha256": lock.lock_sha256,
                    "record_ids": record_ids,
                }
            ],
            "event_locks": [
                {
                    "path": _relative(root, event_path),
                    "sha256": _file_hash(event_path),
                    "lock_sha256": event_lock.lock_sha256,
                    "record_ids": event_record_ids,
                }
            ],
            "build_script": {
                "path": _relative(root, build_script),
                "sha256": _file_hash(build_script),
            },
            "verify_script": {
                "path": _relative(root, verify_script),
                "sha256": _file_hash(verify_script),
            },
            "rebuild_receipt": {
                "path": _relative(root, rebuild_path),
                "sha256": _file_hash(rebuild_path),
            },
            "timing_record_ids": record_ids,
            "input_labels": {
                "observed_record_ids": [],
                "reported_record_ids": record_ids,
                "extracted_artifact_ids": [f"{prefix}.replaystudio.render"],
                "inferred_artifact_ids": sorted(
                    (
                        f"{prefix}.shockcompiler.balance-range",
                        f"{prefix}.trialcourt.retrospective-gate",
                    )
                ),
                "bounded_artifact_ids": [f"{prefix}.shockcompiler.balance-range"],
                "simulated_artifact_ids": [f"{prefix}.trialcourt.retrospective-gate"],
                "absence_reasons": {
                    "observed": (
                        "The lock contains aggregate TGA balances reported in Treasury DTS PDFs, "
                        "not direct counterparty transactions, causal debt-limit effects, or "
                        "future cash observations. The June 2 balance is only in the event lock."
                    )
                },
            },
            "naive_baselines": [
                {
                    "artifact_id": f"{prefix}.shockcompiler.balance-range",
                    "payload_pointer": (
                        "/naive_baseline/next_reported_tga_closing_balance_millions"
                    ),
                    "expected_value": 22_892,
                    "description": (
                        "The decision-time baseline persists the latest known June 1 reported TGA "
                        "closing balance of 22,892 million dollars."
                    ),
                },
                {
                    "artifact_id": f"{prefix}.shockcompiler.balance-range",
                    "payload_pointer": "/bound_construction/probability_assigned",
                    "expected_value": False,
                    "description": (
                        "The two TGA closing-balance endpoints carry no probability or coverage "
                        "claim."
                    ),
                },
            ],
            "deliberate_failure_modes": [
                {
                    "artifact_id": f"{prefix}.trialcourt.retrospective-gate",
                    "payload_pointer": "/decision/disposition",
                    "expected_value": "reject",
                    "description": (
                        "TrialCourt must reject the retrospective two-report attempt rather than "
                        "promote it as a validated forecast."
                    ),
                },
                {
                    "artifact_id": f"{prefix}.trialcourt.retrospective-gate",
                    "payload_pointer": "/manifest/rejected_decisions",
                    "expected_value": 1,
                    "description": "The immutable trial ledger must retain one rejected decision.",
                },
            ],
            "required_rebuild_assertions": REQUIRED_ASSERTIONS,
            "claim_boundary": (
                "This proof counts one internally reproduced Treasury TGA cash boundary because "
                "two pre-decision date-stamped DTS facts, Treasury's following-business-day "
                "publication deadline, a separately locked post-decision June 2 report, an "
                "immutable input lock, separated truth labels, an explicit latest-balance "
                "persistence baseline, a no-probability bounded program, deliberate TrialCourt "
                "failure, deterministic outputs, limitations, and a clean-worktree double rebuild "
                "all verify. The June 2 fact is excluded from ReplayPack inputs and used only for "
                "a labelled post-event check. This does not establish forecast skill, calibrated "
                "coverage, debt-limit causality, fiscal solvency, policy effectiveness, external "
                "validation, deployment, investment performance, or user impact."
            ),
        }
    )
    content = (
        json.dumps(proof.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode()
    if output_path.exists() and output_path.read_bytes() != content:
        raise SystemExit("scenario proof destination contains different bytes")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    verified = verify_scenario_proof(output_path, repository_root=root)
    if verified.pack_sha256 != receipt.pack_sha256:
        raise SystemExit("new proof does not bind the verified pack receipt")
    print(
        f"verified=true scenario={verified.scenario_id} proof_sha256={verified.proof_sha256} "
        f"pack_sha256={verified.pack_sha256} output={output_path}"
    )


def _resolve(root: Path, value: Path) -> Path:
    path = value if value.is_absolute() else root / value
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(root) or not resolved.exists():
        raise SystemExit(f"input path must exist inside repository: {value}")
    return resolved


def _resolve_output(root: Path, value: Path) -> Path:
    path = value if value.is_absolute() else root / value
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(root):
        raise SystemExit(f"output path must remain inside repository: {value}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
