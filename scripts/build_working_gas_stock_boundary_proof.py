#!/usr/bin/env python3
"""Build and verify the eight-gate proof for the March 2020 EIA WNGSR boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import CompiledReplayPack, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    load_working_gas_stock_boundary_input_lock,
    seal_scenario_proof,
    verify_scenario_proof,
)

REQUIRED_ASSERTIONS = (
    "all_artifacts_reproduced",
    "byte_identical_directory_rebuilds",
    "byte_identical_zip_rebuilds",
    "compiled_pack_matches_rebuild",
    "cross_engine_trace_is_stable",
    "current_history_cross_check_is_explicit",
    "declared_range_reuses_only_one_known_decline",
    "distinct_locked_inputs_equal_two",
    "future_event_excluded_from_bound_construction",
    "historical_source_sets_eligible",
    "locked_original_release_pair_is_exact",
    "naive_baseline_is_march13_stock_persistence",
    "no_probability_is_assigned",
    "only_relevant_engines_present",
    "original_value_recovery_is_explicit",
    "post_decision_event_is_disjoint",
    "post_decision_event_is_exact_wngsr_fact",
    "post_decision_event_timing_is_later",
    "post_event_lower_bound_breach_equals_20_bcf",
    "release_time_and_source_count_are_explicit",
    "reported_post_event_level_is_below_declared_range",
    "reported_stock_and_sampling_boundaries_are_explicit",
    "simulation_remains_visible",
    "source_statistics_are_not_used",
    "three_official_response_hashes_are_bound",
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
    build_script = root / "scripts/build_working_gas_stock_boundary_replaypack.py"
    verify_script = root / "scripts/verify_working_gas_stock_boundary_replaypack.py"

    lock = load_working_gas_stock_boundary_input_lock(input_path)
    event_lock = OfficialEventLock.model_validate_json(
        event_path.read_text(encoding="utf-8")
    )
    if (
        event_lock.scenario_id,
        event_lock.scenario_version,
        event_lock.decision_time,
    ) != (lock.scenario_id, lock.scenario_version, lock.decision_time):
        raise SystemExit("event-lock identity does not match EIA WNGSR input lock")
    receipt = ReplayStudio().verify(pack_path)
    compiled = CompiledReplayPack.model_validate_json(
        (pack_path / "report.json").read_text(encoding="utf-8")
    )
    if (compiled.spec.scenario_id, compiled.spec.scenario_version, compiled.spec.replay_id) != (
        lock.scenario_id,
        lock.scenario_version,
        lock.replay_id,
    ):
        raise SystemExit("ReplayPack identity does not match EIA WNGSR input lock")
    prefix = lock.artifact_prefix
    record_ids = tuple(sorted(record.record_id for record in lock.records))
    event_record_ids = tuple(sorted(record.record_id for record in event_lock.records))
    range_artifact = f"{prefix}.shockcompiler.working-gas-stock-range"
    trial_artifact = f"{prefix}.trialcourt.retrospective-gate"
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
                "verification/supporting/eia-wngsr/latest-summary.json"
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
                "inferred_artifact_ids": sorted((range_artifact, trial_artifact)),
                "bounded_artifact_ids": [range_artifact],
                "simulated_artifact_ids": [trial_artifact],
                "absence_reasons": {
                    "observed": (
                        "The lock contains sampled aggregate Lower 48 working-gas estimates "
                        "reported by EIA, not facility, operator, reservoir, pipeline, injection, "
                        "withdrawal, transaction, price, return, causal, or future observations. "
                        "The March 20 stock exists only in the disjoint event lock."
                    )
                },
            },
            "naive_baselines": [
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": (
                        "/naive_baseline/next_lower_48_working_gas_stock_bcf"
                    ),
                    "expected_value": 2_034,
                    "description": (
                        "The decision-time baseline persists the March 13 Lower 48 "
                        "working-gas stock of 2,034 Bcf."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/probability_assigned",
                    "expected_value": False,
                    "description": (
                        "The persistence-or-one-known-decline endpoints carry no probability, "
                        "confidence, or coverage claim."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": (
                        "/bound_construction/source_statistical_measures_used"
                    ),
                    "expected_value": False,
                    "description": (
                        "EIA coefficients of variation and weekly-net-change standard errors "
                        "remain reported source metadata and set neither endpoint."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/original_vintage_values_only",
                    "expected_value": True,
                    "description": (
                        "The two range inputs are the original estimates published on March 12 "
                        "and March 19 and recovered from EIA's revision-safe workbook."
                    ),
                },
            ],
            "deliberate_failure_modes": [
                {
                    "artifact_id": trial_artifact,
                    "payload_pointer": "/decision/disposition",
                    "expected_value": "reject",
                    "description": (
                        "TrialCourt must reject the retrospective one-decline attempt rather "
                        "than promote it as a validated forecast."
                    ),
                },
                {
                    "artifact_id": trial_artifact,
                    "payload_pointer": "/manifest/rejected_decisions",
                    "expected_value": 1,
                    "description": "The immutable trial ledger retains one rejected decision.",
                },
            ],
            "required_rebuild_assertions": REQUIRED_ASSERTIONS,
            "claim_boundary": (
                "This proof counts one internally reproduced March 2020 EIA WNGSR boundary "
                "because two original pre-decision Lower 48 stock estimates, three exact official "
                "response hashes, exact 10:30 a.m. America/New_York release timing, a separately "
                "locked March 26 release for the March 20 event, immutable locks, separated truth "
                "labels, an explicit March 13 stock-persistence baseline, a no-probability "
                "persistence-or-one-known-decline program, deliberate TrialCourt failure, "
                "deterministic outputs, limitations, and a clean-worktree double rebuild all "
                "verify. The event stock of 2,005 Bcf is excluded from ReplayPack inputs and used "
                "only for a labelled post-event check that preserves its 20 Bcf lower-bound breach "
                "rather than widening the 2,025-to-2,034 Bcf range. EIA coefficients of variation "
                "and weekly-net-change standard errors remain source metadata, not range inputs. "
                "Regional rounding differences are retained rather than forcibly reconciled. "
                "The official archive recovers original estimates and the current history matches "
                "the selected values, but later retrieval is not contemporaneous observation. "
                "This proof does not establish an EIA forecast, calibrated coverage, direct "
                "facility measurements, storage capacity, injection or withdrawal volumes, "
                "operator, reservoir, pipeline, price, market, pandemic or policy causality, "
                "external validation, deployment, investment performance, or user impact."
            ),
        }
    )
    content = (
        json.dumps(
            proof.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
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
