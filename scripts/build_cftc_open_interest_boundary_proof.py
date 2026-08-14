#!/usr/bin/env python3
"""Build and verify the eight-gate proof for the July 2026 CFTC TFF boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import CompiledReplayPack, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    load_cftc_open_interest_boundary_input_lock,
    seal_scenario_proof,
    verify_scenario_proof,
)

REQUIRED_ASSERTIONS = (
    "all_artifacts_reproduced",
    "api_annual_crosscheck_is_explicit",
    "auxiliary_source_fields_are_not_used",
    "byte_identical_directory_rebuilds",
    "byte_identical_zip_rebuilds",
    "classification_and_measurement_boundaries_are_explicit",
    "compiled_pack_matches_rebuild",
    "cross_engine_trace_is_stable",
    "declared_range_reuses_only_one_known_decline",
    "distinct_locked_inputs_equal_two",
    "five_official_response_hashes_are_bound",
    "fixed_range_is_not_widened_after_event",
    "future_event_excluded_from_bound_construction",
    "historical_source_sets_eligible",
    "idempotent_supporting_receipt_is_bound",
    "locked_report_rows_are_exact",
    "naive_baseline_is_july21_persistence",
    "no_probability_is_assigned",
    "only_relevant_engines_present",
    "post_decision_event_is_disjoint",
    "post_decision_event_is_exact_cftc_fact",
    "post_decision_event_timing_is_later",
    "post_event_reported_change_equals_71513_contracts",
    "post_event_upper_bound_breach_equals_71513_contracts",
    "reported_post_event_level_is_above_declared_range",
    "scheduled_timing_uncertainty_is_explicit",
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
    build_script = root / "scripts/build_cftc_open_interest_boundary_replaypack.py"
    verify_script = root / "scripts/verify_cftc_open_interest_boundary_replaypack.py"

    lock = load_cftc_open_interest_boundary_input_lock(input_path)
    event_lock = OfficialEventLock.model_validate_json(
        event_path.read_text(encoding="utf-8")
    )
    if (
        event_lock.scenario_id,
        event_lock.scenario_version,
        event_lock.decision_time,
    ) != (lock.scenario_id, lock.scenario_version, lock.decision_time):
        raise SystemExit("event-lock identity does not match CFTC TFF input lock")
    receipt = ReplayStudio().verify(pack_path)
    compiled = CompiledReplayPack.model_validate_json(
        (pack_path / "report.json").read_text(encoding="utf-8")
    )
    if (compiled.spec.scenario_id, compiled.spec.scenario_version, compiled.spec.replay_id) != (
        lock.scenario_id,
        lock.scenario_version,
        lock.replay_id,
    ):
        raise SystemExit("ReplayPack identity does not match CFTC TFF input lock")
    prefix = lock.artifact_prefix
    record_ids = tuple(sorted(record.record_id for record in lock.records))
    event_record_ids = tuple(sorted(record.record_id for record in event_lock.records))
    range_artifact = f"{prefix}.shockcompiler.open-interest-range"
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
                "verification/supporting/cftc-tff-schedule/latest-summary.json"
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
                        "The lock contains aggregate open-interest counts reported by CFTC, "
                        "not observations of traders, accounts, orders, executions, volume, "
                        "direction, intent, notional exposure, returns, or user activity. The "
                        "July 28 row exists only in the disjoint event lock."
                    )
                },
            },
            "naive_baselines": [
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": (
                        "/naive_baseline/next_ust_2y_tff_open_interest_contracts"
                    ),
                    "expected_value": 4_335_075,
                    "description": (
                        "The decision-time baseline persists the July 21 total open-interest "
                        "level of 4,335,075 futures contracts."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/probability_assigned",
                    "expected_value": False,
                    "description": (
                        "The two mechanical endpoints carry no probability, confidence, or "
                        "coverage claim."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/category_positions_used",
                    "expected_value": False,
                    "description": (
                        "Trader-category positions remain source context and set neither "
                        "open-interest endpoint."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/trader_counts_used",
                    "expected_value": False,
                    "description": (
                        "Trader counts remain source context and set neither open-interest "
                        "endpoint."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/contract_face_value_used",
                    "expected_value": False,
                    "description": (
                        "The contract face-value label is not converted into or used as "
                        "notional exposure."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/future_event_used",
                    "expected_value": False,
                    "description": (
                        "The later July 28 open-interest value never enters range construction."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/total_open_interest_only",
                    "expected_value": True,
                    "description": (
                        "Only reported aggregate total open interest defines the two endpoints."
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
                "This proof counts one internally reproduced July 2026 CFTC Traders in "
                "Financial Futures boundary because two pre-decision U.S. Treasury 2-Year "
                "Note Futures Only rows, five exact official response hashes, API/annual-file "
                "agreement, the official scheduled 3:30 p.m. Eastern boundary, immutable "
                "locks, separated truth labels, a July 21 persistence baseline, a no-"
                "probability persistence-or-one-known-decline program, deliberate TrialCourt "
                "failure, deterministic outputs, limitations, and a clean-checkout double "
                "rebuild all verify. The schedule calls itself tentative and CFTC provides no "
                "row-level actual-publication log, so timing confidence remains 0.98 rather "
                "than being represented as an exact actual release time. The July 28 value of "
                "4,406,588 contracts is excluded from ReplayPack inputs and used only for a "
                "labelled post-event check that preserves its 71,513-contract upper-bound "
                "breach rather than widening the fixed 4,204,951-to-4,335,075 range. Category "
                "positions, trader counts, spreading fields, and the contract face-value label "
                "remain source context and set no endpoint. This proof does not establish "
                "actual publication to the second, a CFTC forecast, calibrated coverage, "
                "trader direction or intent, accounts, orders, volume, executions, notional "
                "exposure, P&L, market impact, causality, investment performance, external "
                "validation, deployment, or user impact."
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
        f"verified=true scenario={verified.scenario_id} "
        f"proof_sha256={verified.proof_sha256} "
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
