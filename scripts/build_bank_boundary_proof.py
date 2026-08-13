#!/usr/bin/env python3
"""Build and verify the eight-gate proof for a bank boundary ReplayPack."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import CompiledReplayPack, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    load_bank_boundary_input_lock,
    seal_scenario_proof,
    verify_scenario_proof,
)

REQUIRED_ASSERTIONS = (
    "all_artifacts_reproduced",
    "all_seven_engines_present",
    "allocator_retains_all_cash_boundary",
    "byte_identical_directory_rebuilds",
    "byte_identical_zip_rebuilds",
    "compiled_pack_matches_rebuild",
    "cross_engine_trace_is_stable",
    "distinct_locked_inputs_equal_seven",
    "execution_is_reference_only_simulation",
    "historical_source_sets_eligible",
    "post_decision_event_is_disjoint",
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
    build_script = root / "scripts/build_bank_boundary_replaypack.py"
    verify_script = root / "scripts/verify_bank_boundary_replaypack.py"

    lock = load_bank_boundary_input_lock(input_path)
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
        raise SystemExit("ReplayPack identity does not match bank input lock")
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
            "official_adapter_inventory": "verification/live/latest-summary.json",
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
                        f"{prefix}.capitalallocator.robust-boundary",
                        f"{prefix}.markettwin.loss-envelope",
                        f"{prefix}.shockcompiler.htm-bound",
                        f"{prefix}.trialcourt.retrospective-gate",
                    )
                ),
                "bounded_artifact_ids": sorted(
                    (
                        f"{prefix}.capitalallocator.robust-boundary",
                        f"{prefix}.executionlab.normalized-envelope",
                        f"{prefix}.markettwin.loss-envelope",
                        f"{prefix}.shockcompiler.htm-bound",
                    )
                ),
                "simulated_artifact_ids": sorted(
                    (
                        f"{prefix}.capitalallocator.robust-boundary",
                        f"{prefix}.executionlab.normalized-envelope",
                        f"{prefix}.trialcourt.retrospective-gate",
                    )
                ),
                "absence_reasons": {
                    "observed": (
                        "The minimal decision lock contains filer-reported SEC accounting facts "
                        "and no direct market-tape, order-book, deposit-flow, or regulator-"
                        "observed record. Post-decision official event metadata is kept in a "
                        "separate lock."
                    )
                },
            },
            "naive_baselines": [
                {
                    "artifact_id": f"{prefix}.capitalallocator.robust-boundary",
                    "payload_pointer": "/problem/assets/0/current_weight",
                    "expected_value": 0.0,
                    "description": (
                        "The explicit status-quo baseline assigns zero weight to the synthetic "
                        "model exposure."
                    ),
                },
                {
                    "artifact_id": f"{prefix}.capitalallocator.robust-boundary",
                    "payload_pointer": "/problem/current_cash_weight",
                    "expected_value": 1.0,
                    "description": (
                        "The explicit status-quo baseline begins with the entire model portfolio "
                        "in cash."
                    ),
                },
            ],
            "deliberate_failure_modes": [
                {
                    "artifact_id": f"{prefix}.trialcourt.retrospective-gate",
                    "payload_pointer": "/decision/disposition",
                    "expected_value": "reject",
                    "description": (
                        "The retrospective non-inferential screen must be rejected rather than "
                        "promoted."
                    ),
                },
                {
                    "artifact_id": f"{prefix}.trialcourt.retrospective-gate",
                    "payload_pointer": "/manifest/rejected_decisions",
                    "expected_value": 1,
                    "description": (
                        "The immutable TrialCourt ledger must retain exactly one rejected decision."
                    ),
                },
            ],
            "required_rebuild_assertions": REQUIRED_ASSERTIONS,
            "claim_boundary": (
                f"This proof counts one internally reproduced historical boundary scenario for "
                f"{lock.issuer_label} because official SEC decision-input timing, a separately "
                "locked post-decision SEC event, immutable inputs, separated evidence labels, an "
                "explicit status-quo baseline, deliberate TrialCourt failure, deterministic "
                "outputs, limitations, and a clean-worktree double rebuild all verify. The event "
                "lock is excluded from replay inputs. This does not establish causal completeness, "
                "external method correctness, deployment, investment performance, or user impact."
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
